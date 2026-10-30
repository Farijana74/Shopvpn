# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""ارسال آموزش‌ها به کاربر (متن/عکس/ویدیو، قدم‌به‌قدم).

هر آموزش یک عنوان و چند مرحله دارد (database.py: tutorial_devices/tutorial_steps)
و از طریق tutorial_hub.py به منوی «آموزش»، بعد از خرید موفق یا هر دکمه/بخش
دلخواه بات متصل می‌شود؛ این ماژول فقط مسئول نمایش آن‌ها به کاربر است.
"""

import asyncio
import html
import logging

import tutorial_hub

_log = logging.getLogger("tutorial")
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

_CAPTION_LIMIT = 1000
_TEXT_LIMIT = 4000


async def send_device_steps(bot, chat_id: int, db, device_id: int) -> bool:
    """مراحل یک آموزش را با عنوانش به‌ترتیب برای کاربر می‌فرستد.
    اگر آموزش مرحله‌ای نداشته باشد False برمی‌گرداند."""
    steps = await asyncio.to_thread(db.get_tutorial_steps, device_id)
    if not steps:
        return False
    device = await asyncio.to_thread(db.get_tutorial_device, device_id)
    header = ""
    if device:
        header = f"{html.escape(device['emoji'] or '📚')} <b>{html.escape(device['name'] or '')}</b>"
    for index, s in enumerate(steps):
        body = s["text"] or ""
        lead = header if index == 0 else ""
        try:
            if s["video_file_id"] or s["photo_file_id"]:
                caption = f"{lead}\n\n{body}".strip() if lead else body
                if lead and len(caption) > _CAPTION_LIMIT:
                    await bot.send_message(chat_id, lead)
                    caption = body
                    lead = ""
                caption = caption or None
                if s["video_file_id"]:
                    await bot.send_video(chat_id, s["video_file_id"], caption=caption)
                else:
                    await bot.send_photo(chat_id, s["photo_file_id"], caption=caption)
            elif body or lead:
                text = f"{lead}\n\n{body}".strip() if lead else body
                if lead and len(text) > _TEXT_LIMIT:
                    await bot.send_message(chat_id, lead)
                    text = body
                await bot.send_message(chat_id, text)
        except Exception:
            _log.exception("ارسال مرحله #%s آموزش #%s ناموفق بود.", s["id"], device_id)
    return True


async def send_target_tutorials(bot, chat_id: int, db, target_key: str, intro: str = None,
                                direct_if_single: bool = False) -> bool:
    """آموزش‌های متصل به یک مقصد را می‌فرستد: اگر direct_if_single و فقط یکی بود
    خودِ آموزش، وگرنه منوی انتخاب عنوان. اگر آموزشی نباشد False برمی‌گرداند."""
    import keyboards as kb
    tutorials = await asyncio.to_thread(db.get_tutorials_for_target, target_key)
    if not tutorials:
        return False
    if direct_if_single and len(tutorials) == 1:
        return await send_device_steps(bot, chat_id, db, tutorials[0]["id"])
    text = intro or "📚 یک آموزش را انتخاب کنید:"
    try:
        await bot.send_message(chat_id, text, reply_markup=kb.tutorial_devices_user_kb(tutorials))
    except Exception:
        _log.exception("ارسال منوی آموزش (%s) برای chat_id=%s ناموفق بود.", target_key, chat_id)
        return False
    return True


async def send_device_picker(bot, chat_id: int, db, intro: str = None) -> bool:
    """منوی آموزش‌های «بعد از خرید موفق» را می‌فرستد و اگر آموزشی متصل نباشد
    کاری نمی‌کند (False) - برای فراخوانی بی‌خطر بلافاصله بعد از هر خرید."""
    return await send_target_tutorials(
        bot, chat_id, db, tutorial_hub.POST_PURCHASE,
        intro=intro or "📚 برای اتصال بدون مشکل، آموزش مورد نظرت رو انتخاب کن:",
    )
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
