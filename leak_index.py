# -*- coding: utf-8 -*-
"""ایندکس اثر انگشت کانفیگ‌ها برای تشخیص نشت.

برای هر سرویس فعال روی پنل‌هایی که پشتیبانی می‌شوند (3x-ui، PasarGuard، Hiddify)، مقادیر
محرمانه‌ی آن (UUID، پسورد پروتکل‌ها، توکن اشتراک) گرفته، هش SHA-256 می‌شود و در
credential_index نوشته می‌شود. متن خام هیچ‌وقت ذخیره نمی‌شود.

به‌روزرسانی به دو شکل انجام می‌شود:
  * دوره‌ای: حلقه‌ی run() همه‌ی سرویس‌های فعال را هر چند دقیقه یک‌بار بازسازی می‌کند.
  * فوری: بعد از خرید و قطع دسترسی، schedule_refresh() همان یک سرویس را بی‌درنگ به‌روز می‌کند.
"""

import asyncio
import hashlib
import logging
import random
import re

from panel_providers import get_provider
from panel_providers.base import PanelError, subscription_token

logger = logging.getLogger(__name__)

KEY_INTERVAL = "leak_index_interval_minutes"
DEFAULT_INTERVAL_MINUTES = 10
MIN_SECRET_LEN = 6

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

_background = set()


def normalize_secret(value: str) -> str:
    """UUID بدون توجه به حروف بزرگ/کوچک یکسان است؛ پسورد دقیقاً همان‌طور که هست بررسی می‌شود."""
    value = (value or "").strip()
    if UUID_RE.fullmatch(value):
        return value.lower()
    return value


def fingerprint_hash(value: str) -> str:
    return hashlib.sha256(normalize_secret(value).encode("utf-8")).hexdigest()


def hash_entries(values, kind: str = "secret") -> dict:
    """{hash: kind} برای مقادیر معتبر (خیلی کوتاه‌ها نادیده گرفته می‌شوند)."""
    out = {}
    for value in values:
        if value and len(normalize_secret(value)) >= MIN_SECRET_LEN:
            out.setdefault(fingerprint_hash(value), kind)
    return out


async def collect_entries(provider, username: str, subscription_url: str) -> dict:
    """ترکیب اثر انگشت پنل و توکن لینک اشتراک ذخیره‌شده‌ی خود ما."""
    values = await provider.get_fingerprints(username)
    entries = hash_entries(values, "secret")
    token = subscription_token(subscription_url or "")
    for h, kind in hash_entries([token], "token").items():
        entries.setdefault(h, kind)
    return entries


async def refresh_config(db, config_id: int) -> str:
    """یک سرویس را در ایندکس به‌روز می‌کند. نتیجه: ok | skipped | failed | removed."""
    cc = await asyncio.to_thread(db.get_custom_config_by_id, config_id)
    if not cc:
        await asyncio.to_thread(db.delete_credential_index, config_id)
        return "removed"
    if cc["status"] != "active":
        await asyncio.to_thread(db.delete_credential_index, config_id)
        return "removed"
    server = await asyncio.to_thread(db.get_panel_server, cc["panel_server_id"])
    if not server or not server["is_active"]:
        await asyncio.to_thread(db.delete_credential_index, config_id)
        return "skipped"
    try:
        provider = get_provider(server)
        if not provider.supports_fingerprints:
            await asyncio.to_thread(db.delete_credential_index, config_id)
            return "skipped"
        entries = await collect_entries(provider, cc["username"], cc["subscription_url"] or "")
    except PanelError as exc:
        # کاربر روی پنل پیدا نشد یا پنل در دسترس نیست؛ ایندکس قبلی دست‌نخورده می‌ماند.
        logger.info("leak index: config %s skipped: %s", config_id, exc)
        return "failed"
    except Exception:
        logger.warning("leak index: config %s failed", config_id, exc_info=True)
        return "failed"
    await asyncio.to_thread(
        db.replace_credential_index, config_id, server["id"], cc["username"], cc["user_id"], entries,
    )
    return "ok"


async def refresh_all(db) -> dict:
    config_ids = await asyncio.to_thread(db.list_index_target_config_ids)
    await asyncio.to_thread(db.purge_credential_index_except, config_ids)
    counts = {"ok": 0, "skipped": 0, "failed": 0, "removed": 0}
    for config_id in config_ids:
        try:
            status = await refresh_config(db, config_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("leak index: unexpected error for config %s", config_id, exc_info=True)
            status = "failed"
        counts[status] = counts.get(status, 0) + 1
        # فاصله‌ی تصادفی بین درخواست‌ها تا پنل‌ها زیر فشار نروند
        await asyncio.sleep(random.uniform(0.5, 1.5))
    logger.info("leak index refreshed: %s", counts)
    return counts


def _interval_seconds(db) -> float:
    try:
        minutes = int(db.get_setting(KEY_INTERVAL, str(DEFAULT_INTERVAL_MINUTES)) or DEFAULT_INTERVAL_MINUTES)
    except (TypeError, ValueError):
        minutes = DEFAULT_INTERVAL_MINUTES
    return max(minutes, 1) * 60.0


async def run(db) -> None:
    """حلقه‌ی دوره‌ای؛ تا وقتی برنامه بالا است اجرا می‌شود."""
    await asyncio.sleep(20)  # کمی صبر تا بات کامل بالا بیاید
    while True:
        try:
            await refresh_all(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("leak index loop failed")
        await asyncio.sleep(_interval_seconds(db))


async def _safe_refresh(db, config_id: int) -> None:
    try:
        await refresh_config(db, config_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("leak index: immediate refresh failed for config %s", config_id, exc_info=True)


def schedule_refresh(db, config_id: int) -> None:
    """بعد از خرید/قطع دسترسی صدا زده می‌شود. هیچ‌وقت مسیر اصلی را نمی‌شکند."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_safe_refresh(db, config_id))
    _background.add(task)
    task.add_done_callback(_background.discard)
