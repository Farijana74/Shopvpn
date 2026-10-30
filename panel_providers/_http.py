#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""ابزارهای مشترک HTTP برای providerهای پنل."""
import asyncio
import json

import aiohttp

from .base import PanelError, build_connector

TIMEOUT = aiohttp.ClientTimeout(total=20)


def new_session(headers=None, cookies=False, auth=None, server=None) -> aiohttp.ClientSession:
    """سشن بدون بررسی SSL؛ cookies=True برای پنل‌های لاگین‌محور.
    اگر server داده شود و پروکسی ساکس داشته باشد، اتصال از طریق آن برقرار می‌شود."""
    return aiohttp.ClientSession(
        connector=build_connector(server),
        headers=headers,
        timeout=TIMEOUT,
        cookie_jar=aiohttp.CookieJar(unsafe=True) if cookies else None,
        auth=auth,
    )


async def request(session, method: str, url: str, **kwargs):
    """خروجی: (status, text, headers)."""
    for key in ("data", "params"):
        if isinstance(kwargs.get(key), dict):
            kwargs[key] = {k: str(v) for k, v in kwargs[key].items()}
    try:
        async with session.request(method, url, **kwargs) as resp:
            return resp.status, (await resp.read()).decode("utf-8", errors="replace"), resp.headers
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise PanelError(f"خطا در اتصال به پنل: {exc or 'پاسخی از سرور در زمان مقرر دریافت نشد (timeout)'}") from exc
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


async def request_json(session, method: str, url: str, **kwargs):
    """خروجی: (status, parsed_json_or_None, text)."""
    status, text, _ = await request(session, method, url, **kwargs)
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    return status, data, text


def http_error(action: str, status: int, text: str) -> PanelError:
    return PanelError(f"خطا در {action} (کد {status}): {(text or '')[:300]}")


def load_json(raw, default=None):
    """مقدار ستون‌های JSON دیتابیس (رشته یا مقدار آماده) را برمی‌گرداند."""
    if raw is None or raw == "":
        return default
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def server_value(server, key: str, default=None):
    try:
        value = server[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def gb_to_bytes(gb) -> int:
    return int(float(gb) * (1024 ** 3))


def inbound_ids(server) -> list:
    """id های inbound انتخاب‌شده روی سرور (ستون xui_inbound_ids یا مقدار قدیمی تکی)."""
    ids = load_json(server_value(server, "xui_inbound_ids"))
    if isinstance(ids, list) and ids:
        return [int(i) for i in ids]
    legacy = server_value(server, "xui_inbound_id")
    return [int(legacy)] if legacy else []


def new_limit_bytes(current_limit, used, add_gb, reset_usage: bool, preserve_remaining: bool,
                    unlimited: bool = False) -> int:
    """سقف حجم جدید بر اساس همان قواعد update_user در BasePanelProvider.
    unlimited=True یعنی سرویس نامحدود شود (سقف 0 = بدون محدودیت)."""
    if unlimited:
        return 0
    current_limit = int(current_limit or 0)
    add = int(float(add_gb or 0) * (1024 ** 3))
    if reset_usage and preserve_remaining:
        return max(current_limit - int(used or 0), 0) + add
    if add_gb:
        return add if reset_usage else current_limit + add
    return current_limit
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
