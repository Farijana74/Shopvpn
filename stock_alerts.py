# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
هشدار اتمام موجودی کانفیگ.

بعد از هر بار مصرف یک کانفیگ (فروش/تحویل)، این تابع بررسی می‌کند که آیا موجودی
باقی‌مانده‌ی آن محصول به آستانه‌ی هشدار (تنظیم «low_stock_threshold») رسیده یا نه.
اگر رسیده باشد و قبلاً برای همین افت هشدار داده نشده باشد، هشدار ارسال می‌شود.
وقتی موجودی دوباره بالای آستانه برود، وضعیت ریست می‌شود تا برای افت بعدی دوباره
هشدار بدهد.

اگر گروه گزارش تنظیم شده باشد (report_router)، هشدار در تاپیک «موجودی و انبار» همان گروه
پست می‌شود؛ در غیر این صورت (یا اگر ارسال به گروه ناموفق بود) مثل قبل به همه‌ی
ادمین‌ها پیام خصوصی می‌رود.

send_fn باید یک تابع async باشد که (admin_telegram_id, text) می‌گیرد؛ این کار
باعث می‌شود این ماژول هم در بات (aiogram) و هم در Mini App (aiohttp خام) قابل
استفاده باشد بدون وابستگی به یک ترنسپورت خاص. bot_token هم اختیاری است و فقط
برای ارسال مستقیم (HTTP خام، بدون نیاز به شیء Bot خاصی) به تاپیک «سرویس» گروه
گزارش استفاده می‌شود (از طریق report_router.report_raw)؛ اگر داده نشود یا گروه
تنظیم نباشد، مثل قبل فقط پیام خصوصی ادمین‌ها ارسال می‌شود.
"""

import logging

import report_router

logger = logging.getLogger(__name__)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

SERVICE_TOPIC_KEY = "stock"


async def check_and_notify_low_stock(send_fn, db, product_id: int, bot_token: str = None) -> None:
    try:
        stock = db.count_available_configs(product_id)
        threshold = int(db.get_setting("low_stock_threshold", "3") or 3)
    except Exception:
        return

    should_alert = db.check_low_stock_alert_state(product_id, stock, threshold)
    if not should_alert:
        return

    try:
        import channel_suggestions
        channel_suggestions.queue_low_stock(db, product_id)
    except Exception:
        logger.warning("ثبت پیشنهاد پست برای موجودی کم ناموفق بود.", exc_info=True)

    product = db.get_product(product_id)
    product_name = product["name"] if product else "نامشخص"
    text = (
        "⚠️ هشدار اتمام موجودی\n\n"
        f"📦 محصول «{product_name}» فقط {stock} کانفیگ آزاد باقی مانده "
        f"(آستانه‌ی هشدار: {threshold}).\n"
        "لطفاً هرچه زودتر کانفیگ جدید به انبار اضافه کنید."
    )

    if bot_token:
        try:
            if await report_router.send_raw_to_group(bot_token, db, SERVICE_TOPIC_KEY, text):
                return
        except Exception:
            logger.warning("ارسال هشدار موجودی کم به گروه گزارش ناموفق بود.", exc_info=True)

    for admin_id in db.list_admins():
        try:
            await send_fn(admin_id, text)
        except Exception:
            logger.warning("ارسال هشدار موجودی کم به ادمین %s ناموفق بود.", admin_id)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
