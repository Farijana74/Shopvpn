# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
توکن دسترسی طولانی‌مدت (Personal Access Token) برای اپ موبایل مدیریت.

فرمت توکن:  spat_<base64url(tenant_slug)>_<راز تصادفی ۳۲ بایتی>
    - بخش دوم اسلاگ تننت را به‌صورت خوانا (نه امن) حمل می‌کند تا سرور، بدون
      نیاز به جست‌وجو در همه‌ی دیتابیس‌های نماینده‌ها، بداند این توکن مال کدام
      تننت است و کدام دیتابیس را باید برای اعتبارسنجی هش باز کند.
    - اعتبار واقعی از هشِ SHA-256 کل رشته می‌آید که در ستون token_hash همان
      دیتابیس تننت ذخیره شده؛ خودِ رشته‌ی خام هیچ‌جا ذخیره نمی‌شود.
"""

import base64
import hashlib
import secrets


TOKEN_PREFIX = "spat"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def generate_token(tenant_slug: str = ""):
    """یک توکن جدید می‌سازد. خروجی: (توکن خام - فقط همین یک‌بار نشان داده می‌شود،
    هش برای ذخیره در دیتابیس، پیشوند کوتاه برای نمایش در لیست توکن‌ها)."""
    slug_part = _b64url_encode((tenant_slug or "main").encode("utf-8"))
    secret_part = secrets.token_urlsafe(32)
    full_token = f"{TOKEN_PREFIX}_{slug_part}_{secret_part}"
    token_hash = hash_token(full_token)
    display_prefix = full_token[: len(TOKEN_PREFIX) + 1 + len(slug_part) + 7] + "…"
    return full_token, token_hash, display_prefix


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_tenant_slug(token: str):
    """اسلاگ تننت را از خودِ توکن (بدون نیاز به دیتابیس) استخراج می‌کند.
    اگر فرمت نامعتبر بود None برمی‌گرداند."""
    if not token or not token.startswith(TOKEN_PREFIX + "_"):
        return None
    try:
        _, slug_part, secret_part = token.split("_", 2)
        if not secret_part:
            return None
        slug = _b64url_decode(slug_part).decode("utf-8")
        return "" if slug == "main" else slug
    except Exception:
        return None
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
