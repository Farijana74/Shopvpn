# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""فصل‌های رقابتی زیرمجموعه‌گیری، نشان‌ها و کوییز روزانه‌ی کانال."""

import json
import secrets
from datetime import datetime, timedelta

_FMT = "%Y-%m-%d %H:%M:%S"
_TOP_STORED = 10


def _now() -> str:
    return datetime.utcnow().strftime(_FMT)


class SeasonsMixin:
    def _ensure_season_tables(self):
        if getattr(self, "_season_tables_ready", False):
            return
        with self._get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ref_seasons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    invite_points INTEGER NOT NULL DEFAULT 1,
                    purchase_points INTEGER NOT NULL DEFAULT 5,
                    prizes_json TEXT,
                    announced INTEGER DEFAULT 0,
                    closed_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS ref_season_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    season_id INTEGER NOT NULL,
                    rank INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    invites INTEGER NOT NULL DEFAULT 0,
                    buyers INTEGER NOT NULL DEFAULT 0,
                    prize_wallet INTEGER NOT NULL DEFAULT 0,
                    prize_discount INTEGER NOT NULL DEFAULT 0,
                    prize_product INTEGER NOT NULL DEFAULT 0,
                    prize_code TEXT,
                    award_state TEXT NOT NULL DEFAULT 'pending',
                    awarded_at TEXT,
                    UNIQUE(season_id, rank),
                    UNIQUE(season_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS user_badges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    season_id INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    emoji TEXT,
                    title TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, season_id, code)
                );
                CREATE INDEX IF NOT EXISTS idx_user_badges_user ON user_badges(user_id);
                CREATE TABLE IF NOT EXISTS daily_quiz_bank (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    correct_index INTEGER NOT NULL,
                    explanation TEXT DEFAULT '',
                    source TEXT DEFAULT 'manual',
                    is_active INTEGER DEFAULT 1,
                    used_count INTEGER DEFAULT 0,
                    last_used_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS daily_quizzes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bank_id INTEGER,
                    question TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    correct_index INTEGER NOT NULL,
                    explanation TEXT DEFAULT '',
                    channel_id TEXT,
                    message_id INTEGER,
                    winner_limit INTEGER NOT NULL DEFAULT 3,
                    discount_percent INTEGER NOT NULL DEFAULT 10,
                    code_valid_hours INTEGER NOT NULL DEFAULT 48,
                    status TEXT NOT NULL DEFAULT 'open',
                    posted_at TEXT NOT NULL,
                    closes_at TEXT NOT NULL,
                    closed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS daily_quiz_answers (
                    quiz_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    choice INTEGER NOT NULL,
                    is_correct INTEGER NOT NULL DEFAULT 0,
                    win_rank INTEGER,
                    code TEXT,
                    answered_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (quiz_id, user_id)
                );
                """
            )
            cols = [r[1] for r in conn.execute("PRAGMA table_info(ref_season_results)").fetchall()]
            if "prize_product" not in cols:
                conn.execute("ALTER TABLE ref_season_results ADD COLUMN prize_product INTEGER NOT NULL DEFAULT 0")
        self._season_tables_ready = True

    # ------------------------------------------------------------------
    # فصل‌ها
    # ------------------------------------------------------------------

    def season_get_active(self):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM ref_seasons WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def season_get(self, season_id: int):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM ref_seasons WHERE id=?", (int(season_id),)).fetchone()

    def season_list_closed(self, limit: int = 10):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM ref_seasons WHERE status='closed' ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()

    def season_start(self, title: str, starts_at: str, ends_at: str, invite_points: int, purchase_points: int):
        """شروع فصل تازه؛ اگر فصل فعالی وجود داشته باشد None برمی‌گرداند."""
        self._ensure_season_tables()
        with self._get_conn() as conn:
            if conn.execute("SELECT 1 FROM ref_seasons WHERE status='active'").fetchone():
                return None
            cur = conn.execute(
                "INSERT INTO ref_seasons (title, starts_at, ends_at, invite_points, purchase_points) VALUES (?,?,?,?,?)",
                (title, starts_at, ends_at, int(invite_points), int(purchase_points)),
            )
            return cur.lastrowid

    @staticmethod
    def _season_standings_conn(conn, season, limit: int):
        rows = conn.execute(
            """
            SELECT r.telegram_id AS user_id, COALESCE(r.first_name,'') AS first_name,
                   COALESCE(r.username,'') AS username, s.invites AS invites, s.buyers AS buyers,
                   s.invites*? + s.buyers*? AS score
            FROM (
                SELECT u.referred_by AS rid, COUNT(*) AS invites,
                       SUM(EXISTS(SELECT 1 FROM orders o WHERE o.user_id=u.telegram_id AND o.status='approved'
                                  AND o.created_at>=? AND o.created_at<?)) AS buyers
                FROM users u
                WHERE u.referred_by IS NOT NULL AND u.joined_at>=? AND u.joined_at<?
                      AND COALESCE(u.is_blocked,0)=0
                GROUP BY u.referred_by
            ) s JOIN users r ON r.telegram_id=s.rid
            WHERE COALESCE(r.is_blocked,0)=0 AND COALESCE(r.referral_fraud_suspended,0)=0
            ORDER BY score DESC, s.buyers DESC, s.invites DESC, r.telegram_id ASC
            LIMIT ?
            """,
            (
                int(season["invite_points"]), int(season["purchase_points"]),
                season["starts_at"], season["ends_at"], season["starts_at"], season["ends_at"], int(limit),
            ),
        ).fetchall()
        return [dict(r) for r in rows if int(r["score"] or 0) > 0]

    def season_standings(self, season_id: int, limit: int = 10) -> list:
        self._ensure_season_tables()
        with self._get_conn() as conn:
            season = conn.execute("SELECT * FROM ref_seasons WHERE id=?", (int(season_id),)).fetchone()
            if not season:
                return []
            return self._season_standings_conn(conn, season, limit)

    def season_user_stats(self, season_id: int, user_id: int) -> dict:
        standings = self.season_standings(season_id, 5000)
        for index, row in enumerate(standings, start=1):
            if row["user_id"] == int(user_id):
                return {"rank": index, "score": row["score"], "invites": row["invites"], "buyers": row["buyers"]}
        return {"rank": None, "score": 0, "invites": 0, "buyers": 0}

    def season_close(self, season_id: int, prizes: dict):
        """بستن اتمیک فصل و ثبت نتایج؛ اگر قبلاً بسته شده باشد None.
        prizes: {rank: {"wallet": int, "discount": int}} فقط برای نمونه‌برداری هنگام بستن."""
        self._ensure_season_tables()
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE ref_seasons SET status='closed', closed_at=?, prizes_json=? WHERE id=? AND status='active'",
                (_now(), json.dumps(prizes, ensure_ascii=False), int(season_id)),
            )
            if not cur.rowcount:
                return None
            season = conn.execute("SELECT * FROM ref_seasons WHERE id=?", (int(season_id),)).fetchone()
            standings = self._season_standings_conn(conn, season, _TOP_STORED)
            for rank, row in enumerate(standings, start=1):
                prize = prizes.get(rank) or prizes.get(str(rank)) or {}
                conn.execute(
                    "INSERT INTO ref_season_results (season_id, rank, user_id, score, invites, buyers, prize_wallet, prize_discount, prize_product) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        int(season_id), rank, row["user_id"], row["score"], row["invites"], row["buyers"],
                        int(prize.get("wallet") or 0), int(prize.get("discount") or 0), int(prize.get("product") or 0),
                    ),
                )
            return standings

    def season_results(self, season_id: int):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT res.*, COALESCE(u.first_name,'') AS first_name, COALESCE(u.username,'') AS username "
                "FROM ref_season_results res LEFT JOIN users u ON u.telegram_id=res.user_id "
                "WHERE res.season_id=? ORDER BY res.rank",
                (int(season_id),),
            ).fetchall()

    def season_pending_results(self):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT res.*, s.title AS season_title FROM ref_season_results res "
                "JOIN ref_seasons s ON s.id=res.season_id WHERE res.award_state='pending' ORDER BY res.id"
            ).fetchall()

    def season_claim_result(self, result_id: int) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE ref_season_results SET award_state='processing' WHERE id=? AND award_state='pending'",
                (int(result_id),),
            )
            return bool(cur.rowcount)

    def season_finish_result(self, result_id: int, prize_code: str = None):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE ref_season_results SET award_state='done', awarded_at=?, prize_code=? WHERE id=?",
                (_now(), prize_code, int(result_id)),
            )

    def season_mark_announced(self, season_id: int) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE ref_seasons SET announced=1 WHERE id=? AND COALESCE(announced,0)=0", (int(season_id),)
            )
            return bool(cur.rowcount)

    def season_unannounced_closed(self):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM ref_seasons WHERE status='closed' AND COALESCE(announced,0)=0 ORDER BY id"
            ).fetchall()

    def create_season_prize_code(self, season_id: int, rank: int, user_id: int, percent: int, valid_days: int, product_id: int = None) -> str:
        expires_at = (datetime.utcnow() + timedelta(days=max(1, int(valid_days)))).isoformat()
        prefix = "SSNFREE" if product_id else "SSN"
        code = f"{prefix}{int(season_id)}R{int(rank)}{secrets.token_hex(3).upper()}"
        self.create_discount_code(
            code, percent=int(percent), max_uses=1, expires_at=expires_at, source="season_prize", per_user_limit=1,
            product_id=product_id,
        )
        return code

    # ------------------------------------------------------------------
    # نشان‌ها
    # ------------------------------------------------------------------

    def badge_grant(self, user_id: int, season_id: int, code: str, emoji: str, title: str) -> bool:
        self._ensure_season_tables()
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO user_badges (user_id, season_id, code, emoji, title) VALUES (?,?,?,?,?)",
                (int(user_id), int(season_id), code, emoji, title),
            )
            return bool(cur.rowcount)

    def badge_list(self, user_id: int, limit: int = 50):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM user_badges WHERE user_id=? ORDER BY id DESC LIMIT ?", (int(user_id), int(limit))
            ).fetchall()

    # ------------------------------------------------------------------
    # بانک سوال و کوییز روزانه
    # ------------------------------------------------------------------

    def quiz_bank_add(self, question: str, options: list, correct_index: int, explanation: str = "", source: str = "manual") -> int:
        self._ensure_season_tables()
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO daily_quiz_bank (question, options_json, correct_index, explanation, source) VALUES (?,?,?,?,?)",
                (question, json.dumps(list(options), ensure_ascii=False), int(correct_index), explanation or "", source),
            )
            return cur.lastrowid

    def quiz_bank_count(self) -> int:
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) c FROM daily_quiz_bank WHERE is_active=1").fetchone()["c"]

    def quiz_bank_list(self, limit: int = 8, offset: int = 0):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM daily_quiz_bank WHERE is_active=1 ORDER BY id DESC LIMIT ? OFFSET ?",
                (int(limit), int(offset)),
            ).fetchall()

    def quiz_bank_get(self, bank_id: int):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM daily_quiz_bank WHERE id=?", (int(bank_id),)).fetchone()

    def quiz_bank_delete(self, bank_id: int):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            conn.execute("UPDATE daily_quiz_bank SET is_active=0 WHERE id=?", (int(bank_id),))

    def quiz_bank_pick(self):
        """کم‌استفاده‌ترین سوال فعال؛ در صورت برابری قدیمی‌ترین."""
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM daily_quiz_bank WHERE is_active=1 "
                "ORDER BY used_count ASC, COALESCE(last_used_at,'') ASC, id ASC LIMIT 1"
            ).fetchone()

    def quiz_create(self, item: dict, bank_id, channel_id: str, winner_limit: int, discount_percent: int,
                    code_valid_hours: int, close_hours: int) -> int:
        self._ensure_season_tables()
        now = datetime.utcnow()
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO daily_quizzes (bank_id, question, options_json, correct_index, explanation, channel_id, "
                "winner_limit, discount_percent, code_valid_hours, posted_at, closes_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    bank_id, item["question"], json.dumps(item["options"], ensure_ascii=False), int(item["correct_index"]),
                    item.get("explanation") or "", str(channel_id), int(winner_limit), int(discount_percent),
                    int(code_valid_hours), now.strftime(_FMT), (now + timedelta(hours=int(close_hours))).strftime(_FMT),
                ),
            )
            if bank_id:
                conn.execute(
                    "UPDATE daily_quiz_bank SET used_count=used_count+1, last_used_at=? WHERE id=?", (_now(), bank_id)
                )
            return cur.lastrowid

    def quiz_set_message(self, quiz_id: int, message_id: int):
        with self._get_conn() as conn:
            conn.execute("UPDATE daily_quizzes SET message_id=? WHERE id=?", (int(message_id), int(quiz_id)))

    def quiz_discard(self, quiz_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM daily_quizzes WHERE id=? AND message_id IS NULL", (int(quiz_id),))

    def quiz_get(self, quiz_id: int):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM daily_quizzes WHERE id=?", (int(quiz_id),)).fetchone()

    def quiz_last(self):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM daily_quizzes ORDER BY id DESC LIMIT 1").fetchone()

    def quiz_posted_since(self, since_utc: str) -> bool:
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return bool(conn.execute("SELECT 1 FROM daily_quizzes WHERE posted_at>=?", (since_utc,)).fetchone())

    def quiz_answer(self, quiz_id: int, user_id: int, choice: int, eligible: bool = True) -> dict:
        """ثبت اتمیک پاسخ؛ رتبه‌ی درست‌جواب‌ها و ساخت کد جایزه در همان تراکنش انجام می‌شود."""
        self._ensure_season_tables()
        with self._get_conn() as conn:
            quiz = conn.execute("SELECT * FROM daily_quizzes WHERE id=?", (int(quiz_id),)).fetchone()
            if not quiz:
                return {"ok": False, "reason": "not_found"}
            previous = conn.execute(
                "SELECT * FROM daily_quiz_answers WHERE quiz_id=? AND user_id=?", (int(quiz_id), int(user_id))
            ).fetchone()
            if previous:
                return {"ok": False, "reason": "already", "quiz": quiz, "answer": previous}
            if quiz["status"] != "open" or _now() >= quiz["closes_at"]:
                return {"ok": False, "reason": "closed", "quiz": quiz}
            options = json.loads(quiz["options_json"])
            if not 0 <= int(choice) < len(options):
                return {"ok": False, "reason": "not_found"}
            correct = int(choice) == int(quiz["correct_index"])
            win_rank, code = None, None
            if correct and eligible:
                rank = conn.execute(
                    "SELECT COUNT(*) c FROM daily_quiz_answers WHERE quiz_id=? AND is_correct=1 AND win_rank IS NOT NULL",
                    (int(quiz_id),),
                ).fetchone()["c"] + 1
                if rank <= int(quiz["winner_limit"]):
                    win_rank = rank
                    expires_at = (datetime.utcnow() + timedelta(hours=int(quiz["code_valid_hours"]))).isoformat()
                    code = f"QZ{int(quiz_id)}{secrets.token_hex(3).upper()}"
                    conn.execute(
                        "INSERT INTO discount_codes (code, percent, max_uses, expires_at, source, per_user_limit) "
                        "VALUES (?,?,?,?,?,?)",
                        (code, int(quiz["discount_percent"]), 1, expires_at, "quiz_prize", 1),
                    )
            conn.execute(
                "INSERT INTO daily_quiz_answers (quiz_id, user_id, choice, is_correct, win_rank, code) VALUES (?,?,?,?,?,?)",
                (int(quiz_id), int(user_id), int(choice), 1 if correct else 0, win_rank, code),
            )
            return {
                "ok": True, "quiz": quiz, "correct": correct, "win_rank": win_rank, "code": code,
                "code_expires_at": expires_at if code else None,
            }

    def quiz_close_due(self):
        """کوییزهای سررسیدشده را اتمیک می‌بندد و همان‌ها را برمی‌گرداند."""
        self._ensure_season_tables()
        closed = []
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_quizzes WHERE status='open' AND closes_at<=?", (_now(),)
            ).fetchall()
            for row in rows:
                cur = conn.execute(
                    "UPDATE daily_quizzes SET status='closed', closed_at=? WHERE id=? AND status='open'",
                    (_now(), row["id"]),
                )
                if cur.rowcount:
                    closed.append(row)
        return closed

    def quiz_close_now(self, quiz_id: int):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE daily_quizzes SET status='closed', closed_at=? WHERE id=? AND status='open'",
                (_now(), int(quiz_id)),
            )
            if not cur.rowcount:
                return None
            return conn.execute("SELECT * FROM daily_quizzes WHERE id=?", (int(quiz_id),)).fetchone()

    def quiz_winners(self, quiz_id: int):
        self._ensure_season_tables()
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT a.win_rank, a.user_id, COALESCE(u.first_name,'') AS first_name, COALESCE(u.username,'') AS username "
                "FROM daily_quiz_answers a LEFT JOIN users u ON u.telegram_id=a.user_id "
                "WHERE a.quiz_id=? AND a.win_rank IS NOT NULL ORDER BY a.win_rank",
                (int(quiz_id),),
            ).fetchall()

    def quiz_stats(self, quiz_id: int) -> dict:
        self._ensure_season_tables()
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(is_correct),0) AS correct, "
                "COALESCE(SUM(CASE WHEN win_rank IS NOT NULL THEN 1 ELSE 0 END),0) AS winners "
                "FROM daily_quiz_answers WHERE quiz_id=?",
                (int(quiz_id),),
            ).fetchone()
            return {"total": row["total"], "correct": row["correct"], "winners": row["winners"]}
