#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Authenticated Linux SSH-node agent for ShopVPN.

Each account receives an isolated sshd listener on a unique TCP port. When nftables
accounting is enabled, input and output bytes on that port are counted independently,
so the counter does not rely on sshd socket UID ownership. Run the API behind TLS or
on a private network; the agent uses argv-only system commands and SQLite.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import pwd

from aiohttp import web

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")
ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
logger = logging.getLogger(__name__)
# web.AppKey فقط از aiohttp 3.9 به بعد وجود دارد؛ Ubuntu 22.04 نسخه 3.8 دارد.
ENFORCEMENT_TASK_KEY = web.AppKey("enforcement_task", asyncio.Task) if hasattr(web, "AppKey") else "enforcement_task"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Agent:
    def __init__(self, token: str, db_path: str, nft_accounting: bool | None = None):
        if len(token) < 32:
            raise ValueError("SHOPVPN_AGENT_TOKEN must contain at least 32 characters")
        self.token = token.encode()
        self.db_path = db_path
        self.nft_accounting = (os.getenv("SHOPVPN_NFT_ACCOUNTING", "0").strip().lower() in {"1", "true", "yes", "on"}) if nft_accounting is None else bool(nft_accounting)
        self.nft_available = False
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    ssh_port INTEGER UNIQUE, status TEXT NOT NULL DEFAULT 'active', expires_at TEXT,
                    limit_bytes INTEGER NOT NULL DEFAULT 0,
                    disabled_reason TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage (
                    account_id TEXT PRIMARY KEY REFERENCES accounts(account_id) ON DELETE CASCADE,
                    used_bytes INTEGER NOT NULL DEFAULT 0,
                    uploaded_bytes INTEGER NOT NULL DEFAULT 0,
                    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                    raw_counter_bytes INTEGER NOT NULL DEFAULT 0,
                    counter_offset_bytes INTEGER NOT NULL DEFAULT 0,
                    reported_at TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'unreported'
                );
            """)
            # Safe additive migration for existing agent databases.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(accounts)").fetchall()}
            if "limit_bytes" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN limit_bytes INTEGER NOT NULL DEFAULT 0")
            if "ssh_port" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN ssh_port INTEGER")
            if "disabled_reason" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN disabled_reason TEXT")
            usage_columns = {row["name"] for row in db.execute("PRAGMA table_info(usage)").fetchall()}
            if "raw_counter_bytes" not in usage_columns:
                db.execute("ALTER TABLE usage ADD COLUMN raw_counter_bytes INTEGER NOT NULL DEFAULT 0")
            if "counter_offset_bytes" not in usage_columns:
                db.execute("ALTER TABLE usage ADD COLUMN counter_offset_bytes INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _run(*args: str, input_text: str | None = None, check=True):
        return subprocess.run(args, input=input_text, text=True, capture_output=True,
                              timeout=20, check=check)

    @staticmethod
    def _nft_comment(direction: str, account_id: str) -> str:
        return f"shopvpn-{direction}:{account_id}"

    def _nft_rules(self, chain: str = "output"):
        if chain not in {"input", "output"}:
            raise ValueError("unsupported nftables chain")
        result = self._run("nft", "-j", "list", "chain", "inet", "shopvpn_ssh", chain, check=False)
        if result.returncode != 0:
            return []
        try:
            payload = json.loads(result.stdout or "{}")
        except ValueError:
            return []
        return [item["rule"] for item in payload.get("nftables", []) if isinstance(item, dict) and "rule" in item]

    def _ensure_nft_table(self):
        if not self.nft_accounting:
            return
        table = self._run("nft", "list", "table", "inet", "shopvpn_ssh", check=False)
        if table.returncode != 0:
            self._run("nft", "add", "table", "inet", "shopvpn_ssh")
        for chain in ("input", "output"):
            existing = self._run("nft", "list", "chain", "inet", "shopvpn_ssh", chain, check=False)
            if existing.returncode != 0:
                self._run("nft", "add", "chain", "inet", "shopvpn_ssh", chain,
                          "{ type filter hook " + chain + " priority filter; policy accept; }")
        self.nft_available = True

    def _add_nft_rule(self, account_id: str, port: int):
        """Count both directions of each account's dedicated SSH TCP port."""
        if not self.nft_accounting:
            return
        if not self.nft_available:
            raise RuntimeError("nftables per-account accounting is enabled but unavailable")
        if not port:
            raise RuntimeError("account has no dedicated SSH port; legacy account needs recreation")
        desired = {
            "input": self._nft_comment("in", account_id),
            "output": self._nft_comment("out", account_id),
        }
        for chain, comment in desired.items():
            rules = self._nft_rules(chain)
            if any(rule.get("comment") == comment for rule in rules):
                continue
            match = "dport" if chain == "input" else "sport"
            self._run("nft", "add", "rule", "inet", "shopvpn_ssh", chain,
                      "tcp", match, str(int(port)), "counter", "comment", json.dumps(comment))

    def _delete_nft_rule(self, account_id: str):
        if not self.nft_accounting or not self.nft_available:
            return
        for chain in ("input", "output"):
            for rule in self._nft_rules(chain):
                comment = str(rule.get("comment") or "")
                # Remove both current port rules and legacy per-UID output rules.
                if comment in {self._nft_comment("in", account_id), self._nft_comment("out", account_id), f"shopvpn:{account_id}"} and rule.get("handle") is not None:
                    self._run("nft", "delete", "rule", "inet", "shopvpn_ssh", chain,
                              "handle", str(rule["handle"]))

    def _collect_nft_usage(self):
        """Collect input+output bytes for each dedicated SSH port, not socket UID heuristics."""
        if not self.nft_accounting or not self.nft_available:
            return
        counters = {}
        for chain in ("input", "output"):
            direction = "in" if chain == "input" else "out"
            for rule in self._nft_rules(chain):
                comment = str(rule.get("comment") or "")
                prefix = f"shopvpn-{direction}:"
                if not comment.startswith(prefix):
                    continue
                account_id = comment[len(prefix):]
                byte_count = 0
                for expression in rule.get("expr", []):
                    counter = expression.get("counter") if isinstance(expression, dict) else None
                    if isinstance(counter, dict):
                        byte_count = int(counter.get("bytes", 0) or 0)
                entry = counters.setdefault(account_id, {"bytes": 0, "directions": set()})
                entry["bytes"] += max(0, byte_count)
                entry["directions"].add(direction)
        stamp = utc_now()
        with self._conn() as db:
            rows = db.execute("SELECT account_id,ssh_port,used_bytes,raw_counter_bytes,counter_offset_bytes,source FROM usage JOIN accounts USING(account_id)").fetchall()
            for row in rows:
                # Never mix external authoritative counters with nftables port counters.
                if row["source"] not in {"unreported", "nftables_port_counters"}:
                    continue
                account_id = row["account_id"]
                entry = counters.get(account_id)
                if not row["ssh_port"] or not entry or entry["directions"] != {"in", "out"}:
                    continue
                raw = int(entry["bytes"])
                old_raw = int(row["raw_counter_bytes"] or 0)
                old_used = int(row["used_bytes"] or 0)
                offset = int(row["counter_offset_bytes"] or 0)
                if raw < old_raw:
                    offset = old_used
                used = offset + raw
                db.execute("UPDATE usage SET used_bytes=?,raw_counter_bytes=?,counter_offset_bytes=?,uploaded_bytes=0,downloaded_bytes=0,reported_at=?,source='nftables_port_counters' WHERE account_id=?",
                           (used, raw, offset, stamp, account_id))

    def _allocate_ssh_port(self) -> int:
        with self._conn() as db:
            used = {int(row[0]) for row in db.execute("SELECT ssh_port FROM accounts WHERE ssh_port IS NOT NULL").fetchall()}
        for _ in range(400):
            port = 20000 + secrets.randbelow(20000)
            if port in used:
                continue
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("0.0.0.0", port))
            except OSError:
                continue
            finally:
                sock.close()
            return port
        raise RuntimeError("no free dedicated SSH port available in range 20000-39999")

    def _write_account_sshd_config(self, account_id: str, username: str, port: int) -> str:
        root = Path("/etc/shopvpn/sshd")
        root.mkdir(parents=True, exist_ok=True)
        config_path = root / f"{account_id}.conf"
        Path(f"/run/sshd-shopvpn-{account_id}").mkdir(parents=True, exist_ok=True)
        Path("/run/sshd").mkdir(parents=True, exist_ok=True)
        host_keys = [p for p in ("/etc/ssh/ssh_host_ed25519_key", "/etc/ssh/ssh_host_rsa_key") if os.path.isfile(p)]
        if not host_keys:
            raise RuntimeError("OpenSSH host keys are missing; run ssh-keygen -A")
        lines = [
            f"Port {int(port)}", "AddressFamily any", "ListenAddress 0.0.0.0", "ListenAddress ::",
            *[f"HostKey {key}" for key in host_keys],
            f"PidFile /run/sshd-shopvpn-{account_id}/sshd.pid", "UsePAM yes",
            "PasswordAuthentication yes", "KbdInteractiveAuthentication no", "PubkeyAuthentication no",
            "PermitRootLogin no", f"AllowUsers {username}", "AllowTcpForwarding yes",
            "AllowStreamLocalForwarding no", "AllowAgentForwarding no", "X11Forwarding no",
            "PermitTTY no", "PermitTunnel no", "MaxSessions 0", "ForceCommand /usr/sbin/nologin",
        ]
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)
        self._run("sshd", "-t", "-f", str(config_path))
        return str(config_path)

    @staticmethod
    def _unit_name(account_id: str) -> str:
        return f"shopvpn-sshd@{account_id}.service"

    def _start_account_sshd(self, account_id: str):
        self._run("systemctl", "enable", "--now", self._unit_name(account_id))

    def _stop_account_sshd(self, account_id: str):
        self._run("systemctl", "disable", "--now", self._unit_name(account_id), check=False)
        try:
            (Path("/etc/shopvpn/sshd") / f"{account_id}.conf").unlink(missing_ok=True)
        except Exception:
            logger.exception("Unable to remove sshd config for account %s", account_id)

    async def auth(self, request):
        supplied = request.headers.get("Authorization", "")
        prefix = "Bearer "
        if not supplied.startswith(prefix) or not hmac.compare_digest(supplied[len(prefix):].encode(), self.token):
            raise web.HTTPUnauthorized(text="unauthorized")

    async def health(self, request):
        return web.json_response({"ok": True, "agent": "shopvpn-ssh", "version": 1})

    def _ensure_ssh_group(self):
        """Create a dedicated group for tunnel-only ShopVPN accounts."""
        result = self._run("getent", "group", "shopvpnssh", check=False)
        if getattr(result, "returncode", 1) != 0:
            self._run("groupadd", "--system", "shopvpnssh")

    async def create_account(self, request):
        body = await request.json()
        account_id = str(body.get("account_id", ""))
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        expires_at = body.get("expires_at")
        try:
            limit_bytes = int(body.get("limit_bytes", 0))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="limit_bytes must be an integer")
        if limit_bytes < 0 or limit_bytes > 2**63 - 1:
            raise web.HTTPBadRequest(text="invalid limit_bytes; zero means unlimited")
        if expires_at:
            try:
                datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            except ValueError:
                raise web.HTTPBadRequest(text="expires_at must be ISO-8601")
        if not ACCOUNT_ID_RE.fullmatch(account_id) or not USERNAME_RE.fullmatch(username):
            raise web.HTTPBadRequest(text="invalid account_id or username")
        if len(password) < 16 or len(password) > 128 or "\n" in password or "\r" in password or ":" in password:
            raise web.HTTPBadRequest(text="password must be 16-128 safe characters")
        with self._conn() as db:
            if db.execute("SELECT 1 FROM accounts WHERE account_id=? OR username=?", (account_id, username)).fetchone():
                raise web.HTTPConflict(text="account already exists")
        # Every account gets its own sshd listener port so both ingress and egress
        # bytes can be attributed by port rather than guessing from socket UID.
        ssh_port = None
        try:
            if self.nft_accounting and not self.nft_available:
                self._ensure_nft_table()
            self._ensure_ssh_group()
            nologin = "/usr/sbin/nologin" if os.path.exists("/usr/sbin/nologin") else "/sbin/nologin"
            ssh_port = self._allocate_ssh_port()
            self._run("useradd", "--create-home", "--gid", "shopvpnssh", "--shell", nologin, "--password", "!", username)
            self._run("chpasswd", input_text=f"{username}:{password}\n")
            self._write_account_sshd_config(account_id, username, ssh_port)
            self._start_account_sshd(account_id)
            self._add_nft_rule(account_id, ssh_port)
            now = utc_now()
            with self._conn() as db:
                db.execute("INSERT INTO accounts(account_id,username,ssh_port,status,expires_at,limit_bytes,created_at,updated_at) VALUES(?,?,?, 'active',?,?,?,?)",
                           (account_id, username, ssh_port, expires_at, limit_bytes, now, now))
                db.execute("INSERT INTO usage(account_id,reported_at,source) VALUES(?,?,?)", (account_id, now, "unreported"))
        except Exception as exc:
            try:
                if ssh_port:
                    self._stop_account_sshd(account_id)
                self._delete_nft_rule(account_id)
            except Exception:
                pass
            self._run("userdel", "--remove", username, check=False)
            detail = type(exc).__name__
            if isinstance(exc, subprocess.CalledProcessError):
                # فقط نام دستور و stderr (رمز هرگز در args/stderr نیست) برای عیب‌یابی
                detail += f" [{(exc.cmd or ['?'])[0]}]: {(exc.stderr or '').strip()[-200:]}"
            raise web.HTTPInternalServerError(text=f"account creation failed: {detail}")
        return web.json_response({"ok": True, "account_id": account_id, "username": username,
                                  "port": ssh_port, "status": "active", "limit_bytes": limit_bytes}, status=201)

    async def list_accounts(self, request):
        with self._conn() as db:
            rows = db.execute("SELECT account_id,username,ssh_port,status,expires_at,limit_bytes,disabled_reason,created_at,updated_at FROM accounts ORDER BY created_at DESC").fetchall()
        return web.json_response({"accounts": [dict(r) for r in rows]})

    async def account_action(self, request):
        account_id = request.match_info["account_id"]
        action = request.match_info["action"]
        with self._conn() as db:
            row = db.execute("SELECT username,status,ssh_port FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        if not row:
            raise web.HTTPNotFound(text="account not found")
        username = row["username"]
        body = {}
        if request.can_read_body:
            try:
                body = await request.json()
            except (ValueError, json.JSONDecodeError):
                raise web.HTTPBadRequest(text="request body must be JSON")
        reason = str(body.get("reason") or "manual")
        if reason not in {"manual", "stale", "quota_exceeded", "expired"}:
            raise web.HTTPBadRequest(text="invalid disable reason")
        try:
            if action == "enable":
                self._run("usermod", "--unlock", username)
                if row["ssh_port"]:
                    self._start_account_sshd(account_id)
                status = "active"
            elif action == "disable":
                self._run("usermod", "--lock", username)
                self._run("pkill", "-KILL", "-u", username, check=False)
                if row["ssh_port"]:
                    self._run("systemctl", "disable", "--now", self._unit_name(account_id), check=False)
                status = "disabled"
            elif action == "delete":
                self._run("pkill", "-KILL", "-u", username, check=False)
                if row["ssh_port"]:
                    self._stop_account_sshd(account_id)
                self._delete_nft_rule(account_id)
                self._run("userdel", "--remove", username)
                with self._conn() as db:
                    db.execute("DELETE FROM accounts WHERE account_id=?", (account_id,))
                return web.json_response({"ok": True, "account_id": account_id, "status": "deleted"})
            else:
                raise web.HTTPNotFound(text="unknown action")
        except web.HTTPException:
            raise
        except Exception as exc:
            raise web.HTTPBadGateway(text=f"system account operation failed: {type(exc).__name__}")
        with self._conn() as db:
            db.execute("UPDATE accounts SET status=?,disabled_reason=?,updated_at=? WHERE account_id=?",
                       (status, None if status == "active" else reason, utc_now(), account_id))
        return web.json_response({"ok": True, "account_id": account_id, "status": status})

    async def update_account(self, request):
        account_id = request.match_info["account_id"]
        body = await request.json()
        expires_at = body.get("expires_at")
        limit_bytes = body.get("limit_bytes")
        if expires_at:
            try:
                datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            except ValueError:
                raise web.HTTPBadRequest(text="expires_at must be ISO-8601")
        if limit_bytes is not None:
            try:
                limit_bytes = int(limit_bytes)
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text="limit_bytes must be an integer")
            if limit_bytes < 0 or limit_bytes > 2**63 - 1:
                raise web.HTTPBadRequest(text="invalid limit_bytes")
        with self._conn() as db:
            row = db.execute("SELECT 1 FROM accounts WHERE account_id=?", (account_id,)).fetchone()
            if not row:
                raise web.HTTPNotFound(text="account not found")
            if limit_bytes is None:
                db.execute("UPDATE accounts SET expires_at=?,updated_at=? WHERE account_id=?", (expires_at, utc_now(), account_id))
            else:
                db.execute("UPDATE accounts SET expires_at=?,limit_bytes=?,updated_at=? WHERE account_id=?",
                            (expires_at, limit_bytes, utc_now(), account_id))
        await self.enforce_account(account_id)
        return web.json_response({"ok": True, "account_id": account_id, "expires_at": expires_at,
                                  "limit_bytes": limit_bytes})

    async def enforce_account(self, account_id: str) -> bool:
        """Disable an active Linux account once its authoritative reported quota/expiry is reached."""
        with self._conn() as db:
            row = db.execute("SELECT a.username,a.ssh_port,a.status,a.expires_at,a.limit_bytes,u.used_bytes "
                             "FROM accounts a LEFT JOIN usage u USING(account_id) WHERE a.account_id=?",
                             (account_id,)).fetchone()
        if not row or row["status"] != "active":
            return False
        reason = None
        if row["expires_at"]:
            try:
                expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) >= expires.astimezone(timezone.utc):
                    reason = "expired"
            except ValueError:
                # Invalid legacy metadata must not silently disable an account.
                reason = None
        if not reason and int(row["limit_bytes"] or 0) > 0 and int(row["used_bytes"] or 0) >= int(row["limit_bytes"]):
            reason = "quota_exceeded"
        if not reason:
            return False
        self._run("usermod", "--lock", row["username"])
        # Locking a password alone does not terminate an already authenticated SSH tunnel.
        self._run("pkill", "-KILL", "-u", row["username"], check=False)
        if row["ssh_port"]:
            self._run("systemctl", "disable", "--now", self._unit_name(account_id), check=False)
        with self._conn() as db:
            db.execute("UPDATE accounts SET status='disabled',disabled_reason=?,updated_at=? "
                       "WHERE account_id=? AND status='active'", (reason, utc_now(), account_id))
        return True

    async def enforcement_loop(self, app):
        while True:
            try:
                if self.nft_accounting:
                    try:
                        self._collect_nft_usage()
                    except Exception:
                        # Counter failure must not crash enforcement; the last snapshot ages out centrally.
                        pass
                with self._conn() as db:
                    ids = [r["account_id"] for r in db.execute("SELECT account_id FROM accounts WHERE status='active'").fetchall()]
                for account_id in ids:
                    try:
                        await self.enforce_account(account_id)
                    except Exception:
                        # A single account failure must not stop enforcement for all other accounts.
                        continue
            except Exception:
                pass
            await asyncio.sleep(30)

    async def _start_enforcement(self, app):
        if self.nft_accounting:
            try:
                self._ensure_nft_table()
                with self._conn() as db:
                    rows = db.execute("SELECT account_id,ssh_port FROM accounts").fetchall()
                for row in rows:
                    if row["ssh_port"]:
                        self._add_nft_rule(row["account_id"], int(row["ssh_port"]))
                    else:
                        self._delete_nft_rule(row["account_id"])
            except Exception:
                self.nft_available = False
                logger.exception("nftables accounting initialization failed; SSH accounting will remain unavailable")
        app[ENFORCEMENT_TASK_KEY] = asyncio.create_task(self.enforcement_loop(app))

    async def _stop_enforcement(self, app):
        task = app.get(ENFORCEMENT_TASK_KEY)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def get_usage(self, request):
        account_id = request.match_info["account_id"]
        if self.nft_accounting and self.nft_available:
            try:
                self._collect_nft_usage()
            except Exception:
                logger.exception("Unable to refresh nftables usage for account %s", account_id)
        with self._conn() as db:
            row = db.execute("SELECT a.status,a.ssh_port,a.disabled_reason,u.* FROM accounts a LEFT JOIN usage u USING(account_id) WHERE a.account_id=?", (account_id,)).fetchone()
        if not row:
            raise web.HTTPNotFound(text="account not found")
        source = str(row["source"] or "unreported")
        # Unknown sources must never be treated as authoritative quota telemetry.
        trusted_sources = {"nftables_port_counters", "external-counter", "ssh-traffic-accountant"}
        return web.json_response({"account_id": account_id, "status": row["status"], "disabled_reason": row["disabled_reason"], "port": row["ssh_port"],
            "used_bytes": int(row["used_bytes"] or 0), "uploaded_bytes": int(row["uploaded_bytes"] or 0),
            "downloaded_bytes": int(row["downloaded_bytes"] or 0), "reported_at": row["reported_at"] or None,
            "source": source, "accounting_ready": source in trusted_sources})

    async def report_usage(self, request):
        body = await request.json()
        account_id = str(body.get("account_id", ""))
        has_uploaded = "uploaded_bytes" in body
        has_downloaded = "downloaded_bytes" in body
        try:
            used = int(body["used_bytes"])
            uploaded = int(body.get("uploaded_bytes", 0))
            downloaded = int(body.get("downloaded_bytes", 0))
        except (KeyError, TypeError, ValueError):
            raise web.HTTPBadRequest(text="byte counters must be integers")
        if min(used, uploaded, downloaded) < 0 or max(used, uploaded, downloaded) > 2**63 - 1:
            raise web.HTTPBadRequest(text="invalid byte counter")
        raw_stamp = str(body.get("reported_at") or utc_now())
        try:
            parsed_stamp = datetime.fromisoformat(raw_stamp.replace("Z", "+00:00"))
            if parsed_stamp.tzinfo is None:
                parsed_stamp = parsed_stamp.replace(tzinfo=timezone.utc)
            stamp = parsed_stamp.astimezone(timezone.utc).isoformat()
        except ValueError:
            raise web.HTTPBadRequest(text="reported_at must be a valid ISO-8601 timestamp")
        source = str(body.get("source") or "external-counter")[:80]
        with self._conn() as db:
            exists = db.execute("SELECT 1 FROM accounts WHERE account_id=?", (account_id,)).fetchone()
            if not exists:
                raise web.HTTPNotFound(text="account not found")
            old = db.execute("SELECT used_bytes,reported_at FROM usage WHERE account_id=?", (account_id,)).fetchone()
            # Cumulative counters must never roll backwards, even when the caller's timestamp is bad.
            if old and used < int(old["used_bytes"]):
                raise web.HTTPConflict(text="cumulative counter cannot decrease; reset needs an explicit epoch")
            previous = db.execute("SELECT uploaded_bytes,downloaded_bytes FROM usage WHERE account_id=?", (account_id,)).fetchone()
            if previous:
                if not has_uploaded:
                    uploaded = int(previous["uploaded_bytes"] or 0)
                if not has_downloaded:
                    downloaded = int(previous["downloaded_bytes"] or 0)
                if uploaded < int(previous["uploaded_bytes"]) or downloaded < int(previous["downloaded_bytes"]):
                    raise web.HTTPConflict(text="directional cumulative counters cannot decrease")
            if old and old["reported_at"] and stamp < old["reported_at"]:
                raise web.HTTPConflict(text="stale usage report")
            db.execute("UPDATE usage SET used_bytes=?,uploaded_bytes=?,downloaded_bytes=?,reported_at=?,source=?,raw_counter_bytes=0,counter_offset_bytes=? WHERE account_id=?",
                       (used, uploaded, downloaded, stamp, source, used, account_id))
        enforced = await self.enforce_account(account_id)
        return web.json_response({"ok": True, "account_id": account_id, "used_bytes": used,
                                  "reported_at": stamp, "quota_enforced": enforced})

    def app(self):
        app = web.Application(client_max_size=64 * 1024)
        allowed = {x.strip() for x in os.getenv("SHOPVPN_AGENT_ALLOWED_IPS", "").split(",") if x.strip()}

        @web.middleware
        async def ip_middleware(request, handler):
            if allowed and request.remote not in allowed:
                raise web.HTTPForbidden(text="forbidden")
            return await handler(request)
        app.middlewares.append(ip_middleware)

        @web.middleware
        async def auth_middleware(request, handler):
            if request.path != "/health":
                await self.auth(request)
            return await handler(request)
        app.middlewares.append(auth_middleware)
        app.on_startup.append(self._start_enforcement)
        app.on_cleanup.append(self._stop_enforcement)
        app.router.add_get("/health", self.health)
        app.router.add_post("/v1/accounts", self.create_account)
        app.router.add_get("/v1/accounts", self.list_accounts)
        app.router.add_post("/v1/accounts/{account_id}/{action:(enable|disable|delete)}", self.account_action)
        app.router.add_patch("/v1/accounts/{account_id}", self.update_account)
        app.router.add_get("/v1/accounts/{account_id}/usage", self.get_usage)
        app.router.add_post("/v1/usage/report", self.report_usage)
        return app


def main():
    parser = argparse.ArgumentParser(description="ShopVPN SSH node agent")
    parser.add_argument("--host", default=os.getenv("SHOPVPN_AGENT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SHOPVPN_AGENT_PORT", "8765")))
    parser.add_argument("--db", default=os.getenv("SHOPVPN_AGENT_DB", "/var/lib/shopvpn-ssh-agent/agent.db"))
    args = parser.parse_args()
    token = os.getenv("SHOPVPN_AGENT_TOKEN", "")
    agent = Agent(token, args.db)
    ssl_ctx = None
    cert, key = os.getenv("SHOPVPN_AGENT_TLS_CERT", ""), os.getenv("SHOPVPN_AGENT_TLS_KEY", "")
    if cert and key:
        import ssl
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_ctx.load_cert_chain(cert, key)
    web.run_app(agent.app(), host=args.host, port=args.port, access_log=None, ssl_context=ssl_ctx)


if __name__ == "__main__":
    main()
