# -*- coding: utf-8 -*-
"""ورود سایت وب با تلگرام (بدون پیامک و بدون ویجت تلگرام).

روند: سایت یک «کد لینک» و یک «رمز پولینگ» می‌سازد. کد لینک داخل دیپ‌لینک بات می‌رود
(/start weblogin_<code>) و فقط رمز پولینگ در مرورگر می‌ماند؛ پس کسی که لینک را برای
دیگری بفرستد نمی‌تواند نشست او را بگیرد. بات قبل از تأیید یک دکمه‌ی صریح نشان می‌دهد.
همه‌ی توکن‌ها فقط به‌صورت هش SHA-256 ذخیره می‌شوند.
"""
import hashlib
import re
import secrets
import time
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover
    dotenv_values = None

LOGIN_TTL = 600                 # اعتبار درخواست ورود (ثانیه)
SESSION_TTL = 30 * 86400        # اعتبار نشست سایت
MAX_PENDING_PER_IP = 10         # سقف درخواست ورود فعال برای هر IP
EMAIL_VERIFY_FAILS_PER_HOUR = 10  # سقف تلاش ناموفق کد ایمیل برای هر ایمیل در ساعت
_TOUCH_EVERY = 300
_CODE_RE = re.compile(r"[0-9a-f]{32}")


def _h(value: str) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()


