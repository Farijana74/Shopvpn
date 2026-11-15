"""One atomic trial reservation per Telegram ID across a root and all its shops."""
import os
import logging
import sqlite3
from pathlib import Path
from managed_resellers import AgencyError


def historical_shops(root,user_id):
    used=set()
    for bot in root.list_reseller_bots(active_only=False):
        path=Path(bot['db_path'])
        if not path.is_absolute():path=Path(root.db_path).resolve().parent/path
        if not path.is_file():
            if dict(bot).get('has_live_bot',1):
                logging.getLogger(__name__).error('Trial history database is missing for reseller #%s',bot['id'])
                raise AgencyError('بررسی سوابق تست یکی از فروشگاه‌ها ممکن نیست؛ با پشتیبانی تماس بگیرید.')
            continue
        conn=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=4)
        try:
            tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            row=conn.execute('SELECT test_used FROM users WHERE telegram_id=?',(user_id,)).fetchone() if 'users' in tables else None
            claim=conn.execute("SELECT 1 FROM managed_trials WHERE user_id=? AND status IN ('processing','completed')",(user_id,)).fetchone() if 'managed_trials' in tables else None
            if (row and row[0]) or claim:used.add(str(path.resolve()))
        finally:conn.close()
    return used


def _quota_schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS chain_trial_claims(user_id INTEGER PRIMARY KEY,owner TEXT NOT NULL,status TEXT NOT NULL,extra_used INTEGER NOT NULL DEFAULT 0)')
    if 'extra_used' not in {r[1] for r in conn.execute('PRAGMA table_info(chain_trial_claims)')}:
        conn.execute('ALTER TABLE chain_trial_claims ADD COLUMN extra_used INTEGER NOT NULL DEFAULT 0')
    conn.execute('CREATE TABLE IF NOT EXISTS trial_allowances(user_id INTEGER PRIMARY KEY,remaining INTEGER NOT NULL DEFAULT 0 CHECK(remaining>=0))')
    conn.execute('CREATE TABLE IF NOT EXISTS trial_allowance_grants(request_key TEXT PRIMARY KEY,user_id INTEGER NOT NULL,amount INTEGER NOT NULL)')


def grant_extra(root,user_id,amount,request_key=None):
    if isinstance(amount,bool) or not isinstance(amount,int) or not 1 <= amount <= 1000000:
        raise ValueError('تعداد تست اضافه باید عددی بین ۱ و ۱۰۰۰۰۰۰ باشد.')
    if not isinstance(user_id,int) or isinstance(user_id,bool) or user_id <= 0:
        raise ValueError('آیدی عددی کاربر معتبر نیست.')
    with root._get_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _quota_schema(conn)
        old=conn.execute('SELECT user_id,amount FROM trial_allowance_grants WHERE request_key=?',(request_key,)).fetchone() if request_key else None
        if old and (old['user_id']!=user_id or old['amount']!=amount):
            raise ValueError('این درخواست قبلاً برای سهمیهٔ دیگری ثبت شده است.')
        if not old:
            conn.execute('INSERT INTO trial_allowances(user_id,remaining) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET remaining=remaining+excluded.remaining',(user_id,amount))
            if request_key:conn.execute('INSERT INTO trial_allowance_grants(request_key,user_id,amount) VALUES(?,?,?)',(request_key,user_id,amount))
        return conn.execute('SELECT remaining FROM trial_allowances WHERE user_id=?',(user_id,)).fetchone()[0]


def quota_status(local,user_id):
    root=primary_for(local)
    try:
        history=historical_shops(root,user_id)
        with root._get_conn() as conn:
            tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            old=conn.execute('SELECT status FROM chain_trial_claims WHERE user_id=?',(user_id,)).fetchone() if 'chain_trial_claims' in tables else None
            extra=conn.execute('SELECT remaining FROM trial_allowances WHERE user_id=?',(user_id,)).fetchone() if 'trial_allowances' in tables else None
            user=conn.execute('SELECT test_used FROM users WHERE telegram_id=?',(user_id,)).fetchone()
        local_user=local.get_user(user_id)
        processing=bool(old and old['status']=='processing')
        used=bool(old or history or (user and user[0]) or (local_user and local_user['test_used']))
        bonus=int(extra[0]) if extra else 0
        return dict(remaining=bonus+int(not used),extra_remaining=bonus,processing=processing)
    finally:
        if root is not local:root.close()


