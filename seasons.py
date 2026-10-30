# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""منطق فصل‌های رقابتی زیرمجموعه‌گیری: شروع ماهانه، بستن، جوایز، نشان‌ها و اعلان نتیجه."""

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from jalali import gregorian_to_jalali, jalali_to_gregorian, to_jalali_str

try:
    from zoneinfo import ZoneInfo
    TEHRAN = ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN = timezone(timedelta(hours=3, minutes=30))

_log = logging.getLogger("seasons")
_FMT = "%Y-%m-%d %H:%M:%S"
_MIN_DAYS = 3
_CHANNEL_SETTING = "promo_channel_id"
_username_cache = {}

MONTHS = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")

SETTINGS = {
    "season_invite_points": ("امتیاز هر دعوت جدید", 1, 0, 100),
    "season_purchase_points": ("امتیاز اضافه وقتی دعوت‌شده خرید کند", 5, 0, 1000),
    "season_prize_wallet_1": ("جایزه‌ی کیف پول رتبه ۱ (تومان)", 0, 0, 1_000_000_000),
    "season_prize_wallet_2": ("جایزه‌ی کیف پول رتبه ۲ (تومان)", 0, 0, 1_000_000_000),
    "season_prize_wallet_3": ("جایزه‌ی کیف پول رتبه ۳ (تومان)", 0, 0, 1_000_000_000),
    "season_prize_discount_1": ("کد تخفیف رتبه ۱ (درصد)", 0, 0, 100),
    "season_prize_discount_2": ("کد تخفیف رتبه ۲ (درصد)", 0, 0, 100),
    "season_prize_discount_3": ("کد تخفیف رتبه ۳ (درصد)", 0, 0, 100),
    "season_prize_product_1": ("سرویس رایگان رتبه ۱ (شناسه‌ی محصول، ۰ = ندارد)", 0, 0, 1_000_000_000),
    "season_prize_product_2": ("سرویس رایگان رتبه ۲ (شناسه‌ی محصول، ۰ = ندارد)", 0, 0, 1_000_000_000),
    "season_prize_product_3": ("سرویس رایگان رتبه ۳ (شناسه‌ی محصول، ۰ = ندارد)", 0, 0, 1_000_000_000),
    "season_prize_valid_days": ("مدت اعتبار کد تخفیف جایزه (روز)", 30, 1, 365),
}

BADGES = {
    1: ("season_gold", "🥇", "قهرمان فصل"),
    2: ("season_silver", "🥈", "نایب‌قهرمان فصل"),
    3: ("season_bronze", "🥉", "مقام سوم فصل"),
}
_ELITE_BADGE = ("season_elite", "🎖", "ده نفر برتر فصل")


def money(value) -> str:
    return f"{int(value or 0):,}"


def get_int(db, key: str) -> int:
    _label, default, low, high = SETTINGS[key]
    try:
        return max(low, min(int(db.get_setting(key, str(default)) or default), high))
    except (TypeError, ValueError):
        return default


def enabled(db) -> bool:
    return db.get_setting("season_enabled", "0") == "1"


def auto_renew(db) -> bool:
    return db.get_setting("season_auto", "1") == "1"


def announce_enabled(db) -> bool:
    return db.get_setting("season_announce", "1") == "1"


def prizes_snapshot(db) -> dict:
    return {
        rank: {
            "wallet": get_int(db, f"season_prize_wallet_{rank}"),
            "discount": get_int(db, f"season_prize_discount_{rank}"),
            "product": get_int(db, f"season_prize_product_{rank}"),
        }
        for rank in (1, 2, 3)
    }


def mask_name(first_name: str, username: str, user_id: int) -> str:
    base = (first_name or "").strip() or (username or "").strip() or str(user_id)[-4:]
    return html.escape(base[:2] + "•••")


def fmt_local(value) -> str:
    if not value:
        return "-"
    dt = datetime.strptime(str(value)[:19].replace("T", " "), _FMT).replace(tzinfo=timezone.utc).astimezone(TEHRAN)
    return to_jalali_str(dt.replace(tzinfo=None), with_time=True)


def next_window(now_utc: datetime = None):
    """عنوان و بازه‌ی فصل بعدی تا پایان ماه شمسی (حداقل چند روز)؛ زمان‌ها UTC با فرمت دیتابیس."""
    now_utc = now_utc or datetime.utcnow()
    local = now_utc.replace(tzinfo=timezone.utc).astimezone(TEHRAN)
    jy, jm, _jd = gregorian_to_jalali(local.year, local.month, local.day)

    def month_start(year, month):
        gy, gm, gd = jalali_to_gregorian(year, month, 1)
        return datetime(gy, gm, gd, tzinfo=TEHRAN)

    year, month = (jy + 1, 1) if jm == 12 else (jy, jm + 1)
    end = month_start(year, month)
    if end - local < timedelta(days=_MIN_DAYS):
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        end = month_start(year, month)
    last_day = gregorian_to_jalali(*(end - timedelta(seconds=1)).timetuple()[:3])
    title = f"{MONTHS[last_day[1] - 1]} {last_day[0]}"
    return title, now_utc.strftime(_FMT), end.astimezone(timezone.utc).strftime(_FMT)


def start_season(db):
    title, starts_at, ends_at = next_window()
    return db.season_start(title, starts_at, ends_at, get_int(db, "season_invite_points"), get_int(db, "season_purchase_points"))


