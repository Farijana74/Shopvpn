# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
پیاده‌سازی ساده و سبک یک FSM Storage پایدار (روی دیسک، با SQLite) برای aiogram،
جایگزین MemoryStorage پیش‌فرض.

چرا لازم است؟
--------------
MemoryStorage تمام state‌های در حال انتظار (مثلاً «کاربر منتظر ارسال عکس
رسید پرداخت است») را فقط در RAM نگه می‌دارد. با هر ری‌استارت پروسه‌ی بات -
دیپلوی جدید، کرش، ری‌استارت سرویس توسط manage.sh، یا حتی استارت/استاپ یک
بات نمایندگی توسط BotManager.reconcile_resellers_loop - همه‌ی این state‌ها
یک‌جا پاک می‌شوند. اگر دقیقاً در آن لحظه کاربری عکس رسید پرداختش را بفرستد،
هیچ هندلری با state آن مطابقت پیدا نمی‌کند و پیام (بدون هیچ خطا یا لاگی)
نادیده گرفته می‌شود - از دید کاربر و ادمین انگار رسید اصلاً ارسال نشده.

این کلاس دقیقاً همان رفتار MemoryStorage (state + data به‌ازای هر
(bot, chat, user, ...)) را پیاده‌سازی می‌کند اما روی یک فایل SQLite کنار
دیتابیس اصلی همان بات ذخیره می‌شود، پس با ری‌استارت پروسه از بین نمی‌رود.

فیکس ۱۴۰۴: عملیات‌های SQLite قبلاً مستقیم روی event loop اجرا می‌شدند
(synchronous) و می‌توانستند آن را کوتاه بلوکه کنند. حالا از
asyncio.to_thread استفاده می‌شود تا هیچ‌وقت event loop فریز نشود.
"""

import asyncio
import json
import sqlite3
import threading
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
from typing import Any, Dict, Optional

from aiogram.fsm.storage.base import BaseStorage, StorageKey


class SQLiteStorage(BaseStorage):
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=4000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fsm_storage (
                bot_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                thread_id INTEGER,
                business_connection_id TEXT,
                destiny TEXT NOT NULL DEFAULT 'default',
                state TEXT,
                data TEXT,
                PRIMARY KEY (bot_id, chat_id, user_id, thread_id, business_connection_id, destiny)
            )
            """
        )
        self._conn.commit()
        self._lock = threading.Lock()

    @staticmethod
    def _key_tuple(key: StorageKey):
        thread_id = getattr(key, "thread_id", None)
        business_connection_id = getattr(key, "business_connection_id", None)
        return (
            key.bot_id,
            key.chat_id,
            key.user_id,
            thread_id if thread_id is not None else 0,
            business_connection_id if business_connection_id is not None else "",
            getattr(key, "destiny", "default"),
        )

    def _set_state_sync(self, key_tuple, state_value):
        with self._lock:
            self._conn.execute(
                "INSERT INTO fsm_storage "
                "(bot_id, chat_id, user_id, thread_id, business_connection_id, destiny, state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (bot_id, chat_id, user_id, thread_id, business_connection_id, destiny) "
                "DO UPDATE SET state=excluded.state",
                (*key_tuple, state_value),
            )
            self._conn.commit()

    def _get_state_sync(self, key_tuple):
        with self._lock:
            cur = self._conn.execute(
                "SELECT state FROM fsm_storage WHERE bot_id=? AND chat_id=? AND user_id=? "
                "AND thread_id=? AND business_connection_id=? AND destiny=?",
                key_tuple,
            )
            r = cur.fetchone()
            return r[0] if r else None

    def _set_data_sync(self, key_tuple, payload):
        with self._lock:
            self._conn.execute(
                "INSERT INTO fsm_storage "
                "(bot_id, chat_id, user_id, thread_id, business_connection_id, destiny, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (bot_id, chat_id, user_id, thread_id, business_connection_id, destiny) "
                "DO UPDATE SET data=excluded.data",
                (*key_tuple, payload),
            )
            self._conn.commit()

    def _get_data_sync(self, key_tuple):
        with self._lock:
            cur = self._conn.execute(
                "SELECT data FROM fsm_storage WHERE bot_id=? AND chat_id=? AND user_id=? "
                "AND thread_id=? AND business_connection_id=? AND destiny=?",
                key_tuple,
            )
            r = cur.fetchone()
            if not r or not r[0]:
                return None
            return r[0]

    def _close_sync(self):
        with self._lock:
            self._conn.close()

    async def set_state(self, key: StorageKey, state: Any = None) -> None:
        state_value = state.state if hasattr(state, "state") else state
        await asyncio.to_thread(self._set_state_sync, self._key_tuple(key), state_value)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        return await asyncio.to_thread(self._get_state_sync, self._key_tuple(key))

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        payload = json.dumps(data or {}, ensure_ascii=False)
        await asyncio.to_thread(self._set_data_sync, self._key_tuple(key), payload)

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        raw = await asyncio.to_thread(self._get_data_sync, self._key_tuple(key))
        if raw is None:
            return {}
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {}

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