def claim(root,user_id,owner,allow_retry=False):
    owner=os.path.abspath(owner)
    try:history=historical_shops(root,user_id)
    except sqlite3.Error as exc:raise AgencyError('بررسی سوابق تست ممکن نشد؛ دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.') from exc
    with root._get_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _quota_schema(conn)
        old=conn.execute('SELECT owner,status FROM chain_trial_claims WHERE user_id=?',(user_id,)).fetchone()
        extra=conn.execute('SELECT remaining FROM trial_allowances WHERE user_id=?',(user_id,)).fetchone()
        bonus=int(extra[0]) if extra else 0
        root_user=conn.execute('SELECT test_used FROM users WHERE telegram_id=?',(user_id,)).fetchone()
        previous=bool(old or (root_user and root_user[0]) or history)
        if old and old['status']=='processing':
            if allow_retry and old['owner']==owner:return False
            raise AgencyError('درخواست تست شما در حال پردازش است؛ لطفاً منتظر بمانید.')
        if previous and bonus<=0:
            if old:
                if allow_retry and old['owner']==owner:return False
                raise AgencyError('سهمیهٔ تست شما تمام شده است.')
            previous_owner=os.path.abspath(root.db_path) if root_user and root_user[0] else sorted(history)[0]
            conn.execute("INSERT INTO chain_trial_claims(user_id,owner,status) VALUES(?,?,'completed')",(user_id,previous_owner))
            conn.commit()
            raise AgencyError('شما قبلاً تست رایگان دریافت کرده‌اید.')
        use_extra=int(previous)
        if use_extra:
            conn.execute('UPDATE trial_allowances SET remaining=remaining-1 WHERE user_id=? AND remaining>0',(user_id,))
        conn.execute('UPDATE users SET test_used=test_used+1 WHERE telegram_id=?',(user_id,))
        conn.execute("INSERT INTO chain_trial_claims(user_id,owner,status,extra_used) VALUES(?,?,'processing',?) ON CONFLICT(user_id) DO UPDATE SET owner=excluded.owner,status='processing',extra_used=excluded.extra_used",(user_id,owner,use_extra))
    return True


def release(root,user_id,owner):
    with root._get_conn() as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chain_trial_claims'").fetchone()
        if not exists:return False
        conn.execute('BEGIN IMMEDIATE')
        _quota_schema(conn)
        record=conn.execute("SELECT extra_used FROM chain_trial_claims WHERE user_id=? AND owner=? AND status='processing'",(user_id,os.path.abspath(owner))).fetchone()
        if record and record['extra_used']:
            # Preserve the earlier successful trial when rolling back an extra one.
            deleted=conn.execute("UPDATE chain_trial_claims SET status='completed',extra_used=0 WHERE user_id=? AND owner=? AND status='processing'",(user_id,os.path.abspath(owner))).rowcount
        else:
            deleted=conn.execute("DELETE FROM chain_trial_claims WHERE user_id=? AND owner=? AND status='processing'",(user_id,os.path.abspath(owner))).rowcount
        if deleted:
            conn.execute('UPDATE users SET test_used=MAX(test_used-1,0) WHERE telegram_id=?',(user_id,))
            if record and record['extra_used']:
                conn.execute('INSERT INTO trial_allowances(user_id,remaining) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET remaining=remaining+1',(user_id,))
        return bool(deleted)


def complete(root,user_id,owner):
    with root._get_conn() as conn:
        conn.execute("UPDATE chain_trial_claims SET status='completed' WHERE user_id=? AND owner=?",(user_id,os.path.abspath(owner)))


def primary_for(local):
    """Older full-featured reseller shops share the installation root as well."""
    from database import Database
    from config import DB_PATH
    candidate=local.get_setting('managed_root_db_path','')
    if not candidate and local.get_setting('miniapp_tenant_id',''):candidate=DB_PATH
    if not candidate or os.path.abspath(candidate)==os.path.abspath(local.db_path):return local
    if not Path(candidate).is_file():raise AgencyError('دیتابیس اصلی برای بررسی سهمیه تست در دسترس نیست.')
    root=Database(candidate)
    if not any(os.path.abspath(b['db_path'])==os.path.abspath(local.db_path) for b in root.list_reseller_bots(active_only=False)):
        root.close();raise AgencyError('ارتباط ربات نماینده با ربات اصلی معتبر نیست.')
    return root


def reserve_standard(local,user_id):
    root=primary_for(local)
    acquired=False
    try:
        acquired=claim(root,user_id,local.db_path)
        if root is not local:
            with local._get_conn() as conn:
                changed=conn.execute('UPDATE users SET test_used=test_used+1 WHERE telegram_id=?',(user_id,)).rowcount
            if not changed:
                if acquired:release(root,user_id,local.db_path)
                return False
        return True
    except Exception:
        if acquired:release(root,user_id,local.db_path)
        raise
    finally:
        if root is not local:root.close()


def release_standard(local,user_id):
    root=primary_for(local)
    try:
        released=release(root,user_id,local.db_path)
        if root is not local:
            if released:
                with local._get_conn() as conn:conn.execute('UPDATE users SET test_used=MAX(test_used-1,0) WHERE telegram_id=?',(user_id,))
            return True
        return released
    finally:
        if root is not local:root.close()


def complete_standard(local,user_id):
    root=primary_for(local)
    try:complete(root,user_id,local.db_path)
    finally:
        if root is not local:root.close()
