#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Operational CLI for ShopVPN's Linux SSH nodes and shared quota pools.

Examples are documented in docs/ssh-unified-usage.md. Secrets are read from
process environment using explicit env:NAME references; they are never saved
as raw values in SQLite.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from database import Database
from quota_reconciler import QuotaReconciler, _env_secret_resolver
from ssh_providers import get_ssh_provider, SSHProviderError


def _json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _db(args):
    db = Database(args.db)
    db.init_db(owner_id=int(args.owner_id))
    return db


def _node(db, node_id):
    node = next((n for n in db.list_ssh_nodes() if int(n["id"]) == int(node_id)), None)
    if not node:
        raise ValueError(f"نود {node_id} پیدا نشد")
    return node


async def _run(args):
    db = _db(args)
    try:
        cmd = args.command
        if cmd == "create-pool":
            pool_id = db.create_quota_pool(args.user_id, args.limit_gb, args.expires_at, args.label)
            _json({"ok": True, "pool_id": pool_id, "user_id": args.user_id,
                   "limit_gb": args.limit_gb, "expires_at": args.expires_at})
        elif cmd == "attach-vpn":
            db.attach_custom_config_to_quota_pool(args.pool_id, args.custom_config_id)
            _json({"ok": True, "pool_id": args.pool_id, "service_id": f"custom_config:{args.custom_config_id}"})
        elif cmd == "add-linux-node":
            if not args.agent_url.startswith("https://") and not args.allow_insecure_http:
                raise ValueError("برای Agent از HTTPS استفاده کنید؛ HTTP فقط با --allow-insecure-http مجاز است")
            node_id = db.create_ssh_node(args.name, "linux", args.host, args.port,
                auth_type="api_token", username=args.ssh_username,
                secret_ref=f"env:{args.secret_env}",
                config_json={"agent_url": args.agent_url.rstrip("/"),
                             "allow_insecure_http": bool(args.allow_insecure_http)})
            _json({"ok": True, "node_id": node_id, "status": "pending",
                   "secret_ref": f"env:{args.secret_env}",
                   "note": "توکن را در environment سرویس ShopVPN تنظیم کنید؛ مقدار خام در دیتابیس ذخیره نشده است."})
        elif cmd == "test-node":
            node = _node(db, args.node_id)
            provider = get_ssh_provider(node, secret_resolver=_env_secret_resolver)
            ok = await provider.test_connection()
            db.update_ssh_node_status(args.node_id, "active" if ok else "error", None if ok else "Agent connection failed")
            _json({"ok": bool(ok), "node_id": args.node_id, "status": "active" if ok else "error"})
        elif cmd == "list-nodes":
            _json(db.list_ssh_nodes())
        elif cmd == "create-ssh-account":
            if args.limit_gb < 0 or args.days < 0:
                raise ValueError("سقف حجم و تعداد روز نمی‌توانند منفی باشند")
            node = _node(db, args.node_id)
            if node.get("status") != "active":
                raise ValueError("ابتدا اتصال نود را با test-node بررسی کنید")
            pool = None
            if args.quota_pool_id is not None:
                pool = db.get_quota_pool(args.quota_pool_id)
                if not pool or int(pool["user_id"]) != int(args.user_id) or pool["status"] != "active":
                    raise ValueError("استخر سهمیه فعال نیست یا متعلق به این کاربر نیست")
            provider = get_ssh_provider(node, secret_resolver=_env_secret_resolver)
            from ssh_credentials import encrypt_credential
            # Validate the encryption key before creating a remote system account.
            encrypt_credential("preflight-check")
            expires_at = args.expires_at
            if not expires_at and args.days:
                expires_at = (datetime.now(timezone.utc) + timedelta(days=args.days)).isoformat()
            limit_bytes = int(args.limit_gb * (1024 ** 3))
            result = await provider.create_account(args.username, limit_bytes, expires_at)
            row_id = None
            try:
                usage = await provider.get_usage(result.account_id)
                reliable_sources = {"nftables_port_counters", "external-counter", "ssh-traffic-accountant"}
                volume_limited = limit_bytes > 0 or bool(pool and int(pool.get("limit_bytes") or 0) > 0)
                if volume_limited and (not usage.get("accounting_ready") or usage.get("source") not in reliable_sources):
                    raise ValueError("سهمیه حجمی نیازمند شمارنده‌ی دوطرفه‌ی معتبر SSH است؛ حساب ایجاد نشد")
                credential_enc = encrypt_credential(result.metadata.get("password", ""))
                row_id = db.register_ssh_account(args.node_id, args.user_id, result.account_id,
                    result.username, result.host, result.port, args.quota_pool_id, expires_at, credential_enc)
                if usage.get("accounting_ready"):
                    db.record_usage_snapshot("ssh", result.account_id, int(usage["used_bytes"]),
                        uploaded_bytes=usage.get("uploaded_bytes", 0), downloaded_bytes=usage.get("downloaded_bytes", 0),
                        source=str(usage.get("source") or "ssh-agent"), reported_at=usage.get("reported_at"))
            except Exception:
                if row_id is not None:
                    db.delete_ssh_account_record(row_id)
                try:
                    await provider.delete_account(result.account_id)
                except Exception:
                    pass
                raise
            _json({"ok": True, "database_account_id": row_id, "account_id": result.account_id,
                   "username": result.username, "host": result.host, "port": result.port,
                   "password": result.metadata.get("password"), "expires_at": expires_at,
                   "quota_pool_id": args.quota_pool_id, "accounting_ready": bool(usage.get("accounting_ready")),
                   "warning": None if usage.get("source") in reliable_sources else "شمارنده‌ی معتبر مصرف فعال نیست"})
        elif cmd == "list-ssh-accounts":
            _json(db.list_ssh_accounts(user_id=args.user_id, node_id=args.node_id))
        elif cmd in {"enable-ssh-account", "disable-ssh-account", "delete-ssh-account"}:
            accounts = db.list_ssh_accounts()
            account = next((a for a in accounts if str(a["account_id"]) == args.account_id), None)
            if not account:
                raise ValueError("حساب SSH پیدا نشد")
            node = _node(db, account["node_id"])
            provider = get_ssh_provider(node, secret_resolver=_env_secret_resolver)
            if cmd == "enable-ssh-account":
                await provider.set_account_enabled(args.account_id, True)
                db.set_ssh_account_status(account["id"], "active")
                if account.get("quota_pool_id"):
                    db.set_quota_enforcement_state(int(account["quota_pool_id"]), "ssh", args.account_id,
                        is_disabled=False, reason="manual_enabled", last_error=None)
                _json({"ok": True, "account_id": args.account_id, "status": "active"})
            elif cmd == "disable-ssh-account":
                await provider.set_account_enabled(args.account_id, False)
                db.set_ssh_account_status(account["id"], "disabled")
                if account.get("quota_pool_id"):
                    db.set_quota_enforcement_state(int(account["quota_pool_id"]), "ssh", args.account_id,
                        is_disabled=False, reason="manual_disabled", last_error=None)
                _json({"ok": True, "account_id": args.account_id, "status": "disabled"})
            else:
                if account.get("quota_pool_id"):
                    usage = await provider.get_usage(args.account_id)
                    pool = db.get_quota_pool(int(account["quota_pool_id"]))
                    if pool and int(pool.get("limit_bytes") or 0) > 0 and not usage.get("accounting_ready"):
                        raise ValueError("برای حذف حساب از استخر حجمی مشترک، حسابداری مصرف باید معتبر باشد")
                    if usage.get("accounting_ready"):
                        db.record_usage_snapshot("ssh", args.account_id, int(usage["used_bytes"]),
                            uploaded_bytes=usage.get("uploaded_bytes", 0), downloaded_bytes=usage.get("downloaded_bytes", 0),
                            source=str(usage.get("source") or "ssh-agent"), reported_at=usage.get("reported_at"))
                await provider.delete_account(args.account_id)
                if account.get("quota_pool_id"):
                    db.detach_quota_service(int(account["quota_pool_id"]), "ssh", args.account_id)
                db.set_ssh_account_status(account["id"], "deleted")
                _json({"ok": True, "account_id": args.account_id, "status": "deleted"})
        elif cmd == "quota-usage":
            usage = db.get_quota_usage(args.pool_id)
            if usage is None:
                raise ValueError("استخر سهمیه پیدا نشد")
            _json(usage)
        elif cmd == "reconcile-once":
            _json(await QuotaReconciler(db).poll_once())
        else:
            raise ValueError(f"دستور پشتیبانی نمی‌شود: {cmd}")
    finally:
        db.close()


