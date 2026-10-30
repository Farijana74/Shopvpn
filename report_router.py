#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
from i18n import tr
# -*- coding: utf-8 -*-
"""مسیریابی گزارش‌ها به گروه فوروم تاپیک‌دار؛ بدون گروه یا هنگام خطا، ارسال مستقیم به مدیران."""

import asyncio
import html
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)

SETTING_CHAT_ID = "report_chat_id"
SETTING_TOPIC_PREFS = "report_topic_prefs"
DEFAULT_TOPIC = "other"

# رنگ‌های مجاز آیکون تاپیک در تلگرام (createForumTopic.icon_color)
COLOR_BLUE, COLOR_YELLOW, COLOR_PURPLE = 0x6FB9F0, 0xFFD67E, 0xCB86DB
COLOR_GREEN, COLOR_PINK, COLOR_RED = 0x8EEE98, 0xFF93B2, 0xFB6F5F


@dataclass(frozen=True)
class TopicSpec:
    """مشخصات کامل یک تاپیک گزارش.

    key          شناسه‌ی ثابت (در کد و دیتابیس استفاده می‌شود؛ هرگز تغییر نکند)
    name         نام پیش‌فرض نمایشی (مدیر می‌تواند سفارشی کند)
    description  توضیح کاربرد؛ در منوی مدیریت و پیام پین‌شده‌ی خود تاپیک دیده می‌شود
    group        دسته‌ی نمایشی در منوی مدیریت
    color        رنگ آیکون تاپیک در تلگرام
    silent       True یعنی پیام‌ها بدون نوتیفیکیشن/صدا ارسال شوند (قابل override توسط مدیر)
    locked       True یعنی غیرفعال‌شدنی نیست (مثل «خطا» و «سایر»)
    profiles     در کدام پروفایل‌های آماده (minimal/standard) فعال است؛ «full» همه را فعال می‌کند
    """
    key: str
    name: str
    description: str
    group: str
    color: int = COLOR_BLUE
    silent: bool = False
    locked: bool = False
    profiles: tuple = ("minimal", "standard")


TOPIC_GROUPS = {
    "sales": "🛍 فروش و مالی",
    "customers": "👥 مشتریان و پشتیبانی",
    "ops": "🛠 عملیات و زیرساخت",
    "security": "🛡 امنیت و کنترل",
    "system": "⚙️ سیستم و گزارش‌ها",
}

TOPIC_SPECS = (
    # ---- فروش و مالی ----
    TopicSpec("purchase", "🛒 خرید", "سفارش‌های جدید، رسید کارت‌به‌کارت و نتیجه‌ی تایید/رد سفارش.", "sales", COLOR_GREEN),
    TopicSpec("renewal", "🔄 تمدید", "تمدیدهای انجام‌شده، یادآورهای تمدید و پیشنهادهای تمدید هوشمند.", "sales", COLOR_GREEN, True, profiles=("standard",)),
    TopicSpec("finance", "💰 مالی", "شارژ کیف پول، رسیدهای واریز و تراکنش‌های مالی.", "sales", COLOR_YELLOW),
    TopicSpec("gateway", "💳 درگاه‌های پرداخت", "خطاها و مشکلات درگاه‌های پرداخت اضافه (زرین‌پال، کریپتو، استارز و...).", "sales", COLOR_YELLOW, profiles=("standard",)),
    TopicSpec("refund", "↩️ بازگشت وجه", "بازگشت وجه به کیف پول بعد از حذف سرویس توسط کاربر یا نماینده.", "sales", COLOR_PINK, profiles=("standard",)),
    TopicSpec("discount", "🎴 گیفت‌کارت", "گیفت‌کارت‌های دریافت‌شده توسط کاربران (کیف پول یا کانفیگ).", "sales", COLOR_PINK, True, profiles=()),
    # ---- مشتریان و پشتیبانی ----
    TopicSpec("signup", "🆕 ثبت‌نام", "کاربران جدید و منبع ورودشان (رفرال، مینی‌اپ، کانال).", "customers", COLOR_BLUE, True),
    TopicSpec("referral", "🤝 رفرال", "دعوت‌های موفق و پاداش‌های زیرمجموعه‌گیری.", "customers", COLOR_BLUE, True, profiles=("standard",)),
    TopicSpec("support", "🎧 پشتیبانی", "تیکت‌های جدید، پیام‌های کاربران به پشتیبانی و ارجاع از هوش مصنوعی به انسان.", "customers", COLOR_PURPLE),
    TopicSpec("test", "🧪 تست", "کانفیگ‌های تست تحویل‌داده‌شده؛ فرصت پیگیری برای تبدیل به خرید.", "customers", COLOR_PURPLE, True, profiles=("standard",)),
    TopicSpec("reseller", "🏪 نمایندگی", "درخواست نمایندگی/ارتقای سطح، فعال‌سازی و خرید اعتبار نمایندگان.", "customers", COLOR_PURPLE),
    TopicSpec("prize", "🎁 جوایز", "گردونه‌ی شانس، قرعه‌کشی و هدیه‌ی ثبت‌نام.", "customers", COLOR_PINK, True, profiles=("standard",)),
    TopicSpec("churn", "📉 ریزش مشتری", "کاربران در خطر ریزش و نتیجه‌ی پیشنهادهای بازگشت.", "customers", COLOR_PINK, True, profiles=()),
    # ---- عملیات و زیرساخت ----
    TopicSpec("service", "🛠 سرویس", "رویدادهای عمومی سرویس‌ها و پاکسازی‌های خودکار.", "ops", COLOR_BLUE),
    TopicSpec("config_alert", "🗑 حذف/اتمام کانفیگ", "حذف، اتمام حجم یا انقضای کانفیگ کاربران.", "ops", COLOR_YELLOW, True),
    TopicSpec("panel", "🖥 پنل‌ها و سرورها", "سلامت پنل‌ها: قطعی، اتصال مجدد، کندی و خطای ارتباط با سرور.", "ops", COLOR_RED),
    TopicSpec("stock", "📦 موجودی و انبار", "کمبود و اتمام موجودی محصولات.", "ops", COLOR_YELLOW),
    TopicSpec("campaign", "📢 کمپین و کانال", "نتیجه‌ی ارسال همگانی زمان‌بندی‌شده و پست‌های زمان‌بندی‌شده‌ی کانال.", "ops", COLOR_BLUE, True, profiles=("standard",)),
    TopicSpec("ai", "🤖 هوش مصنوعی", "خطاهای غیرمنتظره‌ی دستیار هوشمند پشتیبانی (مثلاً قطعی سرویس AI).", "ops", COLOR_PURPLE, True, profiles=()),
    # ---- امنیت و کنترل ----
    TopicSpec("security", "🛡 امنیت", "بلاک/آنبلاک کاربران، رفرال مشکوک و هشدارهای امنیتی.", "security", COLOR_RED, locked=False),
    TopicSpec("leak", "🚨 نشت کانفیگ", "هشدار کانفیگ‌های نشت‌کرده در کانال‌ها و گروه‌های عمومی، با دکمه‌ی چرخاندن لینک، اخطار و نادیده‌گرفتن.", "security", COLOR_RED),
    TopicSpec("fraud", "🕵️ رسید مشکوک", "رسیدهای رد‌شده‌ی خودکار، فیش جعلی و تلاش‌های تقلب در پرداخت.", "security", COLOR_RED),
    TopicSpec("login", "🔐 ورود و نشست‌ها", "ورود موفق و تلاش‌های ناموفق ورود به پنل وب (با IP).", "security", COLOR_RED, profiles=("standard",)),
    TopicSpec("audit", "📝 لاگ مدیران", "ردپای کامل اقدامات مدیران (تغییر تنظیمات، قیمت‌ها، موجودی، کلیدها و...).", "security", COLOR_PURPLE, True, profiles=("standard",)),
    # ---- سیستم و گزارش‌ها ----
    TopicSpec("error", "🚨 خطا", "خطاهای پردازش‌نشده‌ی بات و مسیرهای پرداخت؛ همیشه فعال.", "system", COLOR_RED, locked=True),
    TopicSpec("system", "⚙️ سیستم", "راه‌اندازی مجدد بات و نسخه‌ی در حال اجرا.", "system", COLOR_BLUE, True),
    TopicSpec("backup", "🗄 بکاپ", "نتیجه‌ی بکاپ‌های دوره‌ای و ارسال به مقصدهای جانبی.", "system", COLOR_GREEN, True),
    TopicSpec("nightly", "📊 گزارش شبانه", "خلاصه‌ی فروش و آمار روزانه.", "system", COLOR_GREEN, True),
    TopicSpec("other", "📌 سایر", "هر گزارشی که تاپیک اختصاصی ندارد یا تاپیکش غیرفعال شده؛ همیشه فعال.", "system", COLOR_BLUE, locked=True),
)

