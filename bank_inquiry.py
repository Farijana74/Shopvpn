# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
استعلام بانکی برای بررسی رسید جعلی (کارت↔شبا، نام صاحب کارت).

این ماژول دو لایه دارد:

۱) چک‌های محلی (بدون هیچ API و همیشه فعال):
   - کد بانک داخل شبا (رقم ۵ تا ۷) با بانک کارت (از روی BIN) مقایسه می‌شود.
     وقتی رسید «شبا» نشان می‌دهد ولی کارت فروشنده «کارت» است، چک عددیِ
     قدیمی (که فقط هم‌نوع‌ها را مقایسه می‌کند) ساکت می‌ماند؛ این چک حداقل
     بانک را تطبیق می‌دهد.

۲) استعلام از یک سرویس HTTP (اختیاری، پیش‌فرض خاموش):
   هیچ سرویس‌دهنده‌ی مشخصی در کد گذاشته نشده. ادمین آدرس و کلید سرویسی را که
   خودش مجاز به استفاده‌اش است (API رسمی هر شرکتی) در تنظیمات وارد می‌کند و
   مسیر فیلدهای پاسخ JSON را مشخص می‌کند. تنظیمات (همه در جدول settings):

     bank_inquiry_enabled              "1" روشن / "0" خاموش (پیش‌فرض خاموش)
     bank_inquiry_url                  آدرس سرویس؛ می‌تواند {card} داشته باشد
     bank_inquiry_method               GET یا POST (پیش‌فرض GET)
     bank_inquiry_headers_json         هدرها به‌صورت JSON (مثلاً Authorization)؛ {card} هم قابل‌استفاده
     bank_inquiry_body_json            بدنه‌ی JSON برای POST؛ {card} جایگزین می‌شود
     bank_inquiry_iban_path            مسیر شبا در پاسخ، مثلاً data.iban
     bank_inquiry_owner_path           مسیر نام صاحب کارت؛ چند مسیر با ویرگول
                                       (مثلاً data.first_name,data.last_name) با فاصله به‌هم چسبانده می‌شوند
     bank_inquiry_timeout              ثانیه (پیش‌فرض ۱۰)
     bank_inquiry_cache_hours          مدت کش نتیجه (پیش‌فرض ۲۴ ساعت)
     bank_inquiry_auto_reject          "1" یعنی مغایرتِ قطعیِ مقصد (طبق استعلام) رسید را خودکار
                                       رد کند. پیش‌فرض "0": فقط هشدار برای ادمین.

اصول:
  - اگر سرویس خاموش/خطادار/کند بود، نتیجه «available=False» است و هیچ‌وقت به‌عنوان
    «تایید» تفسیر نمی‌شود (رسید ساکت پذیرفته نمی‌شود، فقط ادمین مطلع می‌شود).
  - شماره کارت کامل هرگز در لاگ نوشته نمی‌شود؛ کش با هش انجام می‌شود.
  - کارتِ ماسک‌شده (6037****1234) قابل‌استعلام نیست و رد می‌شود (بدون هشدار الکی).
