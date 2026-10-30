# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""کوییز روزانه‌ی کانال: انتشار، ثبت پاسخ از طریق دیپ‌لینک بات، جایزه‌ی کد تخفیف و بستن."""

import asyncio
import html
import json
import logging
import time
from datetime import datetime, timezone

from seasons import TEHRAN, fmt_local, mask_name

_log = logging.getLogger("daily_quiz")
_FMT = "%Y-%m-%d %H:%M:%S"
_CHANNEL_SETTING = "promo_channel_id"
_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
_username_cache = {}

SETTINGS = {
    "quiz_hour": ("ساعت انتشار کوییز (به وقت تهران، ۰ تا ۲۳)", 20, 0, 23),
    "quiz_winner_limit": ("تعداد برندگان (نفرات اول درست‌جواب)", 3, 1, 50),
    "quiz_discount_percent": ("درصد کد تخفیف برندگان", 10, 1, 100),
    "quiz_code_valid_hours": ("مدت اعتبار کد تخفیف (ساعت)", 48, 1, 720),
    "quiz_close_hours": ("مدت باز بودن کوییز (ساعت)", 20, 1, 23),
}


def get_int(db, key: str) -> int:
    _label, default, low, high = SETTINGS[key]
    try:
        return max(low, min(int(db.get_setting(key, str(default)) or default), high))
    except (TypeError, ValueError):
        return default


def enabled(db) -> bool:
    return db.get_setting("quiz_enabled", "0") == "1"


def ai_fallback(db) -> bool:
    return db.get_setting("quiz_ai_fallback", "1") == "1"


def validate_item(question: str, options: list, correct_index: int, explanation: str = ""):
    question = " ".join(str(question or "").split())
    options = [" ".join(str(o).split()) for o in options]
    explanation = " ".join(str(explanation or "").split())
    if not question or len(question) > 250:
        return None
    if not 2 <= len(options) <= 6 or any(not o or len(o) > 60 for o in options) or len(set(options)) != len(options):
        return None
    if not isinstance(correct_index, int) or not 0 <= correct_index < len(options) or len(explanation) > 200:
        return None
    return {"question": question, "options": options, "correct_index": correct_index, "explanation": explanation}


def _today_start_utc() -> str:
    local = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(TEHRAN)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).strftime(_FMT)


async def _bot_username(bot) -> str:
    if bot.token not in _username_cache:
        _username_cache[bot.token] = (await bot.get_me()).username
    return _username_cache[bot.token]


def post_text(quiz) -> str:
    return (
        "❓ <b>کوییز روزانه</b>\n\n"
        f"{html.escape(quiz['question'])}\n\n"
        f"🎁 {quiz['winner_limit']} نفر اول که درست جواب بدهند، کد تخفیف {quiz['discount_percent']}٪ می‌گیرند.\n"
        f"⏳ مهلت پاسخ: تا {fmt_local(quiz['closes_at'])}\n"
        "👇 گزینه‌ات را انتخاب کن؛ پاسخ در چت خصوصی بات ثبت می‌شود."
    )


async def post_markup(bot, quiz):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    username = await _bot_username(bot)
    options = json.loads(quiz["options_json"])
    rows = [[InlineKeyboardButton(text=option, url=f"https://t.me/{username}?start=quiz_{quiz['id']}_{index}")]
            for index, option in enumerate(options)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _closed_markup(bot):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🛒 ورود به فروشگاه", url=f"https://t.me/{await _bot_username(bot)}?start=buy")
    ]])


def closed_text(db, quiz) -> str:
    options = json.loads(quiz["options_json"])
    stats = db.quiz_stats(quiz["id"])
    winners = db.quiz_winners(quiz["id"])
    lines = [
        "🔒 <b>کوییز روزانه — پایان یافت</b>", "",
        html.escape(quiz["question"]), "",
        f"✅ پاسخ درست: {html.escape(options[quiz['correct_index']])}",
    ]
    if quiz["explanation"]:
        lines.append(f"💡 {html.escape(quiz['explanation'])}")
    lines.append("")
    if winners:
        lines.append("🏆 برندگان: " + "، ".join(
            f"{_MEDALS.get(w['win_rank'], '🎖')} {mask_name(w['first_name'], w['username'], w['user_id'])}" for w in winners
        ))
    else:
        lines.append("این بار کسی برنده نشد.")
    lines.append(f"👥 {stats['total']} نفر شرکت کردند و {stats['correct']} نفر درست جواب دادند.")
    return "\n".join(lines)


async def finalize_message(db, bot, quiz):
    if not quiz["message_id"] or not quiz["channel_id"]:
        return
    try:
        text = await asyncio.to_thread(closed_text, db, quiz)
        await bot.edit_message_text(
            text, chat_id=quiz["channel_id"], message_id=quiz["message_id"], reply_markup=await _closed_markup(bot),
        )
    except Exception:
        _log.warning("quiz %s close edit failed", quiz["id"], exc_info=True)


async def close_due(db, bot):
    for quiz in await asyncio.to_thread(db.quiz_close_due):
        await finalize_message(db, bot, quiz)


