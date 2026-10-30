# -*- coding: utf-8 -*-
"""تشخیص نشت کانفیگ: ایندکس هش اثر انگشت، کانال‌های زیر نظر، پست‌های دیده‌شده و رویدادهای نشت.

فقط هش SHA-256 مقادیر محرمانه (UUID، پسورد، توکن اشتراک) در credential_index ذخیره می‌شود،
نه خود مقادیر. این جدول‌ها کاملاً مستقل از جدول‌های یوزربات و Business هستند."""

from datetime import datetime, timezone

LOOKUP_CHUNK = 400


class LeakStoreMixin:
    # ----- credential index ----------------------------------------------------
    def replace_credential_index(self, config_id: int, panel_server_id, username: str, user_id, entries: dict):
        """entries: {hash: kind}. اثر انگشت قبلی همان سرویس کامل جایگزین می‌شود."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM credential_index WHERE config_id=?", (config_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO credential_index "
                "(hash, kind, panel_server_id, username, user_id, config_id, updated_at) "
                "VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                [(h, kind, panel_server_id, username, user_id, config_id) for h, kind in entries.items()],
            )

    def delete_credential_index(self, config_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM credential_index WHERE config_id=?", (config_id,))

    def purge_credential_index_except(self, active_config_ids):
        """ردیف‌های سرویس‌هایی که دیگر فعال نیستند (حذف/منقضی) را از ایندکس پاک می‌کند."""
        ids = [int(x) for x in active_config_ids]
        with self._get_conn() as conn:
            if not ids:
                conn.execute("DELETE FROM credential_index")
                return
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM credential_index WHERE config_id NOT IN ({placeholders})", ids)

    def lookup_credential_hashes(self, hashes) -> list:
        hashes = list(hashes)
        rows = []
        with self._get_conn() as conn:
            for start in range(0, len(hashes), LOOKUP_CHUNK):
                chunk = hashes[start:start + LOOKUP_CHUNK]
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(conn.execute(
                    f"SELECT * FROM credential_index WHERE hash IN ({placeholders})", chunk,
                ).fetchall())
        return rows

    def list_index_target_config_ids(self) -> list:
        """سرویس‌های فعال روی پنل‌های فعال که باید در ایندکس باشند."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT cc.id FROM custom_configs cc JOIN panel_servers ps ON ps.id = cc.panel_server_id "
                "WHERE cc.status='active' AND ps.is_active=1 ORDER BY cc.id"
            ).fetchall()
        return [r["id"] for r in rows]

    def get_credential_index_stats(self) -> dict:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS hashes, COUNT(DISTINCT config_id) AS configs, "
                "MAX(updated_at) AS last_update FROM credential_index"
            ).fetchone()
        return {"hashes": row["hashes"] or 0, "configs": row["configs"] or 0, "last_update": row["last_update"]}

    # ----- channels under watch -----------------------------------------------
    def list_leak_channels(self) -> list:
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM leak_channels ORDER BY title, channel_id").fetchall()

    def list_active_leak_channels(self) -> list:
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM leak_channels WHERE active=1").fetchall()

    def get_leak_channel(self, channel_id: int):
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM leak_channels WHERE channel_id=?", (channel_id,)).fetchone()

    def add_leak_channel(self, channel_id: int, title: str, username: str, last_post_id: int,
                         source: str = "manual", joined: bool = False, auto_joined: bool = False,
                         score: float = 0.0, members: int = 0):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO leak_channels (channel_id, title, username, active, last_post_id, source, joined, "
                "auto_joined, joined_at, score, members) VALUES (?,?,?,1,?,?,?,?,CASE WHEN ?=1 THEN CURRENT_TIMESTAMP END,?,?) "
                "ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title, username=excluded.username, active=1, "
                "joined=MAX(joined, excluded.joined), auto_joined=MAX(auto_joined, excluded.auto_joined), "
                "joined_at=COALESCE(joined_at, excluded.joined_at), score=MAX(score, excluded.score), "
                "members=MAX(members, excluded.members)",
                (channel_id, title or "", username or "", int(last_post_id or 0), source or "manual",
                 1 if joined else 0, 1 if auto_joined else 0, 1 if joined else 0,
                 float(score or 0), int(members or 0)),
            )

    def set_leak_channel_scanned(self, channel_id: int, ts: float):
        with self._get_conn() as conn:
            conn.execute("UPDATE leak_channels SET last_scan_ts=? WHERE channel_id=?", (float(ts), channel_id))

    def bump_leak_channel_stats(self, channel_id: int, config_posts: int = 0, hits: int = 0):
        if not config_posts and not hits:
            return
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE leak_channels SET config_posts=config_posts+?, leak_hits=leak_hits+? WHERE channel_id=?",
                (int(config_posts), int(hits), channel_id),
            )

    def list_prunable_leak_channels(self, days: int, min_config_posts: int) -> list:
        """کانال‌هایی که خود اسکنر عضو شده، مدتی گذشته و هیچ نشت/کانفیگ قابل‌توجهی نداشته‌اند."""
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM leak_channels WHERE joined=1 AND auto_joined=1 AND leak_hits=0 "
                "AND config_posts < ? AND joined_at IS NOT NULL AND joined_at < datetime('now', ?) "
                "ORDER BY score ASC, joined_at ASC",
                (int(min_config_posts), f"-{int(days)} days"),
            ).fetchall()

    def count_joined_leak_channels(self) -> int:
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) c FROM leak_channels WHERE joined=1 AND auto_joined=1").fetchone()["c"]

    def set_leak_channel_active(self, channel_id: int, active: bool):
        with self._get_conn() as conn:
            conn.execute("UPDATE leak_channels SET active=? WHERE channel_id=?", (1 if active else 0, channel_id))

    def remove_leak_channel(self, channel_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM leak_channels WHERE channel_id=?", (channel_id,))

    def set_leak_channel_last_post(self, channel_id: int, post_id: int):
        """فقط جلو می‌رود؛ هیچ‌وقت به عقب برنمی‌گردد."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE leak_channels SET last_post_id=MAX(last_post_id, ?) WHERE channel_id=?",
                (int(post_id), channel_id),
            )

    # ----- seen posts (جلوگیری از اسکن تکراری) -----------------------------------
    def mark_leak_post_seen(self, channel_id: int, post_id: int) -> bool:
        """True یعنی این پست تازه بود و ثبت شد؛ False یعنی قبلاً دیده شده."""
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO leak_seen_posts (channel_id, post_id) VALUES (?,?)",
                (int(channel_id), int(post_id)),
            )
            if cur.rowcount == 0:
                return False
            conn.execute(
                "DELETE FROM leak_seen_posts WHERE created_at < datetime('now', '-30 days')"
            )
            return True

    # ----- events -----------------------------------------------------------------
    def create_leak_event(self, user_id: int, config_id: int, channel_id: int, channel_title: str,
                          post_link: str, matched_kind: str):
        """id رویداد جدید یا None اگر همین سرویس قبلاً در همین پست ثبت شده بود."""
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO leak_events "
                "(user_id, config_id, channel_id, channel_title, post_link, matched_kind) VALUES (?,?,?,?,?,?)",
                (user_id, config_id, int(channel_id or 0), channel_title or "", post_link, matched_kind or ""),
            )
            return cur.lastrowid if cur.rowcount else None

    def get_leak_event(self, event_id: int):
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM leak_events WHERE id=?", (event_id,)).fetchone()

    def set_leak_event_status(self, event_id: int, status: str):
        resolved = None if status == "new" else datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._get_conn() as conn:
            conn.execute("UPDATE leak_events SET status=?, resolved_at=? WHERE id=?", (status, resolved, event_id))

    def mark_leak_event_warned(self, event_id: int) -> bool:
        """اخطار را روی رویداد ثبت می‌کند. True اگر تازه ثبت شد، False اگر قبلاً اخطار خورده بود."""
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE leak_events SET warn_count=1 WHERE id=? AND warn_count=0", (event_id,),
            )
            return cur.rowcount > 0

    def count_user_leak_warnings(self, user_id: int) -> int:
        """تعداد رویدادهایی از این کاربر که اخطار گرفته‌اند (شمارنده‌ی اخطار و مسدودسازی)."""
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) c FROM leak_events WHERE user_id=? AND warn_count>0", (user_id,),
            ).fetchone()["c"]

    def list_leak_events(self, limit: int = 10) -> list:
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM leak_events ORDER BY id DESC LIMIT ?", (int(limit),),
            ).fetchall()

    def count_leak_events_by_status(self) -> dict:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT status, COUNT(*) c FROM leak_events GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}

    # ----- discovery: candidate pool ----------------------------------------------
    CANDIDATE_FIELDS = (
        "channel_id", "username", "invite_hash", "title", "members", "kind", "state", "score",
        "config_posts", "reason", "source_query", "joined_at",
    )

    def upsert_leak_candidate(self, key: str, channel_id: int = 0, username: str = "", invite_hash: str = "",
                              title: str = "", members: int = 0, kind: str = "", source: str = "",
                              source_query: str = "", boost: int = 1) -> bool:
        """True اگر کاندیدای تازه ثبت شد. کانالی که الان زیر نظر است دوباره کاندید نمی‌شود."""
        with self._get_conn() as conn:
            if username or channel_id:
                known = conn.execute(
                    "SELECT 1 FROM leak_channels WHERE (?<>'' AND lower(username)=lower(?)) OR (?>0 AND channel_id=?)",
                    (username or "", username or "", int(channel_id or 0), int(channel_id or 0)),
                ).fetchone()
                if known:
                    return False
            row = conn.execute("SELECT id FROM leak_candidates WHERE key=?", (key,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE leak_candidates SET seen_count=seen_count+?, updated_at=CURRENT_TIMESTAMP, "
                    "title=CASE WHEN ?<>'' THEN ? ELSE title END, members=MAX(members, ?), "
                    "channel_id=CASE WHEN ?>0 THEN ? ELSE channel_id END WHERE id=?",
                    (int(boost), title or "", title or "", int(members or 0),
                     int(channel_id or 0), int(channel_id or 0), row["id"]),
                )
                return False
            conn.execute(
                "INSERT INTO leak_candidates (key, channel_id, username, invite_hash, title, members, kind, "
                "source, source_query, seen_count) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (key, int(channel_id or 0), username or "", invite_hash or "", title or "", int(members or 0),
                 kind or "", source or "", source_query or "", int(boost)),
            )
            return True

    def get_leak_candidate(self, cand_id: int):
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM leak_candidates WHERE id=?", (int(cand_id),)).fetchone()

    def list_leak_candidates(self, states, limit: int = 20, order: str = "seen") -> list:
        states = list(states)
        if not states:
            return []
        order_sql = {
            "seen": "seen_count DESC, id ASC",
            "score": "score DESC, id ASC",
            "recent": "updated_at DESC, id DESC",
        }.get(order, "id ASC")
        placeholders = ",".join("?" for _ in states)
        with self._get_conn() as conn:
            return conn.execute(
                f"SELECT * FROM leak_candidates WHERE state IN ({placeholders}) ORDER BY {order_sql} LIMIT ?",
                (*states, int(limit)),
            ).fetchall()

    def update_leak_candidate(self, cand_id: int, **fields):
        fields = {k: v for k, v in fields.items() if k in self.CANDIDATE_FIELDS}
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE leak_candidates SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (*fields.values(), int(cand_id)),
            )

    def count_leak_candidates_by_state(self) -> dict:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT state, COUNT(*) c FROM leak_candidates GROUP BY state").fetchall()
        return {r["state"]: r["c"] for r in rows}

    def count_leak_joins_since(self, hours: int) -> int:
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) c FROM leak_candidates WHERE state='joined' AND joined_at >= datetime('now', ?)",
                (f"-{int(hours)} hours",),
            ).fetchone()["c"]

    def trim_leak_candidates(self, keep: int = 3000):
        """کاندیدای ردشده/ناموفق قدیمی پاک می‌شود تا جدول بی‌نهایت بزرگ نشود."""
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM leak_candidates").fetchone()["c"]
            if total <= keep:
                return
            conn.execute(
                "DELETE FROM leak_candidates WHERE id IN (SELECT id FROM leak_candidates "
                "WHERE state IN ('skipped','failed') ORDER BY updated_at ASC LIMIT ?)",
                (total - keep,),
            )

    # ----- discovery: search queries ----------------------------------------------
    def ensure_leak_queries(self, queries, source: str = "seed"):
        with self._get_conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO leak_queries (query, source) VALUES (?,?)",
                [(q, source) for q in queries if q],
            )

    def list_leak_queries(self) -> list:
        with self._get_conn() as conn:
            return conn.execute("SELECT rowid AS id, * FROM leak_queries").fetchall()

    def add_leak_query(self, query: str, source: str = "admin") -> bool:
        """کلیدواژه‌ی دستی مدیر. True اگر تازه بود؛ اگر قبلاً (حتی غیرفعال) بود، فعال می‌شود."""
        query = " ".join((query or "").split())[:60]
        if not query:
            return False
        with self._get_conn() as conn:
            row = conn.execute("SELECT 1 FROM leak_queries WHERE lower(query)=lower(?)", (query,)).fetchone()
            if row:
                conn.execute("UPDATE leak_queries SET active=1 WHERE lower(query)=lower(?)", (query,))
                return False
            conn.execute("INSERT INTO leak_queries (query, source) VALUES (?,?)", (query, source))
            return True

    def get_leak_query(self, query_id: int):
        with self._get_conn() as conn:
            return conn.execute("SELECT rowid AS id, * FROM leak_queries WHERE rowid=?", (int(query_id),)).fetchone()

    def set_leak_query_active(self, query_id: int, active: bool):
        with self._get_conn() as conn:
            conn.execute("UPDATE leak_queries SET active=? WHERE rowid=?", (1 if active else 0, int(query_id)))

    def delete_leak_query(self, query_id: int) -> bool:
        """فقط کلیدواژه‌ی دستی/یادگرفته‌شده حذف می‌شود؛ پیش‌فرض‌ها (seed) فقط غیرفعال می‌شوند."""
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM leak_queries WHERE rowid=? AND source<>'seed'", (int(query_id),))
            return cur.rowcount > 0

    def record_leak_query_run(self, query: str, new_found: int, ts: float):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE leak_queries SET runs=runs+1, new_found=new_found+?, last_run_at=? WHERE query=?",
                (int(new_found), float(ts), query),
            )

    def bump_leak_query_good(self, query: str):
        with self._get_conn() as conn:
            conn.execute("UPDATE leak_queries SET good_found=good_found+1 WHERE query=?", (query,))

    def count_learned_leak_queries(self) -> int:
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) c FROM leak_queries WHERE source='learned'").fetchone()["c"]
