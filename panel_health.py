# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""پایش دوره‌ای سلامت پنل‌های VPN و هشدار قطعی/بازیابی به مدیران."""

import asyncio
import html
import logging
from datetime import datetime, timezone

import report_router
from panel_providers import get_provider, PANEL_TYPE_LABELS

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300
STARTUP_DELAY_SECONDS = 30
FAIL_THRESHOLD = 3
PROBE_TIMEOUT = 20
PROBE_CONCURRENCY = 5
REFERENCE_HOST = ("api.telegram.org", 443)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
REFERENCE_TIMEOUT = 5
OUTAGE_REMINDER_SECONDS = 3600


async def _db(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


async def _probe(server, sem):
    async with sem:
        try:
            return await asyncio.wait_for(get_provider(server).check_connection(), PROBE_TIMEOUT)
        except asyncio.TimeoutError:
            return False, "timeout"
        except Exception as e:
            return False, (str(e) or type(e).__name__)[:300]


async def _reference_reachable() -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(*REFERENCE_HOST), REFERENCE_TIMEOUT)
    except Exception:
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


def _minutes_since(iso: str):
    try:
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return max(int(delta.total_seconds() // 60), 0)


async def _alert_admins(bot, db, text: str) -> None:
    await report_router.report(bot, db, "panel", text, senior_only=True)


def _seconds_since(iso: str):
    try:
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return max(delta.total_seconds(), 0)


async def _active_services_suffix(db, server_id: int) -> str:
    try:
        info = await _db(db.get_panel_capacity_info, server_id)
    except Exception:
        info = None
    if not info:
        return ""
    if info["max_services"]:
        return f"\nسرویس‌های فعال روی این پنل: {info['active_services']}/{info['max_services']}"
    return f"\nسرویس‌های فعال روی این پنل: {info['active_services']}"


async def _apply_result(bot, db, server, ok: bool, error: str, prev) -> None:
    now = datetime.now(timezone.utc).isoformat()
    prev_status = prev["status"] if prev else "up"
    prev_fails = prev["fail_count"] if prev else 0
    prev_change = prev["last_change"] if prev and prev["last_change"] else now
    prev_alert = prev["last_alert"] if prev and prev["last_alert"] else None

    if ok:
        status, fails, err = "up", 0, ""
        last_change = now if prev_status == "down" else prev_change
    else:
        fails = prev_fails + 1
        status = "down" if prev_status == "down" or fails >= FAIL_THRESHOLD else "up"
        last_change = now if status == "down" and prev_status != "down" else prev_change
        err = error

    await _db(db.save_panel_health, server["id"], status, fails, now, last_change, err)

    name = html.escape(server["name"] or "")
    type_label = html.escape(PANEL_TYPE_LABELS.get(server["panel_type"], server["panel_type"] or ""))

    if status == prev_status:
        # هنوز قطع است؛ اگر از آخرین هشدار/یادآوری بیش از یک ساعت گذشته، دوباره یادآوری کن‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
        # تا قطعی چندساعته ساکت نماند.‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
        if status != "down":
            return
        reference = prev_alert or prev_change
        elapsed = _seconds_since(reference)
        if elapsed is None or elapsed < OUTAGE_REMINDER_SECONDS:
            return
        minutes = _minutes_since(prev_change)
        duration = f"حدود {minutes} دقیقه" if minutes is not None else "نامشخص"
        suffix = await _active_services_suffix(db, server["id"])
        text = (
            "🔴 یادآوری قطعی پنل\n\n"
            f"پنل «{name}» ({type_label}) همچنان قطع است.\n"
            f"مدت قطعی: {duration}\n"
            f"خطا: {html.escape(err)}"
            f"{suffix}"
        )
        await _alert_admins(bot, db, text)
        await _db(db.set_panel_health_alert, server["id"], now)
        return

    await _db(db.add_panel_health_event, server["id"], status)
    if status == "down":
        suffix = await _active_services_suffix(db, server["id"])
        text = (
            "🔴 هشدار قطعی پنل\n\n"
            f"پنل «{name}» ({type_label}) بعد از {FAIL_THRESHOLD} بررسی ناموفق پیاپی در دسترس نیست.\n"
            f"آدرس: <code>{html.escape(server['api_url'] or '')}</code>\n"
            f"خطا: {html.escape(err)}"
            f"{suffix}\n\n"
            f"هر {OUTAGE_REMINDER_SECONDS // 60} دقیقه یک‌بار تا رفع مشکل یادآوری می‌شود."
        )
        await _db(db.set_panel_health_alert, server["id"], now)
    else:
        minutes = _minutes_since(prev_change)
        duration = f"\nمدت قطعی: حدود {minutes} دقیقه" if minutes is not None else ""
        text = f"🟢 پنل «{name}» ({type_label}) دوباره در دسترس است.{duration}"
        await _db(db.set_panel_health_alert, server["id"], None)
    await _alert_admins(bot, db, text)


async def _check_capacity(bot, db, server) -> None:
    """اگر ظرفیت پنل به ۹۰٪ رسیده باشد (و قبلاً برای همین پر شدن هشدار داده
    نشده باشد)، به تاپیک «سرویس» اطلاع می‌دهد. پر شدن پنل یعنی فروش روی آن
    متوقف می‌شود، پس این هشدار برای ادامه‌ی فروش مهم است."""
    try:
        should_alert = await _db(db.maybe_mark_panel_capacity_alert, server["id"])
    except Exception:
        logger.exception("بررسی ظرفیت پنل %s ناموفق بود.", server["id"])
        return
    if not should_alert:
        return
    info = await _db(db.get_panel_capacity_info, server["id"])
    if not info:
        return
    name = html.escape(server["name"] or "")
    type_label = html.escape(PANEL_TYPE_LABELS.get(server["panel_type"], server["panel_type"] or ""))
    text = (
        "🟠 هشدار پر شدن ظرفیت پنل\n\n"
        f"پنل «{name}» ({type_label}) به {info['percent']:.0f}٪ ظرفیت رسیده است.\n"
        f"سرویس‌های فعال: {info['active_services']}/{info['max_services']}\n\n"
        "تا افزایش ظرفیت یا اضافه‌کردن پنل دیگر، فروش روی این پنل ممکن است متوقف شود."
    )
    await _alert_admins(bot, db, text)


async def check_panels(bot, db) -> None:
    if db.get_setting("panel_health_enabled", "1") != "1":
        return
    servers = await _db(db.get_panel_servers, True)
    if not servers:
        return
    sem = asyncio.Semaphore(PROBE_CONCURRENCY)
    results = await asyncio.gather(*(_probe(s, sem) for s in servers))
    if any(not ok for ok, _ in results) and not await _reference_reachable():
        logger.warning("اتصال شبکه‌ی خود سرور بات قطع است؛ نتیجه‌ی بررسی پنل‌ها نادیده گرفته شد.")
        return
    health = await _db(db.list_panel_health)
    for server, (ok, error) in zip(servers, results):
        try:
            await _apply_result(bot, db, server, ok, error, health.get(server["id"]))
        except Exception:
            logger.exception("ثبت وضعیت سلامت پنل %s ناموفق بود.", server["id"])
        await _check_capacity(bot, db, server)


async def panel_health_loop(bot, db, interval_seconds: int = INTERVAL_SECONDS) -> None:
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            await check_panels(bot, db)
        except Exception:
            logger.exception("خطا در چرخه‌ی پایش سلامت پنل‌ها")
        await asyncio.sleep(interval_seconds)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
