# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""صندوق‌های شانس (Loot Box)، استریک روزانه و نقاط عطف پیشرفت کاربر."""

import json
import random
import secrets
from datetime import datetime, timedelta

from .constants import _wallet_tag

_FMT = "%Y-%m-%d %H:%M:%S"
_BOX_TYPE_EDITABLE = {"title", "emoji", "price", "is_active"}
_MILESTONE_EDITABLE = {"title", "emoji", "wallet", "discount", "box_type_id", "is_active"}
_DEFAULT_MILESTONES = (
    ("streak", 3, "🔥", "استریک ۳ روزه"), ("streak", 7, "🔥", "استریک ۷ روزه"),
    ("streak", 14, "🔥", "استریک ۱۴ روزه"), ("streak", 30, "🔥", "استریک ۳۰ روزه"),
    ("streak", 100, "🔥", "استریک ۱۰۰ روزه"),
    ("referral", 1, "🤝", "اولین دعوت موفق"), ("referral", 5, "🤝", "۵ دعوت موفق"),
    ("referral", 10, "🤝", "۱۰ دعوت موفق"), ("referral", 25, "🤝", "۲۵ دعوت موفق"),
    ("renewal", 1, "🔄", "اولین تمدید"), ("renewal", 5, "🔄", "۵ تمدید"), ("renewal", 10, "🔄", "۱۰ تمدید"),
)


def _now() -> str:
    return datetime.utcnow().strftime(_FMT)


def _insert_code(conn, prefix: str, percent: int, valid_days: int, source: str, product_id: int = None) -> str:
    code = f"{prefix}{secrets.token_hex(4).upper()}"
    expires_at = (datetime.utcnow() + timedelta(days=max(1, int(valid_days)))).isoformat()
    conn.execute(
        "INSERT INTO discount_codes (code, percent, max_uses, expires_at, source, per_user_limit, product_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (code, int(percent), 1, expires_at, source, 1, product_id or None),
    )
    return code


def _credit_wallet(conn, user_id: int, amount: int, kind: str, note: str):
    with _wallet_tag(conn, user_id, kind, note):
        conn.execute(
            "UPDATE users SET referral_credit = MAX(referral_credit + ?, MIN(referral_credit, 0)) WHERE telegram_id=?",
            (int(amount), int(user_id)),
        )


