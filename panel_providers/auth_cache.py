#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""کش توکن/کوکی احراز هویت پنل‌ها (حافظه‌ای، در سطح پردازه) - قابلیت #180.

قبلاً هر provider (Alireza/IBSng با کوکی، Marzban/Marzneshin/PasarGuard با
Bearer token) روی *هر* عملیات (حتی برای یک درخواست ساده‌ی get_user_usage) از
نو لاگین می‌کرد. این ماژول یک کش مشترک است که توکن/کوکیِ معتبر را نگه
می‌دارد تا وقتی منقضی نشده دوباره استفاده شود.

کلید کش عمداً بر اساس api_url+api_username است، نه panel_servers.id - چون
بات‌های نمایندگی هر کدام دیتابیس sqlite جدای خودشان دارند و همان id عددی در
دو دیتابیس مختلف می‌تواند به دو پنل کاملاً متفاوت اشاره کند (رجوع به یادداشت
مشابه در database.py، بخش mirror_source_id). آدرس+یوزرنیم واقعی پنل شناسه‌ی
درست‌تری است و به‌علاوه اگر دو تننت به یک پنل فیزیکی وصل باشند کش را هم به
درستی به اشتراک می‌گذارد.
"""
import time
import base64
import json

_TOKEN_CACHE: dict = {}
_COOKIE_CACHE: dict = {}

_DEFAULT_TOKEN_TTL = 20 * 60   # ۲۰ دقیقه، وقتی نتوانیم exp واقعی توکن را بخوانیم
_COOKIE_TTL = 15 * 60          # ۱۵ دقیقه برای پنل‌های کوکی‌محور (Alireza/IBSng)‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
_EXP_MARGIN = 30               # ثانیه؛ حاشیه‌ی اطمینان قبل از انقضای واقعی JWT‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


def cache_key(provider_name: str, server) -> str:
    try:
        url = (server["api_url"] or "").rstrip("/").lower()
    except (KeyError, IndexError, TypeError):
        url = ""
    try:
        username = server["api_username"] or ""
    except (KeyError, IndexError, TypeError):
        username = ""
    return f"{provider_name}:{url}:{username}"


def _jwt_exp(token: str):
    """exp (epoch ثانیه) داخل payload یک JWT را می‌خواند؛ اگر توکن JWT نبود یا
    فیلد exp نداشت None برمی‌گرداند."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None


def get_token(key: str):
    entry = _TOKEN_CACHE.get(key)
    if not entry:
        return None
    token, expires_at = entry
    if time.time() >= expires_at:
        _TOKEN_CACHE.pop(key, None)
        return None
    return token


def set_token(key: str, token: str) -> None:
    exp = _jwt_exp(token)
    expires_at = (exp - _EXP_MARGIN) if exp else (time.time() + _DEFAULT_TOKEN_TTL)
    _TOKEN_CACHE[key] = (token, expires_at)


def invalidate_token(key: str) -> None:
    _TOKEN_CACHE.pop(key, None)


def get_cookies(key: str):
    entry = _COOKIE_CACHE.get(key)
    if not entry:
        return None
    cookies, expires_at = entry
    if time.time() >= expires_at:
        _COOKIE_CACHE.pop(key, None)
        return None
    return cookies


def set_cookies(key: str, cookies: dict) -> None:
    if not cookies:
        return
    _COOKIE_CACHE[key] = (dict(cookies), time.time() + _COOKIE_TTL)


def invalidate_cookies(key: str) -> None:
    _COOKIE_CACHE.pop(key, None)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