def prize_lines(prizes: dict, db=None) -> list:
    lines = []
    for rank in (1, 2, 3):
        prize = prizes.get(rank) or prizes.get(str(rank)) or {}
        parts = []
        if prize.get("wallet"):
            parts.append(f"💰 {money(prize['wallet'])} تومان شارژ کیف پول")
        if prize.get("discount"):
            parts.append(f"🎟 کد تخفیف {prize['discount']}٪")
        if prize.get("product"):
            product = db.get_product(prize["product"]) if db else None
            name = html.escape(product["name"]) if product else f"#{prize['product']}"
            parts.append(f"🆓 سرویس رایگان ({name})")
        badge = BADGES[rank]
        parts.append(f"{badge[1]} نشان ویژه")
        lines.append(f"{badge[1]} رتبه {rank}: " + " + ".join(parts))
    return lines


async def _bot_username(bot) -> str:
    if bot.token not in _username_cache:
        _username_cache[bot.token] = (await bot.get_me()).username
    return _username_cache[bot.token]


async def award_pending(db, bot):
    """جوایز نتایج ثبت‌شده را با الگوی claim اتمیک می‌دهد؛ هر نتیجه فقط یک‌بار."""
    valid_days = get_int(db, "season_prize_valid_days")
    for result in await asyncio.to_thread(db.season_pending_results):
        if not await asyncio.to_thread(db.season_claim_result, result["id"]):
            continue
        code = None
        try:
            user_id, rank, season_id = result["user_id"], result["rank"], result["season_id"]
            if result["prize_wallet"] > 0:
                await asyncio.to_thread(
                    db.add_wallet_credit, user_id, int(result["prize_wallet"]), "season_prize",
                    f"جایزه‌ی فصل رقابتی {result['season_title']} (رتبه {rank})",
                )
            codes = []
            if result["prize_discount"] > 0:
                codes.append(await asyncio.to_thread(
                    db.create_season_prize_code, season_id, rank, user_id, result["prize_discount"], valid_days,
                ))
            if result["prize_product"] > 0:
                free_code = await asyncio.to_thread(
                    db.create_season_prize_code, season_id, rank, user_id, 100, valid_days, result["prize_product"],
                )
                codes.append(free_code)
            code = ",".join(codes) if codes else None
            badge = BADGES.get(rank, _ELITE_BADGE)
            await asyncio.to_thread(
                db.badge_grant, user_id, season_id, badge[0], badge[1], f"{badge[2]} {result['season_title']}",
            )
        except Exception:
            _log.exception("season award failed for result %s", result["id"])
            continue
        await asyncio.to_thread(db.season_finish_result, result["id"], code)
        lines = [f"🏆 تبریک! در فصل رقابتی «{html.escape(result['season_title'])}» رتبه‌ی {rank} شدی.", ""]
        if result["prize_wallet"] > 0:
            lines.append(f"💰 {money(result['prize_wallet'])} تومان به کیف پولت اضافه شد.")
        if result["prize_discount"] > 0:
            lines.append(f"🎟 کد تخفیف {result['prize_discount']}٪ (یک‌بار مصرف، {valid_days} روز اعتبار): <code>{codes[0]}</code>")
        if result["prize_product"] > 0:
            lines.append(f"🆓 کد سرویس رایگان (فقط برای همان محصول، یک‌بار مصرف، {valid_days} روز اعتبار): <code>{codes[-1]}</code>")
        badge = BADGES.get(rank, _ELITE_BADGE)
        lines.append(f"{badge[1]} نشان «{badge[2]}» به پروفایلت اضافه شد.")
        try:
            await bot.send_message(result["user_id"], "\n".join(lines))
        except Exception:
            _log.info("season prize notice to %s failed", result["user_id"])


def results_text(season, results) -> str:
    lines = [f"🏁 پایان فصل رقابتی «{html.escape(season['title'])}»", ""]
    if not results:
        lines.append("در این فصل کسی امتیازی کسب نکرد.")
    for row in results[:3]:
        badge = BADGES[row["rank"]]
        lines.append(
            f"{badge[1]} {mask_name(row['first_name'], row['username'], row['user_id'])} — {row['score']} امتیاز "
            f"({row['invites']} دعوت، {row['buyers']} خرید)"
        )
    lines += ["", "فصل تازه شروع شد؛ با دعوت دوستانت در رقابت بعدی شرکت کن."]
    return "\n".join(lines)


async def announce_closed(db, bot):
    if not announce_enabled(db):
        return
    channel = db.get_setting(_CHANNEL_SETTING, "")
    for season in await asyncio.to_thread(db.season_unannounced_closed):
        if not await asyncio.to_thread(db.season_mark_announced, season["id"]):
            continue
        if not channel:
            continue
        results = await asyncio.to_thread(db.season_results, season["id"])
        try:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🏆 ورود به رقابت", url=f"https://t.me/{await _bot_username(bot)}?start=season")
            ]])
            await bot.send_message(channel, results_text(season, results), reply_markup=markup)
        except Exception:
            _log.warning("season announce failed", exc_info=True)


async def close_and_award(db, bot, season_id: int):
    standings = await asyncio.to_thread(db.season_close, season_id, prizes_snapshot(db))
    await award_pending(db, bot)
    await announce_closed(db, bot)
    return standings


async def tick(db, bot):
    active = await asyncio.to_thread(db.season_get_active)
    if active and datetime.utcnow().strftime(_FMT) >= active["ends_at"]:
        await close_and_award(db, bot, active["id"])
        active = None
    await award_pending(db, bot)
    await announce_closed(db, bot)
    if not active and enabled(db) and auto_renew(db):
        await asyncio.to_thread(start_season, db)


async def season_loop(db, bot, interval: int = 300):
    while True:
        try:
            await tick(db, bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("season loop failed")
        await asyncio.sleep(interval)
