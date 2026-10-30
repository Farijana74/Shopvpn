# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""ایجنت‌های متن و تصویر پست کانال. فقط از Jarvis استفاده می‌کند (دو مدل: یکی متن، یکی تصویر).

ایجنت‌های پشتیبانی، دستیار ادمین و بقیه‌ی بخش‌ها هیچ تأثیری روی این فایل ندارند."""

import asyncio
import base64
import io
import json
import logging
import os
import re
from datetime import datetime

import aiohttp

import ai_support
import config
import post_image

_log = logging.getLogger("channel_ai")

JARVIS_BASE_URL = "https://api.jarvis.you/v1"
JARVIS_TEXT_MODEL = "CLAUDE_6_5_HAIKU"
JARVIS_IMAGE_MODEL = "gemini-3-1-flash-image"
CHANNEL_LOGO_PATH = os.path.join(config.BASE_DIR, "channel_banner_logo.png")
LEARNING_KEY = "channel_ai_learning"
LEARNING_LIMIT = 40
LEARNING_IN_PROMPT = 8
COLORS_KEY = "channel_banner_colors"
BANNER_BASE_PROMPT_KEY = "channel_banner_base_prompt"
STYLE_REF_PATH = os.path.join(config.BASE_DIR, "channel_banner_style_ref.jpg")
LOGO_MODE_KEY = "channel_banner_logo_mode"
LOGO_MODES = ("exact", "ai")
DEFAULT_BANNER_BASE_PROMPT = (
    "Premium cinematic advertising banner for a VPN brand, finished like a top ad-agency poster. "
    "Deep dark-navy base with vivid electric-blue and violet neon lighting, strong rim light, volumetric glow and glossy 3D depth. "
    "One clear focal subject (shield, smartphone showing a connected VPN screen, or a glowing globe network) on one side, "
    "dynamic diagonal composition, rich layered background with light trails and soft particles. "
    "Strict visual hierarchy: huge headline first, then product and price, then discount badge, then call to action. "
    "Typography: bold modern Persian font, very large, high contrast, subtle neon gradient on the key word, never tiny. "
    "Light, color and effects must feel like one coherent scene, with nothing flat or pasted-looking. "
    "Avoid generic stock-art look, clutter and random decoration. Vary composition, background and lighting between campaigns "
    "while keeping the blue and violet brand feel."
)
DEFAULT_IMAGE_PROMPT = "A glowing shield and a fast rocket over a futuristic network of light, symbolizing private fast internet"
BANNER_RULES = (
    "Style: premium modern advertising banner, strong contrast so every text is easy to read on a phone screen. "
    "All text must be in Persian (Farsi), right-to-left, spelled exactly as written in quotes, large bold readable font, "
    "no extra letters and no garbled characters. "
    "Keep every text element inside the central safe area with at least 10 percent margin from all edges. "
    "Do not add any other text: no English words, no watermark, no QR code, no real brand names, no human faces."
)
BANNER_RULES_TEMPLATE = (
    "Style: premium modern advertising banner, strong contrast so every text is easy to read on a phone screen. "
    "Primary text must be Persian (Farsi), right-to-left, spelled exactly as written in quotes, large bold readable font, "
    "perfectly connected letters, no extra letters and no garbled characters. "
    "Secondary text exactly as in the style template (such as short English UI labels or platform names) is allowed, but never add any new text. "
    "No watermark, no QR code, no real brand names other than those in the template."
)
STYLE_RULES = (
    "STYLE TEMPLATE: the FIRST attached image is a finished banner that defines the exact target look. Recreate the same design system: "
    "identical layout structure and element positions, same dark-navy cinematic background with glowing world-map network lines, "
    "same neon blue-violet-cyan lighting and glow intensity, same focal subject style (tilted smartphone with glowing connected power button on dark rocks, "
    "hooded faceless silhouette with glowing blue eyes behind it), same typography weight and gradient treatment of the key headline word, "
    "same angled glowing discount ribbon at the lower left, same glass-style info chips on the right edge, same platform strip at the lower left, "
    "same rounded glowing call-to-action button at the lower right, same small tagline row at the bottom. "
    "Replace only the texts, price and offer with the ones given in this request; keep every other design decision from the template. "
    "Do not copy the template's logo or brand name."
)
LOGO_RULES = {
    "exact": (
        "MANDATORY LOGO AREA: do NOT draw, invent or imitate any logo, emblem, brand mark or app icon anywhere in the image. "
        "Keep the top-left corner (a square of about 28 percent of the image width) as a calm, dark, uncluttered area with no text "
        "and no important elements; the official logo is placed there afterwards. Keep the headline and key subjects out of that corner."
    ),
    "ai": (
        "MANDATORY LOGO: the LAST attached image is the official brand logo. Reproduce it EXACTLY: same icon, shapes, colors and lettering. "
        "Never redraw, restyle, simplify, translate or crop it. Place it top-left inside a circular glowing badge (about 22 percent of "
        "the image width) with a thin neon rim light matching the scene palette, integrated with the scene lighting but sharp and unchanged. "
        "Keep the headline and key subjects away from the badge."
    ),
    "none": "Do not draw any logo, emblem, brand mark or app icon anywhere in the image.",
}

_NON_TEXT = ("embed", "image", "tts", "whisper", "audio", "moderation", "rerank", "video")
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_TEXT_TIMEOUT = 300
_RETRY_SUFFIX = "\n\nپاسخ قبلی JSON معتبر نبود. فقط و فقط یک آبجکت JSON معتبر بده؛ بدون توضیح و بدون ```."
_semaphore_obj = None


class ChannelAIError(Exception):
    """خطای ایجنت‌های متن/تصویر کانال با پیام قابل نمایش به ادمین."""


def _semaphore():
    global _semaphore_obj
    if _semaphore_obj is None:
        _semaphore_obj = asyncio.Semaphore(2)
    return _semaphore_obj


def _err(exc) -> str:
    return str(exc).strip() or type(exc).__name__


def is_text_model(model: str) -> bool:
    return bool(model) and not any(b in model.lower() for b in _NON_TEXT)


def jarvis_keys(db) -> list:
    keys = ai_support._split_keys(ai_support._setting(db, "jarvis_api_key"))
    return keys or ai_support._split_keys(getattr(config, "JARVIS_API_KEY", ""))


def jarvis_base(db) -> str:
    return ai_support.normalize_base_url(ai_support._setting(db, "jarvis_base_url")) or JARVIS_BASE_URL


def jarvis_text_model(db) -> str:
    model = ai_support._setting(db, "jarvis_text_model")
    return model if is_text_model(model) else JARVIS_TEXT_MODEL


def jarvis_image_model(db) -> str:
    return ai_support._setting(db, "jarvis_image_model") or JARVIS_IMAGE_MODEL


def jarvis_image_size(db) -> str:
    return ai_support._setting(db, "jarvis_image_size")


def text_configured(db) -> bool:
    return bool(jarvis_keys(db))


def image_configured(db) -> bool:
    return bool(jarvis_keys(db))


async def _request(method: str, url: str, headers: dict = None, payload: dict = None, timeout: int = 90, raw: bool = False, form=None):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout, connect=10)) as session:
        async with session.request(method, url, headers=headers or {}, json=payload, data=form) as resp:
            body = await resp.read()
            if resp.status >= 400:
                text = body.decode("utf-8", "ignore")
                if text.lstrip().lower().startswith(("<!doctype", "<html")):
                    raise ChannelAIError(f"HTTP {resp.status} (سرویس IP یا منطقه‌ی این سرور را بلاک کرده است)")
                raise ChannelAIError(f"HTTP {resp.status}: {text[:200]}")
            if raw:
                return body
            try:
                return json.loads(body)
            except ValueError as exc:
                raise ChannelAIError("پاسخ JSON نامعتبر") from exc


def _parse_json(text: str) -> dict:
    match = _JSON_RE.search(re.sub(r"```(?:json)?", "", text or ""))
    if not match:
        raise ChannelAIError("JSON در پاسخ مدل پیدا نشد")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ChannelAIError("ریشه‌ی JSON آبجکت نیست")
    return data


async def _jarvis_chat(db, system_prompt: str, user_text: str) -> str:
    keys = jarvis_keys(db)
    if not keys:
        raise ChannelAIError("کلید Jarvis تنظیم نشده")
    model = jarvis_text_model(db)
    payload = {"model": model, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}]}
    last = None
    for key in keys:
        try:
            async with _semaphore():
                data = await _request(
                    "POST", f"{jarvis_base(db)}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"}, payload=payload, timeout=_TEXT_TIMEOUT,
                )
            return (data["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:
            last = exc
    raise ChannelAIError(f"jarvis/{model}: {_err(last)}")


async def _jarvis_json(db, system_prompt: str, user_text: str) -> dict:
    try:
        return _parse_json(await _jarvis_chat(db, system_prompt, user_text))
    except ChannelAIError as exc:
        if "JSON" not in str(exc):
            raise
    except ValueError:
        pass
    return _parse_json(await _jarvis_chat(db, system_prompt, user_text + _RETRY_SUFFIX))


async def ask_json(db, system_prompt: str, user_text: str) -> tuple:
    """(داده، منبع) از مدل متن Jarvis. فقط Jarvis؛ اگر کار نکرد خطای قابل نمایش می‌دهد."""
    if not jarvis_keys(db):
        raise ChannelAIError("کلید Jarvis تنظیم نشده")
    try:
        return await _jarvis_json(db, system_prompt, user_text), f"jarvis/{jarvis_text_model(db)}"
    except Exception as exc:
        _log.warning("channel text via jarvis failed: %s", _err(exc)[:200])
        raise ChannelAIError(_err(exc)) from exc


async def generate_json(db, system_prompt: str, user_text: str) -> dict:
    return (await ask_json(db, system_prompt, user_text))[0]


class _RefUnsupported(ChannelAIError):
    """درگاه تصویر مرجع (لوگو) را نپذیرفت؛ باید بدون مرجع و با روش «لوگوی دقیق» ساخته شود."""


_AUTH_CODES = ("HTTP 401", "HTTP 402", "HTTP 403")


async def _extract_image(data: dict):
    """بایت تصویر را از پاسخ images/generations یا chat/completions (قالب Gemini) بیرون می‌کشد؛ نبود تصویر None."""
    item = (data.get("data") or [{}])[0] if isinstance(data.get("data"), list) else {}
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        return await _request("GET", item["url"], timeout=60, raw=True)
    message = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
    candidates = [(i.get("image_url") or {}).get("url") for i in (message.get("images") or []) if isinstance(i, dict)]
    content = message.get("content")
    if isinstance(content, list):
        candidates += [(p.get("image_url") or {}).get("url") for p in content if isinstance(p, dict) and p.get("type") == "image_url"]
    for url in candidates:
        if not url:
            continue
        if url.startswith("data:") and "," in url:
            return base64.b64decode(url.split(",", 1)[1])
        if url.startswith("http"):
            return await _request("GET", url, timeout=60, raw=True)
    return None


def _logo_reference_png() -> bytes:
    """لوگوی ثبت‌شده را روی پس‌زمینه‌ی تیره می‌گذارد (شفافیت برای مدل سیاه/سفید خوانده می‌شود) و به‌صورت PNG مربعی برمی‌گرداند."""
    from PIL import Image
    with Image.open(CHANNEL_LOGO_PATH) as logo_file:
        mark = logo_file.convert("RGBA")
    mark.thumbnail((900, 900), Image.LANCZOS)
    side = max(mark.size) + 120
    canvas = Image.new("RGBA", (side, side), (8, 10, 30, 255))
    canvas.alpha_composite(mark, ((side - mark.width) // 2, (side - mark.height) // 2))
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "PNG")
    return out.getvalue()


async def _image_with_reference(db, key: str, model: str, prompt: str, references: list) -> bytes:
    """دو روش را امتحان می‌کند: images/edits (استاندارد OpenAI) و chat/completions تصویری (قالب Gemini)."""
    import aiohttp as _aiohttp
    headers = {"Authorization": f"Bearer {key}"}
    last = None
    try:
        form = _aiohttp.FormData()
        form.add_field("model", model)
        form.add_field("prompt", prompt)
        form.add_field("n", "1")
        size = jarvis_image_size(db)
        if size:
            form.add_field("size", size)
        for index, reference in enumerate(references):
            form.add_field("image" if len(references) == 1 else "image[]", reference, filename=f"ref{index}.png", content_type="image/png")
        data = await _request("POST", f"{jarvis_base(db)}/images/edits", headers=headers, timeout=180, form=form)
        found = await _extract_image(data)
        if found:
            return found
    except Exception as exc:
        last = exc
        if any(code in str(exc) for code in _AUTH_CODES):
            raise
    try:
        parts = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(reference).decode()}}
            for reference in references
        ]
        payload = {"model": model, "modalities": ["image", "text"], "messages": [{"role": "user", "content": parts}]}
        data = await _request("POST", f"{jarvis_base(db)}/chat/completions", headers=headers, payload=payload, timeout=180)
        found = await _extract_image(data)
        if found:
            return found
    except Exception as exc:
        last = exc
        if any(code in str(exc) for code in _AUTH_CODES):
            raise
    raise _RefUnsupported(_err(last) if last else "پاسخ بدون تصویر")


async def _jarvis_image(db, prompt: str, reference=None) -> bytes:
    keys = jarvis_keys(db)
    if not keys:
        raise ChannelAIError("کلید Jarvis تنظیم نشده")
    model = jarvis_image_model(db)
    body = {"model": model, "prompt": prompt, "n": 1}
    size = jarvis_image_size(db)
    if size:
        body["size"] = size
    references = [reference] if isinstance(reference, (bytes, bytearray)) else list(reference or [])
    last = None
    for key in keys:
        if references:
            try:
                async with _semaphore():
                    return await _image_with_reference(db, key, model, prompt, references)
            except _RefUnsupported:
                raise
            except Exception as exc:
                last = exc
                continue
        for attempt in (1, 2):
            try:
                async with _semaphore():
                    data = await _request(
                        "POST", f"{jarvis_base(db)}/images/generations",
                        headers={"Authorization": f"Bearer {key}"}, payload=body, timeout=180,
                    )
                    found = await _extract_image(data)
                    if found:
                        return found
                raise ChannelAIError("پاسخ تصویر نه b64_json داشت و نه url")
            except Exception as exc:
                last = exc
                if any(code in str(exc) for code in _AUTH_CODES) or attempt == 2:
                    break
                await asyncio.sleep(3)
    raise ChannelAIError(f"jarvis/{model}: {_err(last)}")


def has_style_ref() -> bool:
    return os.path.isfile(STYLE_REF_PATH)


def save_style_ref(raw: bytes) -> None:
    """بنر نمونه‌ی سبک مطلوب؛ برای مدل به‌عنوان تصویر مرجع اول فرستاده می‌شود."""
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as img:
        pic = img.convert("RGB")
    pic.thumbnail((1280, 1280), Image.LANCZOS)
    pic.save(STYLE_REF_PATH, "JPEG", quality=92)


def remove_style_ref() -> bool:
    if os.path.isfile(STYLE_REF_PATH):
        os.remove(STYLE_REF_PATH)
        return True
    return False


def _style_reference_png() -> bytes:
    from PIL import Image
    with Image.open(STYLE_REF_PATH) as img:
        out = io.BytesIO()
        img.convert("RGB").save(out, "PNG")
    return out.getvalue()


def _normalize(raw: bytes) -> bytes:
    from PIL import Image
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail((1600, 1600))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


def saved_banner_base_prompt(db) -> str:
    """پرامپت پایه‌ی قابل ویرایش توسط مدیر؛ خالی بودن آن به معنی استفاده از مقدار پیش‌فرض است."""
    custom = (db.get_setting(BANNER_BASE_PROMPT_KEY, "") or "").strip()
    return custom or DEFAULT_BANNER_BASE_PROMPT


def save_banner_base_prompt(db, value: str) -> None:
    """ذخیره‌ی دستورالعمل پایه‌ی بنر؛ مقدار '-' تنظیم سفارشی را پاک می‌کند."""
    value = (value or "").strip()
    db.set_setting(BANNER_BASE_PROMPT_KEY, "" if value == "-" else value[:3000])


# قالب‌های طراحی بنر بر اساس نوع پست. emphasis همیشه در پرامپت می‌آید؛ layout فقط وقتی بنر مرجع سبک
# نداریم (چون با بنر مرجع، چیدمان را خودِ مرجع تعیین می‌کند و نباید با آن تضاد پیدا کند).
BANNER_TEMPLATES = {
    "discount": {
        "emphasis": "This is a SALE banner: the discount percentage is the hero of the design and must be the first thing the eye sees.",
        "layout": (
            "LAYOUT TEMPLATE (sale): a very large glowing discount badge on one side as the dominant element, "
            "the headline across the upper-center, and below it a clear price block where the old price is smaller with a visible strikethrough line "
            "and the new price is much larger, bold and in the accent color. Any urgency chip is a small ribbon near the price, clearly smaller than the price."
        ),
    },
    "code": {
        "emphasis": "This is a DISCOUNT CODE banner: the discount badge is the hero. Do not write any code text in the image; the code is given in the post caption.",
        "layout": (
            "LAYOUT TEMPLATE (code): a very large glowing discount badge in the center-right as the dominant element, the headline above it, "
            "and a calm empty glass-style ribbon area below the badge that suggests a coupon. Any urgency chip is a small ribbon beside the coupon area."
        ),
    },
    "product": {
        "emphasis": "This is a PRODUCT banner: the headline and the price are the hero; calm, premium and trustworthy, not shouting.",
        "layout": (
            "LAYOUT TEMPLATE (product): one clear focal subject on one side, the headline large on the opposite side, "
            "and the price inside a rounded glass card under the headline."
        ),
    },
    "test": {
        "emphasis": "This is a FREE TRIAL banner: friendly and inviting, no price anywhere; the headline is the hero.",
        "layout": (
            "LAYOUT TEMPLATE (free trial): a glowing unlocked-gift or connected-power-button motif as the focal subject, "
            "the headline very large above or beside it, and a soft rounded glowing button shape without any text near the bottom."
        ),
    },
    "wheel": {
        "emphasis": "This is a LUCKY WHEEL banner: playful and energetic yet still inside the brand palette; the headline is the hero.",
        "layout": (
            "LAYOUT TEMPLATE (lucky wheel): a glossy colorful prize wheel with plain unlabeled segments as the focal subject on one side, "
            "light sparkles around it, and the headline very large on the opposite side."
        ),
    },
}


def build_banner_prompt(overlay: dict, scene: str = "", colors: str = "", base_prompt: str = "", logo_mode: str = "exact", style_ref: bool = False) -> str:
    """پرامپت بنر فارسی. متن‌ها عیناً داخل پرامپت می‌آیند تا مدل تصویر خودش آن‌ها را فارسی بکشد."""
    overlay = overlay or {}
    texts = []
    if overlay.get("title"):
        texts.append(f"headline «{overlay['title']}»")
    if overlay.get("original_price_toman") and overlay.get("price_toman"):
        texts.append(f"old price «{post_image.fa_number(overlay['original_price_toman'])} تومان» drawn smaller with a clear strikethrough line")
    if overlay.get("price_toman"):
        texts.append(f"price «{post_image.fa_number(overlay['price_toman'])} تومان»" + (" (the new price, largest of the two prices)" if overlay.get("original_price_toman") else ""))
    badge = post_image._badge_text(overlay)
    if badge:
        texts.append(f"discount badge «{badge}»")
    for chip in (overlay.get("urgency") or [])[:2]:
        texts.append(f"small urgency chip «{chip}»")
    template = BANNER_TEMPLATES.get(overlay.get("template") or "")
    palette = (colors or "").strip()[:150] or "a modern harmonious palette that suits the scene"
    parts = [
        "Design a Persian advertising banner for a fast and private VPN internet service.",
        ("Persian texts to render exactly: " + "; ".join(texts) + ".") if texts else "",
        f"Brand colors requested by the admin: {palette}.",
        f"Scene and mood: {(scene or '').strip()[:300] or DEFAULT_IMAGE_PROMPT}.",
        ("TEMPLATE PRIORITY (overrides any conflicting visual hierarchy stated elsewhere in this prompt): " + template["emphasis"] + ("" if style_ref else " " + template["layout"])) if template else "",
        "CUSTOMIZABLE BASE PROMPT: " + ((base_prompt or "").strip()[:3000] or DEFAULT_BANNER_BASE_PROMPT),
        BANNER_RULES_TEMPLATE if style_ref else BANNER_RULES,
        STYLE_RULES if style_ref else "",
        LOGO_RULES.get(logo_mode, LOGO_RULES["none"]),
    ]
    return " ".join(p for p in parts if p)


async def generate_image(db, prompt: str) -> tuple:
    """(بایت JPEG، منبع) از مدل تصویر Jarvis. prompt خالی یعنی بنر نمونه برای تست."""
    if not jarvis_keys(db):
        raise ChannelAIError("کلید Jarvis تنظیم نشده")
    prompt = (prompt or "").strip() or build_banner_prompt({"title": "پروکسی امن و سریع", "price_toman": 250000, "discount_percent": 20}, logo_mode="none")
    try:
        raw = await _jarvis_image(db, prompt)
        return await asyncio.to_thread(_normalize, raw), f"jarvis/{jarvis_image_model(db)}"
    except Exception as exc:
        _log.warning("channel image via jarvis failed: %s", _err(exc)[:200])
        raise ChannelAIError(_err(exc)) from exc


def logo_mode(db) -> str:
    """'exact': لوگوی اصلی با Pillow در نشان درخشان؛ 'ai': لوگو به‌عنوان تصویر مرجع به مدل داده می‌شود."""
    value = (db.get_setting(LOGO_MODE_KEY, "exact") or "exact").strip()
    return value if value in LOGO_MODES else "exact"


def toggle_logo_mode(db) -> str:
    new = "ai" if logo_mode(db) == "exact" else "exact"
    db.set_setting(LOGO_MODE_KEY, new)
    return new


async def make_banner(db, overlay: dict, scene: str, colors: str, base_prompt: str) -> tuple:
    """(بایت JPEG، منبع، حالت لوگوی واقعاً استفاده‌شده). تصویر مرجع سبک و/یا لوگو را به مدل می‌دهد؛ اگر درگاه نپذیرد خودکار به حالت بدون مرجع (لوگوی دقیق) برمی‌گردد."""
    if not jarvis_keys(db):
        raise ChannelAIError("کلید Jarvis تنظیم نشده")
    mode = logo_mode(db) if has_logo() else "none"
    use_style = has_style_ref()
    references = []
    if use_style:
        references.append(await asyncio.to_thread(_style_reference_png))
    if mode == "ai":
        references.append(await asyncio.to_thread(_logo_reference_png))
    raw = None
    if references:
        try:
            prompt = build_banner_prompt(overlay, scene, colors, base_prompt, mode, use_style)
            raw = await _jarvis_image(db, prompt, references)
        except _RefUnsupported as exc:
            _log.warning("jarvis image reference unsupported, falling back to text-only: %s", _err(exc)[:200])
            use_style = False
            if mode == "ai":
                mode = "exact"
        except Exception as exc:
            _log.warning("channel image via jarvis failed: %s", _err(exc)[:200])
            raise ChannelAIError(_err(exc)) from exc
    if raw is None:
        try:
            raw = await _jarvis_image(db, build_banner_prompt(overlay, scene, colors, base_prompt, mode))
        except Exception as exc:
            _log.warning("channel image via jarvis failed: %s", _err(exc)[:200])
            raise ChannelAIError(_err(exc)) from exc
    source = f"jarvis/{jarvis_image_model(db)}" + (" + بنر مرجع سبک" if use_style else "") + (" + مرجع لوگو" if mode == "ai" else (" + لوگوی دقیق" if mode == "exact" else ""))
    return await asyncio.to_thread(_normalize, raw), source, mode


def has_logo() -> bool:
    return os.path.isfile(CHANNEL_LOGO_PATH)


def save_logo(raw: bytes) -> None:
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as img:
        mark = img.convert("RGBA")
    mark.thumbnail((800, 800), Image.LANCZOS)
    mark.save(CHANNEL_LOGO_PATH, "PNG")


def remove_logo() -> bool:
    if os.path.isfile(CHANNEL_LOGO_PATH):
        os.remove(CHANNEL_LOGO_PATH)
        return True
    return False


def _brand_accent(mark) -> tuple:
    """پرجان‌ترین رنگ لوگو برای حلقه‌ی نورانی نشان؛ اگر رنگ شاخصی نبود آبی نئونی."""
    small = mark.convert("RGBA").resize((48, 48))
    scored = []
    for r, g, b, a in small.getdata():
        if a < 200:
            continue
        top = max(r, g, b)
        scored.append((((top - min(r, g, b)) / (top or 1)) * (top / 255), (r, g, b)))
    scored.sort(reverse=True)
    best = scored[:max(1, len(scored) // 10)]
    if not best or best[0][0] < 0.3:
        return (60, 160, 255)
    avg = tuple(int(sum(c[1][i] for c in best) / len(best)) for i in range(3))
    boost = 255 / (max(avg) or 1)
    return tuple(min(255, int(v * min(boost, 1.6))) for v in avg)


def _logo_badge(mark, diameter: int, accent: tuple):
    """لوگو را داخل نشان درخشان می‌گذارد (دایره برای لوگوی شفاف، مستطیل گرد برای لوگوی تمام‌قاب) و RGBA با حاشیه‌ی glow می‌دهد."""
    from PIL import Image, ImageDraw, ImageFilter
    scale = 3
    alpha_hist = mark.getchannel("A").histogram()
    opaque = sum(alpha_hist[250:]) / float(mark.width * mark.height) > 0.92
    if opaque:
        fit = mark.copy()
        fit.thumbnail((diameter * scale, diameter * scale), Image.LANCZOS)
        bw, bh = fit.size
        radius = int(min(bw, bh) * 0.22)
        inner = fit
    else:
        bw = bh = diameter * scale
        radius = bw // 2
        inner = mark.copy()
        inner.thumbnail((int(bw * 0.66), int(bh * 0.66)), Image.LANCZOS)
    pad = int(diameter * scale * 0.45)
    canvas_w, canvas_h = bw + 2 * pad, bh + 2 * pad
    box = (pad, pad, pad + bw, pad + bh)
    ring = max(4, int(diameter * scale * 0.035))
    out = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    glow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(box, radius=radius, outline=accent + (255,), width=ring * 2)
    out.alpha_composite(glow.filter(ImageFilter.GaussianBlur(diameter * scale * 0.06)))
    out.alpha_composite(glow.filter(ImageFilter.GaussianBlur(diameter * scale * 0.015)))

    if not opaque:
        shade = Image.new("RGBA", out.size, (0, 0, 0, 0))
        ImageDraw.Draw(shade).rounded_rectangle(box, radius=radius, fill=(7, 10, 28, 235))
        out.alpha_composite(shade)
    else:
        mask = Image.new("L", out.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
        layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
        layer.paste(inner, (pad, pad))
        clipped = Image.new("RGBA", out.size, (0, 0, 0, 0))
        clipped.paste(layer, (0, 0), mask)
        out.alpha_composite(clipped)

    rim = Image.new("RGBA", out.size, (0, 0, 0, 0))
    light = tuple(min(255, c + 70) for c in accent)
    ImageDraw.Draw(rim).rounded_rectangle(box, radius=radius, outline=light + (255,), width=ring)
    out.alpha_composite(rim)

    if not opaque:
        shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
        offset = ((canvas_w - inner.width) // 2, (canvas_h - inner.height) // 2)
        shadow.paste((0, 0, 0, 150), (offset[0], offset[1] + ring), inner.getchannel("A"))
        out.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(diameter * scale * 0.012)))
        out.alpha_composite(inner, offset)

    final_w = canvas_w // scale
    return out.resize((final_w, max(1, canvas_h // scale)), Image.LANCZOS), pad // scale


def compose_banner(raw: bytes, size: str, mode: str = "exact") -> bytes:
    """تصویر AI را به اندازه‌ی انتخابی می‌برد. در حالت «exact» لوگوی اصلی را (بدون دخالت مدل) در نشان درخشان بالا-چپ می‌گذارد."""
    from PIL import Image, ImageEnhance, ImageFilter
    jpeg = post_image.render(raw, None, size)
    if mode != "exact" or not has_logo():
        return jpeg
    base = Image.open(io.BytesIO(jpeg)).convert("RGBA")
    with Image.open(CHANNEL_LOGO_PATH) as logo_file:
        mark = logo_file.convert("RGBA")
    width, height = base.size
    diameter = int(width * 0.2)
    badge, pad = _logo_badge(mark, diameter, _brand_accent(mark))
    margin = int(width * 0.05)
    top = int(height * 0.07) if height > width * 1.5 else margin
    x, y = margin - pad, top - pad

    scrim_size = int(diameter * 2.6)
    fade = Image.radial_gradient("L").resize((scrim_size, scrim_size)).point(lambda v: max(0, 150 - int(v * 0.9)) if v < 170 else 0)
    scrim = Image.new("RGBA", (scrim_size, scrim_size), (4, 6, 20, 0))
    scrim.putalpha(fade)
    cx, cy = margin + diameter // 2, top + diameter // 2
    base.paste(scrim, (cx - scrim_size // 2, cy - scrim_size // 2), scrim)
    base.paste(badge, (x, y), badge)
    final = base.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=3))
    final = ImageEnhance.Contrast(final).enhance(1.03)
    out = io.BytesIO()
    final.save(out, "JPEG", quality=94, subsampling=0)
    return out.getvalue()


def saved_colors(db) -> str:
    return ai_support._setting(db, COLORS_KEY)


def save_colors(db, value: str) -> None:
    db.set_setting(COLORS_KEY, (value or "").strip()[:150])


def _load_learning(db) -> list:
    try:
        data = json.loads(db.get_setting(LEARNING_KEY, "[]") or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def learning_examples(db, limit: int = LEARNING_IN_PROMPT) -> list:
    """آخرین پست‌های تأییدشده (قدیمی به جدید)."""
    return _load_learning(db)[-limit:]


def record_approved(db, variant: dict, goal: str = "") -> None:
    """فقط وقتی ادمین پست را تأیید و به کانال فرستاد صدا زده می‌شود؛ پیش‌نویس و نسخه‌های ردشده یاد گرفته نمی‌شوند."""
    items = _load_learning(db)
    items.append({
        "angle": str(variant.get("angle") or "")[:80],
        "caption": str(variant.get("caption") or "")[:600],
        "button_text": str(variant.get("button_text") or "")[:64],
        "link_type": str((variant.get("deeplink") or {}).get("type") or "none"),
        "goal": (goal or "")[:200],
        "approved_at": datetime.now().isoformat(timespec="minutes"),
    })
    db.set_setting(LEARNING_KEY, json.dumps(items[-LEARNING_LIMIT:], ensure_ascii=False))


def clear_learning(db) -> None:
    db.set_setting(LEARNING_KEY, "[]")


async def list_jarvis_models(db) -> tuple:
    """(مدل‌های متنی، مدل‌های تصویری) از فهرست /models درگاه Jarvis."""
    keys = jarvis_keys(db)
    if not keys:
        return [], []
    data = await _request("GET", f"{jarvis_base(db)}/models", headers={"Authorization": f"Bearer {keys[0]}"}, timeout=20)
    ids = sorted({str(m.get("id", "")) for m in data.get("data", []) if m.get("id")})
    images = [i for i in ids if "image" in i.lower()]
    texts = [i for i in ids if not any(b in i.lower() for b in _NON_TEXT)]
    return texts, images


async def check_jarvis_key(base: str, key: str) -> tuple:
    """(معتبر؟، پیام). فقط 401/403 به‌معنی کلید نامعتبر است؛ خطای شبکه مانع ذخیره نمی‌شود."""
    try:
        await _request("GET", f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        return True, ""
    except Exception as exc:
        text = _err(exc)
        return ("HTTP 401" not in text and "HTTP 403" not in text), text
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
