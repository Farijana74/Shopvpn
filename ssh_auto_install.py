# -*- coding: utf-8 -*-
"""نصب خودکار نود SSH (Linux Agent) روی یک سرور Debian/Ubuntu فقط با IP و رمز root.

پنل با asyncssh به سرور مقصد وصل می‌شود، فایل‌های Agent را آپلود و نصب می‌کند،
توکن و گواهی TLS (با pin کردن fingerprint) می‌سازد، دسترسی Agent را فقط به IP همین
سرور محدود می‌کند، نود را در دیتابیس ثبت و یک تست واقعی (ساخت/حذف حساب آزمایشی) انجام می‌دهد.
رمز root فقط در حافظه استفاده می‌شود و هیچ‌جا ذخیره یا لاگ نمی‌شود.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
import uuid

logger = logging.getLogger(__name__)

AGENT_PORT = 8765
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_FILES = (
    "ssh_node_agent.py",
    "deploy/install-shopvpn-ssh-agent.sh",
    "deploy/shopvpn-ssh-agent.service",
    "deploy/shopvpn-sshd@.service",
)
_JOBS: dict = {}
_MAX_JOBS = 30


class InstallError(Exception):
    pass


def _new_job() -> dict:
    now = time.time()
    for key in [k for k, v in _JOBS.items() if now - v["created"] > 3600]:
        _JOBS.pop(key, None)
    while len(_JOBS) >= _MAX_JOBS:
        _JOBS.pop(next(iter(_JOBS)))
    job = {"id": uuid.uuid4().hex, "status": "running", "log": [], "created": now,
           "node_id": None, "warning": None, "error": None}
    _JOBS[job["id"]] = job
    return job


def get_job(job_id: str):
    job = _JOBS.get(job_id)
    if not job:
        return None
    return {k: job[k] for k in ("id", "status", "log", "node_id", "warning", "error")}


def _log(job, text: str):
    job["log"].append(str(text)[:400])


def start_install(db, *, name, host, ssh_port, username, password=None, private_key=None) -> str:
    """Create a background job and return its id. Secrets live only inside the task closure."""
    job = _new_job()

    async def _runner():
        try:
            await _install(db, job, name, host, int(ssh_port), username, password, private_key)
            job["status"] = "done"
        except InstallError as exc:
            job["status"] = "error"
            job["error"] = str(exc)
        except Exception as exc:  # never leak secrets: only the exception type and a short message
            logger.exception("SSH node auto-install failed")
            job["status"] = "error"
            job["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    task = asyncio.get_running_loop().create_task(_runner())
    job["_task"] = task  # keep a reference so it is not garbage collected
    return job["id"]


async def _install(db, job, name, host, ssh_port, username, password, private_key):
    try:
        import asyncssh
    except ImportError as exc:
        raise InstallError("کتابخانه asyncssh نصب نیست (pip install asyncssh).") from exc
    from ssh_credentials import encrypt_credential
    from ssh_providers import get_ssh_provider
    from quota_reconciler import _env_secret_resolver

    if username != "root":
        raise InstallError("نصب خودکار فقط با کاربر root ممکن است.")
    if not (password or private_key):
        raise InstallError("رمز root یا کلید خصوصی لازم است.")
    encrypt_credential("preflight")  # fail early if the encryption key cannot be created

    kwargs = dict(host=host, port=ssh_port, username=username, known_hosts=None, connect_timeout=20)
    if private_key:
        try:
            kwargs["client_keys"] = [asyncssh.import_private_key(private_key)]
        except Exception as exc:
            raise InstallError("کلید خصوصی نامعتبر است.") from exc
    else:
        kwargs["password"] = password
        kwargs["client_keys"] = None

    _log(job, "در حال اتصال به سرور...")
    try:
        conn = await asyncssh.connect(**kwargs)
    except asyncssh.PermissionDenied as exc:
        raise InstallError("احراز هویت SSH ناموفق بود (رمز یا کلید اشتباه است).") from exc
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as exc:
        raise InstallError(f"اتصال SSH برقرار نشد: {type(exc).__name__}") from exc

    async with conn:
        async def run(cmd, timeout=600, check=True):
            try:
                res = await asyncio.wait_for(conn.run(cmd, check=False), timeout)
            except asyncio.TimeoutError as exc:
                raise InstallError("زمان اجرای دستور روی سرور تمام شد.") from exc
            if check and res.exit_status != 0:
                tail = ((res.stderr or "") + (res.stdout or "")).strip()[-300:]
                raise InstallError(f"دستور روی سرور شکست خورد: {tail}")
            return (res.stdout or "").strip()

        osr = (await run("cat /etc/os-release", check=False)).lower()
        if "debian" not in osr and "ubuntu" not in osr:
            raise InstallError("فقط Debian/Ubuntu پشتیبانی می‌شود.")
        client_ip = (await run("echo ${SSH_CLIENT%% *}", check=False)).strip()
        if not re.fullmatch(r"[0-9a-fA-F:.]{3,45}", client_ip or ""):
            client_ip = ""
        _log(job, f"IP این پنل از دید نود: {client_ip or 'نامشخص'}")

        _log(job, "آپلود فایل‌های Agent...")
        await run("rm -rf /tmp/shopvpn-install && mkdir -p /tmp/shopvpn-install/deploy")
        async with conn.start_sftp_client() as sftp:
            for rel in _FILES:
                local = os.path.join(_PROJECT_ROOT, rel)
                if not os.path.isfile(local):
                    raise InstallError(f"فایل نصب روی پنل پیدا نشد: {rel}")
                await sftp.put(local, f"/tmp/shopvpn-install/{rel}")

        _log(job, "نصب پیش‌نیازها (ممکن است چند دقیقه طول بکشد)...")
        await run("export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq openssl", timeout=900)

        _log(job, "ساخت گواهی TLS و توکن...")
        await run("install -d -m 0755 /etc/shopvpn && "
                  "openssl req -x509 -newkey rsa:2048 -nodes -keyout /etc/shopvpn/agent.key "
                  "-out /etc/shopvpn/agent.crt -days 3650 -subj /CN=shopvpn-agent >/dev/null 2>&1 && "
                  "chmod 600 /etc/shopvpn/agent.key")
        fp_raw = await run("openssl x509 -in /etc/shopvpn/agent.crt -noout -fingerprint -sha256")
        fp = fp_raw.split("=", 1)[-1].replace(":", "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", fp):
            raise InstallError("خواندن fingerprint گواهی ناموفق بود.")

        token = secrets.token_hex(32)
        env_lines = [
            f"SHOPVPN_AGENT_TOKEN={token}",
            "SHOPVPN_AGENT_HOST=0.0.0.0",
            f"SHOPVPN_AGENT_PORT={AGENT_PORT}",
            "SHOPVPN_AGENT_DB=/var/lib/shopvpn-ssh-agent/agent.db",
            "SHOPVPN_NFT_ACCOUNTING=1",
            "SHOPVPN_AGENT_TLS_CERT=/etc/shopvpn/agent.crt",
            "SHOPVPN_AGENT_TLS_KEY=/etc/shopvpn/agent.key",
        ]
        if client_ip:
            env_lines.append(f"SHOPVPN_AGENT_ALLOWED_IPS={client_ip}")
        async with conn.start_sftp_client() as sftp:
            async with sftp.open("/tmp/shopvpn-install/agent.env", "wb") as fh:
                await fh.write(("\n".join(env_lines) + "\n").encode())
        await run("install -m 0600 -o root -g root /tmp/shopvpn-install/agent.env /etc/shopvpn-ssh-agent.env")

        _log(job, "نصب و راه‌اندازی سرویس Agent...")
        await run("cd /tmp/shopvpn-install && bash deploy/install-shopvpn-ssh-agent.sh", timeout=900)
        await run("systemctl restart shopvpn-ssh-agent; sleep 4", check=False)
        state = await run("systemctl is-active shopvpn-ssh-agent", check=False)
        if state != "active":
            tail = await run("journalctl -u shopvpn-ssh-agent -n 12 --no-pager -o cat", check=False)
            raise InstallError(f"سرویس Agent بالا نیامد ({state}). خروجی سرور:\n{tail[-600:]}")

        _log(job, "تنظیم فایروال...")
        rules = []
        if client_ip:
            rules.append(f"ufw allow from {client_ip} to any port {AGENT_PORT} proto tcp")
        else:
            rules.append(f"ufw allow {AGENT_PORT}/tcp")
        rules.append("ufw allow 20000:39999/tcp")
        await run("if command -v ufw >/dev/null 2>&1 && ufw status | grep -q 'Status: active'; then "
                  + " && ".join(rules) + "; fi", check=False)
        await run("rm -rf /tmp/shopvpn-install", check=False)

    # ثبت نود و تست واقعی
    secret_ref = "enc:" + encrypt_credential(token)
    node_id = db.create_ssh_node(
        name, "linux", host, ssh_port, auth_type="api_token", username="root", secret_ref=secret_ref,
        config_json={"agent_url": f"https://{host}:{AGENT_PORT}", "cert_sha256": fp,
                     "auto_installed": True, "agent_allowed_ip": client_ip})
    job["node_id"] = node_id
    node = next(n for n in db.list_ssh_nodes() if int(n["id"]) == node_id)
    provider = get_ssh_provider(node, secret_resolver=_env_secret_resolver)
    _log(job, "تست اتصال به Agent...")
    ok = False
    for _ in range(5):
        try:
            ok = await provider.test_connection()
            if ok:
                break
        except Exception:
            await asyncio.sleep(2)
    if not ok:
        db.update_ssh_node_status(node_id, "error", "Agent connection failed after install")
        raise InstallError("Agent نصب شد ولی پنل نتوانست به آن وصل شود. فایروال/امنیت‌گروه سرویس‌دهنده پورت "
                           f"{AGENT_PORT} را بررسی کنید. نود با شناسه {node_id} ثبت شده و با «تست اتصال» دوباره امتحان می‌شود.")
    db.update_ssh_node_status(node_id, "active", None)

    _log(job, "تست حسابداری حجم (ساخت و حذف حساب آزمایشی)...")
    try:
        test_name = "svtest" + secrets.token_hex(3)
        res = await provider.create_account(test_name, 0, None)
        try:
            usage = await provider.get_usage(res.account_id)
        finally:
            await provider.delete_account(res.account_id)
        if usage.get("accounting_ready"):
            _log(job, "حسابداری حجم فعال است ✅")
        else:
            job["warning"] = ("نود فعال شد ولی شمارنده حجم (nftables) آماده نیست؛ فروش محصول حجمی روی این نود "
                              "تا رفع مشکل متوقف می‌ماند.")
    except Exception as exc:
        job["warning"] = f"تست حساب آزمایشی ناموفق بود: {type(exc).__name__}: {str(exc)[:160]}"
    _log(job, "نود آماده است ✅")
