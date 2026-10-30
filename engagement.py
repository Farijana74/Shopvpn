# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""منطق صندوق شانس و پیشرفت کاربر: ثبت استریک، اهدای نقاط عطف، نوار پیشرفت و انیمیشن باز کردن."""

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from seasons import TEHRAN

_log = logging.getLogger("engagement")
_seen = {}
_tasks = set()

TRACKS = {
    "streak": ("🔥", "روز متوالی استفاده", "استریک {n} روزه"),
    "referral": ("🤝", "دعوت موفق", "{n} دعوت موفق"),
    "renewal": ("🔄", "تمدید سرویس", "{n} تمدید"),
}

SETTINGS = {
    "lootbox_daily_limit": ("سقف خرید صندوق در روز برای هر کاربر (۰ = نامحدود)", 10, 0, 1000),
    "lootbox_code_valid_days": ("مدت اعتبار کد جایزه‌ی صندوق (روز)", 7, 1, 365),
    "streak_code_valid_days": ("مدت اعتبار کد تخفیف نقاط عطف (روز)", 14, 1, 365),
}


def get_int(db, key: str) -> int:
    _label, default, low, high = SETTINGS[key]
    try:
        return max(low, min(int(db.get_setting(key, str(default)) or default), high))
    except (TypeError, ValueError):
        return default


def lootbox_enabled(db) -> bool:
    return db.get_setting("lootbox_enabled", "0") == "1"


def streak_enabled(db) -> bool:
    return db.get_setting("streak_enabled", "0") == "1"


def bar(value: int, target: int, width: int = 10) -> str:
    target = max(1, int(target))
    filled = min(width, int(width * min(value, target) / target))
    return "█" * filled + "░" * (width - filled)


def money(value) -> str:
    return f"{int(value or 0):,}"


def _tehran_days():
    today = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(TEHRAN).date()
    return today.isoformat(), (today - timedelta(days=1)).isoformat()


def reward_text(wallet: int, discount: int, box: str = None) -> str:
    parts = []
    if wallet:
        parts.append(f"💰 {money(wallet)} تومان")
    if discount:
        parts.append(f"🎟 کد تخفیف {discount}٪")
    if box:
        parts.append(f"📦 {html.escape(box)}")
    return " + ".join(parts)


async def process_milestones(db, bot, user_id: int):
    valid_days = get_int(db, "streak_code_valid_days")
    for milestone in await asyncio.to_thread(db.milestone_pending, user_id):
        result = await asyncio.to_thread(db.milestone_claim, user_id, milestone["id"], valid_days)
        if not result:
            continue
        lines = [f"🎉 تبریک! به نقطه‌ی عطف «{html.escape(milestone['title'])}» رسیدی و نشان {milestone['emoji']} گرفتی."]
        if result["wallet"]:
            lines.append(f"💰 {money(result['wallet'])} تومان به کیف پولت اضافه شد.")
        if result["code"]:
            lines.append(f"🎟 کد تخفیف {milestone['discount']}٪ (یک‌بار مصرف، {valid_days} روز اعتبار): <code>{result['code']}</code>")
        if result["box"]:
            lines.append(f"📦 یک «{html.escape(result['box'])}» به صندوق‌هایت اضافه شد.")
        try:
            await bot.send_message(user_id, "\n".join(lines))
        except Exception:
            _log.info("milestone notice to %s failed", user_id)


async def touch(db, bot, user_id: int):
    """ثبت حضور روزانه و بررسی نقاط عطف؛ در هر روز برای هر کاربر فقط یک‌بار کار واقعی انجام می‌دهد."""
    today, yesterday = _tehran_days()
    key = (db.db_path, user_id)
    if _seen.get(key) == today:
        return
    _seen[key] = today
    await asyncio.to_thread(db.streak_touch, user_id, today, yesterday)
    await process_milestones(db, bot, user_id)


