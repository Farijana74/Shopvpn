# -*- coding: utf-8 -*-
"""Userbot (personal-account AI responder): own chats, conversation memory, exceptions and daily stats.

Fully independent from the Telegram Business tables (business_*)."""


class UserbotMixin:
    # ----- chats -----------------------------------------------------------
    def touch_userbot_chat(self, chat_id: int, first_name: str = "", username: str = ""):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO userbot_chats (chat_id, first_name, username, msg_count) VALUES (?,?,?,1) "
                "ON CONFLICT(chat_id) DO UPDATE SET first_name=excluded.first_name, "
                "username=excluded.username, msg_count=msg_count+1, updated_at=CURRENT_TIMESTAMP",
                (chat_id, first_name, username),
            )
            return conn.execute("SELECT * FROM userbot_chats WHERE chat_id=?", (chat_id,)).fetchone()

    def get_userbot_chat(self, chat_id: int):
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM userbot_chats WHERE chat_id=?", (chat_id,)).fetchone()

    def set_userbot_chat_mode(self, chat_id: int, mode: str, first_name: str = "", username: str = ""):
        """mode: 'auto' or 'human'. Creates the chat row when it does not exist yet."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO userbot_chats (chat_id, first_name, username) VALUES (?,?,?)",
                (chat_id, first_name, username),
            )
            if mode == "human":
                conn.execute(
                    "UPDATE userbot_chats SET mode='human', human_since=CURRENT_TIMESTAMP, "
                    "updated_at=CURRENT_TIMESTAMP WHERE chat_id=?", (chat_id,),
                )
            else:
                conn.execute(
                    "UPDATE userbot_chats SET mode='auto', human_since=NULL, "
                    "updated_at=CURRENT_TIMESTAMP WHERE chat_id=?", (chat_id,),
                )

    def mark_userbot_chat_greeted(self, chat_id: int):
        with self._get_conn() as conn:
            conn.execute("UPDATE userbot_chats SET greeted=1 WHERE chat_id=?", (chat_id,))

    def bump_userbot_chat_ai(self, chat_id: int):
        with self._get_conn() as conn:
            conn.execute("UPDATE userbot_chats SET ai_count=ai_count+1 WHERE chat_id=?", (chat_id,))

    def list_userbot_human_chats(self, limit: int = 30):
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM userbot_chats WHERE mode='human' ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()

    def count_userbot_human_chats(self) -> int:
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) c FROM userbot_chats WHERE mode='human'").fetchone()["c"]

    def count_userbot_chats(self) -> int:
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) c FROM userbot_chats").fetchone()["c"]

    def expire_userbot_human_chats(self, hours: int):
        """Returns the chat ids that were silent in 'human' mode for longer than ``hours`` and puts them back to auto."""
        if hours <= 0:
            return []
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT chat_id FROM userbot_chats WHERE mode='human' AND human_since IS NOT NULL "
                "AND human_since < datetime('now', ?)", (f"-{int(hours)} hours",),
            ).fetchall()
            ids = [r["chat_id"] for r in rows]
            for cid in ids:
                conn.execute(
                    "UPDATE userbot_chats SET mode='auto', human_since=NULL, updated_at=CURRENT_TIMESTAMP "
                    "WHERE chat_id=?", (cid,),
                )
                conn.execute("DELETE FROM userbot_ai_messages WHERE chat_id=?", (cid,))
            return ids

    # ----- AI conversation memory --------------------------------------------
    def add_userbot_ai_message(self, chat_id: int, role: str, message: str):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO userbot_ai_messages (chat_id, role, message) VALUES (?,?,?)",
                (chat_id, role, message),
            )
            conn.execute(
                "DELETE FROM userbot_ai_messages WHERE chat_id=? AND id NOT IN "
                "(SELECT id FROM userbot_ai_messages WHERE chat_id=? ORDER BY id DESC LIMIT 40)",
                (chat_id, chat_id),
            )

    def get_userbot_ai_conversation(self, chat_id: int, limit: int = 30):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM userbot_ai_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit),
            ).fetchall()
            return list(reversed(rows))

    def clear_userbot_ai_conversation(self, chat_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM userbot_ai_messages WHERE chat_id=?", (chat_id,))

    # ----- exceptions (customers the userbot never answers) ---------------------
    def list_userbot_exceptions(self):
        with self._get_conn() as conn:
            return conn.execute("SELECT user_id FROM userbot_exceptions ORDER BY user_id").fetchall()

    def add_userbot_exception(self, user_id: int):
        with self._get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO userbot_exceptions (user_id) VALUES (?)", (user_id,))

    def remove_userbot_exception(self, user_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM userbot_exceptions WHERE user_id=?", (user_id,))

    def is_userbot_exception(self, user_id: int) -> bool:
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT 1 FROM userbot_exceptions WHERE user_id=?", (user_id,)
            ).fetchone() is not None

    # ----- daily stats -------------------------------------------------------
    def bump_userbot_stat(self, key: str, n: int = 1):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO userbot_stats (day, key, n) VALUES (date('now'), ?, ?) "
                "ON CONFLICT(day, key) DO UPDATE SET n=n+excluded.n", (key, n),
            )
            conn.execute("DELETE FROM userbot_stats WHERE day < date('now', '-60 days')")

    def get_userbot_stats(self, days: int = 1) -> dict:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT key, SUM(n) n FROM userbot_stats WHERE day >= date('now', ?) GROUP BY key",
                (f"-{max(int(days) - 1, 0)} days",),
            ).fetchall()
        return {r["key"]: int(r["n"] or 0) for r in rows}

    # ----- one-time migration out of Telegram Business -----------------------------
    def migrate_userbot_from_business(self) -> dict:
        """Moves everything the old userbot stored in business_* tables into userbot_* tables and
        removes the pseudo business connection ('userbot:<id>'). Safe to run more than once."""
        info = {"first_message": ""}
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, first_message FROM business_connections WHERE connection_id LIKE 'userbot:%'"
            ).fetchall()
            for r in rows:
                cid = r["id"]
                if (r["first_message"] or "").strip() and not info["first_message"]:
                    info["first_message"] = r["first_message"].strip()
                conn.execute(
                    "INSERT OR IGNORE INTO userbot_chats (chat_id, mode, greeted, first_name, username) "
                    "SELECT chat_id, mode, greeted, first_name, username FROM business_chats WHERE conn_row=?",
                    (cid,),
                )
                conn.execute(
                    "UPDATE userbot_chats SET human_since=CURRENT_TIMESTAMP WHERE mode='human' AND human_since IS NULL"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO userbot_exceptions (user_id) "
                    "SELECT user_id FROM business_exceptions WHERE conn_row=?", (cid,),
                )
                for table in ("business_chats", "business_exceptions", "business_ai_messages", "business_message_log"):
                    conn.execute(f"DELETE FROM {table} WHERE conn_row=?", (cid,))
                conn.execute("DELETE FROM business_connections WHERE id=?", (cid,))
        return info
