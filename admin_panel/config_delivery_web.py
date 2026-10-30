# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
معادل admin_panel-ی تابع deliver_config_to_user در config_delivery.py؛ همان
پیام «شیک» (عکس QR + مشخصات کامل سفارش + پیام تشکر) را برای کاربر می‌فرستد،
اما چون پنل وب مستقل نمونه‌ای از Bot در اختیار ندارد، مستقیم با Bot API خام
(از طریق admin_panel.telegram_notify) کار می‌کند.
"""

from config_delivery import build_qr_bytes, build_summary_text, get_post_delivery_text, prepare_delivery, strip_html
from admin_panel.telegram_notify import send_message as tg_send, send_photo as tg_send_photo
from config import BOT_TOKEN
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


async def _send_individual_configs_web(user_tg_id: int, links: list, bot_token: str) -> None:
    """معادل پنل وب _send_individual_configs در config_delivery.py؛ کانفیگ‌های تکی
    استخراج‌شده از لینک اشتراک را (با رعایت سقف کاراکتری تلگرام) برای کاربر می‌فرستد.
    کاملاً silent-fail است تا مانع تحویل اصلی سفارش نشود."""
    header = "📋 کانفیگ‌های تکی این اشتراک (اگه لینک اشتراک رو نتونستی مستقیم اضافه کنی، هرکدوم از این‌ها رو تکی وارد کن):\n\n"
    chunk = header
    chunks = []
    for c in links:
        piece = f"`{c}`\n\n"
        if len(chunk) + len(piece) > 3800:
            chunks.append(chunk)
            chunk = ""
        chunk += piece
    if chunk.strip():
        chunks.append(chunk)

    for part in chunks:
        try:
            ok = await tg_send(bot_token, user_tg_id, part, parse_mode="Markdown")
            if not ok:
                await tg_send(bot_token, user_tg_id, part)
        except Exception:
            pass


async def _send_tutorial_picker_web(bot_token: str, user_tg_id: int, db) -> None:
    """معادل پنل وب تابع tutorial.send_device_picker (tutorial.py)؛ چون پنل وب
    مستقل نمونه‌ی Bot اَیوگرم در اختیار ندارد، همان کیبورد انتخاب آموزش را
    به‌صورت دیکشنری خام Bot API می‌سازد (دکمه‌ها با همان callback_data الگوی
    tut_pick:<tutorial_id> که در handlers_user.py هندل می‌شود) و مستقیم با
    admin_panel.telegram_notify ارسال می‌کند. کاملاً silent-fail است تا مانع
    تحویل اصلی سفارش نشود."""
    if db is None:
        return
    try:
        tutorials = db.get_tutorials_for_target("post_purchase")
        if not tutorials:
            return
        keyboard = {
            "inline_keyboard": [
                [{"text": f"{d['emoji']} {d['name']}", "callback_data": f"tut_pick:{d['id']}"}]
                for d in tutorials
            ]
        }
        await tg_send(
            bot_token, user_tg_id,
            "📚 برای اتصال بدون مشکل، آموزش مورد نظرت رو انتخاب کن:",
            reply_markup=keyboard,
        )
    except Exception:
        pass


async def deliver_config_to_user_web(
    user_tg_id: int,
    product_name: str,
    links,
    final_price: int = None,
    order_id: int = None,
    db=None,
    bot_token: str = None,
) -> None:
    """نسخه‌ی پنل وب مستقل از تحویل کانفیگ؛ دقیقاً همان قالب پیام بات (عکس QR + کپشن HTML با
    «اطلاعات سرویس» و «لینک های اتصال») از طریق ماژول مشترک config_delivery.prepare_delivery.
    bot_token برای بات‌های نمایندگی (که توکن جدا دارند) باید صریحاً پاس داده شود؛ در غیر این صورت
    توکن بات اصلی استفاده می‌شود."""
    bot_token = bot_token or BOT_TOKEN
    gift_order = db.gift_get_order(order_id) if db is not None and order_id is not None else None
    if gift_order and gift_order["card_id"]:
        if db.gift_mark_order_delivered(order_id):
            await tg_send(bot_token, user_tg_id, "✅ خرید انجام شد و کانفیگ داخل کارت هدیه قرار گرفت. کارت را از «کیف پول ← 🎁 کارت هدیه ← کارت‌های من» ببینید و برای گیرنده بفرستید.")
        return
    if isinstance(links, str):
        links = [links]
    total = len(links)

    async def _send_html(text: str) -> None:
        ok = await tg_send(bot_token, user_tg_id, text, parse_mode="HTML")
        if not ok:
            await tg_send(bot_token, user_tg_id, strip_html(text))

    for idx, link in enumerate(links, start=1):
        caption, extras = await prepare_delivery(link, product_name, idx, total, db=db, user_tg_id=user_tg_id)

        sent = False
        try:
            qr_bytes = build_qr_bytes(link, db=db)
            sent = await tg_send_photo(bot_token, user_tg_id, qr_bytes, "config_qr.png", caption, parse_mode="HTML")
        except Exception:
            sent = False
        if not sent:
            # اگر ساخت/ارسال QR به هر دلیلی ناموفق بود، حداقل متن اطلاعات برای کاربر ارسال شود‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
            await _send_html(caption)

        for extra in extras:
            await _send_html(extra)

    if final_price is not None:
        await tg_send(bot_token, user_tg_id, build_summary_text(final_price, total))

    post_text = get_post_delivery_text(db)
    if post_text:
        try:
            await tg_send(bot_token, user_tg_id, post_text)
        except Exception:
            pass

    await _send_tutorial_picker_web(bot_token, user_tg_id, db)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