def ensure_tables(db) -> None:
    if getattr(db, "_web_auth_ready", False):
        return
    with db._get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS web_login_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code_hash TEXT UNIQUE NOT NULL,
                poll_hash TEXT UNIQUE NOT NULL,
                tg_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                ip TEXT DEFAULT '',
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT UNIQUE NOT NULL,
                tg_id INTEGER NOT NULL,
                ip TEXT DEFAULT '',
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                last_seen REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_web_sessions_tg ON web_sessions(tg_id);
            CREATE TABLE IF NOT EXISTS web_security_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_web_security_events_key_time ON web_security_events(event_key, created_at);
            CREATE TABLE IF NOT EXISTS web_account_link_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code_hash TEXT UNIQUE NOT NULL,
                email_tg_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_merge_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code_hash TEXT UNIQUE NOT NULL,
                origin TEXT NOT NULL,
                owner_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_email_codes (
                email TEXT PRIMARY KEY, code_hash TEXT NOT NULL, tg_id INTEGER NOT NULL,
                created_at REAL NOT NULL, expires_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS web_email_accounts (
                email TEXT PRIMARY KEY, tg_id INTEGER UNIQUE NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_email_telegram_links (
                email TEXT PRIMARY KEY, telegram_id INTEGER UNIQUE NOT NULL, linked_at REAL NOT NULL
            );
            """
        )
    db._web_auth_ready = True


def start_login(db, ip: str = ""):
    """(code, poll_secret) می‌سازد؛ اگر IP زیادی درخواست داده باشد ValueError می‌دهد."""
    ensure_tables(db)
    now = time.time()
    with db._get_conn() as conn:
        conn.execute("DELETE FROM web_login_requests WHERE expires_at < ?", (now - 3600,))
        conn.execute("DELETE FROM web_sessions WHERE expires_at < ?", (now,))
        pending = conn.execute(
            "SELECT COUNT(*) FROM web_login_requests WHERE ip=? AND created_at>?",
            (ip, now - LOGIN_TTL),
        ).fetchone()[0]
        if pending >= MAX_PENDING_PER_IP:
            raise ValueError("rate_limited")
        code = secrets.token_hex(16)
        poll = secrets.token_urlsafe(24)
        conn.execute(
            "INSERT INTO web_login_requests(code_hash, poll_hash, ip, created_at, expires_at) VALUES(?,?,?,?,?)",
            (_h(code), _h(poll), ip, now, now + LOGIN_TTL),
        )
    return code, poll


def confirm_login(db, code: str, tg_id: int) -> bool:
    """از سمت بات بعد از زدن دکمه‌ی تأیید صدا زده می‌شود."""
    if not _CODE_RE.fullmatch(code or ""):
        return False
    ensure_tables(db)
    with db._get_conn() as conn:
        cur = conn.execute(
            "UPDATE web_login_requests SET status='confirmed', tg_id=? "
            "WHERE code_hash=? AND status='pending' AND expires_at>?",
            (int(tg_id), _h(code), time.time()),
        )
        return cur.rowcount == 1


def poll_login(db, poll: str, ip: str = ""):
    """('pending'|'expired'|'ok', token, tg_id). توکن نشست فقط یک بار صادر می‌شود."""
    ensure_tables(db)
    now = time.time()
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT id, status, tg_id, expires_at FROM web_login_requests WHERE poll_hash=?",
            (_h(poll),),
        ).fetchone()
        if not row or row["expires_at"] < now or row["status"] == "used":
            return "expired", None, None
        if row["status"] == "pending":
            return "pending", None, None
        claimed = conn.execute(
            "UPDATE web_login_requests SET status='used' WHERE id=? AND status='confirmed'",
            (row["id"],),
        )
        if claimed.rowcount != 1:
            return "expired", None, None
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO web_sessions(token_hash, tg_id, ip, created_at, expires_at, last_seen) VALUES(?,?,?,?,?,?)",
            (_h(token), row["tg_id"], ip, now, now + SESSION_TTL, now),
        )
        return "ok", token, int(row["tg_id"])


_ENV_FILE = Path(__file__).resolve().parent / ".env"
_env_cache = {"mtime": None, "values": {}}


def _smtp_env(name: str, default: str = "") -> str:
    """مقدار تنظیم SMTP: اول از فایل .env (همیشه تازه)، بعد از محیط پروسه.

    manage.sh مقدارها را در .env می‌نویسد؛ اگر سرویس ری‌استارت نشده یا مقدار بعد از
    بالا آمدن سرویس عوض شده باشد، os.environ کهنه است و ورود ایمیلی پنهان می‌ماند.
    """
    try:
        mtime = _ENV_FILE.stat().st_mtime
        if _env_cache["mtime"] != mtime:
            _env_cache["values"] = dict(dotenv_values(_ENV_FILE)) if dotenv_values else {}
            _env_cache["mtime"] = mtime
        val = _env_cache["values"].get(name)
        if val not in (None, ""):
            return str(val)
    except Exception:
        pass
    return os.getenv(name, default)


def email_configured() -> bool:
    return bool(_smtp_env("SHOPVPN_SMTP_HOST").strip() and _smtp_env("SHOPVPN_SMTP_USER").strip() and _smtp_env("SHOPVPN_SMTP_PASSWORD"))


def send_email_code(email: str, code: str) -> None:
    """Send an email verification code using SMTP settings (.env first, then environment)."""
    host = _smtp_env("SHOPVPN_SMTP_HOST").strip()
    user = _smtp_env("SHOPVPN_SMTP_USER").strip()
    password = _smtp_env("SHOPVPN_SMTP_PASSWORD")
    sender = (_smtp_env("SHOPVPN_SMTP_FROM") or user).strip()
    port = int(_smtp_env("SHOPVPN_SMTP_PORT", "587") or "587")
    use_ssl = _smtp_env("SHOPVPN_SMTP_SSL", "0").lower() in {"1", "true", "yes"}
    if not host or not sender or not user or not password:
        raise RuntimeError("email_not_configured")
    msg = EmailMessage()
    msg["Subject"] = "ShopVPN - Email verification code"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(f"Your ShopVPN verification code is: {code}\n\nIt expires in 10 minutes. If you did not request this, ignore this email.")
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
            smtp.login(user, password); smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo(); smtp.starttls(); smtp.ehlo(); smtp.login(user, password); smtp.send_message(msg)


def start_email_login(db, email: str, ip: str = "") -> str:
    """Create and store a short-lived one-time email code; returns the code for mail delivery."""
    ensure_tables(db)
    email = (email or "").strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("invalid_email")
    now = time.time()
    # Deterministic internal identity keeps orders/services attached to the same email account.
    tg_id = 8_000_000_000_000_000 + (int(hashlib.sha256(email.encode()).hexdigest()[:12], 16) % 900_000_000_000_000)
    code = f"{secrets.randbelow(1_000_000):06d}"
    with db._get_conn() as conn:
        old = conn.execute("SELECT created_at FROM web_email_codes WHERE email=?", (email,)).fetchone()
        if old and now - float(old["created_at"]) < 45:
            raise ValueError("rate_limited")
        conn.execute("INSERT OR IGNORE INTO web_email_accounts(email,tg_id,created_at) VALUES(?,?,?)", (email, tg_id, now))
        row = conn.execute("SELECT tg_id FROM web_email_accounts WHERE email=?", (email,)).fetchone()
        tg_id = int(row["tg_id"])
        linked = conn.execute("SELECT telegram_id FROM web_email_telegram_links WHERE email=?", (email,)).fetchone()
        if linked:
            tg_id = int(linked["telegram_id"])
        conn.execute("INSERT OR REPLACE INTO web_email_codes(email,code_hash,tg_id,created_at,expires_at,attempts) VALUES(?,?,?,?,?,0)", (email, _h(code), tg_id, now, now + LOGIN_TTL))
    return code


def verify_email_code(db, email: str, code: str, ip: str = ""):
    """Return a new web session token and internal user ID on successful verification."""
    ensure_tables(db); email = (email or "").strip().lower(); now = time.time()
    with db._get_conn() as conn:
        # سقف کل تلاش‌های ناموفق برای هر ایمیل در یک ساعت (با درخواست کد جدید ریست نمی‌شود).
        key = "email-verify-fail:" + email
        conn.execute("DELETE FROM web_security_events WHERE created_at<?", (now - 3600,))
        fails = conn.execute(
            "SELECT COUNT(*) FROM web_security_events WHERE event_key=? AND created_at>?", (key, now - 3600)
        ).fetchone()[0]
        if int(fails) >= EMAIL_VERIFY_FAILS_PER_HOUR:
            return None, None
        row = conn.execute("SELECT * FROM web_email_codes WHERE email=?", (email,)).fetchone()
        if not row or float(row["expires_at"]) < now or int(row["attempts"]) >= 5:
            return None, None
        if not secrets.compare_digest(row["code_hash"], _h((code or "").strip())):
            conn.execute("UPDATE web_email_codes SET attempts=attempts+1 WHERE email=?", (email,))
            conn.execute("INSERT INTO web_security_events(event_key,created_at) VALUES(?,?)", (key, now))
            return None, None
        tg_id = int(row["tg_id"])
        conn.execute("DELETE FROM web_email_codes WHERE email=?", (email,))
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO web_sessions(token_hash,tg_id,ip,created_at,expires_at,last_seen) VALUES(?,?,?,?,?,?)", (_h(token), tg_id, ip, now, now + SESSION_TTL, now))
    return token, tg_id



def is_email_account(db, tg_id: int) -> bool:
    """True when tg_id is the internal identity of a verified email account."""
    ensure_tables(db)
    with db._get_conn() as conn:
        row = conn.execute("SELECT 1 FROM web_email_accounts WHERE tg_id=? LIMIT 1", (int(tg_id),)).fetchone()
    return bool(row)


def enforce_rate_limit(db, key: str, limit: int, window_seconds: int) -> bool:
    """Persistent, process-independent sliding-window limiter. Returns True if allowed."""
    ensure_tables(db)
    now = time.time()
    with db._get_conn() as conn:
        conn.execute("DELETE FROM web_security_events WHERE created_at<?", (now - max(window_seconds, 3600),))
        count = conn.execute(
            "SELECT COUNT(*) FROM web_security_events WHERE event_key=? AND created_at>?",
            (key, now - window_seconds),
        ).fetchone()[0]
        if int(count) >= int(limit):
            return False
        conn.execute("INSERT INTO web_security_events(event_key,created_at) VALUES(?,?)", (key, now))
    return True


# ---------------------------------------------------------------------------
# ادغام حساب ایمیلی و حساب تلگرام.
# حساب تلگرام همیشه «حساب نهایی» است (ربات با آی‌دی تلگرام کار می‌کند).
# برای ادغام، باید هر دو طرف در اختیار شما باشند: کدی که در یک طرف ساخته می‌شود
# باید در طرف دیگر، با ورود فعال همان کاربر، وارد شود و سپس تأیید نهایی داده شود.
#   origin='web' : کد در سایت ساخته می‌شود و در ربات با /merge CODE وارد می‌شود.
#   origin='bot' : کد در ربات ساخته می‌شود و در سایت (با ایمیل وارد شده) وارد می‌شود.
# ---------------------------------------------------------------------------
MERGE_CODE_TTL = 600
MERGE_TRIES_PER_HOUR = 20
# ستون‌هایی که به کاربر اشاره می‌کنند؛ هنگام ادغام از حساب ایمیلی به حساب تلگرام منتقل می‌شوند.
USER_REF_COLUMNS = ("user_id", "assigned_user_id", "from_user_id", "referrer_id",
                    "referred_by", "referred_user_id", "sender_id", "owner_telegram_id")
_MERGE_REASONS = {
    "bad_code": "کد نامعتبر یا منقضی شده است.",
    "wrong_side": "این کد را باید در طرف دیگر (سایت یا ربات) وارد کنید.",
    "same_account": "هر دو طرف به یک حساب متصل‌اند؛ ادغامی لازم نیست.",
    "source_not_email": "حساب ایمیلی معتبر پیدا نشد یا این حساب قبلاً به تلگرام متصل شده است.",
    "target_is_email": "حساب مقصد باید حساب تلگرام باشد.",
    "target_linked_other": "این حساب تلگرام قبلاً به ایمیل دیگری متصل است.",
    "source_already_linked": "این ایمیل قبلاً به حساب تلگرام متصل شده است.",
}


def merge_reason_text(reason: str) -> str:
    return _MERGE_REASONS.get(reason, "ادغام انجام نشد.")


def _is_email_tg(conn, tg_id) -> bool:
    return conn.execute("SELECT 1 FROM web_email_accounts WHERE tg_id=? LIMIT 1", (int(tg_id),)).fetchone() is not None


def _email_of(conn, tg_id) -> str:
    row = conn.execute("SELECT email FROM web_email_accounts WHERE tg_id=?", (int(tg_id),)).fetchone()
    return row["email"] if row else ""


def mask_email(email: str) -> str:
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "***"
    return (local[:2] + "***" if len(local) > 2 else "***") + "@" + domain


def create_merge_code(db, origin: str, owner_id: int) -> str:
    if origin not in ("web", "bot"):
        raise ValueError("bad_origin")
    ensure_tables(db)
    now = time.time()
    code = f"{secrets.randbelow(10 ** 8):08d}"
    with db._get_conn() as conn:
        if origin == "web" and not _is_email_tg(conn, owner_id):
            raise ValueError("source_not_email")
        conn.execute("DELETE FROM web_merge_codes WHERE expires_at<? OR (origin=? AND owner_id=?)",
                     (now, origin, int(owner_id)))
        conn.execute(
            "INSERT INTO web_merge_codes(code_hash,origin,owner_id,created_at,expires_at) VALUES(?,?,?,?,?)",
            (_h("merge:" + code), origin, int(owner_id), now, now + MERGE_CODE_TTL),
        )
    return code


def _resolve_merge(conn, code: str, redeemer_id: int, side: str):
    """(code_row_id, source_id, target_id, target_display). ValueError(reason) در صورت خطا."""
    if not re.fullmatch(r"[0-9]{8}", code or ""):
        raise ValueError("bad_code")
    row = conn.execute(
        "SELECT id, origin, owner_id FROM web_merge_codes WHERE code_hash=? AND expires_at>?",
        (_h("merge:" + code), time.time()),
    ).fetchone()
    if not row:
        raise ValueError("bad_code")
    if row["origin"] == "web":
        if side != "bot":
            raise ValueError("wrong_side")
        source, target = int(row["owner_id"]), int(redeemer_id)
    else:
        if side != "web":
            raise ValueError("wrong_side")
        source, target = int(redeemer_id), int(row["owner_id"])
    if source == target:
        raise ValueError("same_account")
    if not _is_email_tg(conn, source):
        raise ValueError("source_not_email")
    if _is_email_tg(conn, target):
        raise ValueError("target_is_email")
    email = _email_of(conn, source)
    if conn.execute("SELECT 1 FROM web_email_telegram_links WHERE email=? LIMIT 1", (email,)).fetchone():
        raise ValueError("source_already_linked")
    if conn.execute("SELECT 1 FROM web_email_telegram_links WHERE telegram_id=? AND email<>? LIMIT 1",
                    (target, email)).fetchone():
        raise ValueError("target_linked_other")
    urow = conn.execute("SELECT username, first_name FROM users WHERE telegram_id=?", (target,)).fetchone()
    display = (urow["username"] or urow["first_name"] or str(target)) if urow else str(target)
    return row["id"], source, target, display


def preview_merge(db, code: str, redeemer_id: int, side: str) -> dict:
    """اطلاعات تأیید برای نمایش به کاربر؛ چیزی تغییر نمی‌دهد."""
    ensure_tables(db)
    with db._get_conn() as conn:
        _, source, target, display = _resolve_merge(conn, code, redeemer_id, side)
        email = _email_of(conn, source)
    return {"email": mask_email(email), "telegram": display, "source": source, "target": target}


def _merge_into(conn, source: int, target: int) -> None:
    email = _email_of(conn, source)
    tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for table in tables:
        if table.startswith("web_") or table in ("settings", "fsm_storage"):
            continue
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for col in USER_REF_COLUMNS:
            if col in cols:
                conn.execute(f"UPDATE OR IGNORE {table} SET {col}=? WHERE {col}=?", (target, source))
    # موجودی و امتیاز حساب ایمیلی به حساب تلگرام اضافه می‌شود.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
        conn.execute(
            "UPDATE users SET referral_credit=COALESCE(referral_credit,0)+(SELECT COALESCE(referral_credit,0) FROM users WHERE telegram_id=?), "
            "score=COALESCE(score,0)+(SELECT COALESCE(score,0) FROM users WHERE telegram_id=?) WHERE telegram_id=?",
            (source, source, target),
        )
        conn.execute("DELETE FROM users WHERE telegram_id=?", (source,))
    # نشست‌های حساب ایمیلی باطل می‌شوند؛ ورود بعدی با همان ایمیل به حساب تلگرام می‌رود.
    conn.execute("DELETE FROM web_sessions WHERE tg_id=?", (source,))
    conn.execute("INSERT OR REPLACE INTO web_email_telegram_links(email,telegram_id,linked_at) VALUES(?,?,?)",
                 (email, target, time.time()))


def complete_merge(db, code: str, redeemer_id: int, side: str) -> dict:
    """ادغام نهایی؛ کد یک‌بارمصرف است و در همان تراکنش باطل می‌شود."""
    ensure_tables(db)
    with db._get_conn() as conn:
        row_id, source, target, _ = _resolve_merge(conn, code, redeemer_id, side)
        _merge_into(conn, source, target)
        conn.execute("DELETE FROM web_merge_codes WHERE id=?", (row_id,))
    return {"source": source, "target": target}


def login_request_info(db, code: str):
    """(ip, زمان) درخواست ورود وب برای نمایش در پیام تأیید بات."""
    if not _CODE_RE.fullmatch(code or ""):
        return "", 0.0
    ensure_tables(db)
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT ip, created_at FROM web_login_requests WHERE code_hash=? AND status='pending' AND expires_at>?",
            (_h(code), time.time()),
        ).fetchone()
    return (row["ip"], float(row["created_at"])) if row else ("", 0.0)


def session_user(db, token: str):
    """tg_id کاربر نشست معتبر، وگرنه None."""
    if not token or len(token) > 200:
        return None
    ensure_tables(db)
    now = time.time()
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT id, tg_id, last_seen FROM web_sessions WHERE token_hash=? AND expires_at>?",
            (_h(token), now),
        ).fetchone()
        if not row:
            return None
        if now - row["last_seen"] > _TOUCH_EVERY:
            conn.execute("UPDATE web_sessions SET last_seen=? WHERE id=?", (now, row["id"]))
        return int(row["tg_id"])


def logout(db, token: str) -> None:
    ensure_tables(db)
    with db._get_conn() as conn:
        conn.execute("DELETE FROM web_sessions WHERE token_hash=?", (_h(token),))
