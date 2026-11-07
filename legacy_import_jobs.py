"""Owner-only UI import jobs, published by an independent systemd worker."""
import argparse
import asyncio
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
import shutil
import stat
import zipfile
from pathlib import PurePosixPath
from config import BASE_DIR,DB_PATH,OWNER_ID,BOT_TOKEN
from legacy_migration import prepare,apply_plan,connect,digest,MigrationError

ROOT=Path(BASE_DIR)
JOBS_DIR=ROOT/'backups'/'legacy-ui'
LOCAL_SOURCE=ROOT.parent/'v2ray_bot'/'bot_database.db'
MAX_UPLOAD=100*1024*1024

def folder(job_id):
    if not re.fullmatch(r'[a-f0-9]{32}',job_id or ''):raise MigrationError('شناسه درخواست معتبر نیست.')
    return JOBS_DIR/job_id

def save(path,value):
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as handle:json.dump(value,handle,ensure_ascii=False,indent=2)
    os.replace(temp,path)

@contextmanager
def locked(job_id):
    base=folder(job_id)
    if not base.is_dir():raise MigrationError('درخواست پیدا نشد.')
    with open(base/'lock','a') as handle:
        os.chmod(base/'lock',0o600);fcntl.flock(handle,fcntl.LOCK_EX)
        try:yield base
        finally:fcntl.flock(handle,fcntl.LOCK_UN)

def load(job_id,principal=None):
    try:value=json.loads((folder(job_id)/'state.json').read_text())
    except (FileNotFoundError,ValueError):raise MigrationError('درخواست پیدا نشد.') from None
    if principal is not None and value['principal']!=principal:raise MigrationError('دسترسی به درخواست مجاز نیست.')
    return value

def public(value):
    return {k:value.get(k) for k in ('job_id','status','error','source_counts','current_counts','backup_available')}

def new_job(principal,local_source=False):
    JOBS_DIR.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(JOBS_DIR,0o700)
    job_id=uuid.uuid4().hex;base=folder(job_id);base.mkdir(mode=0o700)
    source=LOCAL_SOURCE if local_source else base/'source.db'
    if local_source and not source.is_file():raise MigrationError('دیتابیس v2ray_bot روی این سرور پیدا نشد؛ فایل را ارسال کنید.')
    state=dict(job_id=job_id,principal=principal,target=str(Path(DB_PATH).resolve()),owner_id=OWNER_ID,source=str(source.resolve()),status='uploading',created_at=time.time())
    save(base/'state.json',state)
    return state

UPLOAD_SUFFIXES=('.db','.sqlite','.sqlite3','.zip')

