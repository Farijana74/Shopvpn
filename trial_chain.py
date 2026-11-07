"""One atomic trial reservation per Telegram ID across a root and all its shops."""
import os
import sqlite3
from pathlib import Path
from managed_resellers import AgencyError


def historical_shops(root,user_id):
    used=set()
    for bot in root.list_reseller_bots(active_only=False):
        path=Path(bot['db_path'])
        if not path.is_absolute():path=Path(root.db_path).resolve().parent/path
        if not path.is_file():continue
        conn=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=4)
        try:
            tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            row=conn.execute('SELECT test_used FROM users WHERE telegram_id=?',(user_id,)).fetchone() if 'users' in tables else None
            claim=conn.execute("SELECT 1 FROM managed_trials WHERE user_id=? AND status IN ('processing','completed')",(user_id,)).fetchone() if 'managed_trials' in tables else None
            if (row and row[0]) or claim:used.add(str(path.resolve()))
        finally:conn.close()
    return used


def claim(root,user_id,owner,allow_retry=False):
    owner=os.path.abspath(owner)
    try:history=historical_shops(root,user_id)
    except sqlite3.Error as exc:raise AgencyError('بررسی سوابق تست ممکن نشد؛ دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.') from exc
    with root._get_conn() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS chain_trial_claims(user_id INTEGER PRIMARY KEY,owner TEXT NOT NULL,status TEXT NOT NULL)')
        conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT owner,status FROM chain_trial_claims WHERE user_id=?',(user_id,)).fetchone()
        if old:
            if allow_retry and old['owner']==owner:return False
            raise AgencyError('شما قبلاً در این زنجیره تست گرفته‌اید یا درخواست تست در حال پردازش دارید؛ هر کاربر فقط یک تست دارد.')
        root_user=conn.execute('SELECT test_used FROM users WHERE telegram_id=?',(user_id,)).fetchone()
        if (root_user and root_user[0]) or history-{owner}:
            previous=os.path.abspath(root.db_path) if root_user and root_user[0] else sorted(history-{owner})[0]
            conn.execute("INSERT INTO chain_trial_claims(user_id,owner,status) VALUES(?,?,'completed')",(user_id,previous))
            conn.commit()
            raise AgencyError('شما قبلاً در ربات اصلی یا یکی از نماینده‌های این زنجیره تست گرفته‌اید.')
        conn.execute('UPDATE users SET test_used=test_used+1 WHERE telegram_id=?',(user_id,))
        conn.execute("INSERT INTO chain_trial_claims(user_id,owner,status) VALUES(?,?,'processing')",(user_id,owner))
    return not bool(history)


def release(root,user_id,owner):
    with root._get_conn() as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chain_trial_claims'").fetchone()
        if not exists:return False
        deleted=conn.execute("DELETE FROM chain_trial_claims WHERE user_id=? AND owner=? AND status='processing'",(user_id,os.path.abspath(owner))).rowcount
        if deleted:conn.execute('UPDATE users SET test_used=MAX(test_used-1,0) WHERE telegram_id=?',(user_id,))
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
                changed=conn.execute('UPDATE users SET test_used=test_used+1 WHERE telegram_id=? AND test_used<1',(user_id,)).rowcount
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
