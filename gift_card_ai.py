# -*- coding: utf-8 -*-
"""نوشتن عنوان و متن تبریک کارت هدیه: اول مدل قوی Jarvis، بعد Providerهای دستیار، در آخر قالب‌های آماده."""

import asyncio
import json
import logging
import re
import zlib

_log = logging.getLogger("gift_card_ai")

AI_TIMEOUT = 30
MAX_TITLE = 28
MAX_MESSAGE = 220
DEFAULT_SIGNATURE = "با بهترین آرزوها"

_ALLOWED = re.compile(r"[^\u0600-\u06FF\u200c\s0-9۰-۹.,:;!?؟،؛\-—()«»]")
_LATIN = re.compile(r"[A-Za-z]")

_PROMPT = (
    "تو یک کپی‌رایتر حرفه‌ای فارسی هستی و متن کارت هدیه‌ی دیجیتال می‌نویسی. "
    "ورودی یک JSON است با کلیدهای recipient (نام گیرنده)، occasion (مناسبت یا موضوع کارت)، "
    "sender_message (متن فرستنده؛ ممکن است خالی باشد) و mode. "
    "خروجی فقط یک JSON با کلیدهای title و message است.\n"
    "قواعد:\n"
    "- title: تیتر کوتاه و شاد مناسب مناسبت (حداکثر ۴ کلمه)، مثل «تولدت مبارک» یا «سال نو مبارک».\n"
    "- message: ۲ تا ۳ جمله‌ی کوتاه، گرم و صمیمی با لحن محاوره‌ای مودبانه و خطاب مفرد؛ "
    "حداکثر ۱۸۰ حرف؛ مناسب مناسبت؛ اگر مناسبت عمومی بود یک پیام محبت‌آمیز بنویس.\n"
    "- اگر mode برابر title_only بود فقط title را بساز و message را خالی بگذار؛ "
    "متن فرستنده را تغییر نده.\n"
    "- نام گیرنده را حداکثر یک‌بار و فقط در صورت طبیعی بودن در message بیاور؛ نام را در title نیاور.\n"
    "- هیچ ایموجی، هشتگ، مارک‌داون، حروف لاتین، قیمت، مبلغ، حجم، کانفیگ، VPN یا اسم ربات ننویس.\n"
    "- از ادعاهای مذهبی یا سیاسی و شوخی حساس پرهیز کن.\n"
    "- فقط فارسی روان و استاندارد. هیچ توضیح اضافه‌ای نده."
)

_TEMPLATES = (
    (("تولد",), "تولدت مبارک", (
        "امیدوارم این سال پر از شادی، سلامتی، موفقیت و اتفاقات قشنگ باشه. همیشه بدرخشی و لبخند هیچ‌وقت از لبات نره!",
        "تولدت رو از ته دل تبریک می‌گم. آرزو می‌کنم سالی پر از آرامش و اتفاق‌های خوب پیش رو داشته باشی.",
        "یه سال دیگه به قصه‌ی قشنگ زندگیت اضافه شد. امیدوارم هر روزش پر از خوشی و خبرهای خوب باشه.",
    )),
    (("ولنتاین", "عشق", "دوست‌داشتن"), "با عشق", (
        "تو یکی از قشنگ‌ترین اتفاق‌های زندگی منی. این هدیه‌ی کوچیک رو به یادگار از دوست‌داشتنم قبول کن.",
        "بودنت دلیل خیلی از لبخندهامه. امیدوارم این هدیه کمی از محبتم رو بهت نشون بده.",
        "دوست دارم همیشه کنارم باشی. این هدیه‌ی کوچیک نشونه‌ای از احساس بزرگمه.",
    )),
    (("عید", "نوروز", "فطر", "غدیر", "سال نو", "سال‌نو"), "عیدت مبارک", (
        "امیدوارم این عید برات پر از شادی، آرامش و لحظه‌های خوب باشه. سالی پر از سلامتی و موفقیت داشته باشی.",
        "عیدت رو از صمیم قلب تبریک می‌گم. آرزو دارم روزهات پر از نور و خبرهای خوش باشه.",
        "به بهانه‌ی این روز قشنگ، براتون سلامتی، خوشبختی و دلی آروم آرزو می‌کنم.",
    )),
    (("عروسی", "ازدواج", "نامزدی", "همسر"), "تبریک! شاد و خوشبخت باشید", (
        "امیدوارم زندگی مشترکتون پر از عشق، آرامش و خاطره‌های خوب باشه. همیشه کنار هم شاد و سلامت بمونید.",
        "از ته دل براتون خوشبختی و دل‌گرمی آرزو می‌کنم. این هدیه‌ی کوچیک رو از من بپذیرید.",
    )),
    (("سالگرد",), "سالگردتون مبارک", (
        "امیدوارم سال‌های زیادی کنار هم شاد، سلامت و عاشق بمونید. این روز قشنگ مبارک!",
        "هر سال که می‌گذره، قصه‌ی شما قشنگ‌تر می‌شه. سالگردتون مبارک و پر از خاطره‌های خوب.",
    )),
    (("قبولی", "موفقیت", "فارغ", "پیروزی", "تبریک"), "تبریک! افتخار می‌کنم", (
        "این موفقیت حاصل تلاش خودته و حسابی لایقش بودی. برای قدم‌های بعدی هم برات پیروزی آرزو می‌کنم.",
        "خوشحالم که این روز قشنگ رو کنارت جشن می‌گیرم. امیدوارم همیشه موفق و پیروز باشی.",
    )),
)
_DEFAULT_TEMPLATE = ("یک هدیه برای تو", (
    "این هدیه‌ی کوچیک رو با آرزوی روزهای خوب برات فرستادم. امیدوارم لبخندت همیشه پررنگ باشه.",
    "به یاد تو و به بهانه‌ی یه دل‌خوشی کوچیک. امیدوارم این هدیه کمی از شادی رو به روزت اضافه کنه.",
    "بودنت همیشه برام ارزشمنده. امیدوارم این هدیه لحظه‌های قشنگی برات بسازه.",
))


