# -*- coding: utf-8 -*-
"""نوشتن عنوان، قیمت، نشان تخفیف و لوگو روی تصویر پست کانال با Pillow (قالب برند و اندازه‌های پست/استوری)."""

import glob
import io
import os
import re
import time
import uuid

from PIL import Image, ImageDraw, ImageFont, features

import config

SIZES = {"post": (1080, 1080), "portrait": (1080, 1350), "story": (1080, 1920)}
SIZE_ORDER = ("post", "portrait", "story")
SIZE_LABELS = {
    "post": "📐 اندازه: پست مربع",
    "portrait": "📐 اندازه: پست عمودی",
    "story": "📐 اندازه: استوری",
}
DEFAULT_BRAND = {"primary": "#0B5FFF", "accent": "#FFC400", "text": "#FFFFFF"}
LOGO_PATH = os.path.join(config.BASE_DIR, "post_brand_logo.png")
FONT_PATH = os.path.join(config.BASE_DIR, "post_brand_font.ttf")
FONTS_DIR = os.path.join(config.BASE_DIR, "fonts")
TMP_DIR = os.path.join(config.BASE_DIR, "tmp_post_images")
TMP_MAX_AGE = 86400
SAMPLE_OVERLAY = {"title": "فروش ویژه آخر هفته", "price_toman": 250000, "discount_percent": 20}

_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_SYSTEM_FONT_GLOBS = (
    "/usr/share/fonts/**/Vazir*Bold*.ttf",
    "/usr/share/fonts/**/Vazir*.ttf",
    "/usr/share/fonts/**/NotoSansArabic*.ttf",
    "/usr/share/fonts/**/NotoNaskhArabic*.ttf",
    "/usr/share/fonts/**/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/**/DejaVuSans.ttf",
)


class PostImageError(Exception):
    """خطای ساخت تصویر برند (فونت یا موتور متن فارسی در دسترس نیست)."""


def parse_color(value: str):
    """'#RRGGBB' یا 'RRGGBB' را به '#RRGGBB' بزرگ تبدیل می‌کند؛ نامعتبر None."""
    match = _HEX_RE.match((value or "").strip())
    return "#" + match.group(1).upper() if match else None


def _rgb(value: str, default: str):
    hexed = parse_color(value) or default
    return tuple(int(hexed[i:i + 2], 16) for i in (1, 3, 5))


def _luminance(rgb) -> float:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255


def fa_number(value) -> str:
    return f"{int(value):,}".replace(",", "٬").translate(_FA_DIGITS)


def load_brand(db) -> dict:
    return {key: db.get_setting(f"post_brand_{key}", default) for key, default in DEFAULT_BRAND.items()}


def default_size(db) -> str:
    value = db.get_setting("post_image_size", "post")
    return value if value in SIZES else "post"


def overlay_default(db) -> bool:
    return db.get_setting("post_overlay_enabled", "1") != "0"


def next_size(current: str) -> str:
    return SIZE_ORDER[(SIZE_ORDER.index(current) + 1) % len(SIZE_ORDER)] if current in SIZE_ORDER else "post"


def _raqm() -> bool:
    return bool(features.check("raqm") and features.check("fribidi"))


def _bidi_display():
    try:
        from bidi import get_display
    except ImportError:
        from bidi.algorithm import get_display
    return get_display


def engine_status() -> str:
    """'raqm'، 'reshaper' یا '' اگر نوشتن فارسی ممکن نیست."""
    if _raqm():
        return "raqm"
    try:
        import arabic_reshaper
        _bidi_display()
    except ImportError:
        return ""
    return "reshaper"


def _display(text: str) -> str:
    if _raqm():
        return text
    import arabic_reshaper
    return _bidi_display()(arabic_reshaper.reshape(text))


def _text_kwargs() -> dict:
    return {"direction": "rtl", "language": "fa"} if _raqm() else {}


def font_path() -> str:
    candidates = [FONT_PATH] + sorted(glob.glob(os.path.join(FONTS_DIR, "*.[to]tf")), key=lambda p: "vazir" not in p.lower())
    for pattern in _SYSTEM_FONT_GLOBS:
        candidates += sorted(glob.glob(pattern, recursive=True))
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise PostImageError("هیچ فونت فارسی پیدا نشد؛ از «قالب برند تصاویر» یک فونت .ttf (مثلاً وزیرمتن) آپلود کن.")


