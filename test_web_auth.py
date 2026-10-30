# -*- coding: utf-8 -*-
"""تست ورود وب: python test_web_auth.py"""
import sqlite3
import threading
import time
from contextlib import contextmanager

import web_auth


class FakeDB:
    def __init__(self):
        self._c = sqlite3.connect(":memory:", check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    @contextmanager
    def _get_conn(self):
        with self._lock:
            try:
                yield self._c
                self._c.commit()
            except Exception:
                self._c.rollback()
                raise


def test_full_flow():
    db = FakeDB()
    code, poll = web_auth.start_login(db, "1.1.1.1")
    assert web_auth.poll_login(db, poll)[0] == "pending"
    assert not web_auth.confirm_login(db, "zz", 5)
    assert web_auth.confirm_login(db, code, 777)
    assert not web_auth.confirm_login(db, code, 888)          # فقط یک بار
    status, token, tg = web_auth.poll_login(db, poll)
    assert (status, tg) == ("ok", 777) and token
    assert web_auth.poll_login(db, poll)[0] == "expired"      # توکن دوباره صادر نمی‌شود
    assert web_auth.session_user(db, token) == 777
    assert web_auth.session_user(db, "bad") is None
    web_auth.logout(db, token)
    assert web_auth.session_user(db, token) is None


def test_code_alone_cannot_poll():
    db = FakeDB()
    code, poll = web_auth.start_login(db, "1.1.1.1")
    web_auth.confirm_login(db, code, 1)
    assert web_auth.poll_login(db, code)[0] == "expired"      # کد لینک رمز پولینگ نیست


def test_expiry_and_rate_limit():
    db = FakeDB()
    code, poll = web_auth.start_login(db, "2.2.2.2")
    with db._get_conn() as c:
        c.execute("UPDATE web_login_requests SET expires_at=?", (time.time() - 1,))
    assert not web_auth.confirm_login(db, code, 1)
    assert web_auth.poll_login(db, poll)[0] == "expired"
    db2 = FakeDB()
    for _ in range(web_auth.MAX_PENDING_PER_IP):
        web_auth.start_login(db2, "3.3.3.3")
    try:
        web_auth.start_login(db2, "3.3.3.3")
        assert False
    except ValueError:
        pass
    web_auth.start_login(db2, "4.4.4.4")




def test_persistent_rate_limit():
    db = FakeDB()
    assert web_auth.enforce_rate_limit(db, "email-ip:1.2.3.4", 2, 60)
    assert web_auth.enforce_rate_limit(db, "email-ip:1.2.3.4", 2, 60)
    assert not web_auth.enforce_rate_limit(db, "email-ip:1.2.3.4", 2, 60)

def test_email_verify_limit_not_reset_by_new_codes():
    db = FakeDB()
    email = "victim@example.com"
    for _ in range(web_auth.EMAIL_VERIFY_FAILS_PER_HOUR):
        web_auth.start_email_login(db, email, "7.7.7.7")
        with db._get_conn() as c:
            c.execute("UPDATE web_email_codes SET created_at=0 WHERE email=?", (email,))
        web_auth.verify_email_code(db, email, "000000", "7.7.7.7")
    real = web_auth.start_email_login(db, email, "8.8.8.8")
    assert web_auth.verify_email_code(db, email, real, "8.8.8.8") == (None, None)


SCHEMA = """
    CREATE TABLE users (telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        referral_credit INTEGER DEFAULT 0, score INTEGER DEFAULT 0, referred_by INTEGER);
    CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL);
    CREATE TABLE custom_configs (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL);
    CREATE TABLE tickets (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL);
    CREATE TABLE configs (id INTEGER PRIMARY KEY, assigned_user_id INTEGER);
    CREATE TABLE referrals (id INTEGER PRIMARY KEY, referrer_id INTEGER);
"""


def _email_user(db, email, orders=0):
    code = web_auth.start_email_login(db, email, "5.5.5.5")
    token, pseudo = web_auth.verify_email_code(db, email, code, "5.5.5.5")
    with db._get_conn() as c:
        c.execute("INSERT INTO users(telegram_id, referral_credit, score) VALUES(?,?,?)", (pseudo, 30, 4))
        for i in range(orders):
            c.execute("INSERT INTO orders(id,user_id) VALUES(?,?)", (1000 + i + pseudo % 997 * 10, pseudo))
    return token, pseudo


def _tg_user(db, tg_id, credit=5, orders=0):
    with db._get_conn() as c:
        c.execute("INSERT OR IGNORE INTO users(telegram_id, username, referral_credit, score) VALUES(?,?,?,0)", (tg_id, "tguser", credit))
        for i in range(orders):
            c.execute("INSERT INTO orders(id,user_id) VALUES(?,?)", (5000 + tg_id % 1000 * 10 + i, tg_id))


def test_web_origin_merge_into_telegram():
    db = FakeDB()
    with db._get_conn() as c:
        c.executescript(SCHEMA)
    token, pseudo = _email_user(db, "buyer@example.com", orders=1)
    with db._get_conn() as c:
        c.execute("INSERT INTO custom_configs(id,user_id) VALUES(1,?)", (pseudo,))
        c.execute("INSERT INTO tickets(id,user_id) VALUES(1,?)", (pseudo,))
        c.execute("INSERT INTO configs(id,assigned_user_id) VALUES(1,?)", (pseudo,))
        c.execute("INSERT INTO referrals(id,referrer_id) VALUES(1,?)", (pseudo,))
    _tg_user(db, 777, credit=5, orders=1)
    code = web_auth.create_merge_code(db, "web", pseudo)
    info = web_auth.preview_merge(db, code, 777, "bot")
    assert info["telegram"] == "tguser" and info["email"].endswith("@example.com")
    web_auth.complete_merge(db, code, 777, "bot")
    assert web_auth.session_user(db, token) is None
    with db._get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM orders WHERE user_id=777").fetchone()[0] == 2
        assert c.execute("SELECT user_id FROM custom_configs WHERE id=1").fetchone()[0] == 777
        assert c.execute("SELECT user_id FROM tickets WHERE id=1").fetchone()[0] == 777
        assert c.execute("SELECT assigned_user_id FROM configs WHERE id=1").fetchone()[0] == 777
        assert c.execute("SELECT referrer_id FROM referrals WHERE id=1").fetchone()[0] == 777
        assert c.execute("SELECT referral_credit FROM users WHERE telegram_id=777").fetchone()[0] == 35
        assert c.execute("SELECT 1 FROM users WHERE telegram_id=?", (pseudo,)).fetchone() is None
    # code is single-use
    try:
        web_auth.complete_merge(db, code, 777, "bot"); raise AssertionError("reused code")
    except ValueError as e:
        assert str(e) == "bad_code"
    # next email login resolves to the Telegram account
    code2 = web_auth.start_email_login(db, "buyer@example.com", "5.5.5.5")
    assert web_auth.verify_email_code(db, "buyer@example.com", code2, "5.5.5.5")[1] == 777


def test_bot_origin_merge_and_wrong_side():
    db = FakeDB()
    with db._get_conn() as c:
        c.executescript(SCHEMA)
    _tg_user(db, 888, orders=2)
    token, pseudo = _email_user(db, "second@example.com")
    code = web_auth.create_merge_code(db, "bot", 888)
    try:
        web_auth.preview_merge(db, code, 888, "bot"); raise AssertionError("wrong side allowed")
    except ValueError as e:
        assert str(e) == "wrong_side"
    web_auth.complete_merge(db, code, pseudo, "web")
    assert web_auth.session_user(db, token) is None
    with db._get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM orders WHERE user_id=888").fetchone()[0] == 2


def test_merge_refused_when_telegram_linked_to_other_email():
    db = FakeDB()
    with db._get_conn() as c:
        c.executescript(SCHEMA)
    _tg_user(db, 999)
    _, first = _email_user(db, "first@example.com")
    code = web_auth.create_merge_code(db, "web", first)
    web_auth.complete_merge(db, code, 999, "bot")
    _, second = _email_user(db, "other@example.com")
    code = web_auth.create_merge_code(db, "web", second)
    try:
        web_auth.complete_merge(db, code, 999, "bot"); raise AssertionError("allowed")
    except ValueError as e:
        assert str(e) == "target_linked_other"


if __name__ == "__main__":
    test_full_flow(); test_code_alone_cannot_poll(); test_expiry_and_rate_limit()
    test_persistent_rate_limit(); test_web_origin_merge_into_telegram(); test_bot_origin_merge_and_wrong_side()
    test_merge_refused_when_telegram_linked_to_other_email(); test_email_verify_limit_not_reset_by_new_codes()
    print("OK")
