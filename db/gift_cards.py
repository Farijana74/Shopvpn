# -*- coding: utf-8 -*-
"""کارت هدیه: تم‌ها، کارت‌ها (escrow کیف پول و کانفیگ) و لاگ، با تراکنش اتمیک."""

import hashlib
import json
import secrets
from datetime import datetime, timedelta

from .constants import _wallet_tag

_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_OPEN_STATUSES = ("active", "disabled")
_THEME_EDITABLE = {"title", "emoji", "color_a", "color_b", "base_image_path", "layout_json", "is_active", "sort_order"}
_DEFAULT_THEMES = (
    ("general", "عمومی", "🎁", "#6366F1", "#8B5CF6"),
    ("birthday", "تولد", "🎂", "#F59E0B", "#EF4444"),
    ("valentine", "ولنتاین", "💝", "#EC4899", "#9F1239"),
    ("eid", "عید", "🌙", "#10B981", "#065F46"),
)


def normalize_gift_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def gift_code_hash(code: str) -> str:
    return hashlib.sha256(normalize_gift_code(code).encode()).hexdigest()


def generate_gift_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    return f"GIFT-{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def _now() -> str:
    return datetime.utcnow().isoformat()


class GiftCardsMixin:
    def _ensure_gift_tables(self):
        if getattr(self, "_gift_tables_ready", False):
            return
        with self._get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS gift_themes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    emoji TEXT DEFAULT '🎁',
                    color_a TEXT DEFAULT '#6366F1',
                    color_b TEXT DEFAULT '#8B5CF6',
                    base_image_path TEXT,
                    layout_json TEXT,
                    is_active INTEGER DEFAULT 1,
                    sort_order INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS gift_cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code_hash TEXT UNIQUE NOT NULL,
                    code_hint TEXT,
                    code_plain TEXT,
                    kind TEXT NOT NULL,
                    amount INTEGER DEFAULT 0,
                    fee INTEGER DEFAULT 0,
                    custom_config_id INTEGER,
                    config_snapshot TEXT,
                    sender_id INTEGER NOT NULL,
                    recipient_name TEXT,
                    message TEXT,
                    theme_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'active',
                    image_path TEXT,
                    expires_at TEXT,
                    redeemed_by INTEGER,
                    redeemed_at TEXT,
                    closed_by INTEGER,
                    closed_reason TEXT,
                    closed_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_gift_cards_sender ON gift_cards(sender_id, status);
                CREATE INDEX IF NOT EXISTS idx_gift_cards_status ON gift_cards(status, expires_at);
                CREATE TABLE IF NOT EXISTS gift_card_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_id INTEGER,
                    event TEXT NOT NULL,
                    actor_id INTEGER,
                    detail TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_gift_card_log_card ON gift_card_log(card_id);
                CREATE TABLE IF NOT EXISTS gift_intents (
                    user_id INTEGER PRIMARY KEY,
                    theme_id INTEGER NOT NULL,
                    recipient_name TEXT,
                    message TEXT,
                    expires_days INTEGER DEFAULT 30,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gift_orders (
                    order_id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    theme_id INTEGER NOT NULL,
                    recipient_name TEXT,
                    message TEXT,
                    expires_days INTEGER DEFAULT 30,
                    card_id INTEGER,
                    delivered INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_gift_card_log_event ON gift_card_log(event, actor_id, created_at);
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(gift_cards)")}
            if "stock_config_id" not in columns:
                conn.execute("ALTER TABLE gift_cards ADD COLUMN stock_config_id INTEGER")
            if "headline" not in columns:
                conn.execute("ALTER TABLE gift_cards ADD COLUMN headline TEXT")
            if not conn.execute("SELECT 1 FROM gift_themes LIMIT 1").fetchone():
                for order, (code, title, emoji, a, b) in enumerate(_DEFAULT_THEMES):
                    conn.execute(
                        "INSERT INTO gift_themes (code, title, emoji, color_a, color_b, sort_order) VALUES (?,?,?,?,?,?)",
                        (code, title, emoji, a, b, order),
                    )
        self._gift_tables_ready = True

    @staticmethod
    def _gift_log(conn, card_id, event, actor_id=None, detail=None):
        conn.execute(
            "INSERT INTO gift_card_log (card_id, event, actor_id, detail) VALUES (?,?,?,?)",
            (card_id, event, actor_id, detail),
        )

    def gift_list_themes(self, active_only: bool = False):
        self._ensure_gift_tables()
        q = "SELECT * FROM gift_themes" + (" WHERE is_active=1" if active_only else "") + " ORDER BY sort_order, id"
        with self._get_conn() as conn:
            return conn.execute(q).fetchall()

    def gift_get_theme(self, theme_id: int):
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM gift_themes WHERE id=?", (theme_id,)).fetchone()

    def gift_add_theme(self, title: str, emoji: str = "🎁") -> int:
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM gift_themes").fetchone()[0]
            code = f"theme_{secrets.token_hex(4)}"
            cur = conn.execute(
                "INSERT INTO gift_themes (code, title, emoji, sort_order) VALUES (?,?,?,?)",
                (code, title.strip()[:40], (emoji or "🎁")[:8], order),
            )
            return cur.lastrowid

    def gift_update_theme(self, theme_id: int, **fields) -> bool:
        self._ensure_gift_tables()
        clean = {k: v for k, v in fields.items() if k in _THEME_EDITABLE}
        if not clean:
            return False
        sets = ", ".join(f"{k}=?" for k in clean)
        with self._get_conn() as conn:
            cur = conn.execute(f"UPDATE gift_themes SET {sets} WHERE id=?", (*clean.values(), theme_id))
            return cur.rowcount > 0

    def gift_delete_theme(self, theme_id: int) -> str:
        """حذف کامل اگر هیچ کارتی نداشته باشد؛ وگرنه فقط غیرفعال می‌شود. 'deleted' | 'disabled' | 'missing'."""
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            if not conn.execute("SELECT 1 FROM gift_themes WHERE id=?", (theme_id,)).fetchone():
                return "missing"
            if conn.execute("SELECT 1 FROM gift_cards WHERE theme_id=? LIMIT 1", (theme_id,)).fetchone():
                conn.execute("UPDATE gift_themes SET is_active=0 WHERE id=?", (theme_id,))
                return "disabled"
            conn.execute("DELETE FROM gift_themes WHERE id=?", (theme_id,))
            return "deleted"

    def gift_get_card(self, card_id: int):
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM gift_cards WHERE id=?", (card_id,)).fetchone()

    def gift_get_card_by_code(self, code: str):
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM gift_cards WHERE code_hash=?", (gift_code_hash(code),)).fetchone()

    def gift_list_cards(self, status: str = None, sender_id: int = None, limit: int = 10, offset: int = 0):
        self._ensure_gift_tables()
        where, args = [], []
        if status:
            where.append("status=?")
            args.append(status)
        if sender_id is not None:
            where.append("sender_id=?")
            args.append(sender_id)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self._get_conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM gift_cards{clause}", args).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM gift_cards{clause} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset)
            ).fetchall()
        return rows, total

    def gift_list_log(self, card_id: int = None, limit: int = 30):
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            if card_id is None:
                return conn.execute("SELECT * FROM gift_card_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return conn.execute(
                "SELECT * FROM gift_card_log WHERE card_id=? ORDER BY id DESC LIMIT ?", (card_id, limit)
            ).fetchall()

    def gift_stats(self) -> dict:
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            by_status = {r["status"]: r["c"] for r in conn.execute(
                "SELECT status, COUNT(*) AS c FROM gift_cards GROUP BY status").fetchall()}
            held = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM gift_cards WHERE kind='wallet' AND status IN ('active','disabled')"
            ).fetchone()[0]
        return {"by_status": by_status, "held_amount": int(held)}

    def gift_count_open_cards(self, sender_id: int) -> int:
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM gift_cards WHERE sender_id=? AND status IN ('active','disabled')", (sender_id,)
            ).fetchone()[0]

    def gift_failed_attempts(self, user_id: int, minutes: int = 60) -> int:
        self._ensure_gift_tables()
        since = (datetime.utcnow() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM gift_card_log WHERE event='bad_code' AND actor_id=? AND created_at>=?",
                (user_id, since),
            ).fetchone()[0]

    def gift_log_event(self, card_id, event: str, actor_id=None, detail=None):
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            self._gift_log(conn, card_id, event, actor_id, detail)

    def gift_create_wallet_card(self, sender_id: int, amount: int, theme_id: int, recipient_name: str,
                                message: str, expires_days: int, fee_percent: int = 0,
                                min_amount: int = 0, max_amount: int = 0, max_open: int = 0) -> dict:
        self._ensure_gift_tables()
        amount = int(amount)
        if amount <= 0 or (min_amount and amount < min_amount) or (max_amount and amount > max_amount):
            return {"ok": False, "error": "amount_range"}
        fee = amount * max(int(fee_percent), 0) // 100
        total = amount + fee
        code = generate_gift_code()
        expires_at = (datetime.utcnow() + timedelta(days=max(int(expires_days), 1))).isoformat()
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if max_open and conn.execute(
                "SELECT COUNT(*) FROM gift_cards WHERE sender_id=? AND status IN ('active','disabled')", (sender_id,)
            ).fetchone()[0] >= max_open:
                return {"ok": False, "error": "limit"}
            with _wallet_tag(conn, sender_id, "gift_card_out", "ساخت کارت هدیه"):
                cur = conn.execute(
                    "UPDATE users SET referral_credit=referral_credit-? WHERE telegram_id=? AND referral_credit>=?",
                    (total, sender_id, total),
                )
            if cur.rowcount != 1:
                return {"ok": False, "error": "balance"}
            cur = conn.execute(
                "INSERT INTO gift_cards (code_hash, code_hint, code_plain, kind, amount, fee, sender_id, recipient_name, message, "
                "theme_id, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (gift_code_hash(code), code[-4:], code, "wallet", amount, fee, sender_id,
                 (recipient_name or "")[:60], (message or "")[:400], theme_id, expires_at),
            )
            card_id = cur.lastrowid
            self._gift_log(conn, card_id, "created", sender_id, f"wallet amount={amount} fee={fee}")
        return {"ok": True, "id": card_id, "code": code, "fee": fee, "expires_at": expires_at}

    def gift_create_config_card(self, sender_id: int, custom_config_id: int, theme_id: int, recipient_name: str,
                                message: str, expires_days: int, max_open: int = 0, title: str = None) -> dict:
        self._ensure_gift_tables()
        code = generate_gift_code()
        expires_at = (datetime.utcnow() + timedelta(days=max(int(expires_days), 1))).isoformat()
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if max_open and conn.execute(
                "SELECT COUNT(*) FROM gift_cards WHERE sender_id=? AND status IN ('active','disabled')", (sender_id,)
            ).fetchone()[0] >= max_open:
                return {"ok": False, "error": "limit"}
            cfg = conn.execute(
                "SELECT * FROM custom_configs WHERE id=? AND user_id=?", (custom_config_id, sender_id)
            ).fetchone()
            if not cfg or (cfg["status"] or "active") not in ("active",):
                return {"ok": False, "error": "config"}
            snapshot = json.dumps({
                "name": title or (cfg["display_name"] if "display_name" in cfg.keys() else None) or cfg["username"],
                "username": cfg["username"], "volume_gb": cfg["volume_gb"], "duration_days": cfg["duration_days"],
                "expires_at": cfg["expires_at"], "panel_server_id": cfg["panel_server_id"],
            }, ensure_ascii=False)
            cur = conn.execute(
                "INSERT INTO gift_cards (code_hash, code_hint, code_plain, kind, custom_config_id, config_snapshot, sender_id, "
                "recipient_name, message, theme_id, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (gift_code_hash(code), code[-4:], code, "config", custom_config_id, snapshot, sender_id,
                 (recipient_name or "")[:60], (message or "")[:400], theme_id, expires_at),
            )
            card_id = cur.lastrowid
            moved = conn.execute(
                "UPDATE custom_configs SET user_id=? WHERE id=? AND user_id=?", (-card_id, custom_config_id, sender_id)
            )
            if moved.rowcount != 1:
                raise RuntimeError("gift escrow failed")
            conn.execute(
                "INSERT INTO custom_config_history (custom_config_id, event_type, detail) VALUES (?,?,?)",
                (custom_config_id, "gift_escrow", f"card #{card_id} از {sender_id}"),
            )
            self._gift_log(conn, card_id, "created", sender_id, f"config #{custom_config_id}")
        return {"ok": True, "id": card_id, "code": code, "fee": 0, "expires_at": expires_at}

    def gift_create_stock_config_card(self, sender_id: int, stock_config_id: int, theme_id: int, recipient_name: str,
                                      message: str, expires_days: int, max_open: int = 0, title: str = None) -> dict:
        """کانفیگ بانک کانفیگ (جدول configs) که به sender تخصیص یافته را وارد escrow کارت می‌کند."""
        self._ensure_gift_tables()
        code = generate_gift_code()
        expires_at = (datetime.utcnow() + timedelta(days=max(int(expires_days), 1))).isoformat()
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if max_open and conn.execute(
                "SELECT COUNT(*) FROM gift_cards WHERE sender_id=? AND status IN ('active','disabled')", (sender_id,)
            ).fetchone()[0] >= max_open:
                return {"ok": False, "error": "limit"}
            cfg = conn.execute(
                "SELECT c.id, c.expires_at, p.name AS product_name FROM configs c "
                "LEFT JOIN products p ON p.id=c.product_id WHERE c.id=? AND c.assigned_user_id=? AND c.is_used=1",
                (stock_config_id, sender_id),
            ).fetchone()
            if not cfg:
                return {"ok": False, "error": "config"}
            snapshot = json.dumps({
                "name": title or cfg["product_name"] or "کانفیگ", "expires_at": cfg["expires_at"],
            }, ensure_ascii=False)
            cur = conn.execute(
                "INSERT INTO gift_cards (code_hash, code_hint, code_plain, kind, stock_config_id, config_snapshot, "
                "sender_id, recipient_name, message, theme_id, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (gift_code_hash(code), code[-4:], code, "stock", stock_config_id, snapshot, sender_id,
                 (recipient_name or "")[:60], (message or "")[:400], theme_id, expires_at),
            )
            card_id = cur.lastrowid
            moved = conn.execute(
                "UPDATE configs SET assigned_user_id=? WHERE id=? AND assigned_user_id=?",
                (-card_id, stock_config_id, sender_id),
            )
            if moved.rowcount != 1:
                raise RuntimeError("gift stock escrow failed")
            self._gift_log(conn, card_id, "created", sender_id, f"stock config #{stock_config_id}")
        return {"ok": True, "id": card_id, "code": code, "fee": 0, "expires_at": expires_at}

    def gift_stock_link(self, stock_config_id: int) -> str:
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            row = conn.execute("SELECT link FROM configs WHERE id=?", (stock_config_id,)).fetchone()
        return row["link"] if row else ""

    def gift_set_image(self, card_id: int, image_path: str):
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            conn.execute("UPDATE gift_cards SET image_path=? WHERE id=?", (image_path, card_id))

    def gift_set_text(self, card_id: int, message: str = None, headline: str = None):
        self._ensure_gift_tables()
        sets, args = [], []
        if message is not None:
            sets.append("message=?")
            args.append(message[:400])
        if headline is not None:
            sets.append("headline=?")
            args.append(headline[:60])
        if not sets:
            return
        with self._get_conn() as conn:
            conn.execute(f"UPDATE gift_cards SET {', '.join(sets)} WHERE id=?", (*args, card_id))

    def gift_redeem(self, code: str, receiver_id: int) -> dict:
        self._ensure_gift_tables()
        now = _now()
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            card = conn.execute("SELECT * FROM gift_cards WHERE code_hash=?", (gift_code_hash(code),)).fetchone()
            if not card:
                self._gift_log(conn, None, "bad_code", receiver_id, "unknown code")
                return {"ok": False, "error": "not_found"}
            if card["status"] != "active":
                return {"ok": False, "error": card["status"], "card": dict(card)}
            if card["expires_at"] and card["expires_at"] <= now:
                return {"ok": False, "error": "expired", "card": dict(card)}
            if card["sender_id"] == receiver_id:
                return {"ok": False, "error": "self", "card": dict(card)}
            user = conn.execute(
                "SELECT telegram_id, is_blocked FROM users WHERE telegram_id=?", (receiver_id,)
            ).fetchone()
            if not user or user["is_blocked"]:
                return {"ok": False, "error": "receiver", "card": dict(card)}
            if card["kind"] == "wallet":
                with _wallet_tag(conn, receiver_id, "gift_card_in", f"دریافت کارت هدیه #{card['id']}"):
                    conn.execute(
                        "UPDATE users SET referral_credit=referral_credit+? WHERE telegram_id=?",
                        (card["amount"], receiver_id),
                    )
            elif card["kind"] == "stock":
                moved = conn.execute(
                    "UPDATE configs SET assigned_user_id=? WHERE id=? AND assigned_user_id=?",
                    (receiver_id, card["stock_config_id"], -card["id"]),
                )
                if moved.rowcount != 1:
                    return {"ok": False, "error": "escrow_lost", "card": dict(card)}
            else:
                moved = conn.execute(
                    "UPDATE custom_configs SET user_id=? WHERE id=? AND user_id=?",
                    (receiver_id, card["custom_config_id"], -card["id"]),
                )
                if moved.rowcount != 1:
                    return {"ok": False, "error": "escrow_lost", "card": dict(card)}
                conn.execute(
                    "INSERT INTO custom_config_history (custom_config_id, event_type, detail) VALUES (?,?,?)",
                    (card["custom_config_id"], "gift_redeem", f"card #{card['id']} به {receiver_id}"),
                )
            conn.execute(
                "UPDATE gift_cards SET status='redeemed', code_plain=NULL, redeemed_by=?, redeemed_at=? WHERE id=?",
                (receiver_id, now, card["id"]),
            )
            self._gift_log(conn, card["id"], "redeemed", receiver_id, None)
            fresh = conn.execute("SELECT * FROM gift_cards WHERE id=?", (card["id"],)).fetchone()
        return {"ok": True, "card": dict(fresh)}

    def _gift_release_in_conn(self, conn, card, new_status: str, actor_id, reason: str) -> bool:
        if card["kind"] == "wallet":
            refund = int(card["amount"]) + (int(card["fee"] or 0) if new_status == "revoked" else 0)
            with _wallet_tag(conn, card["sender_id"], "gift_card_refund", f"بازگشت کارت هدیه #{card['id']}"):
                conn.execute(
                    "UPDATE users SET referral_credit=referral_credit+? WHERE telegram_id=?",
                    (refund, card["sender_id"]),
                )
        elif card["kind"] == "stock":
            back = conn.execute(
                "UPDATE configs SET assigned_user_id=? WHERE id=? AND assigned_user_id=?",
                (card["sender_id"], card["stock_config_id"], -card["id"]),
            )
            if back.rowcount != 1:
                return False
        else:
            back = conn.execute(
                "UPDATE custom_configs SET user_id=? WHERE id=? AND user_id=?",
                (card["sender_id"], card["custom_config_id"], -card["id"]),
            )
            if back.rowcount != 1:
                return False
            conn.execute(
                "INSERT INTO custom_config_history (custom_config_id, event_type, detail) VALUES (?,?,?)",
                (card["custom_config_id"], "gift_return", f"card #{card['id']} {new_status}"),
            )
        conn.execute(
            "UPDATE gift_cards SET status=?, code_plain=NULL, closed_by=?, closed_reason=?, closed_at=? WHERE id=?",
            (new_status, actor_id, (reason or "")[:200], _now(), card["id"]),
        )
        self._gift_log(conn, card["id"], new_status, actor_id, reason)
        return True

    def gift_release(self, card_id: int, new_status: str, actor_id: int = None, reason: str = "",
                     only_sender: int = None) -> dict:
        """ابطال/لغو: مبلغ یا کانفیگ به فرستنده برمی‌گردد. new_status: revoked | cancelled | expired."""
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            card = conn.execute("SELECT * FROM gift_cards WHERE id=?", (card_id,)).fetchone()
            if not card:
                return {"ok": False, "error": "not_found"}
            if only_sender is not None and card["sender_id"] != only_sender:
                return {"ok": False, "error": "not_found"}
            if card["status"] not in _OPEN_STATUSES:
                return {"ok": False, "error": card["status"], "card": dict(card)}
            if not self._gift_release_in_conn(conn, card, new_status, actor_id, reason):
                return {"ok": False, "error": "escrow_lost", "card": dict(card)}
        return {"ok": True, "card": dict(card)}

    def gift_set_enabled(self, card_id: int, enabled: bool, actor_id: int = None) -> bool:
        self._ensure_gift_tables()
        src, dst = ("disabled", "active") if enabled else ("active", "disabled")
        with self._get_conn() as conn:
            cur = conn.execute("UPDATE gift_cards SET status=? WHERE id=? AND status=?", (dst, card_id, src))
            if cur.rowcount:
                self._gift_log(conn, card_id, "enabled" if enabled else "disabled", actor_id, None)
            return cur.rowcount > 0

    def gift_expire_due(self) -> list:
        """کارت‌های سررسیدشده را منقضی و به فرستنده برمی‌گرداند؛ لیست کارت‌های برگشت‌خورده."""
        self._ensure_gift_tables()
        released = []
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            due = conn.execute(
                "SELECT * FROM gift_cards WHERE status IN ('active','disabled') AND expires_at IS NOT NULL AND expires_at<=?",
                (_now(),),
            ).fetchall()
            for card in due:
                if self._gift_release_in_conn(conn, card, "expired", None, "expired"):
                    released.append(dict(card))
        return released

    def gift_set_intent(self, user_id: int, theme_id: int, recipient_name: str, message: str, expires_days: int):
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO gift_intents (user_id, theme_id, recipient_name, message, expires_days, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, theme_id, (recipient_name or "")[:60], (message or "")[:400], int(expires_days), _now()),
            )

    def gift_get_intent(self, user_id: int, ttl_minutes: int = 60):
        self._ensure_gift_tables()
        limit = (datetime.utcnow() - timedelta(minutes=ttl_minutes)).isoformat()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM gift_intents WHERE user_id=? AND created_at>=?", (user_id, limit)
            ).fetchone()

    def gift_clear_intent(self, user_id: int):
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            conn.execute("DELETE FROM gift_intents WHERE user_id=?", (user_id,))

    def gift_register_order(self, user_id: int, order_id: int, ttl_minutes: int = 60) -> bool:
        """اگر کاربر قصد هدیه‌دادن دارد، این سفارش را «سفارش هدیه» ثبت می‌کند و intent را مصرف می‌کند."""
        self._ensure_gift_tables()
        limit = (datetime.utcnow() - timedelta(minutes=ttl_minutes)).isoformat()
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            intent = conn.execute(
                "SELECT * FROM gift_intents WHERE user_id=? AND created_at>=?", (user_id, limit)
            ).fetchone()
            if not intent:
                return False
            conn.execute(
                "INSERT OR IGNORE INTO gift_orders (order_id, user_id, theme_id, recipient_name, message, expires_days) "
                "VALUES (?,?,?,?,?,?)",
                (order_id, user_id, intent["theme_id"], intent["recipient_name"], intent["message"], intent["expires_days"]),
            )
            conn.execute("DELETE FROM gift_intents WHERE user_id=?", (user_id,))
            self._gift_log(conn, None, "order_marked", user_id, f"order #{order_id}")
        return True

    def gift_get_order(self, order_id: int):
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM gift_orders WHERE order_id=?", (order_id,)).fetchone()

    def gift_attach_order(self, order_id: int, item_id: int, buyer_id: int, kind: str = "custom"):
        """بعد از ساخت/تخصیص کانفیگِ یک سفارش هدیه، همان کانفیگ را وارد کارت (escrow) می‌کند.
        kind: custom (جدول custom_configs: کانفیگ شخصی و محصولات متصل به پنل) | stock (بانک کانفیگ)."""
        self._ensure_gift_tables()
        order = self.gift_get_order(order_id)
        if not order or order["card_id"] or order["user_id"] != buyer_id:
            return None
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT p.name FROM orders o LEFT JOIN products p ON p.id=o.product_id WHERE o.id=?", (order_id,)
            ).fetchone()
        title = row["name"] if row and row["name"] else None
        make = self.gift_create_stock_config_card if kind == "stock" else self.gift_create_config_card
        result = make(
            buyer_id, item_id, order["theme_id"], order["recipient_name"], order["message"],
            order["expires_days"], title=title,
        )
        if not result["ok"]:
            return None
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE gift_orders SET card_id=? WHERE order_id=? AND card_id IS NULL", (result["id"], order_id)
            )
            self._gift_log(conn, result["id"], "bought_as_gift", buyer_id, f"order #{order_id}")
        return result["id"]

    def gift_mark_order_delivered(self, order_id: int) -> bool:
        self._ensure_gift_tables()
        with self._get_conn() as conn:
            cur = conn.execute("UPDATE gift_orders SET delivered=1 WHERE order_id=? AND delivered=0", (order_id,))
            return cur.rowcount > 0
