# -*- coding: utf-8 -*-
"""رندر کارت هدیه: چیدمان متن را AI از روی عکس پایه تشخیص می‌دهد و Pillow متن فارسی، تیتر و QR را روی آن چاپ می‌کند."""

import asyncio
import functools
import io
import json
import logging
import os
import tempfile
import threading
import urllib.request

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

import post_image as pi

_log = logging.getLogger("gift_card_render")

CARD_WIDTH = 1080
MIN_HEIGHT = 1080
MAX_HEIGHT = 1920
FIELDS = ("recipient", "title", "message", "qr", "info", "code")
OPTIONAL_FIELDS = ("signature",)
ALL_FIELDS = FIELDS + OPTIONAL_FIELDS
MIN_SIZE = {"qr": (0.2, 0.14)}
QR_CAPTION = "اسکن کن\nو هدیه‌ات را دریافت کن"

_VAZIR = "https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/fonts/ttf/{0}"
_VAZIR_ALT = "https://github.com/rastikerdar/vazirmatn/raw/v33.003/fonts/ttf/{0}"
_GFONTS = "https://raw.githubusercontent.com/google/fonts/{0}/ofl/lalezar/Lalezar-Regular.ttf"
FONT_SOURCES = {
    "body": ("Vazirmatn-Medium.ttf", (_VAZIR.format("Vazirmatn-Medium.ttf"), _VAZIR_ALT.format("Vazirmatn-Medium.ttf"))),
    "bold": ("Vazirmatn-Bold.ttf", (_VAZIR.format("Vazirmatn-Bold.ttf"), _VAZIR_ALT.format("Vazirmatn-Bold.ttf"))),
    "display": ("Lalezar-Regular.ttf", (_GFONTS.format("2796410152d4f9524b68ed46e69c1b60f8e0f7c3"), _GFONTS.format("main"))),
}
_FALLBACK_ROLES = {"display": ("display", "bold", "body"), "bold": ("bold", "body"), "body": ("body", "bold")}
_font_failed = set()
_font_lock = threading.Lock()

DEFAULT_LAYOUT = {
    "fields": {
        "recipient": {"x": 0.10, "y": 0.06, "w": 0.80, "h": 0.09, "align": "center"},
        "title": {"x": 0.10, "y": 0.16, "w": 0.80, "h": 0.12, "align": "center"},
        "message": {"x": 0.12, "y": 0.30, "w": 0.76, "h": 0.20, "align": "center"},
        "qr": {"x": 0.30, "y": 0.52, "w": 0.40, "h": 0.28, "align": "center"},
        "info": {"x": 0.10, "y": 0.82, "w": 0.80, "h": 0.08, "align": "center"},
        "code": {"x": 0.10, "y": 0.91, "w": 0.80, "h": 0.06, "align": "center"},
        "signature": {"x": 0.70, "y": 0.60, "w": 0.24, "h": 0.10, "align": "center"},
    },
    "source": "default",
}

_LAYOUT_PROMPT = (
    "تو یک طراح کارت هدیه هستی. تصویر پایه‌ی یک کارت هدیه به تو داده می‌شود. "
    "هفت ناحیه‌ی مستطیلی برای چاپ روی آن پیدا کن که روی بخش‌های خلوت و کم‌جزئیات تصویر (مثلاً کاغذ یا فضای صاف وسط کارت) باشند "
    "و روی چهره، آرم، گل‌ها، کیک، روبان، المان اصلی طرح یا لبه‌ها نیفتند و با هم همپوشانی نداشته باشند. "
    "نواحی: recipient (نام گیرنده، کوتاه، بالای کارت)، title (تیتر درشت مناسبت مثل تولدت مبارک، زیر نام)، "
    "message (متن پیام، ۳ تا ۴ خط، زیر تیتر)، qr (کد QR مربعی با یک خط توضیح زیرش؛ ناحیه باید کمی بلندتر از عرضش باشد "
    "و روی بخش صاف و روشن قرار بگیرد، نزدیک وسط-پایین کارت)، info (مبلغ یا مشخصات کانفیگ، ۱ تا ۲ خط کوتاه)، "
    "code (کد هدیه و تاریخ انقضا، کوچک، نزدیک پایین)، signature (امضای کوتاه مثل «با بهترین آرزوها»، کنار QR). "
    "ترتیب عمودی معمول: recipient، title، message، qr، info، code. "
    "مختصات نسبت به ابعاد تصویر و بین ۰ و ۱ باشد. فقط و فقط یک JSON با این ساختار برگردان: "
    '{"fields":{"recipient":{"x":0,"y":0,"w":0,"h":0,"align":"center"},"title":{...},"message":{...},'
    '"qr":{...},"info":{...},"code":{...},"signature":{...}}} '
    "که x و y گوشه‌ی بالا-چپ ناحیه و align یکی از right، center یا left است."
)


