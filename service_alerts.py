# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""اعلان کانالی رویدادهای اتمام و حذف کانفیگ (قابلیت ۱۱۵)."""
import logging
import aiohttp

import report_router

logger = logging.getLogger(__name__)

TOPIC_KEY = "config_alert"
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


def normalize_channel(value: str) -> str:
    value = (value or "").strip()
    if value and not value.startswith("@") and not value.startswith("-"):
        value = "@" + value
    return value


async def send_service_alert(bot, db, text: str) -> bool:
    """اول به تاپیک «حذف/اتمام کانفیگ» گروه گزارش؛ اگر گروه تنظیم نبود یا ارسال شکست خورد، به کانال قدیمی (در صورت ثبت)."""
    if await report_router.send_text(bot, db, TOPIC_KEY, text):
        return True
    channel = normalize_channel(db.get_setting("service_alert_channel", ""))
    if not channel:
        return False
    try:
        await bot.send_message(chat_id=channel, text=text)
        return True
    except Exception:
        logger.exception("ارسال اعلان سرویس به کانال %s ناموفق بود", channel)
        return False


async def send_service_alert_http(bot_token: str, db, text: str) -> bool:
    """نسخه‌ی مستقل از aiogram برای APIهای Mini App/پنل وب؛ اول تاپیک گروه گزارش، بعد کانال قدیمی."""
    if await report_router.send_raw_to_group(bot_token, db, TOPIC_KEY, text):
        return True
    channel = normalize_channel(db.get_setting("service_alert_channel", ""))
    if not channel or not bot_token:
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"chat_id": channel, "text": text},
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
    except Exception:
        logger.exception("ارسال اعلان سرویس از API به کانال %s ناموفق بود", channel)
        return False


def send_service_alert_sync(bot_token: str, db, text: str) -> None:
    """برای endpointهای sync وب؛ ارسال را در یک loop موقت اجرا می‌کند."""
    import asyncio as _asyncio
    try:
        loop = _asyncio.new_event_loop()
        try:
            loop.run_until_complete(send_service_alert_http(bot_token, db, text))
        finally:
            loop.close()
    except Exception:
        logger.exception("ارسال sync اعلان سرویس ناموفق بود")
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