def schedule_touch(db, bot, user_id: int):
    task = asyncio.create_task(_safe_touch(db, bot, user_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _safe_touch(db, bot, user_id: int):
    try:
        await touch(db, bot, user_id)
    except Exception:
        _log.exception("streak touch failed for %s", user_id)


def progress_text(db, user_id: int) -> str:
    values = db.progress_values(user_id)
    milestones = db.milestone_list(active_only=True)
    done = {m["id"] for m in milestones if values.get(m["track"], 0) >= m["threshold"]}
    lines = ["📈 پیشرفت من", ""]
    for track, (emoji, label, _title) in TRACKS.items():
        value = values[track]
        track_ms = [m for m in milestones if m["track"] == track]
        nxt = next((m for m in track_ms if m["id"] not in done), None)
        if track == "streak":
            lines.append(f"{emoji} استریک فعلی: {values['streak_current']} روز | رکورد: {values['streak_best']} روز")
        else:
            lines.append(f"{emoji} {label}: {value}")
        if nxt:
            reward = reward_text(nxt["wallet"], nxt["discount"], None)
            if nxt["box_type_id"]:
                box_type = db.box_type_get(nxt["box_type_id"])
                reward = reward_text(nxt["wallet"], nxt["discount"], box_type["title"] if box_type else None)
            lines.append(f"{bar(value, nxt['threshold'])} {min(value, nxt['threshold'])}/{nxt['threshold']}")
            lines.append(f"   هدف بعدی: «{html.escape(nxt['title'])}»" + (f" ← {reward}" if reward else " ← نشان ویژه"))
        elif track_ms:
            lines.append("✅ همه‌ی نقاط عطف این بخش را گرفته‌ای!")
        lines.append("")
    lines.append("استریک با هر بار استفاده از بات در یک روز (وقت تهران) ادامه پیدا می‌کند؛ یک روز غیبت آن را از ۱ شروع می‌کند.")
    return "\n".join(lines)


def prize_summary(prize) -> str:
    if prize["kind"] == "wallet":
        return f"💰 {money(prize['value'])} تومان شارژ کیف پول"
    if prize["kind"] == "discount":
        return f"🎟 کد تخفیف {prize['value']}٪"
    if prize["kind"] == "product":
        return f"🆓 {html.escape(prize['title'])}"
    return html.escape(prize["title"])


def odds_lines(db, type_id: int) -> list:
    prizes = db.box_prize_list(type_id)
    lines = [f"• {prize_summary(p)}: {p['chance']:g}٪" for p in prizes]
    total = sum(float(p["chance"]) for p in prizes)
    if total < 100:
        lines.append(f"• پوچ: {round(100 - total, 4):g}٪")
    return lines


FRAMES = ("📦 صندوق در حال باز شدن...", "🔒📦🔒 قفل صندوق می‌لرزد...", "✨📦✨ نور از شکاف بیرون می‌زند...", "🎆🎁🎆 …")


async def animate(message):
    """نمایش چند فریم با ویرایش یک پیام؛ پیام نهایی را برمی‌گرداند تا نتیجه روی همان نوشته شود."""
    frame_message = await message.answer(FRAMES[0])
    for frame in FRAMES[1:]:
        await asyncio.sleep(0.8)
        try:
            await frame_message.edit_text(frame)
        except Exception:
            break
    await asyncio.sleep(0.6)
    return frame_message


def result_text(result: dict, valid_days: int) -> str:
    box = result["box"]
    head = f"{box['emoji'] or '📦'} {html.escape(box['type_title'] or '')}\n\n"
    if result["kind"] == "wallet":
        return head + f"🎉 تبریک! {money(result['value'])} تومان به کیف پولت اضافه شد."
    if result["kind"] == "discount":
        return head + (
            f"🎉 تبریک! کد تخفیف {result['value']}٪ (یک‌بار مصرف، {valid_days} روز اعتبار):\n<code>{result['code']}</code>"
        )
    if result["kind"] == "product":
        return head + (
            f"🎉 جایزه‌ی بزرگ! «{html.escape(result['title'])}»\n"
            f"کد سرویس رایگان (فقط برای همان محصول، یک‌بار مصرف، {valid_days} روز اعتبار):\n<code>{result['code']}</code>"
        )
    return head + "😔 این بار پوچ بود؛ شانست را با صندوق بعدی امتحان کن!"