def strip_emoji(text: str) -> str:
    return re.sub(r"\s+", " ", _ALLOWED.sub(" ", text or "")).strip()


def clean_text(text, limit: int) -> str:
    """متن مدل را تمیز می‌کند؛ اگر حروف لاتین یا ساختار نامعتبر داشت رشته‌ی خالی برمی‌گرداند."""
    text = str(text or "").replace("\n", " ").strip()
    if _LATIN.search(text):
        return ""
    text = strip_emoji(text)
    return text if 0 < len(text) <= limit else ""


def fallback(recipient: str, occasion: str) -> dict:
    """عنوان و متن آماده بر اساس مناسبت؛ انتخاب بین قالب‌ها با نام گیرنده پایدار است."""
    topic = strip_emoji(occasion)
    title, messages = next(((t, m) for keys, t, m in _TEMPLATES if any(k in topic for k in keys)), _DEFAULT_TEMPLATE)
    index = zlib.crc32((recipient or topic).encode("utf-8")) % len(messages)
    return {"title": title, "message": messages[index]}


async def _ask(db, payload: str) -> dict:
    import ai_support
    import channel_ai

    if channel_ai.text_configured(db):
        try:
            data, _source = await asyncio.wait_for(channel_ai.ask_json(db, _PROMPT, payload), AI_TIMEOUT)
            return data
        except Exception as exc:
            _log.warning("gift greeting via jarvis failed: %s", exc)
    if ai_support.is_configured(db):
        import ai_text

        return await asyncio.wait_for(ai_text.generate_json(db, _PROMPT, payload), AI_TIMEOUT)
    raise RuntimeError("no AI agent configured")


async def write_greeting(db, recipient: str, occasion: str, sender_message: str = "") -> dict:
    """{title, message} را برمی‌گرداند. اگر پیام فرستنده هست همان حفظ می‌شود و فقط عنوان ساخته می‌شود."""
    base = fallback(recipient, occasion)
    sender_message = (sender_message or "").strip()
    payload = json.dumps({
        "recipient": recipient or "", "occasion": strip_emoji(occasion) or "عمومی",
        "sender_message": sender_message, "mode": "title_only" if sender_message else "full",
    }, ensure_ascii=False)
    try:
        data = await _ask(db, payload)
    except Exception as exc:
        _log.info("gift greeting fallback used: %s", exc)
        data = {}
    title = clean_text(data.get("title"), MAX_TITLE) or base["title"]
    if sender_message:
        message = sender_message
    else:
        message = clean_text(data.get("message"), MAX_MESSAGE)
        if len(message) < 20:
            message = base["message"]
    return {"title": title, "message": message}