def _download_font(role: str) -> str:
    name, urls = FONT_SOURCES[role]
    path = os.path.join(pi.FONTS_DIR, name)
    with _font_lock:
        if os.path.isfile(path):
            return path
        if role in _font_failed:
            return ""
        os.makedirs(pi.FONTS_DIR, exist_ok=True)
        for url in urls:
            tmp = ""
            try:
                with urllib.request.urlopen(url, timeout=25) as resp:
                    data = resp.read()
                if len(data) < 30_000:
                    continue
                fd, tmp = tempfile.mkstemp(dir=pi.FONTS_DIR, suffix=".part")
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                ImageFont.truetype(tmp, 20)
                os.replace(tmp, path)
                return path
            except Exception as exc:
                _log.warning("gift font download failed (%s): %s", url, exc)
                if tmp and os.path.isfile(tmp):
                    os.remove(tmp)
        _font_failed.add(role)
    return ""


def _role_path(role: str) -> str:
    for candidate in _FALLBACK_ROLES.get(role, ("body",)):
        path = os.path.join(pi.FONTS_DIR, FONT_SOURCES[candidate][0])
        if os.path.isfile(path) or _download_font(candidate):
            return path
    return ""


@functools.lru_cache(maxsize=256)
def _font_cached(size: int, role: str, path: str):
    if not path:
        return pi._font(size)
    engine = ImageFont.Layout.RAQM if pi._raqm() else ImageFont.Layout.BASIC
    return ImageFont.truetype(path, int(size), layout_engine=engine)


def _font(size, role: str = "body"):
    return _font_cached(int(size), role, _role_path(role))


def _clamp(value, low, high):
    return max(low, min(high, value))


def _default_layout() -> dict:
    return json.loads(json.dumps(DEFAULT_LAYOUT))


def layout_is_current(raw) -> bool:
    """آیا چیدمان ذخیره‌شده همه‌ی نواحی نسخه‌ی جدید (تیتر و QR) را دارد؟"""
    try:
        return all(name in raw["fields"] for name in FIELDS)
    except (KeyError, TypeError):
        return False


def sanitize_layout(raw) -> dict:
    """چیدمان خام را اعتبارسنجی می‌کند؛ در صورت نامعتبر بودن چیدمان پیش‌فرض برمی‌گردد."""
    try:
        fields = raw["fields"]
        clean = {}
        for name in ALL_FIELDS:
            if name not in fields and name in OPTIONAL_FIELDS:
                clean[name] = dict(DEFAULT_LAYOUT["fields"][name])
                continue
            box = fields[name]
            min_w, min_h = MIN_SIZE.get(name, (0.12, 0.04))
            x = _clamp(float(box["x"]), 0.0, 0.9)
            y = _clamp(float(box["y"]), 0.0, 0.95)
            w = _clamp(float(box["w"]), min_w, 1.0 - x)
            h = _clamp(float(box["h"]), min_h, 1.0 - y)
            align = box.get("align") if box.get("align") in ("right", "center", "left") else "center"
            clean[name] = {"x": x, "y": y, "w": w, "h": h, "align": align}
    except (KeyError, TypeError, ValueError, AttributeError):
        return _default_layout()
    if _overlap_ratio(clean) > 0.25:
        return _default_layout()
    return {"fields": clean, "source": "ai"}


