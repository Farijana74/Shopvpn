# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""Telegram Business: connections, per-chat state, exceptions, short message log."""


class BusinessMixin:
    def upsert_business_connection(self, connection_id: str, user_id: int, user_chat_id: int,
                                   can_reply: bool, can_read: bool, is_enabled: bool,
                                   user_name: str = "", username: str = "") -> int:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO business_connections "
                "(connection_id, user_id, user_chat_id, user_name, username, can_reply, can_read, is_enabled) "
                "VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(connection_id) DO UPDATE SET user_id=excluded.user_id, "
                "user_chat_id=excluded.user_chat_id, user_name=excluded.user_name, "
                "username=excluded.username, can_reply=excluded.can_reply, can_read=excluded.can_read, "
                "is_enabled=excluded.is_enabled, updated_at=CURRENT_TIMESTAMP",
                (connection_id, user_id, user_chat_id, user_name, username,
                 1 if can_reply else 0, 1 if can_read else 0, 1 if is_enabled else 0),
            )
            row = conn.execute(
                "SELECT id FROM business_connections WHERE connection_id=?", (connection_id,)
            ).fetchone()
            return row["id"]

    def get_business_connection(self, connection_id: str):
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM business_connections WHERE connection_id=?", (connection_id,)
            ).fetchone()

    def get_business_connection_by_id(self, row_id: int):
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM business_connections WHERE id=?", (row_id,)).fetchone()

    def list_business_connections(self):
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM business_connections ORDER BY id DESC").fetchall()

    def set_business_connection_auto_reply(self, row_id: int, value: bool):
        with self._get_conn() as conn:
            conn.execute("UPDATE business_connections SET auto_reply=? WHERE id=?",
                         (1 if value else 0, row_id))

    def set_business_connection_first_message(self, row_id: int, text: str):
        with self._get_conn() as conn:
            conn.execute("UPDATE business_connections SET first_message=? WHERE id=?", (text or "", row_id))

    def list_business_exceptions(self, row_id: int):
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT user_id FROM business_exceptions WHERE conn_row=? ORDER BY user_id", (row_id,)
            ).fetchall()

    def add_business_exception(self, row_id: int, user_id: int):
        with self._get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO business_exceptions (conn_row, user_id) VALUES (?,?)",
                         (row_id, user_id))

    def remove_business_exception(self, row_id: int, user_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM business_exceptions WHERE conn_row=? AND user_id=?", (row_id, user_id))

    def is_business_exception(self, row_id: int, user_id: int) -> bool:
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT 1 FROM business_exceptions WHERE conn_row=? AND user_id=?", (row_id, user_id)
            ).fetchone() is not None

    def touch_business_chat(self, row_id: int, chat_id: int, first_name: str = "", username: str = ""):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO business_chats (conn_row, chat_id, first_name, username) VALUES (?,?,?,?) "
                "ON CONFLICT(conn_row, chat_id) DO UPDATE SET first_name=excluded.first_name, "
                "username=excluded.username, updated_at=CURRENT_TIMESTAMP",
                (row_id, chat_id, first_name, username),
            )
            return conn.execute(
                "SELECT * FROM business_chats WHERE conn_row=? AND chat_id=?", (row_id, chat_id)
            ).fetchone()

    def get_business_chat(self, row_id: int, chat_id: int):
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM business_chats WHERE conn_row=? AND chat_id=?", (row_id, chat_id)
            ).fetchone()

    def set_business_chat_mode(self, row_id: int, chat_id: int, mode: str):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE business_chats SET mode=?, updated_at=CURRENT_TIMESTAMP WHERE conn_row=? AND chat_id=?",
                (mode, row_id, chat_id),
            )

    def mark_business_chat_greeted(self, row_id: int, chat_id: int):
        with self._get_conn() as conn:
            conn.execute("UPDATE business_chats SET greeted=1 WHERE conn_row=? AND chat_id=?", (row_id, chat_id))

    def list_business_human_chats(self, limit: int = 20):
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM business_chats WHERE mode='human' ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()

    def count_business_human_chats(self) -> int:
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) c FROM business_chats WHERE mode='human'").fetchone()["c"]

    def add_business_ai_message(self, row_id: int, chat_id: int, role: str, message: str):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO business_ai_messages (conn_row, chat_id, role, message) VALUES (?,?,?,?)",
                (row_id, chat_id, role, message),
            )
            conn.execute(
                "DELETE FROM business_ai_messages WHERE conn_row=? AND chat_id=? AND id NOT IN "
                "(SELECT id FROM business_ai_messages WHERE conn_row=? AND chat_id=? ORDER BY id DESC LIMIT 40)",
                (row_id, chat_id, row_id, chat_id),
            )

    def get_business_ai_conversation(self, row_id: int, chat_id: int, limit: int = 30):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM business_ai_messages WHERE conn_row=? AND chat_id=? ORDER BY id DESC LIMIT ?",
                (row_id, chat_id, limit),
            ).fetchall()
            return list(reversed(rows))

    def clear_business_ai_conversation(self, row_id: int, chat_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM business_ai_messages WHERE conn_row=? AND chat_id=?", (row_id, chat_id))

    def log_business_message(self, row_id: int, chat_id: int, message_id: int, from_user_id: int, text: str):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO business_message_log (conn_row, chat_id, message_id, from_user_id, text) "
                "VALUES (?,?,?,?,?)",
                (row_id, chat_id, message_id, from_user_id, text),
            )

    def update_business_message_text(self, row_id: int, chat_id: int, message_id: int, text: str):
        """Replaces the logged text and returns the previous one (or None)."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT text FROM business_message_log WHERE conn_row=? AND chat_id=? AND message_id=?",
                (row_id, chat_id, message_id),
            ).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO business_message_log (conn_row, chat_id, message_id, from_user_id, text) "
                "VALUES (?,?,?,?,?)",
                (row_id, chat_id, message_id, chat_id, text),
            )
            return row["text"] if row else None

    def pop_business_messages(self, row_id: int, chat_id: int, message_ids):
        ids = [int(i) for i in message_ids][:100]
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT message_id, text FROM business_message_log WHERE conn_row=? AND chat_id=? "
                f"AND message_id IN ({marks}) ORDER BY message_id",
                (row_id, chat_id, *ids),
            ).fetchall()
            conn.execute(
                f"DELETE FROM business_message_log WHERE conn_row=? AND chat_id=? AND message_id IN ({marks})",
                (row_id, chat_id, *ids),
            )
            return rows

    def prune_business_message_log(self, days: int = 3):
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM business_message_log WHERE created_at < datetime('now', ?)", (f"-{int(days)} days",)
            )
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
