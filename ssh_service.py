# -*- coding: utf-8 -*-
"""منطق مشترک مدیریت نود/حساب/استخر SSH.

پنل وب، بات ادمین و مینی‌اپ هر سه از همین ماژول استفاده می‌کنند تا رفتار یکسان باشد.
خطاها با SSHServiceError (دارای status شبیه HTTP) برگردانده می‌شوند.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

RELIABLE_SOURCES = {"nftables_port_counters", "external-counter", "ssh-traffic-accountant"}


class SSHServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def overview(db) -> dict:
    pool_items = []
    for pool in db.list_quota_pools(active_only=False):
        item = dict(pool)
        item["usage"] = db.get_quota_usage(int(pool["id"]))
        item["members"] = db.get_quota_members(int(pool["id"]))
        pool_items.append(item)
    nodes = []
    for node in db.list_ssh_nodes():
        item = dict(node)
        try:
            cfg = json.loads(item.get("config_json") or "{}")
        except (TypeError, ValueError):
            cfg = {}
        item["agent_url"] = cfg.get("agent_url", "")
        item.pop("config_json", None)
        nodes.append(item)
    return {"nodes": nodes, "pools": pool_items, "accounts": db.list_ssh_accounts()}


def start_auto_install(db, *, name: str, host: str, ssh_port: int = 22, password: str = "") -> str:
    name, host = (name or "").strip(), (host or "").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9.\-:]{3,255}", host):
        raise SSHServiceError("نام و میزبان (IP یا دامنه) معتبر لازم است.")
    if not 1 <= int(ssh_port) <= 65535:
        raise SSHServiceError("پورت SSH نامعتبر است.")
    if not password:
        raise SSHServiceError("رمز root لازم است.")
    import ssh_auto_install
    return ssh_auto_install.start_install(db, name=name, host=host, ssh_port=int(ssh_port),
                                          username="root", password=password, private_key=None)


def get_install_job(job_id: str) -> Optional[dict]:
    import ssh_auto_install
    return ssh_auto_install.get_job(job_id)


def delete_node(db, node_id: int) -> None:
    try:
        removed = db.delete_ssh_node(int(node_id))
    except ValueError as exc:
        raise SSHServiceError(str(exc))
    if not removed:
        raise SSHServiceError("نود پیدا نشد.", 404)


def rename_node(db, node_id: int, name: str) -> None:
    try:
        updated = db.update_ssh_node_name(int(node_id), name)
    except ValueError as exc:
        raise SSHServiceError(str(exc))
    if not updated:
        raise SSHServiceError("نود پیدا نشد.", 404)


async def test_node(db, node_id: int) -> bool:
    from ssh_providers import get_ssh_provider
    from quota_reconciler import _env_secret_resolver
    node = next((n for n in db.list_ssh_nodes() if int(n["id"]) == int(node_id)), None)
    if not node:
        raise SSHServiceError("نود پیدا نشد.", 404)
    try:
        ok = await get_ssh_provider(node, secret_resolver=_env_secret_resolver).test_connection()
        db.update_ssh_node_status(node_id, "active" if ok else "error", None if ok else "Agent response invalid")
    except Exception as exc:
        db.update_ssh_node_status(node_id, "error", str(exc)[:500])
        raise SSHServiceError(str(exc)[:300])
    return bool(ok)


def create_pool(db, user_id: int, limit_gb: float, expires_at: Optional[str], label: str) -> int:
    if int(user_id) <= 0 or float(limit_gb) < 0:
        raise SSHServiceError("کاربر یا سقف حجم نامعتبر است.")
    if not db.get_user(int(user_id)):
        raise SSHServiceError("کاربر در دیتابیس بات پیدا نشد.", 404)
    try:
        return db.create_quota_pool(int(user_id), float(limit_gb), expires_at, (label or "").strip() or "Shared VPN+SSH")
    except (ValueError, OverflowError) as exc:
        raise SSHServiceError(str(exc))


def attach_vpn(db, pool_id: int, custom_config_id: int) -> None:
    try:
        db.attach_custom_config_to_quota_pool(int(pool_id), int(custom_config_id))
    except (ValueError, RuntimeError) as exc:
        raise SSHServiceError(str(exc))


async def create_account(db, *, node_id: int, user_id: int, username: str, limit_gb: float = 0,
                         days: int = 0, expires_at: Optional[str] = None,
                         quota_pool_id: Optional[int] = None) -> dict:
    from ssh_providers import get_ssh_provider, SSHProviderError
    from quota_reconciler import _env_secret_resolver
    from ssh_credentials import encrypt_credential
    username = (username or "").strip()
    if int(user_id) <= 0 or not username or float(limit_gb) < 0 or int(days) < 0:
        raise SSHServiceError("اطلاعات حساب SSH نامعتبر است.")
    if not db.get_user(int(user_id)):
        raise SSHServiceError("کاربر در دیتابیس بات پیدا نشد.", 404)
    node = next((n for n in db.list_ssh_nodes(active_only=True) if int(n["id"]) == int(node_id)), None)
    if not node:
        raise SSHServiceError("نود فعال انتخاب‌شده پیدا نشد.")
    pool = None
    if quota_pool_id is not None:
        pool = db.get_quota_pool(int(quota_pool_id))
        if not pool or int(pool["user_id"]) != int(user_id) or pool["status"] != "active":
            raise SSHServiceError("استخر انتخاب‌شده فعال نیست یا برای این کاربر نیست.")
    if pool:
        # استخر مشترک مرجع انقضا و حجم است.
        expires_at = pool.get("expires_at") or expires_at
        limit_bytes = 0
    else:
        if not expires_at and days:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=int(days))).isoformat()
        limit_bytes = int(float(limit_gb) * (1024 ** 3))
    if expires_at:
        from db.usage import _normalize_timestamp
        try:
            expires_at = _normalize_timestamp(expires_at)
        except ValueError as exc:
            raise SSHServiceError(str(exc))
    provider = get_ssh_provider(node, secret_resolver=_env_secret_resolver)
    try:
        encrypt_credential("preflight-check")
    except RuntimeError as exc:
        raise SSHServiceError(str(exc), 500)
    try:
        result = await provider.create_account(username, limit_bytes, expires_at)
        row_id = None
        try:
            usage = await provider.get_usage(result.account_id)
            volume_limited = limit_bytes > 0 or bool(pool and int(pool.get("limit_bytes") or 0) > 0)
            if volume_limited and (not usage.get("accounting_ready") or usage.get("source") not in RELIABLE_SOURCES):
                raise SSHServiceError("ساخت متوقف شد: شمارنده‌ی معتبر حجم برای حساب SSH آماده نیست.", 409)
            credential_enc = encrypt_credential(result.metadata.get("password", ""))
            row_id = db.register_ssh_account(int(node_id), int(user_id), result.account_id,
                result.username, result.host, result.port, quota_pool_id, expires_at, credential_enc)
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
                logger.exception("Failed to roll back SSH account on node %s", node_id)
            raise
    except SSHServiceError:
        raise
    except (ValueError, SSHProviderError) as exc:
        raise SSHServiceError(str(exc)[:300])
    return {"ok": True, "id": row_id, "account_id": result.account_id, "username": result.username,
            "host": result.host, "port": result.port, "password": result.metadata.get("password"),
            "expires_at": expires_at, "quota_pool_id": quota_pool_id, "node_name": node.get("name"),
            "accounting_ready": bool(usage.get("accounting_ready")),
            "warning": (None if usage.get("source") in RELIABLE_SOURCES
                        else "حسابداری حجم دقیق نیست؛ قبل از فروش حجمی وضعیت نود را بررسی کنید.")}


async def account_action(db, account_id: str, action: str) -> str:
    from ssh_providers import get_ssh_provider, SSHProviderError
    from quota_reconciler import _env_secret_resolver
    action = (action or "").strip().lower()
    if action not in {"enable", "disable", "delete"}:
        raise SSHServiceError("عملیات نامعتبر است.")
    account = next((a for a in db.list_ssh_accounts() if str(a["account_id"]) == str(account_id)), None)
    if not account:
        raise SSHServiceError("حساب SSH پیدا نشد.", 404)
    node = next((n for n in db.list_ssh_nodes() if int(n["id"]) == int(account["node_id"])), None)
    if not node:
        raise SSHServiceError("نود حساب پیدا نشد.", 404)
    try:
        provider = get_ssh_provider(node, secret_resolver=_env_secret_resolver)
        if action == "enable":
            await provider.set_account_enabled(account_id, True)
            db.set_ssh_account_status(account["id"], "active")
            if account.get("quota_pool_id"):
                db.set_quota_enforcement_state(int(account["quota_pool_id"]), "ssh", account_id,
                    is_disabled=False, reason="manual_enabled", last_error=None)
        elif action == "disable":
            await provider.set_account_enabled(account_id, False)
            db.set_ssh_account_status(account["id"], "disabled")
            if account.get("quota_pool_id"):
                db.set_quota_enforcement_state(int(account["quota_pool_id"]), "ssh", account_id,
                    is_disabled=False, reason="manual_disabled", last_error=None)
        else:
            pool_id = account.get("quota_pool_id")
            if pool_id:
                usage = await provider.get_usage(account_id)
                pool = db.get_quota_pool(int(pool_id))
                if pool and int(pool.get("limit_bytes") or 0) > 0 and not usage.get("accounting_ready"):
                    raise SSHServiceError("حذف متوقف شد: مصرف حساب SSH برای ثبت در استخر قابل خواندن نیست.", 409)
                if usage.get("accounting_ready"):
                    db.record_usage_snapshot("ssh", account_id, int(usage["used_bytes"]),
                        uploaded_bytes=usage.get("uploaded_bytes", 0), downloaded_bytes=usage.get("downloaded_bytes", 0),
                        source=str(usage.get("source") or "ssh-agent"), reported_at=usage.get("reported_at"))
            await provider.delete_account(account_id)
            if pool_id:
                db.detach_quota_service(int(pool_id), "ssh", account_id)
            db.set_ssh_account_status(account["id"], "deleted")
    except SSHServiceError:
        raise
    except SSHProviderError as exc:
        raise SSHServiceError(str(exc)[:300])
    return "deleted" if action == "delete" else ("active" if action == "enable" else "disabled")


async def reconcile(db) -> list:
    from quota_reconciler import QuotaReconciler
    return await QuotaReconciler(db).poll_once()