def unpack_backup(job_id,principal):
    """Normalize an uploaded SQLite/ZIP backup, without touching live databases."""
    from legacy_migration import tables,_safe_rebase
    value=load(job_id,principal);base=folder(job_id);source=base/'source.db'
    if value['status']!='uploading':raise MigrationError('این فایل قبلاً بررسی شده است.')
    if not zipfile.is_zipfile(source):return
    archive=base/'backup.zip';source.rename(archive)
    unpacked=base/'archive';unpacked.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(archive) as zf:
            infos=zf.infolist()
            if len(infos)>2000 or sum(i.file_size for i in infos)>MAX_UPLOAD*5:
                raise MigrationError('حجم بازشده یا تعداد فایل‌های بکاپ بیش از حد مجاز است.')
            names=set()
            for info in infos:
                name=PurePosixPath(info.filename)
                mode=info.external_attr>>16
                if name.is_absolute() or '..' in name.parts or '\\' in info.filename or stat.S_ISLNK(mode):
                    raise MigrationError('مسیر نامعتبر در فایل بکاپ وجود دارد.')
                key=str(name)
                if key in names:raise MigrationError('نام فایل تکراری در بکاپ وجود دارد.')
                names.add(key)
                dest=unpacked.joinpath(*name.parts)
                if info.is_dir():dest.mkdir(parents=True,exist_ok=True,mode=0o700);continue
                dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with zf.open(info) as inp,open(dest,'xb') as out:
                    os.chmod(dest,0o600);shutil.copyfileobj(inp,out)
        candidates=[p for p in unpacked.rglob('*') if p.is_file() and p.suffix.lower() in UPLOAD_SUFFIXES[:-1]]
        preferred=[p for p in candidates if p.name in ('main_bot.db','bot_database.db')]
        if len(preferred)==1:main=preferred[0]
        elif not preferred and len(candidates)==1:main=candidates[0]
        else:raise MigrationError('دیتابیس اصلی بکاپ مشخص نیست؛ فایل main_bot.db یا bot_database.db باید یکتا باشد.')
        shutil.copyfile(main,source);os.chmod(source,0o600)
        conn=connect(source)
        mapping={};old_main=None
        try:
            if 'reseller_bots' in tables(conn):
                for row in conn.execute('SELECT * FROM reseller_bots').fetchall():
                    if 'has_live_bot' in row.keys() and not row['has_live_bot']:continue
                    old=str(row['db_path']);basename=Path(old).name
                    matches=[p for p in candidates if p!=main and p.name in (f"reseller_{row['id']}.db",basename)]
                    if len(matches)!=1:raise MigrationError('فایل دیتابیس یکی از نمایندگان در بکاپ موجود نیست یا مبهم است.')
                    mapping[old]=str(matches[0])
                    conn.execute('UPDATE reseller_bots SET db_path=? WHERE id=?',(str(matches[0]),row['id']))
            manifest=main.parent/'manifest.txt'
            if manifest.is_file():
                for line in manifest.read_text(errors='replace').splitlines():
                    if line.startswith(main.name+'  <-  '):old_main=Path(line.split('  <-  ',1)[1].strip())
            if old_main:mapping[str(old_main)]=str(source)
            for db in [source]+[Path(p) for p in mapping.values() if p!=str(source)]:
                current=conn if db==source else connect(db)
                try:
                    _safe_rebase(current,old_main or source,source,mapping);current.commit()
                finally:
                    if current is not conn:current.close()
            conn.commit()
        finally:conn.close()
    except Exception as exc:
        if isinstance(exc,MigrationError):raise
        raise MigrationError('فایل ZIP بکاپ معتبر نیست یا دیتابیس قابل خواندن ندارد.') from exc

def prepare_job(job_id,principal):
    value=load(job_id,principal);base=folder(job_id)
    with locked(job_id):
        if value['status']!='uploading':raise MigrationError('این فایل قبلاً بررسی شده است.')
        value['status']='preparing';save(base/'state.json',value)
    try:
        report=prepare(value['source'],value['target'],base/'preview',value['owner_id'],mode='replace')
        if not report['ready']:raise MigrationError(report.get('error','بررسی دیتابیس ناموفق بود.'))
        value.update(status='ready',source_counts=report['report'].get('imported',{}),current_counts=report['report'].get('replaced',{}))
    except Exception as exc:
        value.update(status='failed',error=str(exc) if isinstance(exc,MigrationError) else 'فایل قابل تبدیل نیست؛ دیتابیس فعلی تغییر نکرد.')
    save(base/'state.json',value)
    return public(value)

