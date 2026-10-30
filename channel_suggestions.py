# -*- coding: utf-8 -*-
"""پیشنهاد خودکار ساخت پست کانال به مدیران وقتی محصول جدید اضافه شود، موجودی کم شود یا کد تخفیف عمومی ساخته شود."""

import asyncio
import json
import logging
import time
from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)
INTERVAL_SECONDS = 60
QUEUE_KEY = "sugg_lowstock_queue"
ENABLED_KEY = "channel_suggest_enabled"
_last_run = {}


def queue_low_stock(db, product_id: int) -> None:
    """افت موجودی را برای حلقه‌ی پیشنهاد صف می‌کند (بدون وابستگی به ترنسپورت بات یا Mini App)."""
    try:
        queue = json.loads(db.get_setting(QUEUE_KEY, "[]") or "[]")
        if int(product_id) not in queue:
            queue.append(int(product_id))
        db.set_setting(QUEUE_KEY, json.dumps(queue[-20:]))
    except Exception:
        logger.warning("صف‌کردن پیشنهاد موجودی کم ناموفق بود.", exc_info=True)


def _markup(data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 ساخت پست با AI", callback_data=data)],
        [InlineKeyboardButton(text="🙈 نادیده", callback_data="adm_sugg_skip")],
    ])


async def _send(bot, db, text: str, data: str) -> None:
    for admin_id in await asyncio.to_thread(db.list_admins):
        if not await asyncio.to_thread(db.is_senior_admin, admin_id):
            continue
        try:
            await bot.send_message(admin_id, text, reply_markup=_markup(data))
        except Exception:
            logger.warning("ارسال پیشنهاد پست به ادمین %s ناموفق بود.", admin_id)


async def tick(bot, db) -> None:
    if _last_run.get(id(db), 0) + INTERVAL_SECONDS > time.monotonic():
        return
    _last_run[id(db)] = time.monotonic()
    if await asyncio.to_thread(db.get_setting, ENABLED_KEY, "1") != "1":
        return
    import campaign_ai
    import channel_ai

    max_product, max_code = await asyncio.to_thread(db.get_catalog_max_ids)
    last_product = await asyncio.to_thread(db.get_setting, "sugg_last_product_id", "")
    last_code = await asyncio.to_thread(db.get_setting, "sugg_last_code_id", "")
    if last_product == "" or last_code == "":
        await asyncio.to_thread(db.set_setting, "sugg_last_product_id", str(max_product))
        await asyncio.to_thread(db.set_setting, "sugg_last_code_id", str(max_code))
        return
    products = await asyncio.to_thread(db.list_products_after, int(last_product), 5)
    codes = [dict(r) for r in await asyncio.to_thread(db.list_discount_codes) if r["id"] > int(last_code)]
    queue = json.loads(await asyncio.to_thread(db.get_setting, QUEUE_KEY, "[]") or "[]")
    if not (products or codes or queue) and max_product == int(last_product) and max_code == int(last_code):
        return
    await asyncio.to_thread(db.set_setting, "sugg_last_product_id", str(max_product))
    await asyncio.to_thread(db.set_setting, "sugg_last_code_id", str(max_code))
    await asyncio.to_thread(db.set_setting, QUEUE_KEY, "[]")
    if not await asyncio.to_thread(channel_ai.text_configured, db):
        return
    for product in products:
        if product["is_active"]:
            await _send(bot, db, f"💡 محصول جدید «{product['name']}» اضافه شد. پستی برای معرفی‌اش در کانال بسازم؟", f"adm_sugg:p:{product['id']}")
    now = datetime.utcnow()
    for code in codes:
        if campaign_ai._is_active_code(code, now):
            await _send(bot, db, f"💡 کد تخفیف عمومی «{code['code']}» ساخته شد. پستی برای اطلاع‌رسانی‌اش در کانال بسازم؟", f"adm_sugg:d:{code['id']}")
    for product_id in queue:
        product = await asyncio.to_thread(db.get_product, product_id)
        if product and product["is_active"]:
            await _send(bot, db, f"💡 موجودی «{product['name']}» کم شده. پست «آخرین موجودی» برای کانال بسازم؟", f"adm_sugg:l:{product_id}")
