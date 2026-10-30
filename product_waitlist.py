#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
import asyncio
import logging

logger = logging.getLogger(__name__)

SEND_DELAY = 0.05


def available_markup(product_id: int, button_text: str) -> dict:
    return {"inline_keyboard": [[{"text": button_text, "callback_data": f"prod:{product_id}"}]]}
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


async def notify_waitlist(db, product_id: int, send) -> int:
    """send(user_id, text, markup_dict) -> awaitable bool. Returns the number of users notified."""
    product = await asyncio.to_thread(db.get_product, product_id)
    if not product or not product["is_active"]:
        return 0
    if await asyncio.to_thread(db.count_available_configs, product_id) <= 0:
        return 0
    user_ids = await asyncio.to_thread(db.get_product_waitlist, product_id)
    if not user_ids:
        return 0
    text = db.get_text(
        "product_waitlist.available",
        "🔔 محصول «{name}» اکنون در دسترس است و می‌توانید خرید کنید.",
    ).replace("{name}", product["name"])
    markup = available_markup(
        product_id, db.get_text("product_waitlist.buy_button", "🛒 خرید سریع")
    )
    sent = 0
    for user_id in user_ids:
        try:
            if await send(user_id, text, markup) is not False:
                sent += 1
        except Exception:
            logger.warning("product waitlist notify failed for %s", user_id, exc_info=True)
        await asyncio.to_thread(db.remove_product_waitlist, product_id, user_id)
        await asyncio.sleep(SEND_DELAY)
    return sent


def bot_sender(bot):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    async def send(user_id, text, markup):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=b["text"], callback_data=b["callback_data"]) for b in row]
            for row in markup["inline_keyboard"]
        ])
        await bot.send_message(user_id, text, reply_markup=kb)

    return send


def token_sender(send_message, bot_token: str):
    """Adapter for the web surfaces' raw Telegram HTTP helper (returns False on failure)."""
    async def send(user_id, text, markup):
        return await send_message(bot_token, user_id, text, reply_markup=markup)

    return send
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