def font_label() -> str:
    if os.path.isfile(FONT_PATH):
        return "سفارشی (آپلودشده)"
    try:
        return os.path.basename(font_path())
    except PostImageError:
        return "پیدا نشد"


def _font(size: int):
    engine = ImageFont.Layout.RAQM if _raqm() else ImageFont.Layout.BASIC
    return ImageFont.truetype(font_path(), int(size), layout_engine=engine)


def _width(draw, text: str, font) -> float:
    return draw.textlength(_display(text), font=font, **_text_kwargs())


def _wrap(draw, text: str, font, max_width: float) -> list:
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and _width(draw, trial, font) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def _fit_title(draw, text: str, max_width: float, start: int, minimum: int):
    size = start
    while True:
        font = _font(size)
        lines = _wrap(draw, text, font, max_width)
        fits = len(lines) <= 3 and all(_width(draw, line, font) <= max_width for line in lines)
        if fits or size <= minimum:
            return font, lines
        size -= 4


def _cover(img, size: tuple):
    width, height = size
    scale = max(width / img.width, height / img.height)
    img = img.resize((max(int(img.width * scale), width), max(int(img.height * scale), height)), Image.LANCZOS)
    left, top = (img.width - width) // 2, (img.height - height) // 2
    return img.crop((left, top, left + width, top + height))


def _badge_text(overlay: dict) -> str:
    if overlay.get("discount_percent"):
        return f"{fa_number(overlay['discount_percent'])}٪ تخفیف"
    if overlay.get("discount_fixed"):
        return f"{fa_number(overlay['discount_fixed'])} تومان تخفیف"
    return ""


def _pill(canvas, draw, text: str, font, right: int, top: int, fill, ink, pad: int):
    width = _width(draw, text, font)
    box = (right - width - 2 * pad, top, right, top + font.size + 2 * pad)
    draw.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, fill=fill)
    draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), _display(text), font=font, fill=ink, anchor="mm", **_text_kwargs())
    return box


def render(bg_bytes: bytes, overlay, size: str = "post", brand: dict = None, logo: bool = True) -> bytes:
    """پس‌زمینه را به اندازه‌ی انتخابی برش می‌دهد و اگر overlay داده شود عنوان/قیمت/نشان/لوگو را رویش می‌نویسد."""
    if size not in SIZES:
        size = "post"
    width, height = SIZES[size]
    canvas = _cover(Image.open(io.BytesIO(bg_bytes)).convert("RGB"), (width, height))
    if overlay:
        if not engine_status():
            raise PostImageError("برای نوشتن فارسی روی تصویر باید بسته‌های arabic-reshaper و python-bidi نصب باشند (pip install -r requirements.txt).")
        canvas = _draw_overlay(canvas, overlay, size, {**DEFAULT_BRAND, **(brand or {})}, logo)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=92)
    return out.getvalue()