SPECS = {s.key: s for s in TOPIC_SPECS}
# سازگاری با کدهای قدیمی که TOPICS[key] را به‌صورت «نام پیش‌فرض» می‌خوانند.
TOPICS = {s.key: s.name for s in TOPIC_SPECS}

PROFILES = {
    "minimal": "🔹 پایه — فقط تاپیک‌های ضروری روزمره",
    "standard": "🔸 استاندارد — پیشنهادی برای اغلب فروشگاه‌ها",
    "full": "💠 کامل — همه‌ی تاپیک‌ها",
}
MISSING_THREAD_MARKERS = ("thread not found", "topic_id_invalid", "topic_deleted")
GROUP_CALLBACK_PREFIXES = (
    "order_approve:", "order_reject:", "order_fake_receipt:",
    "topup_approve:", "topup_reject:",
    "qa_profile:", "qa_block:", "qa_block_go:", "qa_cancel", "qa_msg:",
    "lk:rot:", "lk:warn:", "lk:ign:",
    "adm_user_configs:",
)
RETRY_AFTER_CAP = 30

_topic_lock = asyncio.Lock()


class SetupError(Exception):
    """خطای راه‌اندازی گروه گزارش با پیام قابل نمایش به مدیر."""


async def _db(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def get_chat_id(db):
    raw = (db.get_setting(SETTING_CHAT_ID, "") or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def is_report_group_message(db, message) -> bool:
    """True اگر پیام داخل گروه گزارش باشد."""
    chat_id = get_chat_id(db)
    return chat_id is not None and message is not None and message.chat.id == chat_id


def topic_display_name(db, chat_id: int, topic_key: str) -> str:
    """نام قابل‌نمایش تاپیک: نام سفارشی که مدیر تنظیم کرده (فاز ۱)، وگرنه
    نام پیش‌فرض ثابت پروژه."""
    labels = db.get_report_topic_labels(chat_id)
    return labels.get(topic_key) or TOPICS.get(topic_key, topic_key)


# ---------------------------------------------------------------------------
# تنظیمات هر تاپیک (فعال/غیرفعال، بی‌صدا) و پروفایل‌های آماده
# ---------------------------------------------------------------------------

def _profile_keys(profile: str) -> set:
    if profile == "full":
        return {s.key for s in TOPIC_SPECS}
    return {s.key for s in TOPIC_SPECS if s.locked or profile in s.profiles}


def load_prefs(db) -> dict:
    """{topic_key: {"enabled": bool, "silent": bool}} - فقط کلیدهایی که مدیر دست زده."""
    raw = (db.get_setting(SETTING_TOPIC_PREFS, "") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return {k: v for k, v in data.items() if k in SPECS and isinstance(v, dict)} if isinstance(data, dict) else {}


def save_prefs(db, prefs: dict) -> None:
    db.set_setting(SETTING_TOPIC_PREFS, json.dumps(prefs, ensure_ascii=False, separators=(",", ":")))


def is_enabled(db, topic_key: str, prefs: dict = None) -> bool:
    spec = SPECS.get(topic_key)
    if spec is None:
        return False
    if spec.locked:
        return True
    prefs = load_prefs(db) if prefs is None else prefs
    if topic_key in prefs and "enabled" in prefs[topic_key]:
        return bool(prefs[topic_key]["enabled"])
    return "standard" in spec.profiles


def is_silent(db, topic_key: str, prefs: dict = None) -> bool:
    spec = SPECS.get(topic_key)
    if spec is None:
        return False
    prefs = load_prefs(db) if prefs is None else prefs
    if topic_key in prefs and "silent" in prefs[topic_key]:
        return bool(prefs[topic_key]["silent"])
    return spec.silent


def set_pref(db, topic_key: str, **changes) -> None:
    """enabled=/silent= را برای یک تاپیک ذخیره می‌کند؛ تاپیک قفل‌شده غیرفعال نمی‌شود."""
    spec = SPECS.get(topic_key)
    if spec is None:
        raise SetupError("تاپیک نامعتبر است.")
    if spec.locked and changes.get("enabled") is False:
        raise SetupError("این تاپیک ضروری است و قابل غیرفعال‌سازی نیست.")
    prefs = load_prefs(db)
    entry = dict(prefs.get(topic_key, {}))
    for k in ("enabled", "silent"):
        if k in changes:
            entry[k] = bool(changes[k])
    prefs[topic_key] = entry
    save_prefs(db, prefs)


def apply_profile(db, profile: str) -> int:
    """یکی از پروفایل‌های آماده را اعمال می‌کند (فقط وضعیت فعال/غیرفعال؛ حالت بی‌صدا دست‌نخورده)؛
    تعداد تاپیک‌های فعال را برمی‌گرداند."""
    if profile not in PROFILES:
        raise SetupError("پروفایل نامعتبر است.")
    keys = _profile_keys(profile)
    prefs = load_prefs(db)
    for spec in TOPIC_SPECS:
        entry = dict(prefs.get(spec.key, {}))
        entry["enabled"] = spec.key in keys
        prefs[spec.key] = entry
    save_prefs(db, prefs)
    return len(keys)


def resolve_topic(db, topic_key: str):
    """کلید نهایی مسیریابی و حالت بی‌صدا را برمی‌گرداند. کلید ناشناخته یا تاپیک
    غیرفعال به «سایر» می‌رود تا هیچ گزارشی گم نشود."""
    prefs = load_prefs(db)
    key = topic_key if topic_key in SPECS else DEFAULT_TOPIC
    if not is_enabled(db, key, prefs):
        key = DEFAULT_TOPIC
    return key, is_silent(db, key, prefs)


def intro_text(db, chat_id: int, topic_key: str) -> str:
    spec = SPECS[topic_key]
    name = topic_display_name(db, chat_id, topic_key)
    return (
        f"{name}\n\n"
        f"📖 کاربرد: {spec.description}\n"
        f"🗂 دسته: {TOPIC_GROUPS.get(spec.group, spec.group)}\n"
        f"🔔 نوتیفیکیشن: {'بی‌صدا' if is_silent(db, topic_key) else 'عادی'}\n\n"
        "ℹ️ این پیام را بات خودکار ساخته و پین کرده است. نام، بی‌صدا یا خاموش‌کردن این تاپیک از "
        "«پنل مدیریت ← گروه گزارش ← مدیریت تاپیک‌ها» انجام می‌شود."
    )


def _bump_stat(db, chat_id: int, topic_key: str) -> None:
    try:
        db.bump_report_topic_stat(chat_id, topic_key)
    except Exception:
        logger.debug("ثبت آمار تاپیک %s ناموفق بود.", topic_key, exc_info=True)


async def _post_intro(bot, db, chat_id: int, topic_key: str, thread_id: int) -> None:
    """پیام راهنمای تاپیک را می‌فرستد و (در صورت داشتن دسترسی) پین می‌کند؛ best-effort."""
    try:
        text = await _db(intro_text, db, chat_id, topic_key)
        msg = await bot.send_message(chat_id, text, message_thread_id=thread_id, disable_notification=True)
    except Exception:
        logger.debug("ارسال پیام راهنمای تاپیک %s ناموفق بود.", topic_key, exc_info=True)
        return
    try:
        await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
    except Exception:
        logger.debug("پین پیام راهنمای تاپیک %s ناموفق بود (دسترسی Pin لازم است).", topic_key, exc_info=True)


async def _create_topic(bot, db, chat_id: int, topic_key: str) -> int:
    name = await _db(topic_display_name, db, chat_id, topic_key)
    spec = SPECS.get(topic_key)
    kwargs = {"icon_color": spec.color} if spec else {}
    try:
        created = await bot.create_forum_topic(chat_id, name, **kwargs)
    except TelegramBadRequest as e:
        if kwargs and "icon" in str(e).lower():
            created = await bot.create_forum_topic(chat_id, name)
        else:
            raise
    await _db(db.set_report_topic, chat_id, topic_key, created.message_thread_id)
    await _post_intro(bot, db, chat_id, topic_key, created.message_thread_id)
    return created.message_thread_id
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


async def rename_topic(bot, db, chat_id: int, topic_key: str, new_name: str) -> None:
    """نام تاپیک را هم در دیتابیس (برای ساخت‌های بعدی) و هم - در صورت وجود -
    روی خودِ تاپیک واقعی تلگرام (با edit_forum_topic) تغییر می‌دهد."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise SetupError("نام تاپیک نمی‌تواند خالی باشد.")
    await _db(db.set_report_topic_label, chat_id, topic_key, new_name)
    thread_id = (await _db(db.get_report_topics, chat_id)).get(topic_key)
    if thread_id:
        try:
            await bot.edit_forum_topic(chat_id, thread_id, name=new_name)
        except TelegramAPIError as e:
            raise SetupError(f"نام در دیتابیس ذخیره شد ولی تغییر نام خودِ تاپیک در تلگرام ناموفق بود: {e}")


async def _thread_for(bot, db, chat_id: int, topic_key: str, refresh: bool = False) -> int:
    if not refresh:
        thread_id = (await _db(db.get_report_topics, chat_id)).get(topic_key)
        if thread_id:
            return thread_id
    async with _topic_lock:
        if not refresh:
            thread_id = (await _db(db.get_report_topics, chat_id)).get(topic_key)
            if thread_id:
                return thread_id
        return await _create_topic(bot, db, chat_id, topic_key)


async def _attempt(factory, chat_id: int, thread_id: int):
    try:
        return await factory(chat_id, thread_id)
    except TelegramRetryAfter as e:
        await asyncio.sleep(min(e.retry_after, RETRY_AFTER_CAP) + 1)
        return await factory(chat_id, thread_id)


async def send_to_group(bot, db, topic_key: str, factory):
    """factory(chat_id, thread_id) پیام را می‌فرستد؛ اگر گروه تنظیم نباشد یا خطا بدهد None برمی‌گرداند.
    تاپیک غیرفعال/ناشناخته به «سایر» هدایت می‌شود."""
    chat_id = get_chat_id(db)
    if chat_id is None:
        return None
    topic_key, _silent = resolve_topic(db, topic_key)
    try:
        thread_id = await _thread_for(bot, db, chat_id, topic_key)
        try:
            sent = await _attempt(factory, chat_id, thread_id)
        except TelegramBadRequest as e:
            if not any(marker in str(e).lower() for marker in MISSING_THREAD_MARKERS):
                raise
            thread_id = await _thread_for(bot, db, chat_id, topic_key, refresh=True)
            sent = await _attempt(factory, chat_id, thread_id)
        if sent is not None:
            await _db(_bump_stat, db, chat_id, topic_key)
        return sent
    except Exception as e:
        logger.warning("ارسال گزارش «%s» به گروه %s ناموفق بود (%s)؛ ارسال مستقیم به مدیران.", topic_key, chat_id, e)
        return None


async def send_text(bot, db, topic_key: str, text: str, reply_markup=None):
    _key, silent = resolve_topic(db, topic_key)
    return await send_to_group(
        bot, db, topic_key,
        lambda chat_id, thread_id: bot.send_message(
            chat_id, text, message_thread_id=thread_id, reply_markup=reply_markup,
            disable_notification=silent,
        ),
    )


async def send_media(bot, db, topic_key: str, file_id: str, media_type: str, caption: str, reply_markup=None):
    """ارسال رسید/مدیای گزارش با رعایت سقف ۱۰۲۴ کاراکتری کپشن تلگرام.

    در صورت طولانی بودن کپشن، رسید با کپشن کوتاه و دکمه‌های همان گزارش ارسال
    می‌شود و متن کامل بلافاصله در پیام بعدی همان تاپیک قرار می‌گیرد. این کار
    باعث نمی‌شود خطای ``message caption is too long`` کل مسیر گزارش را متوقف کند.
    """
    max_caption = 1000
    _key, silent = resolve_topic(db, topic_key)

    async def _send_full_text(chat_id: int, thread_id: int):
        text = caption or ""
        for start in range(0, len(text), 4000):
            await bot.send_message(
                chat_id, text[start:start + 4000], message_thread_id=thread_id,
                disable_notification=silent,
            )

    def _factory(chat_id, thread_id):
        if len(caption or "") <= max_caption:
            if media_type == "document":
                return bot.send_document(chat_id, file_id, caption=caption, message_thread_id=thread_id, reply_markup=reply_markup, disable_notification=silent)
            return bot.send_photo(chat_id, file_id, caption=caption, message_thread_id=thread_id, reply_markup=reply_markup, disable_notification=silent)

        short_caption = (caption[:max_caption - 30].rstrip() +
                         "\n\n⚠️ ادامه جزئیات در پیام بعدی…")
        if media_type == "document":
            return bot.send_document(chat_id, file_id, caption=short_caption, message_thread_id=thread_id, reply_markup=reply_markup, disable_notification=silent)
        return bot.send_photo(chat_id, file_id, caption=short_caption, message_thread_id=thread_id, reply_markup=reply_markup, disable_notification=silent)

    sent = await send_to_group(bot, db, topic_key, _factory)
    if sent and len(caption or "") > max_caption:
        try:
            await _send_full_text(sent.chat.id, sent.message_thread_id or 0)
        except Exception:
            logger.exception("ارسال جزئیات کامل گزارش مدیا ناموفق بود؛ خود رسید ارسال شده است.")
    return sent


async def report(bot, db, topic_key: str, text: str, reply_markup=None, senior_only: bool = False) -> bool:
    """ارسال به تاپیک گروه گزارش و در غیاب آن پیام خصوصی به مدیران."""
    if await send_text(bot, db, topic_key, text, reply_markup):
        return True
    try:
        admin_ids = await _db(db.list_admins)
    except Exception:
        logger.exception("خواندن لیست ادمین‌ها برای ارسال گزارش ناموفق بود.")
        return False
    delivered = False
    for admin_id in admin_ids:
        if senior_only and not db.is_senior_admin(admin_id):
            continue
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
            delivered = True
        except Exception:
            logger.warning("ارسال گزارش به ادمین %s ناموفق بود.", admin_id)
    return delivered


# ---------------------------------------------------------------------------
# قالب یکسان برای رویدادهای جدید + ارسال فقط-به-گروه (بدون فالبک پیام خصوصی)
# ---------------------------------------------------------------------------

class Html(str):
    """رشته‌ای که قبلاً امن/HTML شده و دوباره escape نمی‌شود."""


def code(value) -> Html:
    return Html(f"<code>{html.escape(str(value))}</code>")


def user_link(user_id: int, name: str = "", username: str = "") -> Html:
    label = html.escape(name or str(user_id))
    handle = f" (@{html.escape(username)})" if username else ""
    return Html(f'<a href="tg://user?id={user_id}">{label}</a>{handle}')


def format_event(title: str, rows=(), *, icon: str = "", note: str = "", with_time: bool = True) -> str:
    """کارت استاندارد گزارش (HTML): عنوان پررنگ، ردیف‌های «برچسب: مقدار»، یادداشت و زمان شمسی.
    مقدارهای عادی escape می‌شوند؛ برای مقدار آماده از Html/code()/user_link() استفاده کن."""
    head = f"{icon} " if icon else ""
    lines = [f"{head}<b>{html.escape(title)}</b>", ""]
    for label, value in rows:
        if value is None or value == "":
            continue
        shown = value if isinstance(value, Html) else html.escape(str(value))
        lines.append(f"▫️ {html.escape(label)}: {shown}")
    if note:
        lines += ["", html.escape(note)]
    if with_time:
        try:
            from jalali import to_jalali_str
            lines += ["", f"🕒 {to_jalali_str(datetime.now(), with_time=True)}"]
        except Exception:
            lines += ["", f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    return "\n".join(lines)


def build_refund_event(user_id: int, service_name: str, quote: dict, refunded: int, via: str) -> str:
    """کارت تاپیک «بازگشت وجه» برای حذف سرویس (بات، مینی‌اپ و پنل وب)."""
    unit = {"wallet": "تومان به کیف پول", "credit_unit": "عدد به موجودی محصول نماینده"}.get(
        quote.get("kind"), "گیگ به اعتبار نماینده")
    amount = f"{int(refunded):,} {unit}" if quote.get("kind") != "credit_unit" else f"{int(refunded)} {unit}"
    return format_event("بازگشت وجه بعد از حذف سرویس", [
        ("کاربر", user_link(user_id)),
        ("سرویس", service_name),
        ("مقدار برگشتی", amount),
        ("مسیر", via),
    ], icon="↩️")


def build_fraud_event(kind: str, ref_id, user_id: int, reason: str = "", amount=None, source: str = "") -> str:
    """کارت تاپیک «رسید مشکوک». kind: order_ai | topup_ai | order_manual"""
    titles = {
        "order_ai": "رسید سفارش — ردشده‌ی خودکار توسط هوش مصنوعی",
        "topup_ai": "رسید شارژ کیف پول — ردشده‌ی خودکار توسط هوش مصنوعی",
        "order_manual": "فیش فیک تایید‌شده توسط مدیر (کاربر بلاک شد)",
    }
    ref_label = "سفارش" if kind.startswith("order") else "شارژ"
    return format_event(titles.get(kind, "رسید مشکوک"), [
        (ref_label, code(f"#{ref_id}")),
        ("کاربر", user_link(user_id)),
        ("مبلغ", f"{int(amount):,} تومان" if amount else ""),
        ("دلیل", (reason or "")[:500]),
        ("مسیر", source),
    ], icon="🕵️")


_throttle_last: dict = {}


def fire_and_forget_raw(bot_token: str, db, topic_key: str, text: str, throttle_key: str = "", throttle_seconds: int = 60) -> None:
    """برای کدهای sync (مثل endpointهای def در FastAPI) که event loop در اختیار ندارند: ارسال را در یک
    thread جدا و بدون بلاک‌کردن کار اصلی انجام می‌دهد. throttle_key برای جلوگیری از سیل پیام
    (مثلاً brute-force ورود) است."""
    import threading
    import time as _time
    if throttle_key:
        now = _time.monotonic()
        if now - _throttle_last.get(throttle_key, 0) < throttle_seconds:
            return
        _throttle_last[throttle_key] = now
        if len(_throttle_last) > 2000:
            for k in [k for k, v in _throttle_last.items() if now - v > 3600]:
                _throttle_last.pop(k, None)

    def _run():
        try:
            asyncio.run(notify_raw(bot_token, db, topic_key, text))
        except Exception:
            logger.debug("fire_and_forget_raw(%s) ناموفق بود.", topic_key, exc_info=True)

    threading.Thread(target=_run, daemon=True).start()


async def notify(bot, db, topic_key: str, text: str, reply_markup=None) -> bool:
    """گزارش اطلاع‌رسانی فقط در تاپیک (بدون فالبک به پیام خصوصی)؛ هیچ‌وقت exception نمی‌دهد."""
    try:
        return (await send_text(bot, db, topic_key, text, reply_markup)) is not None
    except Exception:
        logger.warning("notify(%s) ناموفق بود.", topic_key, exc_info=True)
        return False


async def notify_raw(bot_token: str, db, topic_key: str, text: str, parse_mode: str = "HTML") -> bool:
    try:
        return bool(await send_raw_to_group(bot_token, db, topic_key, text, parse_mode))
    except Exception:
        logger.warning("notify_raw(%s) ناموفق بود.", topic_key, exc_info=True)
        return False


def build_block_toggle_text(user_row, tg_id: int, blocked: bool, actor_label: str) -> str:
    """متن کارت تاپیک «امنیت» برای بلاک/آنبلاک کاربر - مشترک بین بات، پنل وب
    و مینی‌اپ (سه مسیر مستقل تغییر وضعیت بلاک)."""
    name = html.escape((user_row["first_name"] if user_row else "") or "")
    username = (user_row["username"] if user_row else "") or ""
    handle = f" (@{html.escape(username)})" if username else ""
    icon = "🚫" if blocked else "✅"
    action = "بلاک شد" if blocked else "آنبلاک شد"
    return (
        f"{icon} کاربر {action}\n\n"
        f"👤 {name}{handle}\n"
        f"🆔 <code>{tg_id}</code>\n"
        f"👮 توسط: {html.escape(actor_label)}"
    )


async def notify_test_config(bot, db, user_id: int, name: str, username, detail_html: str) -> None:
    """اعلان تحویل کانفیگ تست به تاپیک «تست»؛ بدون گروه گزارش چیزی ارسال نمی‌شود."""
    handle = f" (@{html.escape(username)})" if username else ""
    from datetime import datetime
    from jalali import to_jalali_str
    text = (
        "🧪 کانفیگ تست تحویل داده شد\n\n"
        f"👤 <a href=\"tg://user?id={user_id}\">{html.escape(name or '')}</a>{handle}\n"
        f"🆔 <code>{user_id}</code>\n"
        f"{detail_html}\n\n"
        "💡 پیشنهاد: یه پیام براش بفرست و ترغیبش کن به خرید اکانت 😍\n"
        f"🕒 {to_jalali_str(datetime.now(), with_time=True)}"
    )
    try:
        import keyboards as _kb
        await send_text(bot, db, "test", text, _kb.user_quick_actions_kb(user_id))
    except Exception:
        logger.warning("ارسال اعلان کانفیگ تست ناموفق بود.", exc_info=True)


def build_prize_text(source_label: str, user_id: int, name: str, username, detail: str, html_mode: bool = True) -> str:
    """متن کارت تاپیک «جوایز» برای هر جایزه‌ای که به کاربر داده می‌شود (گردونه، قرعه‌کشی، هدیه‌ی عضویت)."""
    handle = f" (@{username})" if username else ""
    if html_mode:
        who = f'<a href="tg://user?id={user_id}">{html.escape(name or str(user_id))}</a>{html.escape(handle)}'
        return f"🎁 {html.escape(source_label)}\n\n👤 {who}\n🆔 <code>{user_id}</code>\n{detail}"
    return f"🎁 {source_label}\n\n👤 {name or user_id}{handle}\n🆔 {user_id}\n{detail}"


async def notify_prize(bot, db, source_label: str, user_id: int, name: str, username, detail_html: str) -> None:
    """گزارش جایزه به تاپیک «جوایز»؛ بدون گروه گزارش چیزی ارسال نمی‌شود."""
    try:
        await send_text(bot, db, "prize", build_prize_text(source_label, user_id, name, username, detail_html))
    except Exception:
        logger.warning("ارسال گزارش جایزه ناموفق بود.", exc_info=True)


async def notify_prize_raw(bot_token: str, db, source_label: str, user_id: int, name: str, username, detail_text: str) -> None:
    """مثل notify_prize برای مسیرهایی که فقط توکن بات دارند (متن ساده، بدون HTML)."""
    try:
        await send_raw_to_group(bot_token, db, "prize", build_prize_text(source_label, user_id, name, username, detail_text, html_mode=False))
    except Exception:
        logger.warning("ارسال گزارش جایزه (raw) ناموفق بود.", exc_info=True)


@dataclass
class SyncReport:
    created: int = 0
    reopened: int = 0
    recreated: int = 0
    skipped_disabled: int = 0
    failed: int = 0

    def summary(self) -> str:
        parts = [f"{self.created} تاپیک جدید", f"{self.recreated} بازسازی", f"{self.reopened} بازبازشده"]
        text = "، ".join(parts)
        if self.skipped_disabled:
            text += f" ({self.skipped_disabled} تاپیک غیرفعال ساخته نشد)"
        if self.failed:
            text += f" — ⚠️ {self.failed} مورد ناموفق"
        return text


async def _validate_group(bot, chat_id: int) -> None:
    try:
        chat = await bot.get_chat(chat_id)
    except TelegramAPIError:
        raise SetupError("چت پیدا نشد. بات را به گروه اضافه کن و آیدی عددی درست را بفرست.")
    if chat.type != "supergroup":
        raise SetupError("این چت سوپرگروه نیست. یک سوپرگروه بساز یا گروه را به سوپرگروه تبدیل کن.")
    if not getattr(chat, "is_forum", False):
        raise SetupError("حالت «تاپیک‌ها» (Topics) در تنظیمات گروه روشن نیست.")


async def sync_topics(bot, db, chat_id: int, rebuild_missing: bool = True) -> SyncReport:
    """تاپیک‌های فعال را کامل می‌کند: ناموجود می‌سازد، بسته‌شده را دوباره باز می‌کند و
    تاپیکی را که در تلگرام پاک شده از نو می‌سازد. تاپیک غیرفعال ساخته نمی‌شود."""
    report_ = SyncReport()
    existing = await _db(db.get_report_topics, chat_id)
    prefs = await _db(load_prefs, db)
    for spec in TOPIC_SPECS:
        key = spec.key
        if not is_enabled(db, key, prefs):
            report_.skipped_disabled += 1
            continue
        try:
            thread_id = existing.get(key)
            if not thread_id:
                async with _topic_lock:
                    await _create_topic(bot, db, chat_id, key)
                report_.created += 1
                await asyncio.sleep(0.4)
                continue
            try:
                await bot.reopen_forum_topic(chat_id, thread_id)
                report_.reopened += 1
            except TelegramBadRequest as e:
                msg = str(e).lower()
                if "not_modified" in msg or "not modified" in msg:
                    pass
                elif rebuild_missing and any(m in msg for m in MISSING_THREAD_MARKERS):
                    async with _topic_lock:
                        await _create_topic(bot, db, chat_id, key)
                    report_.recreated += 1
                else:
                    raise
            await asyncio.sleep(0.2)
        except TelegramBadRequest as e:
            if "rights" in str(e).lower():
                raise SetupError("بات دسترسی ساخت تاپیک ندارد. بات را ادمین گروه کن و گزینه‌ی Manage Topics را روشن کن.")
            logger.warning("همگام‌سازی تاپیک %s ناموفق بود: %s", key, e)
            report_.failed += 1
        except TelegramAPIError as e:
            logger.warning("همگام‌سازی تاپیک %s ناموفق بود: %s", key, e)
            report_.failed += 1
    return report_


async def setup_group(bot, db, chat_id: int) -> int:
    """گروه را اعتبارسنجی می‌کند، تاپیک‌های فعال ناموجود را می‌سازد و ذخیره می‌کند؛ تعداد تاپیک‌های جدید را برمی‌گرداند."""
    await _validate_group(bot, chat_id)
    try:
        rep = await sync_topics(bot, db, chat_id)
    except TelegramBadRequest as e:
        raise SetupError(f"ساخت تاپیک ناموفق بود: {e}")
    created = rep.created + rep.recreated

    previous = db.get_setting(SETTING_CHAT_ID, "") or ""
    await _db(db.set_setting, SETTING_CHAT_ID, str(chat_id))
    sent = await send_text(bot, db, DEFAULT_TOPIC, "✅ گروه گزارش فعال شد. از این پس گزارش‌ها در تاپیک مربوط به خودشان ارسال می‌شوند.")
    if sent is None:
        await _db(db.set_setting, SETTING_CHAT_ID, previous)
        raise SetupError("ارسال پیام در گروه ناموفق بود. مطمئن شو بات ادمین گروه است و اجازه‌ی ارسال پیام دارد.")
    return created


async def full_sync(bot, db, chat_id: int) -> SyncReport:
    """همگام‌سازی کامل برای دکمه‌ی «بررسی و ترمیم»؛ گزارش تفصیلی برمی‌گرداند."""
    await _validate_group(bot, chat_id)
    try:
        return await sync_topics(bot, db, chat_id)
    except TelegramBadRequest as e:
        raise SetupError(f"همگام‌سازی ناموفق بود: {e}")


async def send_test(bot, db, topic_key: str) -> bool:
    """پیام تست به همان تاپیک (بدون فالبک به پیام خصوصی)؛ برای دکمه‌ی «تست» مدیریت تاپیک."""
    if topic_key not in SPECS or get_chat_id(db) is None:
        return False
    name = await _db(topic_display_name, db, get_chat_id(db), topic_key)
    text = f"🧪 پیام تست — {name}\n🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    _key, silent = resolve_topic(db, topic_key)
    sent = await send_to_group(
        bot, db, topic_key,
        lambda chat_id, thread_id: bot.send_message(chat_id, text, message_thread_id=thread_id, disable_notification=True),
    )
    return sent is not None


async def rebuild_topic(bot, db, chat_id: int, topic_key: str) -> int:
    """تاپیک را از نو می‌سازد (مثلاً وقتی تاپیک قدیمی خراب/شلوغ شده). تاپیک قدیمی فقط بسته می‌شود
    تا تاریخچه‌اش پاک نشود."""
    if topic_key not in SPECS:
        raise SetupError("تاپیک نامعتبر است.")
    old = (await _db(db.get_report_topics, chat_id)).get(topic_key)
    if old:
        try:
            await bot.close_forum_topic(chat_id, old)
        except TelegramAPIError:
            pass
    try:
        async with _topic_lock:
            return await _create_topic(bot, db, chat_id, topic_key)
    except TelegramAPIError as e:
        raise SetupError(f"ساخت دوباره‌ی تاپیک ناموفق بود: {e}")


async def clear_group(db) -> None:
    await _db(db.set_setting, SETTING_CHAT_ID, "")
    await _db(db.clear_report_topics)


async def _raw_api(bot_token: str, method: str, payload: dict, timeout: int = 10):
    """فراخوانی خام Bot API؛ dict پاسخ تلگرام (یا None در خطای شبکه) را برمی‌گرداند."""
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                return await resp.json()
    except Exception:
        logger.warning("فراخوانی خام %s ناموفق بود.", method, exc_info=True)
        return None


async def _raw_create_topic(bot_token: str, chat_id: int, topic_key: str, name: str = None, db=None):
    spec = SPECS.get(topic_key)
    payload = {"chat_id": chat_id, "name": name or (spec.name if spec else TOPICS[topic_key])}
    if spec:
        payload["icon_color"] = spec.color
    data = await _raw_api(bot_token, "createForumTopic", payload)
    if data and not data.get("ok") and "icon" in str(data.get("description", "")).lower():
        payload.pop("icon_color", None)
        data = await _raw_api(bot_token, "createForumTopic", payload)
    if not data or not data.get("ok"):
        return None
    thread_id = data["result"]["message_thread_id"]
    if db is not None and spec:
        try:
            text = await _db(intro_text, db, chat_id, topic_key)
            sent = await _raw_api(bot_token, "sendMessage", {
                "chat_id": chat_id, "text": text, "message_thread_id": thread_id, "disable_notification": True,
            })
            if sent and sent.get("ok"):
                await _raw_api(bot_token, "pinChatMessage", {
                    "chat_id": chat_id, "message_id": sent["result"]["message_id"], "disable_notification": True,
                })
        except Exception:
            logger.debug("ارسال/پین پیام راهنمای تاپیک %s (raw) ناموفق بود.", topic_key, exc_info=True)
    return thread_id


async def _raw_send_message(bot_token: str, chat_id: int, text: str, thread_id: int = None, parse_mode: str = None, silent: bool = False) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if thread_id:
        payload["message_thread_id"] = thread_id
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if silent:
        payload["disable_notification"] = True
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return True
                body = (await resp.text()).lower()
                if thread_id and any(marker in body for marker in MISSING_THREAD_MARKERS):
                    return None
                return False
    except Exception:
        logger.warning("ارسال پیام HTTP خام به تلگرام ناموفق بود.", exc_info=True)
        return False


async def send_raw_to_group(bot_token: str, db, topic_key: str, text: str, parse_mode: str = None) -> bool:
    """فقط تلاش برای ارسال به تاپیک گروه گزارش با HTTP خام؛ اگر گروه تنظیم
    نباشد یا ارسال ناموفق باشد False برمی‌گرداند (بدون فالبک به پیام خصوصی -
    آن تصمیم به عهده‌ی فراخوان است). تاپیک غیرفعال به «سایر» می‌رود."""
    if not bot_token:
        return False
    chat_id = get_chat_id(db)
    if chat_id is None:
        return False
    key, silent = resolve_topic(db, topic_key)
    topics = await _db(db.get_report_topics, chat_id)
    thread_id = topics.get(key)

    async def _create():
        name = await _db(topic_display_name, db, chat_id, key)
        new_id = await _raw_create_topic(bot_token, chat_id, key, name, db)
        if new_id:
            await _db(db.set_report_topic, chat_id, key, new_id)
        return new_id

    if not thread_id:
        thread_id = await _create()
    if not thread_id:
        return False
    result = await _raw_send_message(bot_token, chat_id, text, thread_id, parse_mode, silent)
    if result is None:
        thread_id = await _create()
        if not thread_id:
            return False
        result = await _raw_send_message(bot_token, chat_id, text, thread_id, parse_mode, silent)
    if result:
        await _db(_bump_stat, db, chat_id, key)
    return bool(result)


async def _raw_send_photo(bot_token: str, chat_id: int, thread_id: int, caption: str, reply_markup: str,
                         photo_bytes: bytes, filename: str, content_type: str):
    form = aiohttp.FormData()
    form.add_field("chat_id", str(chat_id))
    form.add_field("message_thread_id", str(thread_id))
    form.add_field("caption", caption[:1000])
    if reply_markup:
        form.add_field("reply_markup", reply_markup)
    form.add_field("photo", photo_bytes, filename=filename, content_type=content_type)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://api.telegram.org/bot{bot_token}/sendPhoto", data=form,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    return data["result"], True
                desc = str(data.get("description", "")).lower()
                return None, any(marker in desc for marker in MISSING_THREAD_MARKERS)
    except Exception:
        logger.warning("ارسال عکس رسید به گروه گزارش با HTTP خام ناموفق بود.", exc_info=True)
        return None, False


async def send_raw_photo_to_group(bot_token: str, db, topic_key: str, caption: str, reply_markup: str,
                                  photo_bytes: bytes, filename: str, content_type: str):
    """ارسال عکس رسید با HTTP خام به تاپیک گروه گزارش؛ پیام تلگرام یا None برمی‌گرداند."""
    if not bot_token:
        return None
    chat_id = get_chat_id(db)
    if chat_id is None:
        return None
    key, _silent = resolve_topic(db, topic_key)
    thread_id = (await _db(db.get_report_topics, chat_id)).get(key)

    async def _create():
        name = await _db(topic_display_name, db, chat_id, key)
        new_id = await _raw_create_topic(bot_token, chat_id, key, name, db)
        if new_id:
            await _db(db.set_report_topic, chat_id, key, new_id)
        return new_id

    if not thread_id:
        thread_id = await _create()
    if not thread_id:
        return None
    msg, thread_missing = await _raw_send_photo(bot_token, chat_id, thread_id, caption, reply_markup, photo_bytes, filename, content_type)
    if msg is None and thread_missing:
        thread_id = await _create()
        if not thread_id:
            return None
        msg, _ = await _raw_send_photo(bot_token, chat_id, thread_id, caption, reply_markup, photo_bytes, filename, content_type)
    if msg is not None:
        await _db(_bump_stat, db, chat_id, key)
    return msg


async def report_raw(bot_token: str, db, topic_key: str, text: str, senior_only: bool = True, parse_mode: str = None) -> bool:
    """مثل ``report()`` بالا، ولی برای پردازه‌هایی (مثل پنل وب مستقل) که شیء
    Bot از aiogram در اختیار ندارند و فقط توکن بات را دارند - با HTTP خام به
    Telegram Bot API وصل می‌شود. اول تلاش می‌کند در تاپیک مربوطه‌ی گروه گزارش
    پست کند؛ اگر گروه تنظیم نبود یا ارسال ناموفق بود، به پیام خصوصی ادمین‌های
    ارشد برمی‌گردد."""
    if await send_raw_to_group(bot_token, db, topic_key, text, parse_mode):
        return True
    try:
        admin_ids = await _db(db.list_admins)
    except Exception:
        logger.exception("خواندن لیست ادمین‌ها برای ارسال گزارش خام ناموفق بود.")
        return False
    delivered = False
    for admin_id in admin_ids:
        if senior_only and not db.is_senior_admin(admin_id):
            continue
        if await _raw_send_message(bot_token, admin_id, text, None, parse_mode):
            delivered = True
    return delivered


class ReportGroupGuardMiddleware(BaseMiddleware):
    """پیام‌های داخل گروه گزارش را نادیده می‌گیرد و دکمه‌های آن را فقط برای مدیران فعال نگه می‌دارد."""

    def __init__(self, db):
        super().__init__()
        self.db = db

    async def __call__(self, handler, event, data: dict):
        chat_id = get_chat_id(self.db)
        if chat_id is None:
            return await handler(event, data)
        if isinstance(event, Message):
            if event.chat.id == chat_id:
                return None
        elif isinstance(event, CallbackQuery):
            message = event.message
            user = event.from_user
            if message is not None and message.chat.id == chat_id:
                if user is None or not self.db.is_admin(user.id):
                    try:
                        await event.answer(tr("⛔️ این دکمه‌ها فقط برای مدیران فعال است."), show_alert=True)
                    except Exception:
                        logger.warning("پاسخ به دکمه‌ی غیرمجاز در گروه گزارش ناموفق بود.", exc_info=True)
                    return None
                if not (event.data or "").startswith(GROUP_CALLBACK_PREFIXES):
                    try:
                        await event.answer(tr("⛔️ منوی مدیریت در گروه گزارش در دسترس نیست؛ از پی‌وی بات استفاده کن."), show_alert=True)
                    except Exception:
                        logger.warning("پاسخ به دکمه‌ی خارج از لیست مجاز در گروه گزارش ناموفق بود.", exc_info=True)
                    return None
        return await handler(event, data)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