async def _pick_item(db):
    row = await asyncio.to_thread(db.quiz_bank_pick)
    if row:
        return row["id"], {
            "question": row["question"], "options": json.loads(row["options_json"]),
            "correct_index": row["correct_index"], "explanation": row["explanation"],
        }
    if not ai_fallback(db):
        raise ValueError("بانک سوال خالی است و ساخت با AI خاموش است.")
    import campaign_ai
    import channel_ai
    if not await asyncio.to_thread(channel_ai.text_configured, db):
        raise ValueError("بانک سوال خالی است و مدل متن کانال هم تنظیم نشده.")
    generated = await campaign_ai.generate_engagement(db, "quiz", "")
    item = validate_item(generated["question"], generated["options"], generated["correct_index"], generated["explanation"])
    if not item:
        raise ValueError("سوال ساخته‌شده با AI معتبر نبود.")
    bank_id = await asyncio.to_thread(db.quiz_bank_add, item["question"], item["options"], item["correct_index"], item["explanation"], "ai")
    return bank_id, item


async def post_quiz(db, bot, force: bool = False):
    """کوییز را در کانال منتشر می‌کند؛ خروجی (موفق، پیام)."""
    channel = db.get_setting(_CHANNEL_SETTING, "")
    if not channel:
        return False, "کانال تبلیغاتی تنظیم نشده است."
    if not force and await asyncio.to_thread(db.quiz_posted_since, _today_start_utc()):
        return False, "کوییز امروز قبلاً منتشر شده است."
    try:
        bank_id, item = await _pick_item(db)
    except Exception as exc:
        return False, str(exc)
    quiz_id = await asyncio.to_thread(
        db.quiz_create, item, bank_id, channel, get_int(db, "quiz_winner_limit"), get_int(db, "quiz_discount_percent"),
        get_int(db, "quiz_code_valid_hours"), get_int(db, "quiz_close_hours"),
    )
    quiz = await asyncio.to_thread(db.quiz_get, quiz_id)
    try:
        message = await bot.send_message(channel, post_text(quiz), reply_markup=await post_markup(bot, quiz))
    except Exception as exc:
        await asyncio.to_thread(db.quiz_discard, quiz_id)
        return False, f"ارسال به کانال ناموفق بود: {exc}"
    await asyncio.to_thread(db.quiz_set_message, quiz_id, message.message_id)
    return True, str(quiz_id)


async def answer_quiz_for_message(db, message, quiz_id: int, choice: int):
    """ثبت پاسخ کاربر (از دیپ‌لینک کانال) و اعلام نتیجه در چت خصوصی."""
    user = message.from_user
    eligible = not await asyncio.to_thread(db.is_admin, user.id)
    result = await asyncio.to_thread(db.quiz_answer, quiz_id, user.id, choice, eligible)
    if not result["ok"]:
        if result["reason"] == "already":
            previous = result["answer"]
            if previous["code"]:
                await message.answer(f"ℹ️ قبلاً به این کوییز پاسخ داده‌ای.\n🎟 کد تخفیف تو: <code>{previous['code']}</code>")
            else:
                await message.answer("ℹ️ قبلاً به این کوییز پاسخ داده‌ای؛ هر نفر یک‌بار می‌تواند شرکت کند.")
        elif result["reason"] == "closed":
            await message.answer("⌛️ مهلت پاسخ به این کوییز تمام شده است. کوییز بعدی را از دست نده!")
        else:
            await message.answer("❌ این کوییز پیدا نشد.")
        return
    if not result["correct"]:
        await message.answer("❌ پاسخ درست نبود. فردا دوباره شانست را امتحان کن!")
    elif not eligible:
        await message.answer("✅ پاسخ درست بود. (ادمین‌ها جایزه نمی‌گیرند.)")
    elif result["code"]:
        await message.answer(
            f"🎉 آفرین! پاسخ درست بود و نفر {result['win_rank']} شدی.\n"
            f"🎟 کد تخفیف {result['quiz']['discount_percent']}٪ (یک‌بار مصرف، تا {fmt_local(result['code_expires_at'])}):\n"
            f"<code>{result['code']}</code>"
        )
    else:
        await message.answer("✅ پاسخ درست بود، اما ظرفیت جایزه‌ی این کوییز تکمیل شده بود. فردا دوباره امتحان کن!")


_RETRY_SECONDS = 1800
_last_failure = {}


async def tick(db, bot):
    await close_due(db, bot)
    if not enabled(db):
        return
    local = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(TEHRAN)
    if local.hour < get_int(db, "quiz_hour"):
        return
    failed_at = _last_failure.get(bot.token)
    if failed_at and time.monotonic() - failed_at < _RETRY_SECONDS:
        return
    ok, info = await post_quiz(db, bot)
    if ok or "قبلاً" in info:
        _last_failure.pop(bot.token, None)
        return
    _last_failure[bot.token] = time.monotonic()
    _log.warning("daily quiz post failed: %s", info)


async def quiz_loop(db, bot, interval: int = 120):
    while True:
        try:
            await tick(db, bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("quiz loop failed")
        await asyncio.sleep(interval)
