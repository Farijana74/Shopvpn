# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""پیدا کردن یک کاربر موجود روی پنل‌ها از روی لینک ساب یا نام کاربری (برای «افزودن حساب»)."""
import asyncio
import logging
import math
import re
import time
import urllib.parse
from datetime import datetime

import aiohttp

import sub_info
from panel_providers import PanelError, get_provider
from panel_providers._http import server_value

_log = logging.getLogger("account_link")

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@\-]{2,64}$")
_UA = {"User-Agent": "v2rayNG/1.8.29"}
_SERVER_TIMEOUT = 25
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
_FAIL_WINDOW = 1800
_FAIL_LIMIT = 8
_fails: dict = {}

INFO_PANEL_TYPES = ("marzban", "pasarguard", "marzneshin", "rebecca")


def is_blocked(user_id: int) -> bool:
    now = time.time()
    recent = [t for t in _fails.get(user_id, []) if now - t < _FAIL_WINDOW]
    _fails[user_id] = recent
    return len(recent) >= _FAIL_LIMIT


def register_fail(user_id: int) -> None:
    _fails.setdefault(user_id, []).append(time.time())


def parse_input(text: str):
    """(kind, value): kind برابر 'link' یا 'username' یا None."""
    text = (text or "").strip()
    match = _URL_RE.search(text)
    if match:
        return "link", match.group(0).rstrip(").,;،")
    if len(text.split()) == 1 and _USERNAME_RE.match(text):
        return "username", text
    return None, None


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _path_parts(link: str) -> list:
    path = urllib.parse.urlparse(link).path
    return [urllib.parse.unquote(p) for p in path.split("/") if p]


def _servers_for_link(servers, link: str) -> list:
    host = _host(link)
    matched = []
    for srv in servers:
        hosts = {_host(server_value(srv, "api_url", "")), _host(server_value(srv, "xui_sub_base_url", ""))}
        if host and host in hosts:
            matched.append(srv)
    return matched or list(servers)


async def _username_from_info(link: str):
    url = link.split("?", 1)[0].rstrip("/") + "/info"
    try:
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False), timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            async with session.get(url, headers=_UA) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
    except Exception:
        return None
    name = data.get("username") if isinstance(data, dict) else None
    return name if isinstance(name, str) and _USERNAME_RE.match(name) else None


async def _username_from_link(server, link: str):
    ptype = server["panel_type"]
    parts = _path_parts(link)
    if not parts:
        return None
    provider = get_provider(server)
    if ptype in ("3xui", "alireza"):
        return await provider.find_username_by_sub_id(parts[-1])
    if ptype == "hiddify":
        return await provider.find_username_by_uuid(parts[-1])
    if ptype in INFO_PANEL_TYPES:
        name = await _username_from_info(link)
        if name:
            return name
        if ptype == "marzneshin" and len(parts) >= 2 and _USERNAME_RE.match(parts[-2]):
            return parts[-2] if (await sub_info.fetch_sub_info(link)).get("ok") else None
        return None
    if ptype == "sui" and _USERNAME_RE.match(parts[-1]):
        return parts[-1] if (await sub_info.fetch_sub_info(link)).get("ok") else None
    return None


async def _build_candidate(server, username: str) -> dict:
    provider = get_provider(server)
    result = await provider.get_user(username)
    usage = await provider.get_user_usage(username)
    limit = int(usage.get("data_limit_bytes") or 0)
    expires = usage.get("expires_at")
    sub_url = result.subscription_url or ""
    if not expires and sub_url:
        info = await sub_info.fetch_sub_info(sub_url)
        if info.get("ok") and info.get("expire"):
            expires = info["expire"]
    expires = int(expires) if expires else None
    now = time.time()
    volume_gb = max(1, round(limit / (1024 ** 3))) if limit else 0
    duration_days = max(1, math.ceil((expires - now) / 86400)) if expires else 0
    return {
        "server_id": server["id"],
        "server_name": server["name"],
        "username": result.username or username,
        "volume_gb": volume_gb,
        "duration_days": duration_days,
        "expires_at": datetime.utcfromtimestamp(expires).isoformat() if expires else None,
        "expire": expires,
        "used_bytes": int(usage.get("used_bytes") or 0),
        "limit_bytes": limit,
        "status": usage.get("status") or "",
        "sub_url": sub_url,
    }


async def _lookup_on_server(server, kind: str, value: str):
    username = value if kind == "username" else await _username_from_link(server, value)
    if not username:
        return None
    return await _build_candidate(server, username)


async def _safe_lookup(server, kind: str, value: str):
    try:
        return await asyncio.wait_for(_lookup_on_server(server, kind, value), _SERVER_TIMEOUT), None
    except PanelError as exc:
        return None, exc
    except Exception as exc:
        _log.warning("account lookup failed on server %s: %s", server["id"], exc)
        return None, exc


async def resolve_account(db, user_id: int, kind: str, value: str) -> dict:
    """{'candidates': [...], 'already_yours': bool, 'taken': bool, 'unreachable': bool}"""
    servers = await asyncio.to_thread(db.get_panel_servers, True)
    if kind == "link":
        servers = _servers_for_link(servers, value)
    outcomes = await asyncio.gather(*[_safe_lookup(s, kind, value) for s in servers])
    out = {"candidates": [], "already_yours": False, "taken": False, "unreachable": False}
    failures = 0
    link_host = _host(value) if kind == "link" else ""
    # یک اشتراک/نام کاربری ممکن است روی چند پنل (از جمله پنل نمایندگی که الان
    # زیر همین بات است) وجود داشته باشد. وضعیت همه‌ی نسخه‌ها را جداگانه می‌سنجیم،
    # سپس به ازای هر نام کاربری فقط یک کاندید نشان می‌دهیم.
    groups: dict = {}
    for srv, (cand, err) in zip(servers, outcomes):
        if err is not None and "پیدا نشد" not in str(err):
            failures += 1
        if not cand:
            continue
        srv_hosts = {_host(server_value(srv, "api_url", "")), _host(server_value(srv, "xui_sub_base_url", ""))}
        matches_host = bool(link_host) and link_host in srv_hosts
        existing = await asyncio.to_thread(
            db.find_custom_config_by_panel_username, cand["server_id"], cand["username"]
        )
        key = str(cand["username"]).lower()
        groups.setdefault(key, []).append((cand, matches_host, existing))

    for key, copies in groups.items():
        if any(ex and int(ex["user_id"]) == int(user_id) for _, _, ex in copies):
            out["already_yours"] = True
            continue
        owned_by_other = [(c, ex) for c, _, ex in copies if ex]
        if owned_by_other:
            cand, ex = owned_by_other[0]
            if ex["source"] == "reseller" and kind == "link":
                # کانفیگی که نماینده برای مشتری ساخته؛ با لینک ساب (اثبات مالکیت) به حساب
                # مشتری منتقل می‌شود. با نام کاربری تنها انتقال نمی‌شود.
                out["candidates"].append(dict(cand, claim_id=int(ex["id"]), claim_from=int(ex["user_id"])))
            else:
                out["taken"] = True
            continue
        free = sorted(copies, key=lambda t: not t[1])
        out["candidates"].append(free[0][0])
    out["unreachable"] = bool(failures) and failures == len(outcomes) and not out["candidates"]
    return out
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
