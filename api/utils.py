# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""ابزارهای مشترک API عمومی ShopVPN."""
import hashlib
import hmac
import os
import threading
import time
from collections import deque
from functools import wraps
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def token_from_header(headers) -> str:
    # طبق قرارداد F20، هدر دقیقاً Token است.‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
    value = headers.get("Token") or headers.get("token") or ""
    return value.strip()


def has_scope(scope_text: str, required: str) -> bool:
    scopes = {x.strip().lower() for x in (scope_text or "").split(",") if x.strip()}
    return "*" in scopes or required.lower() in scopes


def safe_row(row, allowed=None):
    if row is None:
        return None
    data = dict(row)
    if allowed is not None:
        data = {k: data.get(k) for k in allowed if k in data}
    return data


def pagination(limit: int = 50, offset: int = 0):
    return min(max(int(limit or 50), 1), 100), max(int(offset or 0), 0)


class RateLimiter:
    """محدودکننده‌ی پنجره‌ی لغزان درون‌حافظه‌ای؛ check ثانیه‌های انتظار را برمی‌گرداند و 0 یعنی مجاز."""

    def __init__(self, limit: int, window: float = 60.0):
        self.limit = limit
        self.window = window
        self._hits = {}
        self._lock = threading.Lock()

    def check(self, key) -> int:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return int(self.window - (now - hits[0])) + 1
            hits.append(now)
            return 0
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