def _draw_overlay(canvas, overlay: dict, size: str, brand: dict, logo: bool):
    width, height = canvas.size
    primary = _rgb(brand["primary"], DEFAULT_BRAND["primary"])
    accent = _rgb(brand["accent"], DEFAULT_BRAND["accent"])
    ink = _rgb(brand["text"], DEFAULT_BRAND["text"])
    accent_ink = (20, 20, 20) if _luminance(accent) > 0.55 else (255, 255, 255)
    margin = int(width * 0.07)
    safe_top = int(height * 0.09) if size == "story" else margin
    safe_bottom = int(height * 0.11) if size == "story" else margin

    grad_h = int(height * 0.62)
    shade = tuple(int(c * 0.3) for c in primary)
    alpha = Image.linear_gradient("L").resize((width, grad_h)).point(lambda v: int(v * 0.88))
    canvas = canvas.convert("RGBA")
    canvas.paste(Image.new("RGBA", (width, grad_h), shade + (255,)), (0, height - grad_h), alpha)
    draw = ImageDraw.Draw(canvas)

    bottom = height - safe_bottom
    price = overlay.get("price_toman")
    if price:
        price_font = _font(width * 0.058)
        pad = int(width * 0.025)
        text = f"{fa_number(price)} تومان"
        box = _pill(canvas, draw, text, price_font, (width + _width(draw, text, price_font) + 2 * pad) // 2, int(bottom - price_font.size - 2 * pad), accent, accent_ink, pad)
        bottom = box[1] - int(width * 0.04)
        original = overlay.get("original_price_toman")
        if original and int(original) > int(price):
            # قیمت قبل از تخفیف: بالای قیمت جدید، با خط روی آن
            old_font = _font(width * 0.04)
            old_text = f"{fa_number(original)} تومان"
            old_w = _width(draw, old_text, old_font)
            old_y = bottom - old_font.size
            draw.text((width / 2, old_y), _display(old_text), font=old_font, fill=ink, anchor="ma", **_text_kwargs())
            line_y = old_y + old_font.size * 0.78
            draw.line((width / 2 - old_w / 2 - 4, line_y, width / 2 + old_w / 2 + 4, line_y), fill=ink, width=max(2, int(width * 0.004)))
            bottom = old_y - int(width * 0.025)

    title_font, lines = _fit_title(draw, overlay["title"], width - 2 * margin, int(width * 0.085), int(width * 0.04))
    step = int(title_font.size * 1.4)
    top = bottom - step * len(lines)
    for index, line in enumerate(lines):
        draw.text((width / 2, top + index * step), _display(line), font=title_font, fill=ink, anchor="ma", **_text_kwargs())

    badge = _badge_text(overlay)
    if badge:
        badge_font = _font(width * 0.05)
        _pill(canvas, draw, badge, badge_font, width - margin, safe_top, accent, accent_ink, int(width * 0.022))

    if logo and os.path.isfile(LOGO_PATH):
        with Image.open(LOGO_PATH) as raw:
            mark = raw.convert("RGBA")
        mark.thumbnail((int(width * 0.24), int(height * 0.12)), Image.LANCZOS)
        canvas.alpha_composite(mark, (margin, safe_top))
    return canvas


def sample_background(size: str = "post") -> bytes:
    """پس‌زمینه‌ی ساده برای پیش‌نمایش قالب برند وقتی تصویر AI در دسترس نیست."""
    width, height = SIZES.get(size, SIZES["post"])
    img = Image.linear_gradient("L").resize((width, height))
    tinted = Image.merge("RGB", (img.point(lambda v: 40 + v // 4), img.point(lambda v: 90 + v // 3), img.point(lambda v: 160 + v // 3)))
    out = io.BytesIO()
    tinted.save(out, "JPEG", quality=90)
    return out.getvalue()


def save_logo(raw: bytes) -> None:
    with Image.open(io.BytesIO(raw)) as img:
        mark = img.convert("RGBA")
    mark.thumbnail((800, 800), Image.LANCZOS)
    mark.save(LOGO_PATH, "PNG")


def remove_logo() -> bool:
    if os.path.isfile(LOGO_PATH):
        os.remove(LOGO_PATH)
        return True
    return False


def save_font(raw: bytes) -> None:
    """فونت آپلودشده را اعتبارسنجی و ذخیره می‌کند؛ فایل نامعتبر OSError می‌دهد."""
    ImageFont.truetype(io.BytesIO(raw), 40)
    with open(FONT_PATH, "wb") as handle:
        handle.write(raw)


def remove_font() -> bool:
    if os.path.isfile(FONT_PATH):
        os.remove(FONT_PATH)
        return True
    return False


def has_logo() -> bool:
    return os.path.isfile(LOGO_PATH)


def save_raw(raw: bytes) -> str:
    """تصویر پس‌زمینه‌ی خام را موقتاً نگه می‌دارد تا اندازه/متن بدون ساخت دوباره‌ی AI عوض شود."""
    os.makedirs(TMP_DIR, exist_ok=True)
    cutoff = time.time() - TMP_MAX_AGE
    for name in os.listdir(TMP_DIR):
        path = os.path.join(TMP_DIR, name)
        if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
            os.remove(path)
    token = uuid.uuid4().hex
    with open(os.path.join(TMP_DIR, token + ".jpg"), "wb") as handle:
        handle.write(raw)
    return token


def load_raw(token):
    if not token or not _TOKEN_RE.match(str(token)):
        return None
    path = os.path.join(TMP_DIR, token + ".jpg")
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        return handle.read()