def _overlap_ratio(fields: dict) -> float:
    names = list(fields)
    worst = 0.0
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            fa, fb = fields[a], fields[b]
            ix = max(0.0, min(fa["x"] + fa["w"], fb["x"] + fb["w"]) - max(fa["x"], fb["x"]))
            iy = max(0.0, min(fa["y"] + fa["h"], fb["y"] + fb["h"]) - max(fa["y"], fb["y"]))
            smaller = min(fa["w"] * fa["h"], fb["w"] * fb["h"])
            worst = max(worst, (ix * iy) / smaller if smaller else 1.0)
    return worst


async def detect_layout(db, image_bytes: bytes) -> dict:
    """از قوی‌ترین مدل بینایی در دسترس می‌خواهد جای مناسب نواحی را روی عکس پیدا کند؛ در خطا چیدمان پیش‌فرض."""
    import ai_support
    import ai_text

    keys = ai_support.resolve_gemini_keys(db)
    if not keys:
        return _default_layout()
    from google.genai import types

    thumb = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    thumb.thumbnail((1024, 1024))
    buf = io.BytesIO()
    thumb.save(buf, "JPEG", quality=85)
    contents = [types.Content(role="user", parts=[
        types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"),
        types.Part(text=_LAYOUT_PROMPT),
    ])]
    config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2)
    model = ai_support.resolve_gemini_model(db)
    for api_key in keys:
        client = ai_support._build_client(api_key)
        try:
            response = await asyncio.to_thread(
                client.models.generate_content, model=model, contents=contents, config=config,
            )
            return sanitize_layout(ai_text.parse_json_object(response.text or ""))
        except Exception as exc:
            _log.warning("gift layout detection failed: %s", exc)
            if not ai_support._is_retryable(exc):
                break
    return _default_layout()


def fallback_background(color_a: str, color_b: str, height: int = 1440) -> bytes:
    """پس‌زمینه‌ی گرادیانی برای تمی که هنوز عکس پایه ندارد."""
    a, b = pi._rgb(color_a, "#7C3AED"), pi._rgb(color_b, "#EC4899")
    ramp = Image.linear_gradient("L").resize((CARD_WIDTH, height))
    channels = [ramp.point(lambda v, lo=a[i], hi=b[i]: int(lo + (hi - lo) * v / 255)) for i in range(3)]
    out = io.BytesIO()
    Image.merge("RGB", channels).save(out, "PNG")
    return out.getvalue()


def _prepare_base(base_bytes: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(base_bytes)).convert("RGB")
    scale = CARD_WIDTH / img.width
    height = _clamp(int(img.height * scale), MIN_HEIGHT, MAX_HEIGHT)
    return pi._cover(img, (CARD_WIDTH, height))


def _region_style(base: Image.Image, box: tuple):
    region = base.crop(box)
    stat = ImageStat.Stat(region.convert("L"))
    luminance = stat.mean[0] / 255.0
    busy = stat.stddev[0] > 30
    ink = (24, 24, 28) if luminance > 0.58 else (255, 255, 255)
    return ink, busy