def build_parser():
    parser = argparse.ArgumentParser(description="ShopVPN SSH/shared-quota management")
    parser.add_argument("--db", default=os.getenv("SHOPVPN_DB_PATH", "bot_database.db"), help="SQLite database path")
    parser.add_argument("--owner-id", default=os.getenv("OWNER_ID", "1"), help="Owner Telegram ID for additive DB migrations")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-pool", help="Create a shared VPN+SSH quota pool")
    p.add_argument("--user-id", type=int, required=True); p.add_argument("--limit-gb", type=float, required=True)
    p.add_argument("--expires-at"); p.add_argument("--label", default="Shared VPN+SSH")

    p = sub.add_parser("attach-vpn", help="Attach an existing custom VPN config to a pool")
    p.add_argument("--pool-id", type=int, required=True); p.add_argument("--custom-config-id", type=int, required=True)

    p = sub.add_parser("add-linux-node", help="Register a Linux Agent node")
    p.add_argument("--name", required=True); p.add_argument("--host", required=True)
    p.add_argument("--agent-url", required=True); p.add_argument("--secret-env", required=True, help="Environment variable containing the agent token")
    p.add_argument("--ssh-username", default="root"); p.add_argument("--port", type=int, default=22)
    p.add_argument("--allow-insecure-http", action="store_true")

    p = sub.add_parser("test-node"); p.add_argument("--node-id", type=int, required=True)
    sub.add_parser("list-nodes")

    p = sub.add_parser("create-ssh-account")
    p.add_argument("--node-id", type=int, required=True); p.add_argument("--user-id", type=int, required=True)
    p.add_argument("--username", required=True); p.add_argument("--limit-gb", type=float, default=0)
    p.add_argument("--days", type=int, default=30); p.add_argument("--expires-at")
    p.add_argument("--quota-pool-id", type=int)

    p = sub.add_parser("list-ssh-accounts"); p.add_argument("--user-id", type=int); p.add_argument("--node-id", type=int)
    for name in ("enable-ssh-account", "disable-ssh-account", "delete-ssh-account"):
        p = sub.add_parser(name); p.add_argument("--account-id", required=True)
    p = sub.add_parser("quota-usage"); p.add_argument("--pool-id", type=int, required=True)
    sub.add_parser("reconcile-once")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        asyncio.run(_run(args))
    except (ValueError, SSHProviderError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
