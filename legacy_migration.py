"""Offline, staged v2ray_bot import. Preparation never modifies either live DB."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid
from datetime import datetime,timezone

class MigrationError(ValueError):pass

def quote(value):return '"'+value.replace('"','""')+'"'

def connect(path,readonly=False):
    path=Path(path).resolve()
    conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10) if readonly else sqlite3.connect(path,timeout=10)
    conn.row_factory=sqlite3.Row
    conn.enable_load_extension(False)
    return conn

def tables(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

def digest(conn):
    result=hashlib.sha256()
    for row in conn.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"):
        result.update(json.dumps(tuple(row),ensure_ascii=True).encode())
    names=tables(conn)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():names+=['sqlite_sequence']
    for name in names:
        result.update(name.encode())
        cols=[r[1] for r in conn.execute('PRAGMA table_info('+quote(name)+')')]
        query='SELECT * FROM '+quote(name)+' ORDER BY '+','.join(quote(c) for c in cols)
        for row in conn.execute(query):
            values=[{'bytes':v.hex()} if isinstance(v,bytes) else v for v in row]
            result.update(json.dumps(values,ensure_ascii=True,separators=(',',':')).encode())
    return result.hexdigest()

def snapshot(source,target):
    src=connect(source,True)
    dst=connect(target)
    try:
        src.backup(dst)
        dst.commit()
        check(dst)
        return digest(dst)
    finally:dst.close();src.close();os.chmod(target,0o600)

def check(conn):
    if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise MigrationError('فایل دیتابیس سالم نیست.')
    if list(conn.execute('PRAGMA foreign_key_check')):raise MigrationError('ارجاع ناسالم در دیتابیس وجود دارد؛ قبل از انتقال باید بررسی شود.')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE sql LIKE 'CREATE VIRTUAL TABLE%'").fetchone():raise MigrationError('انتقال جدول مجازی در این ابزار پشتیبانی نشده است.')

def _owner(conn,fallback):
    if 'admins' not in tables(conn):return fallback
    cols={r[1] for r in conn.execute('PRAGMA table_info(admins)')}
    row=conn.execute("SELECT telegram_id FROM admins WHERE role='owner' LIMIT 1").fetchone() if 'role' in cols else conn.execute('SELECT telegram_id FROM admins LIMIT 1').fetchone()
    return int(row[0]) if row else fallback

def upgrade(path,owner_id):
    # Only the private snapshot is opened for schema migration. No queue worker,
    # Telegram message, remote provisioning or file-index scan may run here.
    from database import Database
    conn=connect(path)
    try:
        old_owner=_owner(conn,owner_id)
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():conn.execute('DROP TRIGGER '+quote(row[0]))
        conn.commit()
    finally:conn.close()
    db=Database(str(path))
    try:
        db.init_db(old_owner,maintenance=True)
        with db._get_conn() as c:
            if old_owner!=owner_id:
                c.execute("UPDATE admins SET role='admin' WHERE role='owner'")
                c.execute("INSERT INTO admins(telegram_id,role) VALUES(?,'owner') ON CONFLICT(telegram_id) DO UPDATE SET role='owner'",(owner_id,))
    finally:db.close()
    conn=connect(path)
    try:check(conn)
    finally:conn.close()

# Core customer data with explicitly reviewed relationships. Advanced business
# modules are never silently discarded: nonempty unhandled tables block merging.
CORE=['panel_servers','categories','custom_gateways','card_to_card_cards','custom_config_products',
      'custom_config_pricing_tiers','custom_config_product_pricing_tiers','products','test_config_plans',
      'users','discount_codes','configs','custom_configs','orders','ticket_departments','tickets',
      'ticket_department_admins','ticket_messages','wallet_transactions','wallet_topups',
      'custom_config_history','config_activity','service_ratings','location_change_log',
      'reseller_product_credit','reseller_credit_log','admin_logs','test_configs','product_waitlist','order_surveys']
IGNORE={'settings','admins','languages','translations','translation_manifests','translation_history','bot_text_registry',
        'wheel_prizes','reseller_tiers','gift_themes','panel_health','panel_health_events','admin_presence',
        'report_topics','report_topic_labels','report_topic_stats','credential_index','web_push_subscriptions',
        'mobile_app_tokens','mobile_fcm_tokens','web_admins','wallet_tx_label','sub_access_log','sub_usage_log',
        'payment_webhook_logs','legacy_import_map','legacy_import_runs'}
NATURAL={'panel_servers':('panel_type','api_url','api_username','is_mirror'), 'categories':('name',),
         'users':('telegram_id',),'products':('category_id','name','provision_server_id','provision_ssh_node_id'),
         'custom_configs':('panel_server_id','username'),'configs':('link',),'test_configs':('link',),
         'orders':('id','user_id','created_at','base_price','quantity','config_name'),'wallet_topups':('id','user_id','created_at','amount','receipt_file_id'),'tickets':('id','user_id','created_at','subject'),'discount_codes':('code',),'ticket_departments':('name',),
         'custom_gateways':('gateway_key','name'),'card_to_card_cards':('card_number',),
         'custom_config_products':('name','panel_server_id'),
         'test_config_plans':('name','panel_server_id','volume_mb','duration_hours'),
         'reseller_product_credit':('reseller_id','product_id')}
REL={'category_id':'categories','panel_server_id':'panel_servers','mirror_source_id':'panel_servers',
     'custom_product_id':'custom_config_products','custom_panel_server_id':'panel_servers','old_panel_server_id':'panel_servers','new_panel_server_id':'panel_servers',
     'product_id':'products','reseller_product_id':'products','fixed_product_main_id':'products',
     'reseller_panel_id':'panel_servers','order_id':'orders','config_id':'configs','custom_config_id':'custom_configs',
     'discount_code_id':'discount_codes','department_id':'ticket_departments','ticket_id':'tickets'}
OVERRIDES={('custom_configs','product_id'):'custom_config_products',('custom_config_product_pricing_tiers','product_id'):'custom_config_products'}
MONEY={'users':('referral_credit','reseller_credit_gb','score','credit_limit','reseller_tier'),
       'reseller_product_credit':('qty_remaining',)}

def _row_digest(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,default=lambda v:{'bytes':v.hex()},ensure_ascii=True).encode()).hexdigest()

def merge(source,target,source_key,wallet_policy='reject',raw_digest=None):
    src=connect(source,True);dst=connect(target)
    summary={'inserted':{},'matched':{},'preserved_configuration':[],'conflicts':[],'wallet_policy':wallet_policy,'kept_current_financial':[]}
    try:
        if raw_digest and 'legacy_import_runs' in tables(dst) and dst.execute('SELECT 1 FROM legacy_import_runs WHERE source_digest=? LIMIT 1',(raw_digest,)).fetchone():
            summary['already_imported_snapshot']=True
            return summary
        for table in tables(src):
            count=src.execute('SELECT COUNT(*) FROM '+quote(table)).fetchone()[0]
            if not count:continue
            if table in IGNORE:summary['preserved_configuration'].append(table)
            elif table not in CORE:summary['conflicts'].append({'table':table,'kind':'unsupported_nonempty_table','count':count})
        if summary['conflicts']:return summary
        dst.executescript('''CREATE TABLE IF NOT EXISTS legacy_import_map(source_key TEXT,table_name TEXT,old_id TEXT,new_id INTEGER,row_digest TEXT,PRIMARY KEY(source_key,table_name,old_id));
            CREATE TABLE IF NOT EXISTS legacy_import_runs(source_key TEXT,source_digest TEXT,created_at TEXT,PRIMARY KEY(source_key,source_digest));''')
        dst.execute('PRAGMA foreign_keys=OFF')
        dst.execute('BEGIN IMMEDIATE')
        snapshot_digest=raw_digest or digest(src)
        same_snapshot=bool(dst.execute('SELECT 1 FROM legacy_import_runs WHERE source_key=? AND source_digest=?',(source_key,snapshot_digest)).fetchone())
        maps={};data={};existing={};pks={};recorded_keys=set()
        for table in CORE:
            if table not in tables(src):continue
            info=list(src.execute('PRAGMA table_info('+quote(table)+')'))
            pks[table]=[r['name'] for r in sorted(info,key=lambda r:r['pk']) if r['pk']]
            data[table]=[dict(r) for r in src.execute('SELECT * FROM '+quote(table))]
            if pks[table]!=['id']:continue
            maximum=dst.execute('SELECT COALESCE(MAX(id),0) FROM '+quote(table)).fetchone()[0]
            maps[table]={}
            for offset,row in enumerate(data[table],1):
                recorded=dst.execute('SELECT new_id,row_digest FROM legacy_import_map WHERE source_key=? AND table_name=? AND old_id=?',(source_key,table,str(row['id']))).fetchone()
                maps[table][row['id']]=recorded[0] if recorded else maximum+offset
                if recorded:
                    if not same_snapshot and recorded[1]!=_row_digest(row):raise MigrationError(f'رکورد {table} پس از انتقال قبلی در مبدأ تغییر کرده است؛ بررسی دستی لازم است.')
                    if not dst.execute('SELECT 1 FROM '+quote(table)+' WHERE id=?',(recorded[0],)).fetchone():raise MigrationError('رکورد انتقال‌یافته در مقصد حذف شده؛ بازسازی خودکار انجام نشد.')
                    existing[table,row['id']]=True
                    recorded_keys.add((table,row['id']))
        def transform(table,row):
            row=row.copy()
            for col,val in list(row.items()):
                ref=OVERRIDES.get((table,col),REL.get(col))
                if table=='custom_configs' and col=='product_id' and row.get('source')=='test':ref='test_config_plans'
                if table=='orders' and col=='renewal_target_id':ref='custom_configs' if row.get('renewal_target_kind')=='custom' else 'configs'
                if not ref or val in (None,0,''):continue
                if val not in maps.get(ref,{}):raise MigrationError(f'ارجاع حل‌نشده در {table}.{col}؛ انتقال متوقف شد.')
                row[col]=maps[ref][val]
            if table=='users' and (row.get('coin_mode')=='lottery' or row.get('coin_credit_synced_tx')):raise MigrationError('کیف پول سکه‌ای به انتقال کامل نیاز دارد تا ترتیب تراکنش‌ها حفظ شود.')
            if table=='orders' and row.get('lootbox_type_id'):raise MigrationError('سفارش بسته شانسی به انتقال کامل نیاز دارد.')
            if table=='products' and row.get('provision_ssh_node_id'):raise MigrationError('محصول SSH به انتقال کامل یا بررسی کلیدهای SSH نیاز دارد.')
            if table=='discount_codes' and row.get('product_ids'):
                try:
                    ids=json.loads(row['product_ids'])
                    row['product_ids']=json.dumps([maps['products'][int(v)] for v in ids])
                except (ValueError,TypeError,KeyError):raise MigrationError('محدوده محصولات کد تخفیف قابل تبدیل نیست.')
            if row.get('payment_methods'):
                try:
                    methods=json.loads(row['payment_methods'])
                    row['payment_methods']=json.dumps(['customgw:'+str(maps['custom_gateways'][int(v.split(':')[1])]) if isinstance(v,str) and v.startswith('customgw:') else v for v in methods])
                except (ValueError,TypeError,KeyError,IndexError):raise MigrationError('روش پرداخت محصول قابل تبدیل نیست.')
            return row
        # Resolve natural identities before writing rows; all IDs already have
        # provisional mappings, including cyclic order/config relationships.
        for table,rows in data.items():
            if pks[table]!=['id']:continue
            for row in rows:
                if (table,row['id']) in existing:continue
                transformed=transform(table,row)
                keys=NATURAL.get(table)
                if not keys:keys=tuple(row.keys())
                clauses=[];values=[]
                for key in keys:
                    clauses.append(('rtrim('+quote(key)+", '/' )" if table=='panel_servers' and key=='api_url' else quote(key))+' IS ?');values.append(str(transformed.get(key)).rstrip('/') if table=='panel_servers' and key=='api_url' else transformed.get(key))
                matches=dst.execute('SELECT * FROM '+quote(table)+' WHERE '+' AND '.join(clauses),values).fetchall()
                if len(matches)>1:raise MigrationError(f'چند رکورد هم‌نام در {table} پیدا شد؛ انتخاب خودکار امن نیست.')
                if matches:
                    maps[table][row['id']]=matches[0]['id'];existing[table,row['id']]=True
        for table,rows in data.items():
            target_cols={r[1] for r in dst.execute('PRAGMA table_info('+quote(table)+')')}
            for row in rows:
                new=transform(table,row)
                if pks[table]==['id']:
                    new['id']=maps[table][row['id']]
                    same=dst.execute('SELECT * FROM '+quote(table)+' WHERE id=?',(new['id'],)).fetchone() if (table,row['id']) in existing else None
                else:
                    keys=pks[table]
                    if not keys:raise MigrationError(f'جدول {table} کلید مشخصی ندارد.')
                    same=dst.execute('SELECT * FROM '+quote(table)+' WHERE '+' AND '.join(quote(k)+' IS ?' for k in keys),[new[k] for k in keys]).fetchone()
                if same:
                    differences=[] if (table,row.get('id')) in recorded_keys else [col for col in MONEY.get(table,()) if col in new and new[col]!=same[col]]
                    if differences and wallet_policy=='reject':
                        summary['conflicts'].append({'table':table,'kind':'financial_conflict','fields':differences})
                    if differences and wallet_policy=='keep-current':summary['kept_current_financial'].append({'table':table,'fields':differences})
                    if table=='custom_configs' and same['user_id']!=new['user_id']:raise MigrationError('مالکیت سرویس مشابه در دو دیتابیس متفاوت است.')
                    if table=='configs' and same['assigned_user_id'] not in (None,new['assigned_user_id']) and new['assigned_user_id'] is not None:raise MigrationError('یک لینک به دو کاربر متفاوت تعلق دارد.')
                    counters_by_table={'users':('test_used','referral_first_purchase_rewarded','referral_renewal_rewarded_count','referral_invite_bonus_given','referral_free_config_given','signup_gift_given'),'discount_codes':('used_count',),'products':('discount_used_count',)}
                    fields=counters_by_table.get(table,())
                    counters={k:max(int(new.get(k) or 0),int(same[k] or 0)) for k in fields if k in target_cols}
                    if counters:dst.execute('UPDATE '+quote(table)+' SET '+','.join(quote(k)+'=?' for k in counters)+' WHERE id=?',list(counters.values())+[same['id']])
                    summary['matched'][table]=summary['matched'].get(table,0)+1
                else:
                    new={k:v for k,v in new.items() if k in target_cols}
                    if 'admin_message_id' in new:new['admin_message_id']=None
                    if 'admin_chat_id' in new:new['admin_chat_id']=None
                    dst.execute('INSERT INTO '+quote(table)+'('+','.join(quote(k) for k in new)+') VALUES('+','.join('?' for _ in new)+')',list(new.values()))
                    summary['inserted'][table]=summary['inserted'].get(table,0)+1
                if pks[table]==['id']:
                    dst.execute('INSERT OR REPLACE INTO legacy_import_map VALUES(?,?,?,?,?)',(source_key,table,str(row['id']),new['id'],_row_digest(row)))
        if summary['conflicts']:dst.rollback();return summary
        dst.execute('INSERT OR IGNORE INTO legacy_import_runs VALUES(?,?,?)',(source_key,snapshot_digest,datetime.now(timezone.utc).isoformat()))
        check(dst)
        dst.commit()
        return summary
    except Exception:
        dst.rollback();raise
    finally:src.close();dst.close()


def _empty_destination(conn,owner_id):
    # Default settings and the owner's automatically created zero-balance
    # profile are a fresh installation, not a customer database to merge.
    for table in tables(conn):
        if table in IGNORE:continue
        if table=='users':
            for user in conn.execute('SELECT * FROM users'):
                if user['telegram_id']!=owner_id:return False
                fields=('referral_credit','score','reseller_credit_gb','credit_limit','test_used','is_reseller','coin_credit_synced_tx')
                if any(user[field] for field in fields if field in user.keys()):return False
                if 'reseller_tier' in user.keys() and user['reseller_tier']:return False
            continue
        if conn.execute('SELECT EXISTS(SELECT 1 FROM '+quote(table)+')').fetchone()[0]:return False
    return True


def _safe_rebase(conn,old_db,new_db,overrides=None):
    old_root,new_root=old_db.parent,new_db.parent
    def rebase(value):
        if overrides and value in overrides:return overrides[value]
        if value==str(old_db):return str(new_db)
        if value==str(new_db) or value.startswith(str(new_root)+os.sep):return value
        return str(new_root)+value[len(str(old_root)):] if value.startswith(str(old_root)+os.sep) else value
    for table in tables(conn):
        columns={r[1] for r in conn.execute('PRAGMA table_info('+quote(table)+')')}
        for column in ('managed_root_db_path','managed_root_db','db_path'):
            if column not in columns or table=='reseller_bots':continue
            for row in conn.execute('SELECT rowid,'+quote(column)+' FROM '+quote(table)+' WHERE '+quote(column)+' IS NOT NULL').fetchall():
                value=str(row[1])
                if rebase(value)!=value:
                    conn.execute('UPDATE '+quote(table)+' SET '+quote(column)+'=? WHERE rowid=?',(rebase(value),row[0]))
    if 'managed_purchases' in tables(conn):
        for row in conn.execute('SELECT request_key,charges FROM managed_purchases').fetchall():
            charges=json.loads(row[1])
            for charge in charges:charge[0]=rebase(str(charge[0]))
            conn.execute('UPDATE managed_purchases SET charges=? WHERE request_key=?',(json.dumps(charges),row[0]))
    if 'pending_db_purges' in tables(conn):conn.execute('DELETE FROM pending_db_purges')
    if 'settings' in tables(conn):
        for key in ('managed_root_db_path',):
            row=conn.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            if row and rebase(str(row[0]))!=str(row[0]):conn.execute('UPDATE settings SET value=? WHERE key=?',(rebase(str(row[0])),key))


def prepare(source,target,plan_dir,owner_id,mode='replace',source_id=None,wallet_policy='reject'):
    source,target=Path(source).resolve(),Path(target).resolve()
    if not isinstance(owner_id,int) or owner_id<=0:raise MigrationError('شناسه مالک نصب جدید باید عدد مثبت باشد.')
    folder=Path(plan_dir).resolve()
    if source==target or (target.exists() and source.exists() and os.path.samefile(source,target)):raise MigrationError('مبدأ و مقصد نباید یک فایل باشند.')
    if not source.is_file():raise MigrationError('دیتابیس قدیمی پیدا نشد.')
    if mode=='auto':
        if target.exists():
            current=connect(target,True)
            try:mode='upgrade' if _empty_destination(current,owner_id) else 'merge'
            finally:current.close()
        else:mode='upgrade'
    if folder.exists():raise MigrationError('پوشه گزارش باید جدید و خالی باشد.')
    folder.mkdir(parents=True,mode=0o700)
    os.chmod(folder,0o700)
    manifest={'version':1,'mode':mode,'source':str(source),'target':str(target),'owner_id':owner_id,
              'children':[],'report':{},'ready':False,'created_at':datetime.now(timezone.utc).isoformat()}
    try:
        manifest['source_digest']=snapshot(source,folder/'source.db')
        src=connect(folder/'source.db',True)
        try:
            if not {'users','categories','products','orders','settings'}<=set(tables(src)):raise MigrationError('فایل، دیتابیس پشتیبانی‌شده v2ray_bot نیست.')
            user_columns={r[1] for r in src.execute('PRAGMA table_info(users)')}
            if not {'telegram_id','referral_credit'}<=user_columns:raise MigrationError('ساختار کیف پول این نسخه شناخته‌شده نیست؛ تبدیل دستی لازم است.')
        finally:src.close()
        snapshot(folder/'source.db',folder/'upgraded.db')
        upgrade(folder/'upgraded.db',owner_id)
        if target.exists():manifest['target_digest']=snapshot(target,folder/'before.db')
        else:manifest['target_digest']=None
        if mode in ('upgrade','replace'):
            if target.exists() and mode=='upgrade':
                conn=connect(folder/'before.db',True)
                try:
                    if not _empty_destination(conn,owner_id):raise MigrationError('مقصد اطلاعات دارد؛ انتقال کامل مجاز نیست. حالت merge را انتخاب کنید.')
                finally:conn.close()
            if mode=='replace' and target.exists():
                current=connect(folder/'before.db',True)
                try:
                    manifest['report']['replaced']={t:n for t in tables(current) if (n:=current.execute('SELECT COUNT(*) FROM '+quote(t)).fetchone()[0])>0}
                    manifest['current_children_backups']=[]
                    if 'reseller_bots' in tables(current):
                        for bot in current.execute('SELECT * FROM reseller_bots').fetchall():
                            path=Path(bot['db_path']);path=path.resolve() if path.is_absolute() else (target.parent/path).resolve()
                            if not path.is_relative_to(target.parent):raise MigrationError('مسیر نمایندگی فعلی خارج از پوشه نصب است؛ پشتیبان‌گیری دستی لازم است.')
                            if path.is_file():
                                artifact=f"before-child-{bot['id']}.db"
                                previous_digest=snapshot(path,folder/artifact)
                                manifest['current_children_backups'].append({'original_path':str(path),'artifact':artifact,'digest':previous_digest})
                finally:current.close()
            snapshot(folder/'upgraded.db',folder/'result.db')
            conn=connect(folder/'result.db')
            try:
                _safe_rebase(conn,source,target)
                if 'reseller_bots' in tables(conn):
                    for bot in conn.execute('SELECT * FROM reseller_bots').fetchall():
                        if 'has_live_bot' in bot.keys() and not bot['has_live_bot']:continue
                        path=Path(bot['db_path'])
                        path=(source.parent/path).resolve() if not path.is_absolute() else path.resolve()
                        if not path.is_relative_to(source.parent) or not path.is_file():raise MigrationError('مسیر یکی از دیتابیس‌های نمایندگی پیدا نشد یا خارج از پوشه قدیمی است.')
                        username=bot['bot_username']
                        if not re.fullmatch(r'[A-Za-z0-9_]{3,64}',username or ''):raise MigrationError('نام فایل نمایندگی معتبر نیست.')
                        destination=target.parent/'reseller_dbs'/(username+'.db')
                        if destination.exists():
                            if mode!='replace':raise MigrationError('فایل نمایندگی در مقصد از قبل وجود دارد؛ بازنویسی انجام نشد.')
                            destination=destination.with_name(username+'-legacy-'+uuid.uuid4().hex[:10]+'.db')
                        artifact=f"child-{bot['id']}.db"
                        original_digest=snapshot(path,folder/artifact)
                        upgrade(folder/artifact,int(bot['owner_telegram_id']))
                        child=connect(folder/artifact)
                        try:_safe_rebase(child,source,target);child.commit();check(child)
                        finally:child.close()
                        manifest['children'].append({'source':str(path),'source_digest':original_digest,'target':str(destination),'artifact':artifact})
                        conn.execute('UPDATE reseller_bots SET db_path=? WHERE id=?',(str(destination),bot['id']))
                paths={}
                for item in manifest['children']:
                    previous=Path(item['source'])
                    paths[str(previous)]=item['target']
                    paths[str(target.parent/previous.relative_to(source.parent))]=item['target']
                if paths:
                    _safe_rebase(conn,source,target,paths)
                    for item in manifest['children']:
                        child=connect(folder/item['artifact'])
                        try:_safe_rebase(child,source,target,paths);child.commit();check(child)
                        finally:child.close()
                conn.execute('CREATE TABLE IF NOT EXISTS legacy_import_runs(source_key TEXT,source_digest TEXT,created_at TEXT,PRIMARY KEY(source_key,source_digest))')
                conn.execute('INSERT OR IGNORE INTO legacy_import_runs VALUES(?,?,?)',(hashlib.sha256((source_id or str(source)).encode()).hexdigest(),manifest['source_digest'],datetime.now(timezone.utc).isoformat()))
                conn.commit();check(conn)
                manifest['report']['imported']={t:conn.execute('SELECT COUNT(*) FROM '+quote(t)).fetchone()[0] for t in tables(conn)}
            finally:conn.close()
        elif mode=='merge':
            if target.exists():snapshot(folder/'before.db',folder/'result.db')
            else:
                conn=connect(folder/'result.db');conn.close()
            upgrade(folder/'result.db',owner_id)
            key=source_id or str(source)
            manifest['report']=merge(folder/'upgraded.db',folder/'result.db',hashlib.sha256(key.encode()).hexdigest(),wallet_policy,manifest['source_digest'])
            if manifest['report'].get('conflicts'):raise MigrationError('تعارض پیدا شد؛ هیچ تغییری روی دیتابیس اصلی انجام نشده است.')
        else:raise MigrationError('حالت انتقال معتبر نیست.')
        conn=connect(folder/'result.db',True)
        try:manifest['result_digest']=digest(conn)
        finally:conn.close()
        for child in manifest['children']:
            conn=connect(folder/child['artifact'],True)
            try:child['result_digest']=digest(conn)
            finally:conn.close()
        manifest['ready']=True
    except (MigrationError,sqlite3.Error) as exc:
        manifest['error']=str(exc)
    finally:
        for item in folder.iterdir():
            if item.is_file():os.chmod(item,0o600)
        report=folder/'manifest.json'
        report.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
        os.chmod(report,0o600)
    return manifest


def _copy_contents(source,conn):
    src=connect(source,True)
    try:
        schema=src.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").fetchall()
        for kind in ('trigger','view'):
            for row in conn.execute('SELECT name FROM sqlite_master WHERE type=?',(kind,)).fetchall():conn.execute('DROP '+kind+' '+quote(row[0]))
        for name in tables(conn):conn.execute('DROP TABLE '+quote(name))
        for row in schema:
            if row['type']=='table':conn.execute(row['sql'])
        for name in tables(src):
            cols=[r[1] for r in src.execute('PRAGMA table_info('+quote(name)+')')]
            query='INSERT INTO '+quote(name)+'('+','.join(quote(c) for c in cols)+') VALUES('+','.join('?' for _ in cols)+')'
            conn.executemany(query,(tuple(row) for row in src.execute('SELECT * FROM '+quote(name))))
        if src.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
            conn.execute('DELETE FROM sqlite_sequence')
            conn.executemany('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)',(tuple(row) for row in src.execute('SELECT name,seq FROM sqlite_sequence')))
        for row in schema:
            if row['type']!='table':conn.execute(row['sql'])
        check(conn)
    finally:src.close()


def apply_plan(plan_dir,services_stopped=False):
    if not services_stopped:raise MigrationError('ابتدا سرویس‌های مبدأ و مقصد را متوقف کنید و --services-stopped را اضافه کنید.')
    folder=Path(plan_dir).resolve()
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf8'))
    if manifest.get('version')!=1 or not manifest.get('ready'):raise MigrationError('گزارش انتقال آماده اعمال نیست.')
    if (folder/'applied.json').exists():return {'already_applied':True}
    source=connect(manifest['source'],True)
    try:
        if digest(source)!=manifest['source_digest']:raise MigrationError('مبدأ پس از پیش‌نمایش تغییر کرده؛ گزارش تازه بسازید.')
    finally:source.close()
    target_path=Path(manifest['target'])
    if target_path.exists() and os.path.samefile(manifest['source'],target_path):raise MigrationError('مبدأ و مقصد به یک فایل اشاره می‌کنند.')
    staged=folder/'result.db';conn=connect(staged,True)
    try:
        if digest(conn)!=manifest['result_digest']:raise MigrationError('فایل آماده انتقال تغییر کرده است.')
    finally:conn.close()
    if manifest['target_digest'] is not None:
        before=connect(folder/'before.db',True)
        try:
            if digest(before)!=manifest['target_digest']:raise MigrationError('بکاپ پیش از انتقال تغییر کرده است؛ گزارش تازه بسازید.')
        finally:before.close()
    for previous in manifest.get('current_children_backups',[]):
        for path in (previous['original_path'],folder/previous['artifact']):
            current=connect(path,True)
            try:
                if digest(current)!=previous['digest']:raise MigrationError('اطلاعات یا بکاپ نمایندگی فعلی تغییر کرده است؛ گزارش تازه بسازید.')
            finally:current.close()
    created=[];target=Path(manifest['target']);was_new=not target.exists();committed=False
    if was_new and manifest['target_digest'] is not None:raise MigrationError('مقصد حذف شده است؛ انتقال متوقف شد.')
    target.parent.mkdir(parents=True,exist_ok=True)
    dst=connect(target)
    fsm_path=Path(str(target)+'.fsm.sqlite3');fsm_backup=folder/'before-fsm.db';fsm_changed=False
    try:
        dst.execute('PRAGMA foreign_keys=OFF');dst.execute('BEGIN IMMEDIATE')
        if manifest['target_digest'] is None:
            if tables(dst):raise MigrationError('پس از پیش‌نمایش، دیتابیس مقصد ایجاد شده است.')
        elif digest(dst)!=manifest['target_digest']:raise MigrationError('مقصد پس از پیش‌نمایش تغییر کرده؛ گزارش تازه بسازید.')
        for child in manifest['children']:
            conn=connect(child['source'],True)
            try:
                if digest(conn)!=child['source_digest']:raise MigrationError('دیتابیس نمایندگی مبدأ تغییر کرده است.')
            finally:conn.close()
            conn=connect(folder/child['artifact'],True)
            try:
                if digest(conn)!=child['result_digest']:raise MigrationError('فایل آماده نمایندگی تغییر کرده است.')
            finally:conn.close()
            path=Path(child['target']);path.parent.mkdir(parents=True,exist_ok=True)
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
            created.append(path);snapshot(folder/child['artifact'],path)
        if manifest['mode'] in ('replace','upgrade') and fsm_path.is_file():
            snapshot(fsm_path,fsm_backup)
            state_db=connect(fsm_path)
            try:
                if 'fsm_storage' not in tables(state_db):raise MigrationError('ساختار مراحل ذخیره‌شده ربات شناخته‌شده نیست.')
                state_db.execute('DELETE FROM fsm_storage');state_db.commit();fsm_changed=True
            finally:state_db.close()
        _copy_contents(staged,dst)
        dst.commit();committed=True
    except Exception:
        dst.rollback()
        if fsm_changed:
            snapshot(fsm_backup,fsm_path)
        for path in created:
            for suffix in ('','-wal','-shm','-journal'):Path(str(path)+suffix).unlink(missing_ok=True)
        raise
    finally:
        dst.close()
        if not committed and was_new:
            for suffix in ('','-wal','-shm','-journal'):Path(str(target)+suffix).unlink(missing_ok=True)
    os.chmod(target,0o600)
    result={'target':str(target),'backup':str(folder/'before.db') if manifest['target_digest'] else None,'applied_at':datetime.now(timezone.utc).isoformat()}
    marker=folder/'applied.json';marker.write_text(json.dumps(result,ensure_ascii=False,indent=2));os.chmod(marker,0o600)
    return result


def main():
    parser=argparse.ArgumentParser(description='انتقال امن دیتابیس قدیمی v2ray_bot با پیش‌نمایش و پشتیبان')
    commands=parser.add_subparsers(dest='command',required=True)
    prep=commands.add_parser('prepare')
    prep.add_argument('--source',required=True);prep.add_argument('--target');prep.add_argument('--owner-id',type=int)
    prep.add_argument('--mode',choices=['replace','auto','merge','upgrade'],default='replace');prep.add_argument('--plan-dir')
    prep.add_argument('--source-id');prep.add_argument('--wallet-policy',choices=['reject','keep-current'],default='reject')
    do=commands.add_parser('apply');do.add_argument('--plan-dir',required=True);do.add_argument('--services-stopped',action='store_true')
    args=parser.parse_args()
    try:
        if args.command=='prepare':
            from config import DB_PATH,OWNER_ID
            folder=args.plan_dir or str(Path(DB_PATH).parent/'backups'/('legacy-import-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]))
            result=prepare(args.source,args.target or DB_PATH,folder,args.owner_id or OWNER_ID,args.mode,args.source_id,args.wallet_policy)
            print(json.dumps({'plan_dir':folder,**result},ensure_ascii=False,indent=2))
            return 0 if result['ready'] else 2
        print(json.dumps(apply_plan(args.plan_dir,args.services_stopped),ensure_ascii=False,indent=2));return 0
    except (MigrationError,sqlite3.Error,OSError,ValueError) as exc:
        print('خطای انتقال: '+str(exc));return 2

if __name__=='__main__':raise SystemExit(main())