def _accent_color(base: Image.Image) -> tuple:
    """رنگ شاخص و پررنگ طرح (برای تیتر، قاب QR و امضا) را از خود عکس پایه برمی‌دارد."""
    small = base.convert("RGB").resize((72, 72))
    scored = []
    for r, g, b in small.getdata():
        high, low = max(r, g, b), min(r, g, b)
        sat = (high - low) / high if high else 0.0
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        scored.append((sat * (1 - min(abs(lum - 0.42) * 1.5, 1.0)), (r, g, b)))
    scored.sort(key=lambda row: row[0], reverse=True)
    top = [color for _, color in scored[:max(len(scored) // 20, 1)]]
    r, g, b = (sum(c[i] for c in top) // len(top) for i in range(3))
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    if lum > 0.42:
        factor = 0.42 / lum
        r, g, b = int(r * factor), int(g * factor), int(b * factor)
    return (r, g, b)


def _fit(draw, lines: list, width: float, height: float, start: int, minimum: int = 20,
         role: str = "body", spacing: float = 1.45):
    size = int(start)
    while True:
        font = _font(size, role)
        wrapped = []
        for text, weight in lines:
            wrapped += [(row, weight) for row in pi._wrap(draw, text, font, width / weight)] or []
        total = sum(int(size * weight * spacing) for _, weight in wrapped)
        widest = max((pi._width(draw, row, _font(int(size * w), role)) for row, w in wrapped), default=0)
        if (total <= height and widest <= width) or size <= minimum:
            return size, wrapped
        size -= 2


def _has_rtl(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def _pixel_box(spec: dict, size: tuple) -> tuple:
    width, height = size
    return (int(spec["x"] * width), int(spec["y"] * height),
            int((spec["x"] + spec["w"]) * width), int((spec["y"] + spec["h"]) * height))


def _composite(canvas: Image.Image, layer: Image.Image, x: int, y: int):
    left, top = max(-x, 0), max(-y, 0)
    if left or top:
        layer = layer.crop((left, top, layer.width, layer.height))
    canvas.alpha_composite(layer, (max(x, 0), max(y, 0)))


def _draw_text_box(canvas: Image.Image, box: tuple, align: str, lines: list, role: str = "body",
                   color=None, spacing: float = 1.45, start_ratio: float = 0.5, shadow: bool = True,
                   force_color=None):
    if not lines:
        return
    bw, bh = box[2] - box[0], box[3] - box[1]
    ink, busy = _region_style(canvas.convert("RGB"), box)
    if busy:
        plate = Image.new("RGBA", (bw + 40, bh + 24), (0, 0, 0, 0))
        fill = (255, 255, 255, 120) if ink[0] < 128 else (0, 0, 0, 120)
        ImageDraw.Draw(plate).rounded_rectangle((0, 0, plate.width - 1, plate.height - 1), radius=28, fill=fill)
        plate = plate.filter(ImageFilter.GaussianBlur(2))
        _composite(canvas, plate, box[0] - 20, box[1] - 12)
    fill_color = force_color or (color if (color is not None and ink[0] < 128) else ink)
    draw = ImageDraw.Draw(canvas, "RGBA")
    base_size = min(bh * start_ratio, bw * 0.12 * (start_ratio / 0.5))
    size, rows = _fit(draw, lines, bw - 8, bh, base_size, role=role, spacing=spacing)
    step_total = sum(int(size * w * spacing) for _, w in rows)
    y = box[1] + max((bh - step_total) // 2, 0)
    shade = (0, 0, 0, 55) if fill_color[0] > 128 else (255, 255, 255, 90)
    for text, weight in rows:
        font = _font(int(size * weight), role)
        if align == "right":
            anchor, x = "ra", box[2]
        elif align == "left":
            anchor, x = "la", box[0]
        else:
            anchor, x = "ma", (box[0] + box[2]) / 2
        rtl = _has_rtl(text)
        shown = pi._display(text) if rtl else text
        kwargs = pi._text_kwargs() if rtl else {}
        if shadow:
            draw.text((x + 1, y + 2), shown, font=font, fill=shade, anchor=anchor, **kwargs)
        draw.text((x, y), shown, font=font, fill=fill_color, anchor=anchor, **kwargs)
        y += int(size * weight * spacing)


def _draw_field(canvas: Image.Image, spec: dict, lines: list, **options):
    _draw_text_box(canvas, _pixel_box(spec, canvas.size), spec["align"], lines, **options)


def _qr_image(link: str, side: int) -> Image.Image:
    import qrcode

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0, box_size=10)
    qr.add_data(link)
    qr.make(fit=True)
    modules = qr.modules_count
    cell = max(side // modules, 2)
    raw = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    raw = raw.resize((modules * cell, modules * cell), Image.NEAREST)
    tile = Image.new("RGB", (side, side), "white")
    tile.paste(raw, ((side - raw.width) // 2, (side - raw.height) // 2))
    return tile


def _draw_qr(canvas: Image.Image, spec: dict, link: str, caption: str, accent: tuple):
    box = _pixel_box(spec, canvas.size)
    bw, bh = box[2] - box[0], box[3] - box[1]
    caption_h = max(int(bh * 0.22), 56) if caption else 0
    outer = min(bw, bh - caption_h)
    if outer < 120:
        return
    pad = max(14, int(outer * 0.07))
    side = outer - 2 * pad
    left = box[0] + (bw - outer) // 2
    top = box[1]
    shadow = Image.new("RGBA", (outer + 40, outer + 40), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((20, 26, outer + 20, outer + 26), radius=30, fill=(0, 0, 0, 55))
    _composite(canvas, shadow.filter(ImageFilter.GaussianBlur(10)), left - 20, top - 20)
    plate = ImageDraw.Draw(canvas, "RGBA")
    plate.rounded_rectangle((left, top, left + outer, top + outer), radius=30, fill=(255, 255, 255, 255),
                            outline=accent + (255,), width=max(3, outer // 70))
    canvas.paste(_qr_image(link, side), (left + pad, top + pad))
    if caption:
        caption_box = (box[0], top + outer + 6, box[2], box[3])
        _draw_text_box(canvas, caption_box, "center", [(part, 1.0) for part in caption.split("\n") if part.strip()],
                       role="body", start_ratio=0.42, shadow=False)


def _draw_signature(canvas: Image.Image, spec: dict, text: str, accent: tuple):
    if not text:
        return
    box = _pixel_box(spec, canvas.size)
    bw, bh = box[2] - box[0], box[3] - box[1]
    ink, _ = _region_style(canvas.convert("RGB"), box)
    layer = Image.new("RGBA", (bw + 80, bh + 80), (0, 0, 0, 0))
    _draw_text_box(layer, (40, 40, 40 + bw, 40 + bh), "center", [(text, 1.0)], role="display",
                   force_color=accent if ink[0] < 128 else ink, start_ratio=0.5, shadow=False)
    layer = layer.rotate(7, resample=Image.BICUBIC, expand=True)
    _composite(canvas, layer, box[0] - (layer.width - bw) // 2, box[1] - (layer.height - bh) // 2)


def render_card(base_bytes: bytes, layout: dict, content: dict) -> bytes:
    """content: recipient، title، message، signature، qr_link، qr_caption، info_lines و code_lines [(متن، وزن فونت)] ← PNG."""
    if not pi.engine_status():
        raise pi.PostImageError("برای نوشتن فارسی روی کارت باید arabic-reshaper و python-bidi نصب باشند.")
    layout = layout if layout_is_current(layout) else _default_layout()
    layout = sanitize_layout(layout) if "signature" not in layout["fields"] else layout
    canvas = _prepare_base(base_bytes).convert("RGBA")
    fields = layout["fields"]
    accent = _accent_color(canvas)
    recipient = (content.get("recipient") or "").strip()
    label = (content.get("recipient_label") or (f"برای {recipient}" if recipient else "")).strip()
    title = (content.get("title") or "").strip()
    message = (content.get("message") or "").strip()
    _draw_field(canvas, fields["recipient"], [(label, 1.0)] if label else [], role="display", start_ratio=0.62)
    _draw_field(canvas, fields["title"], [(title, 1.0)] if title else [], role="display", color=accent, start_ratio=0.9)
    _draw_field(canvas, fields["message"], [(message, 1.0)] if message else [], role="body", spacing=1.75)
    if content.get("qr_link"):
        _draw_qr(canvas, fields["qr"], content["qr_link"], content.get("qr_caption", QR_CAPTION), accent)
    _draw_signature(canvas, fields["signature"], (content.get("signature") or "").strip(), accent)
    _draw_field(canvas, fields["info"], content.get("info_lines") or [], role="bold")
    _draw_field(canvas, fields["code"], content.get("code_lines") or [], role="bold")
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()