class EngagementMixin:
    def _ensure_engage_tables(self):
        if getattr(self, "_engage_tables_ready", False):
            return
        self._ensure_season_tables()
        with self._get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS lootbox_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    emoji TEXT DEFAULT '📦',
                    price INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS lootbox_prizes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    value INTEGER NOT NULL DEFAULT 0,
                    product_id INTEGER NOT NULL DEFAULT 0,
                    chance REAL NOT NULL DEFAULT 0,
                    is_active INTEGER DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_lootbox_prizes_type ON lootbox_prizes(type_id);
                CREATE TABLE IF NOT EXISTS user_lootboxes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    type_id INTEGER NOT NULL,
                    type_title TEXT,
                    emoji TEXT,
                    source TEXT NOT NULL DEFAULT 'purchase',
                    price_paid INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'sealed',
                    prize_kind TEXT,
                    prize_title TEXT,
                    prize_value INTEGER,
                    prize_code TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    opened_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_user_lootboxes_user ON user_lootboxes(user_id, status);
                CREATE TABLE IF NOT EXISTS user_streaks (
                    user_id INTEGER PRIMARY KEY,
                    current INTEGER NOT NULL DEFAULT 0,
                    best INTEGER NOT NULL DEFAULT 0,
                    last_day TEXT
                );
                CREATE TABLE IF NOT EXISTS streak_milestones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track TEXT NOT NULL,
                    threshold INTEGER NOT NULL,
                    emoji TEXT DEFAULT '🏅',
                    title TEXT NOT NULL,
                    wallet INTEGER NOT NULL DEFAULT 0,
                    discount INTEGER NOT NULL DEFAULT 0,
                    box_type_id INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    UNIQUE(track, threshold)
                );
                CREATE TABLE IF NOT EXISTS user_milestones (
                    user_id INTEGER NOT NULL,
                    milestone_id INTEGER NOT NULL,
                    granted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, milestone_id)
                );
                """
            )
            # روش‌های پرداخت مجاز هر نوع صندوق (JSON آرایه‌ی کلیدها؛ NULL = همه‌ی روش‌ها) - مثل products.payment_methods
            if not self._column_exists(conn, "lootbox_types", "payment_methods"):
                conn.execute("ALTER TABLE lootbox_types ADD COLUMN payment_methods TEXT")
            seeded = conn.execute("SELECT value FROM settings WHERE key='streak_milestones_seeded'").fetchone()
            if not seeded:
                for track, threshold, emoji, title in _DEFAULT_MILESTONES:
                    conn.execute(
                        "INSERT OR IGNORE INTO streak_milestones (track, threshold, emoji, title) VALUES (?,?,?,?)",
                        (track, threshold, emoji, title),
                    )
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('streak_milestones_seeded', '1')")
        self._engage_tables_ready = True

    # ------------------------------------------------------------------
    # انواع صندوق و جوایز
    # ------------------------------------------------------------------

    def box_type_list(self, active_only: bool = False):
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM lootbox_types" + (" WHERE is_active=1" if active_only else "") + " ORDER BY id"
            ).fetchall()

    def box_type_get(self, type_id: int):
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM lootbox_types WHERE id=?", (int(type_id),)).fetchone()

    def box_type_add(self, title: str, emoji: str, price: int) -> int:
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "INSERT INTO lootbox_types (title, emoji, price) VALUES (?,?,?)", (title, emoji or "📦", int(price))
            ).lastrowid

    def box_type_update(self, type_id: int, **fields):
        sets = {k: v for k, v in fields.items() if k in _BOX_TYPE_EDITABLE}
        if not sets:
            return
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE lootbox_types SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", [*sets.values(), int(type_id)]
            )

    def box_prize_list(self, type_id: int):
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM lootbox_prizes WHERE type_id=? AND is_active=1 ORDER BY id", (int(type_id),)
            ).fetchall()

    def box_prize_get(self, prize_id: int):
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM lootbox_prizes WHERE id=?", (int(prize_id),)).fetchone()

    def box_prize_add(self, type_id: int, kind: str, title: str, value: int, product_id: int, chance: float) -> int:
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "INSERT INTO lootbox_prizes (type_id, kind, title, value, product_id, chance) VALUES (?,?,?,?,?,?)",
                (int(type_id), kind, title, int(value), int(product_id or 0), float(chance)),
            ).lastrowid

    def box_prize_delete(self, prize_id: int):
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            conn.execute("UPDATE lootbox_prizes SET is_active=0 WHERE id=?", (int(prize_id),))

    def box_chance_total(self, type_id: int) -> float:
        return round(sum(float(p["chance"]) for p in self.box_prize_list(type_id)), 4)

    # ------------------------------------------------------------------
    # خرید، اهدا و باز کردن
    # ------------------------------------------------------------------

    def box_buy(self, user_id: int, type_id: int, daily_limit: int = 0) -> dict:
        """خرید اتمیک صندوق با کیف پول؛ سقف خرید روزانه (۰ = نامحدود)."""
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            box_type = conn.execute("SELECT * FROM lootbox_types WHERE id=? AND is_active=1", (int(type_id),)).fetchone()
            if not box_type or box_type["price"] <= 0:
                return {"ok": False, "error": "unavailable"}
            if daily_limit:
                bought = conn.execute(
                    "SELECT COUNT(*) c FROM user_lootboxes WHERE user_id=? AND source='purchase' AND date(created_at)=date('now')",
                    (int(user_id),),
                ).fetchone()["c"]
                if bought >= int(daily_limit):
                    return {"ok": False, "error": "limit"}
            with _wallet_tag(conn, user_id, "lootbox_buy", f"خرید {box_type['title']}"):
                cur = conn.execute(
                    "UPDATE users SET referral_credit=referral_credit-? WHERE telegram_id=? AND referral_credit>=?",
                    (box_type["price"], int(user_id), box_type["price"]),
                )
            if cur.rowcount != 1:
                return {"ok": False, "error": "balance"}
            box_id = conn.execute(
                "INSERT INTO user_lootboxes (user_id, type_id, type_title, emoji, source, price_paid) VALUES (?,?,?,?,?,?)",
                (int(user_id), box_type["id"], box_type["title"], box_type["emoji"], "purchase", box_type["price"]),
            ).lastrowid
            return {"ok": True, "box_id": box_id, "box_type": box_type}

    def box_grant(self, user_id: int, type_id: int, source: str = "admin", count: int = 1) -> int:
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            box_type = conn.execute("SELECT * FROM lootbox_types WHERE id=?", (int(type_id),)).fetchone()
            if not box_type or not conn.execute("SELECT 1 FROM users WHERE telegram_id=?", (int(user_id),)).fetchone():
                return 0
            for _ in range(max(1, int(count))):
                conn.execute(
                    "INSERT INTO user_lootboxes (user_id, type_id, type_title, emoji, source) VALUES (?,?,?,?,?)",
                    (int(user_id), box_type["id"], box_type["title"], box_type["emoji"], source),
                )
            return max(1, int(count))

    # ------------------------------------------------------------------
    # خرید صندوق مثل یک محصول: روش‌های پرداخت + سفارش + تحویل بعد از پرداخت
    # ------------------------------------------------------------------

    def box_type_payment_methods(self, type_id: int):
        """None = همه‌ی روش‌های پرداخت برای این صندوق مجازند؛ وگرنه لیست کلیدهای مجاز."""
        row = self.box_type_get(type_id)
        if not row:
            return None
        raw = row["payment_methods"] if "payment_methods" in row.keys() else None
        if not raw:
            return None
        try:
            methods = json.loads(raw)
        except Exception:
            return None
        return methods or None

    def box_type_set_payment_methods(self, type_id: int, methods):
        """methods=None یا [] یعنی «همه‌ی روش‌ها مجاز» (حذف محدودیت)."""
        self._ensure_engage_tables()
        value = json.dumps(list(methods), ensure_ascii=False) if methods else None
        with self._get_conn() as conn:
            conn.execute("UPDATE lootbox_types SET payment_methods=? WHERE id=?", (value, int(type_id)))

    def box_type_allows_payment_method(self, type_id: int, method_key: str) -> bool:
        allowed = self.box_type_payment_methods(type_id)
        return allowed is None or method_key in allowed

    def box_bought_today(self, user_id: int) -> int:
        """تعداد صندوق‌های خریداری‌شده‌ی امروز + صندوق‌های سفارش‌های در انتظار پرداخت (برای سقف روزانه)."""
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            bought = conn.execute(
                "SELECT COUNT(*) c FROM user_lootboxes WHERE user_id=? AND source='purchase' AND date(created_at)=date('now')",
                (int(user_id),),
            ).fetchone()["c"]
            pending = conn.execute(
                "SELECT COALESCE(SUM(quantity),0) s FROM orders WHERE user_id=? AND lootbox_type_id>0 "
                "AND status IN ('pending','processing') AND date(created_at)=date('now')",
                (int(user_id),),
            ).fetchone()["s"]
            return int(bought) + int(pending or 0)

    def create_lootbox_order(self, user_tg_id: int, type_id: int, quantity: int, base_price: int, wallet_used: int = 0) -> int:
        """سفارش «خرید صندوق شانس» - از همان جدول orders با product_id=0 سنتینل (مثل تمدید) تا
        کارت‌به‌کارت/کیف‌پول/کریپتو/درگاه‌ها بدون تغییر کار کنند."""
        quantity = max(1, int(quantity))
        final_price = max(int(base_price) - int(wallet_used), 0)
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO orders (user_id, product_id, status, base_price, wallet_used, final_price, "
                "quantity, lootbox_type_id) VALUES (?, 0, 'pending', ?, ?, ?, ?, ?)",
                (int(user_tg_id), int(base_price), int(wallet_used), final_price, quantity, int(type_id)),
            )
            return cur.lastrowid

    def approve_lootbox_order(self, order_id: int):
        """تایید اتمیک سفارش صندوق و تحویل همان لحظه‌ی صندوق‌ها (فقط یک‌بار؛ pending/processing).
        خروجی: لیست شناسه‌ی صندوق‌های ساخته‌شده، یا None اگر سفارش معتبر/در انتظار نبود."""
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM orders WHERE id=?", (int(order_id),)).fetchone()
            if not row or not row["lootbox_type_id"]:
                return None
            cur = conn.execute(
                "UPDATE orders SET status='approved', updated_at=? WHERE id=? AND status IN ('pending','processing')",
                (datetime.utcnow().isoformat(), int(order_id)),
            )
            if cur.rowcount == 0:
                return None
            self._mark_receipt_decision(conn, "order", int(order_id), "approved")
            box_type = conn.execute("SELECT * FROM lootbox_types WHERE id=?", (int(row["lootbox_type_id"]),)).fetchone()
            quantity = max(1, int(row["quantity"] or 1))
            unit_price = int(row["base_price"] or 0) // quantity
            title = box_type["title"] if box_type else ""
            emoji = box_type["emoji"] if box_type else "📦"
            ids = []
            for _ in range(quantity):
                ids.append(conn.execute(
                    "INSERT INTO user_lootboxes (user_id, type_id, type_title, emoji, source, price_paid) VALUES (?,?,?,?,?,?)",
                    (int(row["user_id"]), int(row["lootbox_type_id"]), title, emoji, "purchase", unit_price),
                ).lastrowid)
            return ids

    def box_list_user(self, user_id: int):
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT type_id, type_title, emoji, COUNT(*) AS n, MIN(id) AS first_id FROM user_lootboxes "
                "WHERE user_id=? AND status='sealed' GROUP BY type_id ORDER BY type_id",
                (int(user_id),),
            ).fetchall()

    def box_history(self, user_id: int, limit: int = 8):
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM user_lootboxes WHERE user_id=? AND status='opened' ORDER BY id DESC LIMIT ?",
                (int(user_id), int(limit)),
            ).fetchall()

    def box_open(self, box_id: int, user_id: int, valid_days: int) -> dict:
        """باز کردن اتمیک: انتخاب جایزه و پرداخت آن در همان تراکنش؛ هر صندوق فقط یک‌بار."""
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE user_lootboxes SET status='opened', opened_at=? WHERE id=? AND user_id=? AND status='sealed'",
                (_now(), int(box_id), int(user_id)),
            )
            if cur.rowcount != 1:
                return {"ok": False, "error": "not_found"}
            box = conn.execute("SELECT * FROM user_lootboxes WHERE id=?", (int(box_id),)).fetchone()
            prizes = conn.execute(
                "SELECT * FROM lootbox_prizes WHERE type_id=? AND is_active=1 AND chance>0 ORDER BY id", (box["type_id"],)
            ).fetchall()
            roll, cumulative, prize = random.SystemRandom().uniform(0, 100), 0.0, None
            for row in prizes:
                cumulative += float(row["chance"])
                if roll < cumulative:
                    prize = row
                    break
            kind, title, value, code = "empty", "پوچ", 0, None
            if prize:
                kind, title, value = prize["kind"], prize["title"], int(prize["value"])
                if kind == "wallet":
                    _credit_wallet(conn, user_id, value, "lootbox_prize", f"جایزه‌ی {box['type_title']}")
                elif kind == "discount":
                    code = _insert_code(conn, "BOX", value, valid_days, "lootbox_prize")
                elif kind == "product":
                    value = 100
                    code = _insert_code(conn, "BOXFREE", 100, valid_days, "lootbox_prize", prize["product_id"])
            conn.execute(
                "UPDATE user_lootboxes SET prize_kind=?, prize_title=?, prize_value=?, prize_code=? WHERE id=?",
                (kind, title, value, code, int(box_id)),
            )
            return {"ok": True, "kind": kind, "title": title, "value": value, "code": code, "box": box}

    def box_stats(self) -> dict:
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN status='sealed' THEN 1 ELSE 0 END),0) AS sealed, "
                "COALESCE(SUM(CASE WHEN status='opened' THEN 1 ELSE 0 END),0) AS opened, "
                "COALESCE(SUM(price_paid),0) AS revenue, "
                "COALESCE(SUM(CASE WHEN prize_kind='wallet' THEN prize_value ELSE 0 END),0) AS wallet_paid "
                "FROM user_lootboxes"
            ).fetchone()
            return dict(row)

    # ------------------------------------------------------------------
    # استریک و نقاط عطف
    # ------------------------------------------------------------------

    def streak_touch(self, user_id: int, today: str, yesterday: str) -> dict:
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM user_streaks WHERE user_id=?", (int(user_id),)).fetchone()
            if row and row["last_day"] == today:
                return {"changed": False, "current": row["current"], "best": row["best"]}
            current = (row["current"] + 1) if row and row["last_day"] == yesterday else 1
            best = max(current, row["best"] if row else 0)
            conn.execute(
                "INSERT INTO user_streaks (user_id, current, best, last_day) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET current=excluded.current, best=excluded.best, last_day=excluded.last_day",
                (int(user_id), current, best, today),
            )
            return {"changed": True, "current": current, "best": best}

    def streak_get(self, user_id: int) -> dict:
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM user_streaks WHERE user_id=?", (int(user_id),)).fetchone()
        return {"current": row["current"], "best": row["best"], "last_day": row["last_day"]} if row else {"current": 0, "best": 0, "last_day": None}

    def progress_values(self, user_id: int) -> dict:
        streak = self.streak_get(user_id)
        with self._get_conn() as conn:
            referral = conn.execute("SELECT COUNT(*) c FROM users WHERE referred_by=?", (int(user_id),)).fetchone()["c"]
            renewal = conn.execute(
                "SELECT COUNT(*) c FROM orders WHERE user_id=? AND status='approved' AND COALESCE(is_renewal,0)=1",
                (int(user_id),),
            ).fetchone()["c"]
        return {"streak": max(streak["current"], streak["best"]), "streak_current": streak["current"],
                "streak_best": streak["best"], "referral": referral, "renewal": renewal}

    def milestone_list(self, track: str = None, active_only: bool = False):
        self._ensure_engage_tables()
        clauses, params = [], []
        if track:
            clauses.append("track=?"); params.append(track)
        if active_only:
            clauses.append("is_active=1")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._get_conn() as conn:
            return conn.execute(f"SELECT * FROM streak_milestones {where} ORDER BY track, threshold", params).fetchall()

    def milestone_get(self, milestone_id: int):
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM streak_milestones WHERE id=?", (int(milestone_id),)).fetchone()

    def milestone_add(self, track: str, threshold: int, emoji: str, title: str):
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO streak_milestones (track, threshold, emoji, title) VALUES (?,?,?,?)",
                (track, int(threshold), emoji, title),
            )
            return cur.lastrowid if cur.rowcount else None

    def milestone_update(self, milestone_id: int, **fields):
        sets = {k: v for k, v in fields.items() if k in _MILESTONE_EDITABLE}
        if not sets:
            return
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE streak_milestones SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", [*sets.values(), int(milestone_id)]
            )

    def milestone_delete(self, milestone_id: int):
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            conn.execute("DELETE FROM streak_milestones WHERE id=?", (int(milestone_id),))

    def milestone_pending(self, user_id: int) -> list:
        values = self.progress_values(user_id)
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT m.* FROM streak_milestones m WHERE m.is_active=1 AND NOT EXISTS "
                "(SELECT 1 FROM user_milestones u WHERE u.user_id=? AND u.milestone_id=m.id) ORDER BY m.track, m.threshold",
                (int(user_id),),
            ).fetchall()
        return [m for m in rows if values.get(m["track"], 0) >= m["threshold"]]

    def milestone_claim(self, user_id: int, milestone_id: int, valid_days: int):
        """اهدای اتمیک نقطه‌ی عطف (نشان و جایزه‌ها)؛ اگر قبلاً گرفته شده باشد None."""
        self._ensure_engage_tables()
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            milestone = conn.execute("SELECT * FROM streak_milestones WHERE id=?", (int(milestone_id),)).fetchone()
            if not milestone:
                return None
            cur = conn.execute(
                "INSERT OR IGNORE INTO user_milestones (user_id, milestone_id) VALUES (?,?)", (int(user_id), int(milestone_id))
            )
            if not cur.rowcount:
                return None
            conn.execute(
                "INSERT OR IGNORE INTO user_badges (user_id, season_id, code, emoji, title) VALUES (?,?,?,?,?)",
                (int(user_id), 0, f"milestone_{milestone['id']}", milestone["emoji"], milestone["title"]),
            )
            result = {"milestone": milestone, "wallet": 0, "code": None, "box": None}
            if milestone["wallet"] > 0:
                _credit_wallet(conn, user_id, milestone["wallet"], "milestone_prize", f"جایزه‌ی «{milestone['title']}»")
                result["wallet"] = milestone["wallet"]
            if milestone["discount"] > 0:
                result["code"] = _insert_code(conn, "MS", milestone["discount"], valid_days, "milestone_prize")
            if milestone["box_type_id"] > 0:
                box_type = conn.execute("SELECT * FROM lootbox_types WHERE id=?", (milestone["box_type_id"],)).fetchone()
                if box_type:
                    conn.execute(
                        "INSERT INTO user_lootboxes (user_id, type_id, type_title, emoji, source) VALUES (?,?,?,?,?)",
                        (int(user_id), box_type["id"], box_type["title"], box_type["emoji"], "streak"),
                    )
                    result["box"] = box_type["title"]
            return result