"""

import asyncio
import difflib
import hashlib
import json
import logging
import re
import time

import aiohttp

_log = logging.getLogger("bank_inquiry")

# کد بانک داخل شبا: IR + ۲ رقم کنترل + ۳ رقم کد بانک + ... (فهرست عمومی بانک مرکزی)
IBAN_BANK_CODES = {
    "010": "بانک مرکزی",
    "011": "بانک صنعت و معدن",
    "012": "بانک ملت",
    "013": "بانک رفاه کارگران",
    "014": "بانک مسکن",
    "015": "بانک سپه",
    "016": "بانک کشاورزی",
    "017": "بانک ملی",
    "018": "بانک تجارت",
    "019": "بانک صادرات",
    "020": "بانک توسعه صادرات",
    "021": "پست بانک ایران",
    "022": "بانک توسعه تعاون",
    "051": "موسسه اعتباری توسعه",
    "053": "بانک کارآفرین",
    "054": "بانک پارسیان",
    "055": "بانک اقتصاد نوین",
    "056": "بانک سامان",
    "057": "بانک پاسارگاد",
    "058": "بانک سرمایه",
    "059": "بانک سینا",
    "060": "بانک قرض‌الحسنه مهر ایران",
    "061": "بانک شهر",
    "062": "بانک آینده",
    "063": "بانک انصار",
    "064": "بانک گردشگری",
    "065": "بانک حکمت ایرانیان",
    "066": "بانک دی",
    "069": "بانک ایران زمین",
    "070": "بانک قرض‌الحسنه رسالت",
    "075": "موسسه اعتباری ملل",
    "078": "بانک خاورمیانه",
}

_DIGIT_TRANS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _norm_acct(raw) -> str:
    if not raw:
        return ""
    s = str(raw).translate(_DIGIT_TRANS)
    return re.sub(r"[\s\-_\u200c]", "", s).upper()


def _is_iban(s: str) -> bool:
    return s.startswith("IR") and len(s) == 26 and s[2:].isdigit()


def _is_full_card(s: str) -> bool:
    return len(s) == 16 and s.isdigit()


def iban_valid(iban: str) -> bool:
    s = _norm_acct(iban)
    if not _is_iban(s):
        return False
    rearranged = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1
    except ValueError:
        return False
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


def bank_from_iban(iban: str) -> str:
    s = _norm_acct(iban)
    if not _is_iban(s):
        return ""
    return IBAN_BANK_CODES.get(s[4:7], "")


def _bank_key(name: str) -> str:
    n = (name or "").strip().lower()
    for junk in ("بانک", "موسسه", "اعتباری", "قرض‌الحسنه", "قرضالحسنه", "bank", "\u200c", " ", "-", "_"):
        n = n.replace(junk, "")
    return n


def banks_differ(a: str, b: str) -> bool:
    ka, kb = _bank_key(a), _bank_key(b)
    if not ka or not kb:
        return False
    return not (ka in kb or kb in ka)


def mask(value: str) -> str:
    v = _norm_acct(value)
    if _is_iban(v):
        return v[:6] + "…" + v[-4:]
    if _is_full_card(v):
        return v[:6] + "******" + v[-4:]
    return v


# ---------------------------------------------------------------- چک محلی

def check_destination_bank_local(dest_raw: str, expected_raw: str, bin_bank_of_card) -> "str | None":
    """وقتی یکی شبا و دیگری کارت است، بانک‌ها را تطبیق می‌دهد.
    bin_bank_of_card: تابعی که شماره کارت را می‌گیرد و نام بانک (از BIN) را برمی‌گرداند."""
    d, e = _norm_acct(dest_raw), _norm_acct(expected_raw)
    if not d or not e or "*" in d or "*" in e:
        return None
    if _is_iban(d) and _is_full_card(e):
        iban, card = d, e
    elif _is_full_card(d) and _is_iban(e):
        iban, card = e, d
    else:
        return None
    if not iban_valid(iban):
        return None  # ساختار نامعتبر را چک قدیمی گزارش می‌کند
    iban_bank, card_bank = bank_from_iban(iban), bin_bank_of_card(card)
    if banks_differ(iban_bank, card_bank):
        return (
            f"⚠️ شبای مقصد رسید ({mask(dest_raw)}) متعلق به «{iban_bank}» است ولی کارت واقعی دریافت‌کننده "
            f"به «{card_bank}» تعلق دارد؛ این دو باید یک بانک باشند (مقایسه‌ی کد بانک داخل شبا با پیش‌شماره‌ی کارت)."
        )
    return None


# ---------------------------------------------------------------- استعلام HTTP

_CACHE = {}  # sha256(card) -> (expires_ts, result)‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


def _setting(db, key: str, default: str = "") -> str:
    try:
        v = db.get_setting(key, default)
    except Exception:
        return default
    return v if v not in (None, "") else default


def _dig(data, path: str):
    cur = data
    for part in (path or "").split("."):
        part = part.strip()
        if not part:
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _fill(obj, card: str):
    if isinstance(obj, str):
        return obj.replace("{card}", card)
    if isinstance(obj, dict):
        return {k: _fill(v, card) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, card) for v in obj]
    return obj


def _enabled(db) -> bool:
    return _setting(db, "bank_inquiry_enabled", "0") == "1" and bool(_setting(db, "bank_inquiry_url"))


async def inquire_card(db, card_digits: str) -> dict:
    """استعلام یک کارت کامل. خروجی همیشه دیکشنری با کلیدهای
    ok, iban, owner, error است؛ ok=False یعنی استعلام انجام نشد (نه اینکه کارت بد است)."""
    card = _norm_acct(card_digits)
    if not _is_full_card(card):
        return {"ok": False, "iban": "", "owner": "", "error": "کارت کامل نیست"}
    enabled = await asyncio.to_thread(_enabled, db)
    if not enabled:
        return {"ok": False, "iban": "", "owner": "", "error": "استعلام بانکی خاموش است"}

    key = hashlib.sha256(card.encode()).hexdigest()
    now = time.time()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]

    cfg = await asyncio.to_thread(lambda: {
        "url": _setting(db, "bank_inquiry_url"),
        "method": _setting(db, "bank_inquiry_method", "GET").upper(),
        "headers": _setting(db, "bank_inquiry_headers_json", "{}"),
        "body": _setting(db, "bank_inquiry_body_json", ""),
        "iban_path": _setting(db, "bank_inquiry_iban_path"),
        "owner_path": _setting(db, "bank_inquiry_owner_path"),
        "timeout": _setting(db, "bank_inquiry_timeout", "10"),
        "cache_h": _setting(db, "bank_inquiry_cache_hours", "24"),
    })
    try:
        headers = _fill(json.loads(cfg["headers"] or "{}"), card)
        body = _fill(json.loads(cfg["body"]), card) if cfg["body"].strip() else None
        timeout = aiohttp.ClientTimeout(total=max(3.0, float(cfg["timeout"])))
        url = cfg["url"].replace("{card}", card)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if cfg["method"] == "POST":
                resp_ctx = session.post(url, headers=headers, json=body)
            else:
                resp_ctx = session.get(url, headers=headers)
            async with resp_ctx as resp:
                status = resp.status
                data = await resp.json(content_type=None)
        if status >= 400:
            raise RuntimeError(f"HTTP {status}")
    except Exception as exc:
        # شماره کارت در پیام خطا نیاید
        _log.warning("bank_inquiry: استعلام ناموفق (%s) برای %s", type(exc).__name__, mask(card))
        return {"ok": False, "iban": "", "owner": "", "error": "سرویس استعلام پاسخ نداد"}

    iban_val = _dig(data, cfg["iban_path"]) if cfg["iban_path"] else None
    owner_parts = [_dig(data, p) for p in cfg["owner_path"].split(",") if p.strip()]
    owner = " ".join(str(p).strip() for p in owner_parts if p)
    iban = _norm_acct(iban_val) if iban_val else ""
    if iban and not iban.startswith("IR") and iban.isdigit() and len(iban) == 24:
        iban = "IR" + iban
    if iban and not iban_valid(iban):
        iban = ""
    if not iban and not owner:
        return {"ok": False, "iban": "", "owner": "", "error": "پاسخ سرویس قابل‌خواندن نبود"}

    result = {"ok": True, "iban": iban, "owner": owner, "error": ""}
    try:
        ttl = max(0.0, float(cfg["cache_h"])) * 3600
    except ValueError:
        ttl = 24 * 3600
    if ttl:
        if len(_CACHE) > 2000:
            _CACHE.clear()
        _CACHE[key] = (now + ttl, result)
    return result


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    name = str(name).strip().replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    return re.sub(r"\s+", " ", name).casefold()


def names_match(a: str, b: str) -> bool:
    x, y = _normalize_name(a), _normalize_name(b)
    if len(x) < 3 or len(y) < 3:
        return True  # داده‌ی کافی برای قضاوت نیست
    xw, yw = x.split(), y.split()
    if len(xw) >= 2 and len(yw) >= 2:
        return len(set(xw) & set(yw)) / max(1, min(len(xw), len(yw))) >= 0.5
    return difflib.SequenceMatcher(None, x, y).ratio() >= 0.6


async def run_checks(db, *, dest_raw: str, expected_card: str, source_raw: str,
                     source_holder_raw: str, bin_bank_of_card) -> dict:
    """همه‌ی چک‌های این ماژول برای یک رسید.

    خروجی:
      notes:        هشدارهای معمولی (برای پیام ادمین)
      hard_notes:   مغایرت قطعیِ مقصد طبق استعلام (فقط با bank_inquiry_auto_reject به رد خودکار می‌رسد)
      infos:        اطلاعات کمکی (مثل نام صاحب کارت مبدأ طبق استعلام)
      inquiry_ran:  آیا استعلام واقعاً انجام شد
      inquiry_error: اگر استعلام لازم بود ولی انجام نشد، دلیل کوتاه
    """
    out = {"notes": [], "hard_notes": [], "infos": [], "inquiry_ran": False, "inquiry_error": ""}

    local = check_destination_bank_local(dest_raw, expected_card, bin_bank_of_card)
    if local:
        out["notes"].append(local)

    if not await asyncio.to_thread(_enabled, db):
        return out

    d, e = _norm_acct(dest_raw), _norm_acct(expected_card)
    # --- مقصد: شبای رسید در برابر کارت فروشنده (یا برعکس) با استعلام دقیق
    pair = None
    if _is_iban(d) and iban_valid(d) and _is_full_card(e):
        pair = (e, d)      # کارت فروشنده را استعلام کن، با شبای رسید مقایسه کن‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
    elif _is_full_card(d) and _is_iban(e) and iban_valid(e):
        pair = (d, e)      # کارتِ داخل رسید را استعلام کن، با شبای فروشنده مقایسه کن
    if pair:
        card, iban_expected_side = pair
        res = await inquire_card(db, card)
        if res["ok"] and res["iban"]:
            out["inquiry_ran"] = True
            if res["iban"] != iban_expected_side:
                out["hard_notes"].append(
                    f"⛔️ طبق استعلام بانکی، کارت {mask(card)} متعلق به شبای {mask(res['iban'])} است، ولی رسید/تنظیمات "
                    f"شبای {mask(iban_expected_side)} را نشان می‌دهد - مقصد واقعی پول با حساب فروشنده یکی نیست."
                )
        elif not res["ok"]:
            out["inquiry_error"] = res["error"]

    # --- مبدأ: نام واقعی صاحب کارت مبدأ
    s = _norm_acct(source_raw)
    if _is_full_card(s):
        res = await inquire_card(db, s)
        if res["ok"] and res["owner"]:
            out["inquiry_ran"] = True
            out["infos"].append(f"👤 صاحب کارت مبدأ ({mask(s)}) طبق استعلام بانکی: «{res['owner']}»")
            if source_holder_raw and not names_match(res["owner"], source_holder_raw):
                out["notes"].append(
                    f"⚠️ نام فرستنده‌ای که روی رسید چاپ شده («{source_holder_raw}») با نام واقعی صاحب کارت مبدأ "
                    f"طبق استعلام بانکی («{res['owner']}») همخوانی ندارد - ممکن است رسید دستکاری شده باشد."
                )
        elif not res["ok"] and not out["inquiry_error"]:
            out["inquiry_error"] = res["error"]
    elif s and "*" in s:
        pass  # کارت ماسک‌شده؛ قابل‌استعلام نیست و طبیعی است

    return out
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