@contextmanager
def import_lock(blocking=True):
    JOBS_DIR.mkdir(parents=True,exist_ok=True,mode=0o700)
    with open(JOBS_DIR/'import.lock','a') as handle:
        os.chmod(JOBS_DIR/'import.lock',0o600)
        try:fcntl.flock(handle,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:raise MigrationError('یک مهاجرت دیگر در حال اجراست؛ وضعیت همان درخواست را بررسی کنید.') from None
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def queue_job(job_id,principal,confirmation,launcher=None):
    if confirmation!='REPLACE':raise MigrationError('برای جایگزینی کامل، REPLACE را دقیق وارد کنید.')
    load(job_id,principal)
    with import_lock(blocking=False):
        marker=JOBS_DIR/'active.json'
        if marker.is_file():
            try:
                active=json.loads(marker.read_text())['job_id']
                current=load(active)
                if active!=job_id and current['status'] in ('queued','running'):
                    raise MigrationError('یک مهاجرت دیگر در صف یا در حال اجراست.')
            except (KeyError,ValueError,FileNotFoundError) as exc:
                if isinstance(exc,MigrationError):raise
        save(marker,{'job_id':job_id})
        return _queue_job(job_id,principal,confirmation,launcher)


def _queue_job(job_id,principal,confirmation,launcher=None):
    if confirmation!='REPLACE':raise MigrationError('برای جایگزینی کامل، REPLACE را دقیق وارد کنید.')
    with locked(job_id) as base:
        value=load(job_id,principal)
        if value['status'] in ('queued','running','completed'):return public(value)
        if value['status']!='ready':raise MigrationError('درخواست آماده تأیید نیست؛ پیش‌نمایش تازه تهیه کنید.')
        value['status']='queued';value.pop('error',None);save(base/'state.json',value)
        try:(launcher or launch)(job_id)
        except Exception:
            value.update(status='ready',error='راه‌اندازی اجرای مستقل ممکن نشد؛ سرویس‌ها تغییر نکردند. تنظیمات systemd سرور را بررسی کنید.')
            save(base/'state.json',value)
            raise MigrationError(value['error']) from None
    return public(value)

def cancel_job(job_id,principal):
    with locked(job_id) as base:
        value=load(job_id,principal)
        if value['status'] not in ('ready','failed','uploading'):raise MigrationError('اجرای در حال انجام را نمی‌توان لغو کرد.')
        value['status']='cancelled';save(base/'state.json',value)
    return public(value)

def launch(job_id):
    base=folder(job_id)
    subprocess.run(['systemd-run','--quiet','--no-ask-password','--collect',f'--unit=shopvpn-legacy-import-{job_id}',
                    '--property=Type=exec','--property=UMask=0077',f'--property=StandardOutput=append:{base / "worker.log"}',
                    f'--property=StandardError=append:{base / "worker.log"}',f'--working-directory={ROOT}',
                    sys.executable,str(ROOT/'legacy_import_jobs.py'),'--run',job_id],check=True,capture_output=True,timeout=15)

def preserve_owner_login(staged,before):
    # Preserve current owner logins, not source sessions whose numeric account
    # IDs could otherwise point at a different principal after replacement.
    src=connect(before,True);dst=connect(staged)
    try:
        owners=src.execute("SELECT * FROM web_admins WHERE role='owner' AND is_active=1").fetchall()
        if owners:
            dst.execute('DELETE FROM web_admins')
            columns={r[1] for r in dst.execute('PRAGMA table_info(web_admins)')}
            for row in owners:
                values={k:row[k] for k in row.keys() if k in columns}
                dst.execute('INSERT INTO web_admins('+','.join('"'+k+'"' for k in values)+') VALUES('+','.join('?' for _ in values)+')',list(values.values()))
        for name in ('mobile_app_tokens','mobile_fcm_tokens','web_push_subscriptions'):
            if dst.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone():dst.execute('DELETE FROM '+name)
        dst.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('admin_auth_epoch',?)",(uuid.uuid4().hex,))
        dst.commit()
    finally:src.close();dst.close()

def _run_job(job_id,runner=subprocess.run,delay=3,notify=True):
    base=folder(job_id)
    with locked(job_id):
        value=load(job_id)
        if value['status']!='queued':return public(value)
        if value['target']!=str(Path(DB_PATH).resolve()) or value['owner_id']!=OWNER_ID:raise MigrationError('مقصد درخواست مجاز نیست.')
        if value['source'] not in (str((base/'source.db').resolve()),str(LOCAL_SOURCE.resolve())):raise MigrationError('مبدأ درخواست مجاز نیست.')
        value.update(status='running');save(base/'state.json',value)
    if delay:time.sleep(delay)
    old=os.getenv('SHOPVPN_LEGACY_SERVICE','v2raybot');new=os.getenv('SHOPVPN_SERVICE_NAME','shopvpn-independent')
    stopped=[];success=False
    try:
        if old==new or not all(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*',s) for s in (old,new)):raise MigrationError('نام سرویس نصب قدیمی و جدید معتبر نیست.')
        suffixes=('', '-adminpanel','-miniapp','-api','-sub','-store')
        for prefix in (old,new):
            for suffix in suffixes:
                unit=prefix+suffix
                status=runner(['systemctl','is-active','--quiet',unit],capture_output=True,timeout=20).returncode
                if status not in (0,3,4):raise MigrationError('بررسی وضعیت سرویس‌ها ممکن نیست؛ انتقال انجام نشد.')
                if status==0:
                    stopped.append(unit)
                    value['stopped_units']=stopped;save(base/'state.json',value)
                    runner(['systemctl','stop',unit],check=True,capture_output=True,timeout=120)
        report=prepare(value['source'],value['target'],base/'execution',value['owner_id'],mode='replace')
        if not report['ready']:raise MigrationError(report.get('error','آماده‌سازی نهایی ناموفق بود.'))
        preserve_owner_login(base/'execution'/'result.db',base/'execution'/'before.db')
        staged=connect(base/'execution'/'result.db',True)
        try:report['result_digest']=digest(staged)
        finally:staged.close()
        save(base/'execution'/'manifest.json',report)
        apply_plan(base/'execution',services_stopped=True)
        success=True;value.update(status='completed',backup_available=True)
    except Exception as exc:
        # A report write can fail after SQLite committed. Detect publication
        # before deciding to resume the old polling service.
        try:
            expected=json.loads((base/'execution'/'manifest.json').read_text())['result_digest']
            current=connect(value['target'],True)
            try:success=digest(current)==expected
            finally:current.close()
        except Exception:success=False
        if success:
            value.update(status='completed',backup_available=True)
        else:
            value.update(status='failed',error=str(exc) if isinstance(exc,MigrationError) else 'مهاجرت کامل نشد؛ برای جزئیات، گزارش درخواست را بررسی کنید.')
    finally:
        final_status=value['status']
        value['status']='running'
        try:save(base/'state.json',value)
        except OSError:pass
        restart_failed=[]
        for unit in stopped:
            if success and unit in {old+s for s in ('','-adminpanel','-miniapp','-api','-sub','-store')}:continue
            try:
                result=runner(['systemctl','start',unit],capture_output=True,timeout=120)
                if result.returncode:restart_failed.append(unit)
            except Exception:restart_failed.append(unit)
        value['status']=final_status
        if restart_failed:
            value['error']='دیتابیس منتقل شد، اما راه‌اندازی این سرویس‌ها ناموفق بود: '+', '.join(restart_failed) if success else 'عملیات ناموفق بود و راه‌اندازی دوباره این سرویس‌ها هم ناموفق بود: '+', '.join(restart_failed)
        try:save(base/'state.json',value)
        except OSError:pass
    if notify:
        try:
            from admin_panel.telegram_notify import send_message
            text=('⚠️ دیتابیس منتقل شد، اما راه‌اندازی برخی سرویس‌ها ناموفق بود؛ وضعیت درخواست را بررسی کنید.' if restart_failed else '✅ مهاجرت دیتابیس قدیمی کامل شد. اطلاعات قبلی در بکاپ محفوظ است. برای ورود پنل وب دوباره لاگین کنید.') if success else '❌ مهاجرت انجام نشد. وضعیت درخواست و گزارش خطا را بررسی کنید.'
            asyncio.run(send_message(BOT_TOKEN,OWNER_ID,text))
        except Exception:pass
    return public(value)

def run_job(job_id,runner=subprocess.run,delay=3,notify=True):
    with import_lock():
        return _run_job(job_id,runner,delay,notify)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True)
    run_job(parser.parse_args().run)
