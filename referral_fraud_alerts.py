# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
هشدار زیرمجموعه‌گیری فیک.

بعد از ثبت هر دعوت تازه (ref<id> در /start)، db.check_referral_fraud یک بار
معیارهای دعوت انبوه و نرخ بی‌خریدی زیرمجموعه‌ها را برای همان دعوت‌کننده بررسی
می‌کند. اگر تازه مشکوک تشخیص داده شود (یعنی هنوز فلگ بازی برایش نبوده)، این
ماژول متن هشدار را می‌سازد و مثل stock_alerts آن را - اگر گروه گزارش تنظیم شده
باشد در تاپیک «امنیت»، وگرنه با پیام خصوصی - به همه‌ی ادمین‌ها ارسال می‌کند.

send_fn مثل stock_alerts یک تابع async است که (admin_telegram_id, text) می‌گیرد؛
همین امضا اجازه می‌دهد این ماژول هم در بات (aiogram) و هم در Mini App/پنل ادمین
(aiohttp/FastAPI خام) بدون وابستگی به یک ترنسپورت خاص استفاده شود.
"""

import logging

import report_router

logger = logging.getLogger(__name__)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

TOPIC_KEY = "security"


async def check_and_notify_referral_fraud(send_fn, db, referrer_id: int, bot_token: str = None) -> None:
    try:
        flag = db.check_referral_fraud(referrer_id)
    except Exception:
        logger.exception("بررسی زیرمجموعه‌گیری فیک برای کاربر %s ناموفق بود.", referrer_id)
        return
    if not flag:
        return

    referrer = db.get_user(referrer_id)
    referrer_label = (
        f"@{referrer['username']}" if referrer and referrer["username"] else (referrer["first_name"] if referrer else "")
    ) or str(referrer_id)

    text = (
        "🚨 هشدار زیرمجموعه‌گیری فیک\n\n"
        f"👤 دعوت‌کننده: {referrer_label} ({referrer_id})\n"
        f"📊 تعداد کل دعوت‌ها: {flag['invited_total']}\n"
        f"🛒 بی‌خرید: {flag['zero_purchase_count']} ({flag.get('zero_purchase_ratio', 0)}٪)\n"
        f"⏱ دعوت در {flag['burst_minutes']} دقیقه‌ی اخیر: {flag['burst_count']}\n"
        f"❗️ دلیل: {flag['reason']}\n"
    )
    if flag.get("auto_suspended"):
        text += "\n⏸ پاداش‌های رفرال این کاربر تا بررسی دستی متوقف شد."
    else:
        text += "\nℹ️ توقف خودکار پاداش غیرفعال است؛ در صورت نیاز دستی بررسی کنید."

    if bot_token:
        try:
            if await report_router.send_raw_to_group(bot_token, db, TOPIC_KEY, text):
                return
        except Exception:
            logger.warning("ارسال هشدار زیرمجموعه‌گیری فیک به گروه گزارش ناموفق بود.", exc_info=True)

    for admin_id in db.list_admins():
        try:
            await send_fn(admin_id, text)
        except Exception:
            logger.warning("ارسال هشدار زیرمجموعه‌گیری فیک به ادمین %s ناموفق بود.", admin_id)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
