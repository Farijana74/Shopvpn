"""Root-controlled reseller catalogs, price floors and durable wholesale purchases."""
import asyncio
import json
import logging
import os
import sqlite3
from fractions import Fraction


class AgencyError(ValueError):
    pass


def install_schema(conn):
    statements = (
        'CREATE TABLE IF NOT EXISTS managed_catalog (bot_id INTEGER NOT NULL, product_id INTEGER NOT NULL, allowed INTEGER NOT NULL DEFAULT 1, selected INTEGER NOT NULL DEFAULT 1, buy_price INTEGER, PRIMARY KEY(bot_id,product_id))',
        'CREATE TABLE IF NOT EXISTS managed_purchases (request_key TEXT PRIMARY KEY, bot_id INTEGER NOT NULL, local_order_id INTEGER NOT NULL, root_order_id INTEGER NOT NULL, charges TEXT NOT NULL, status TEXT NOT NULL DEFAULT \'reserved\')',
        'CREATE TABLE IF NOT EXISTS managed_invites (id INTEGER PRIMARY KEY, parent_bot_id INTEGER, owner_id INTEGER NOT NULL, bot_token TEXT NOT NULL, bot_username TEXT NOT NULL, completed INTEGER DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS managed_notices (id INTEGER PRIMARY KEY, bot_id INTEGER NOT NULL, product_id INTEGER NOT NULL, old_price INTEGER, new_price INTEGER NOT NULL, delivered INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)',
    )
    for statement in statements:
        conn.execute(statement)
    conn.execute('CREATE TABLE IF NOT EXISTS managed_trials (user_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, policy TEXT NOT NULL, status TEXT NOT NULL DEFAULT \'pending\')')
    conn.execute('CREATE TABLE IF NOT EXISTS managed_sale_notices (request_key TEXT NOT NULL, admin_id INTEGER NOT NULL, PRIMARY KEY(request_key,admin_id))')
    columns={row[1] for row in conn.execute('PRAGMA table_info(managed_purchases)')}
    for name,definition in [('is_trial','INTEGER NOT NULL DEFAULT 0'),('root_notified','INTEGER NOT NULL DEFAULT 1')]:
        if name not in columns:
            try:
                conn.execute(f'ALTER TABLE managed_purchases ADD COLUMN {name} {definition}')
            except sqlite3.OperationalError as exc:
                if 'duplicate column name' not in str(exc).lower():raise
    if 'prepaid_qty' not in columns:
        try:
            conn.execute('ALTER TABLE managed_purchases ADD COLUMN prepaid_qty INTEGER DEFAULT 0')
        except sqlite3.OperationalError as exc:
            if 'duplicate column name' not in str(exc).lower():
                raise


def root_db():
    from config import DB_PATH
    from database import Database
    return Database(DB_PATH)


def bot_for(db, root):
    if os.path.abspath(db.db_path) == os.path.abspath(root.db_path):
        return None
    with root._get_conn() as conn:
        return conn.execute('SELECT * FROM reseller_bots WHERE db_path=? AND is_active=1', (db.db_path,)).fetchone()


def chain(root, bot_id):
    result, seen = [], set()
    while bot_id:
        if bot_id in seen or len(seen) >= 8:
            raise AgencyError('زنجیره نمایندگی معتبر نیست یا بیش از حد مجاز است.')
        seen.add(bot_id)
        bot = root.get_reseller_bot(bot_id)
        if not bot or not bot['is_active']:
            raise AgencyError('نمایندگی بالادست فعال نیست.')
        result.append(dict(bot))
        bot_id = dict(bot).get('managed_parent_bot_id')
    return list(reversed(result))


def rate(root, bot_id, product_id, require_selected=True):
    product = root.get_product(product_id)
    if not product or not product['is_active']:
        raise AgencyError('محصول بالادست غیرفعال یا حذف شده است.')
    product = dict(product)
    category = root.get_category(product['category_id'])
    if category and not category['is_active']:
        raise AgencyError('دسته‌بندی محصول در ربات اصلی غیرفعال است.')
    cost = int(product.get('reseller_wholesale_price') if product.get('reseller_wholesale_price') is not None else product['price'])
    bots = chain(root, bot_id)
    with root._get_conn() as conn:
        for bot in bots:
            offer = conn.execute('SELECT * FROM managed_catalog WHERE bot_id=? AND product_id=?', (bot['id'], product_id)).fetchone()
            if not offer or not offer['allowed'] or ((require_selected or bot['id'] != bot_id) and not offer['selected']):
                raise AgencyError('این محصول برای این زنجیره نمایندگی مجاز نیست.')
            cost = max(cost, int(offer['buy_price'] or 0))
    return cost


def configure(root, bot_id, product_id, *, allowed=None, selected=None, buy_price=None, actor_bot_id=None):
    bot = root.get_reseller_bot(bot_id)
    if not bot:
        raise AgencyError('نمایندگی یافت نشد.')
    product = root.get_product(product_id)
    if not product or not product['is_auto_provision'] or not product['provision_server_id']:
        raise AgencyError('محصول باید به پنل اصلی متصل و قابل ساخت خودکار باشد.')
    with root._get_conn() as conn:
        previous=conn.execute('SELECT allowed,selected,buy_price FROM managed_catalog WHERE bot_id=? AND product_id=?',(bot_id,product_id)).fetchone()
    if allowed is None:allowed=bool(previous['allowed']) if previous else True
    if selected is None:selected=bool(previous['selected']) if previous else True
    parent = dict(bot).get('managed_parent_bot_id')
    if actor_bot_id is not None and parent != actor_bot_id:
        raise AgencyError('اجازه مدیریت این نماینده را ندارید.')
    floor = (rate(root, parent, product_id) if parent else int(dict(product).get('reseller_wholesale_price') if dict(product).get('reseller_wholesale_price') is not None else product['price'])) if allowed or buy_price is not None else 0
    if buy_price is not None and int(buy_price) < floor:
        raise AgencyError('نرخ نماینده نمی‌تواند کمتر از نرخ خرید خودتان باشد.')
    if buy_price is None and previous:buy_price=previous['buy_price']
    with root._get_conn() as conn:
        conn.execute('INSERT INTO managed_catalog(bot_id,product_id,allowed,selected,buy_price) VALUES(?,?,?,?,?) ON CONFLICT(bot_id,product_id) DO UPDATE SET allowed=excluded.allowed,selected=excluded.selected,buy_price=excluded.buy_price', (bot_id, product_id, int(bool(allowed)), int(bool(selected)), buy_price))
    return sync_bot(root, bot_id)


def select_product(root, bot_id, product_id, selected):
    if selected:
        rate(root, bot_id, product_id, require_selected=False)
    with root._get_conn() as conn:
        conn.execute('UPDATE managed_catalog SET selected=? WHERE bot_id=? AND product_id=? AND allowed=1', (int(bool(selected)), bot_id, product_id))
    return sync_bot(root, bot_id)


def bootstrap(root, bot_id, product_ids=None, parent_id=None):
    from database import Database
    bot = root.get_reseller_bot(bot_id)
    if not bot or not bot['is_active']:
        raise AgencyError('نمایندگی فعال نیست.')
    if parent_id:
        parents = chain(root, parent_id)
        if bot_id in {item['id'] for item in parents}:
            raise AgencyError('نماینده نمی‌تواند بالادست خودش باشد.')
    with root._get_conn() as conn:
        conn.execute('UPDATE reseller_bots SET managed_parent_bot_id=? WHERE id=?', (parent_id, bot_id))
        existing = conn.execute('SELECT 1 FROM managed_catalog WHERE bot_id=? LIMIT 1', (bot_id,)).fetchone()
        if product_ids is None and not existing:
            if parent_id:
                product_ids = [r[0] for r in conn.execute("SELECT product_id FROM managed_catalog WHERE bot_id=? AND allowed=1 AND selected=1 AND NOT EXISTS (SELECT 1 FROM settings WHERE key='volume_template:'||managed_catalog.product_id AND value='1')", (parent_id,))]
            else:
                product_ids = [r[0] for r in conn.execute("SELECT id FROM products WHERE is_active=1 AND is_auto_provision=1 AND provision_server_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM settings WHERE key='volume_template:'||products.id AND value='1')")]
        for product_id in product_ids or []:
            conn.execute('INSERT OR IGNORE INTO managed_catalog(bot_id,product_id) VALUES(?,?)', (bot_id,int(product_id)))
    local = Database(bot['db_path'])
    try:
        with local._get_conn() as conn:
            product_columns={r[1] for r in conn.execute('PRAGMA table_info(products)')}
            panel_columns={r[1] for r in conn.execute('PRAGMA table_info(panel_servers)')}
            category_columns={r[1] for r in conn.execute('PRAGMA table_info(categories)')}
        ready='upstream_product_id' in product_columns and 'managed_root_db_path' in panel_columns and 'upstream_category_id' in category_columns
        owner=local.get_owner_telegram_id() if ready else None
        if not ready or owner != bot['owner_telegram_id']:
            local.init_db(bot['owner_telegram_id'])
        local.set_setting('managed_reseller_bot_id', str(bot_id))
        local.set_setting('managed_root_db_path', root.db_path)
        local.set_setting('bot_role', 'managed_reseller')
        local.set_setting('custom_config_enabled', '0')
        local.set_setting('test_enabled', '0')
        with local._get_conn() as conn:
            conn.execute('UPDATE products SET is_active=0 WHERE upstream_product_id IS NULL')
    finally:
        local.close()
    return sync_bot(root, bot_id)


def sync_bot(root, bot_id):
    from database import Database
    bot = root.get_reseller_bot(bot_id)
    if not bot:
        raise AgencyError('نمایندگی یافت نشد.')
    local = Database(bot['db_path'])
    changed = 0
    try:
        with root._get_conn() as conn:
            offers = [dict(r) for r in conn.execute("SELECT * FROM managed_catalog WHERE bot_id=? AND NOT EXISTS (SELECT 1 FROM settings WHERE key='volume_template:'||managed_catalog.product_id AND value='1')", (bot_id,))]
        for offer in offers:
            if root.get_setting(f"volume_template:{offer['product_id']}",'')=='1':continue
            product = root.get_product(offer['product_id'])
            try:
                purchase_price = rate(root, bot_id, offer['product_id'],require_selected=False)
                active = bool(offer['selected'])
            except AgencyError:
                purchase_price, active = 0, False
            with local._get_conn() as conn:
                old = conn.execute('SELECT * FROM products WHERE upstream_product_id=?', (offer['product_id'],)).fetchone()
                if not product:
                    if old:
                        conn.execute('UPDATE products SET is_active=0 WHERE id=?', (old['id'],))
                    continue
                source = dict(product)
                category = root.get_category(source['category_id'])
                category_name = category['name'] if category else 'محصولات'
                cat = conn.execute('SELECT * FROM categories WHERE upstream_category_id=?',(source['category_id'],)).fetchone()
                if not cat and old:
                    cat = conn.execute('SELECT * FROM categories WHERE id=? AND upstream_category_id IS NULL',(old['category_id'],)).fetchone()
                if not cat:
                    cat = conn.execute('SELECT * FROM categories WHERE name=? AND upstream_category_id IS NULL',(category_name,)).fetchone()
                cat_id = cat['id'] if cat else conn.execute('INSERT INTO categories(name,upstream_category_id) VALUES(?,?)',(category_name,source['category_id'])).lastrowid
                conn.execute('UPDATE categories SET upstream_category_id=? WHERE id=?',(source['category_id'],cat_id))
                conn.execute('UPDATE categories SET name=? WHERE id=? AND managed_name_override=0',(category_name,cat_id))
                if category:
                    conn.execute('UPDATE categories SET icon_custom_emoji_id=? WHERE id=? AND managed_icon_override=0',(category['icon_custom_emoji_id'],cat_id))
                category_enabled=conn.execute('SELECT is_active FROM categories WHERE id=?',(cat_id,)).fetchone()[0]
                active = active and bool(category_enabled) and (bool(category['is_active']) if category else True)
                server = root.get_panel_server(source['provision_server_id']) if source['provision_server_id'] else None
                if not server:
                    active = False
                    proxy_id = None
                else:
                    proxy = conn.execute('SELECT id FROM panel_servers WHERE managed_source_server_id=?', (server['id'],)).fetchone()
                    proxy_id = proxy[0] if proxy else conn.execute('INSERT INTO panel_servers(name,panel_type,api_url,managed_source_server_id,managed_root_db_path) VALUES(?,?,?,?,?)', ('پنل مدیریت‌شده',server['panel_type'],'https://managed.invalid',server['id'],root.db_path)).lastrowid
                columns = {r[1] for r in conn.execute('PRAGMA table_info(products)')}
                values = {k:v for k,v in source.items() if k in columns and k not in ('id','price','description','is_active','discount_used_count','managed_local_enabled')}
                values.update(category_id=cat_id, upstream_product_id=source['id'], provision_server_id=proxy_id, provision_ssh_node_id=None, managed_buy_price=purchase_price)
                values['is_active'] = int(active and (bool(old['managed_local_enabled']) if old else True))
                discount=conn.execute("SELECT MAX(reseller_discount_percent) FROM users WHERE reseller_tier='silver'").fetchone()[0] or 0
                discount=max(0,min(99,int(discount)))
                minimum=(purchase_price*100+99-discount)//(100-discount)
                values['price'] = max(int(old['price']) if old else int(source['price']), minimum)
                values['description'] = old['description'] if old else source['description']
                if old and conn.execute("SELECT 1 FROM settings WHERE key=? AND value='1'",(f"managed_product_name_override:{old['id']}",)).fetchone():
                    values['name']=old['name']
                if old:
                    conn.execute('UPDATE products SET '+','.join(f'{k}=?' for k in values)+' WHERE id=?', [*values.values(),old['id']])
                else:
                    conn.execute('INSERT INTO products('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')', list(values.values()))
                if active and old and old['managed_buy_price'] != purchase_price:
                    with root._get_conn() as central:
                        central.execute('INSERT INTO managed_notices(bot_id,product_id,old_price,new_price) VALUES(?,?,?,?)', (bot_id,source['id'],old['managed_buy_price'],purchase_price))
                changed += 1
        from managed_trials import trial_policy as read_trial_policy
        policy=read_trial_policy(root)
        local.set_setting('test_enabled','1' if policy.get('enabled') else '0')
        if local.get_setting('managed_referral_label_migrated','') != '1':
            if local.get_setting('btn_referral','') in ('🤝 زیرمجموعه‌گیری من','زیرمجموعه‌گیری','زیر مجموعه گیری'):
                local.set_setting('btn_referral','🤝 دعوت دوستان')
            local.set_setting('managed_referral_label_migrated','1')
        from managed_native_volume import available,plans
        local.set_setting('volume_native_visible','1')
        local.set_setting('custom_config_enabled','1' if available(local,root,bot_id) else '0')
        local.set_setting('btn_custom_config_text', root.get_setting('btn_custom_config_text', root.get_setting('btn_custom_config','🛠 حجم دلخواه')))
        return changed
    finally:
        local.close()


def validate_edit(db, product_id, changes):
    if not db.get_setting('managed_reseller_bot_id',''):
        return
    old = db.get_product(product_id)
    if not old or not dict(old).get('upstream_product_id'):
        raise AgencyError('فقط محصولات مجاز بالادست قابل مدیریت هستند.')
    allowed = {'name','price','description','is_active'}
    for key,value in changes.items():
        if key not in allowed and value is not ... and value is not None and ((key in old.keys() and value != old[key]) or (key not in old.keys() and value not in (0,''))):
            raise AgencyError('نماینده فقط نام، قیمت فروش، توضیحات و وضعیت محصول را تغییر می‌دهد.')
    if changes.get('price') is not None:
        from database import Database
        central=Database(db.get_setting('managed_root_db_path',''))
        try:
            minimum=rate(central,int(db.get_setting('managed_reseller_bot_id')),old['upstream_product_id'],require_selected=False)
        finally:
            central.close()
        if int(changes['price']) < minimum:
            raise AgencyError('قیمت فروش کمتر از قیمت تأمین مجاز نیست.')


def reserve(root, bot_id, local_order_id, product_id, quantity, cost_factor=Fraction(1,1), extra_per_unit=0, *, allow_zero=False, panel_trial=False):
    if not isinstance(quantity,int) or isinstance(quantity,bool) or quantity < 1 or cost_factor < 0 or extra_per_unit < 0 or (cost_factor == 0 and extra_per_unit == 0 and not allow_zero):
        raise AgencyError('تعداد یا ضریب قیمت نامعتبر است.')
    def cost(value):
        exact=Fraction(int(value)*quantity)*cost_factor+Fraction(extra_per_unit)*quantity
        return (exact.numerator+exact.denominator-1)//exact.denominator
    request_key = f'{bot_id}:{local_order_id}'
    with root._get_conn() as history:
        recorded=history.execute('SELECT p.status,p.root_order_id,o.product_id,o.quantity FROM managed_purchases p JOIN orders o ON o.id=p.root_order_id WHERE p.request_key=?',(request_key,)).fetchone()
    if recorded and recorded['status'] != 'refunded':
        if recorded['product_id'] != product_id or recorded['quantity'] != quantity:
            raise AgencyError('مشخصات سفارش با رزرو قبلی مطابقت ندارد.')
        return recorded['root_order_id']
    bots = chain(root, bot_id)
    if panel_trial and (quantity!=1 or cost_factor!=0):raise AgencyError('رزرو تست پنلی معتبر نیست.')
    root_price = 0 if panel_trial else rate(root, bots[0]['id'], product_id)
    charges = [(root.db_path, bots[0]['owner_telegram_id'], cost(root_price))]
    for index in range(1,len(bots)):
        charges.append((bots[index-1]['db_path'],bots[index]['owner_telegram_id'],cost(0 if panel_trial else rate(root,bots[index]['id'],product_id))))
    conn = sqlite3.connect(root.db_path,timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        aliases = {os.path.abspath(root.db_path):'main'}
        for path,owner,amount in charges:
            path = os.path.abspath(path)
            if path not in aliases:
                alias = f'edge{len(aliases)}'
                conn.execute(f'ATTACH DATABASE ? AS {alias}',(path,))
                aliases[path] = alias
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT * FROM managed_purchases WHERE request_key=?',(request_key,)).fetchone()
        if old:
            prior=conn.execute('SELECT product_id,quantity FROM orders WHERE id=?',(old['root_order_id'],)).fetchone()
            if not prior or prior['product_id'] != product_id or prior['quantity'] != quantity:
                raise AgencyError('مشخصات سفارش با رزرو قبلی مطابقت ندارد.')
        if old and old['status'] != 'refunded':
            conn.commit()
            return old['root_order_id']
        prepaid=0
        if cost_factor == 1:
            inventory=conn.execute('SELECT qty_remaining FROM reseller_product_credit WHERE reseller_id=? AND product_id=?',(bots[0]['owner_telegram_id'],product_id)).fetchone()
            prepaid=min(quantity,max(0,int(inventory[0] or 0))) if inventory else 0
            if prepaid:
                conn.execute('UPDATE reseller_product_credit SET qty_remaining=qty_remaining-? WHERE reseller_id=? AND product_id=?',(prepaid,bots[0]['owner_telegram_id'],product_id))
                charges[0]=(root.db_path,bots[0]['owner_telegram_id'],cost(root_price)-root_price*prepaid)
        for path,owner,amount in charges:
            alias=aliases[os.path.abspath(path)]
            previous=conn.execute(f'SELECT kind,note FROM {alias}.wallet_tx_label WHERE user_id=?',(owner,)).fetchone()
            conn.execute(f'INSERT OR REPLACE INTO {alias}.wallet_tx_label(user_id,kind,note) VALUES(?,?,?)',(owner,'reseller_supply',f'تأمین نمایندگی #{bot_id} سفارش #{local_order_id}'))
            changed=conn.execute(f'UPDATE {alias}.users SET referral_credit=referral_credit-? WHERE telegram_id=? AND referral_credit>=?',(amount,owner,amount)).rowcount
            if previous:
                conn.execute(f'INSERT OR REPLACE INTO {alias}.wallet_tx_label(user_id,kind,note) VALUES(?,?,?)',(owner,previous[0],previous[1]))
            else:
                conn.execute(f'DELETE FROM {alias}.wallet_tx_label WHERE user_id=?',(owner,))
            if changed != 1:
                raise AgencyError('اعتبار کیف پول یکی از نمایندگان زنجیره کافی نیست؛ هیچ هزینه‌ای کسر نشد.')
        if old:
            root_order=old['root_order_id']
            conn.execute("UPDATE orders SET status='processing',wallet_used=? WHERE id=?",(charges[0][2],root_order))
            conn.execute("UPDATE managed_purchases SET status='reserved',charges=?,prepaid_qty=? WHERE request_key=?",(json.dumps(charges),prepaid,request_key))
        else:
            conn.execute('INSERT OR IGNORE INTO users(telegram_id,first_name) VALUES(?,?)',(-bot_id,'تأمین نمایندگی'))
            root_order=conn.execute("INSERT INTO orders(user_id,product_id,status,base_price,wallet_used,final_price,quantity,managed_internal) VALUES(?,?,'processing',?,?,0,?,1)",(-bot_id,product_id,cost(root_price),charges[0][2],quantity)).lastrowid
            conn.execute('INSERT INTO managed_purchases(request_key,bot_id,local_order_id,root_order_id,charges,prepaid_qty,root_notified) VALUES(?,?,?,?,?,?,0)',(request_key,bot_id,local_order_id,root_order,json.dumps(charges),prepaid))
        conn.commit()
        return root_order
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def provision(local_db, product, quantity, user_id, order_id, *, trial_policy=None):
    from direct_panel_provision import provision_direct
    if order_id is None or user_id is None:
        raise AgencyError('سفارش معتبر برای تأمین نمایندگی لازم است.')
    root_path=local_db.get_setting('managed_root_db_path','')
    from database import Database
    root=Database(root_path)
    bot_id=int(local_db.get_setting('managed_reseller_bot_id','0'))
    try:
        volume_quote=json.loads(local_db.get_setting(f"volume_quote:{product['id']}",'{}'))
        if volume_quote and volume_quote['user_id']!=user_id:raise AgencyError('این پیش‌فاکتور برای کاربر دیگری است.')
        with root._get_conn() as conn:
            funded=conn.execute("SELECT 1 FROM managed_purchases WHERE request_key=? AND status!='refunded'",(f'{bot_id}:{order_id}',)).fetchone()
        native=volume_quote.get('native_plan_id') is not None
        if native and not funded:
            from managed_native_volume import refresh
            refresh(local_db,root,bot_id,product['upstream_product_id'],volume_quote['native_plan_id'],int(volume_quote['gb']))
        original=root.get_product(product['upstream_product_id'])
        if not original:
            raise AgencyError('محصول بالادست حذف شده است؛ پشتیبانی باید بررسی کند.')
        if volume_quote:
            from managed_volume import policy
            rules={"enabled":True,"product_id":original["id"]} if native else policy(root,bot_id)
            original={**dict(original),'auto_provision_volume_gb':int(volume_quote['gb']),'duration_days':30}
        if trial_policy:
            original={**dict(original),'auto_provision_volume_gb':trial_policy['volume_mb']/1024,
                      'duration_days':trial_policy['hours']/24,'base_users':1,'max_users':1,'extra_user_price':0,
                      'provision_ssh_node_id':None,'delivery_mode':'normal'}
            if trial_policy.get('panel_id'):
                original.update(provision_server_id=trial_policy['panel_id'],is_auto_provision=1,delivery_mode='normal')
        with root._get_conn() as conn:
            funded=conn.execute("SELECT 1 FROM managed_purchases WHERE request_key=? AND status!='refunded'",(f'{bot_id}:{order_id}',)).fetchone()
        if volume_quote and not funded and (not rules.get('enabled') or rules.get('product_id')!=original['id']):raise AgencyError('حجم دلخواه این نماینده غیرفعال شده است.')
        current_cost=rate(root,bot_id,product['upstream_product_id']) if not funded and not (trial_policy and trial_policy.get('panel_id')) else 0
        if not trial_policy and not funded and int(product['price']) < current_cost:
            raise AgencyError('نرخ تأمین تغییر کرده؛ ابتدا محصولات را بروزرسانی کنید.')
        if not trial_policy and not funded:
            current=local_db.get_product(product['id'])
            category=local_db.get_category(current['category_id']) if current else None
            if not current or not current['is_active'] or (category and not category['is_active']):
                raise AgencyError('محصول یا دسته‌بندی در این فروشگاه غیرفعال است.')
        local_order=local_db.get_order(order_id)
        if not local_order or local_order['user_id'] != user_id:
            raise AgencyError('مالکیت سفارش معتبر نیست.')
        from user_limit import order_user_limit
        users=1 if trial_policy else order_user_limit(local_order,product)
        base_users=int(original['base_users'] or 0)
        maximum=int(original['max_users'] or base_users)
        if users and maximum and users > maximum:
            raise AgencyError('تعداد کاربر بالاتر از حد مجاز محصول بالادست است.')
        extra=max(0,int(users or base_users)-base_users)*int(original['extra_user_price'] or 0)
        cost_factor=int(volume_quote['gb']) if volume_quote else 1
        if not trial_policy and not funded and int(local_order['base_price']) < (current_cost*cost_factor+extra)*quantity:
            raise AgencyError('قیمت سفارش از نرخ تأمین جدید کمتر است؛ ابتدا قیمت‌ها را بروزرسانی کنید.')
        if trial_policy:
            purchase=await asyncio.to_thread(reserve,root,bot_id,order_id,original['id'],1,Fraction(0,1),trial_policy['cost'],allow_zero=True,panel_trial=bool(trial_policy.get('panel_id')))
            with root._get_conn() as conn:
                conn.execute('UPDATE managed_purchases SET is_trial=1 WHERE request_key=?',(f'{bot_id}:{order_id}',))
        else:
            purchase=await asyncio.to_thread(reserve,root,bot_id,order_id,original['id'],quantity,Fraction(cost_factor,1),extra)
        name=(str(volume_quote['username'])+f'a{bot_id}') if volume_quote.get('username') else f"{local_db.get_custom_config_prefix()+'-' if local_db.get_custom_config_prefix() else ''}a{bot_id}o{purchase}"
        with root._get_conn() as conn:
            conn.execute('UPDATE orders SET config_name=COALESCE(config_name,?),user_limit=? WHERE id=?',(name,users,purchase))
            ledger=conn.execute('SELECT status FROM managed_purchases WHERE request_key=?',(f'{bot_id}:{order_id}',)).fetchone()
            if ledger and ledger['status']=='completed':
                count=conn.execute('SELECT COUNT(*) FROM custom_configs WHERE order_id=?',(purchase,)).fetchone()[0]
                if count < quantity:
                    raise AgencyError('سرویس قبلی بالادست حذف شده است؛ ایجاد مجدد نیازمند بررسی مدیر است.')
        with root._get_conn() as conn:
            saved_services=conn.execute('SELECT * FROM custom_configs WHERE order_id=? ORDER BY id',(purchase,)).fetchall()
        if saved_services and len(saved_services)<quantity:
            raise AgencyError('ثبت تأمین قبلی ناقص است؛ مدیر باید آن را بررسی کند.')
        try:
            if saved_services:
                results=[{'username':row['username'],'subscription_url':row['subscription_url'],'volume_gb':row['volume_gb'],
                          'duration_days':row['duration_days'],'user_limit':row['user_limit'],'panel_server_id':row['panel_server_id'],
                          'custom_config_id':row['id'],'_managed_delivery_mode':row['delivery_mode']} for row in saved_services[:quantity]]
            else:
                results=await provision_direct(root,original,quantity,user_id=-bot_id,order_id=purchase)
        except Exception:
            await asyncio.to_thread(refund_reservation,root,bot_id,order_id)
            raise
        await asyncio.to_thread(root.approve_order_auto,purchase)
        mirrored=[]
        for result in results:
            server=root.get_panel_server(result['panel_server_id'])
            if not server:raise AgencyError('پنل سرویس تأمین‌شده یافت نشد.')
            with local_db._get_conn() as conn:
                proxy=conn.execute('SELECT id FROM panel_servers WHERE managed_source_server_id=?',(server['id'],)).fetchone()
                proxy_id=proxy[0] if proxy else conn.execute('INSERT INTO panel_servers(name,panel_type,api_url,managed_source_server_id,managed_root_db_path) VALUES(?,?,?,?,?)',('پنل مدیریت‌شده',server['panel_type'],'https://managed.invalid',server['id'],root.db_path)).lastrowid
            with local_db._get_conn() as conn:
                row=conn.execute('SELECT id FROM custom_configs WHERE order_id=? AND username=?',(order_id,result['username'])).fetchone()
            config_id=row['id'] if row else local_db.add_custom_config(user_id,proxy_id,result['username'],result['volume_gb'],result['duration_days'],result['subscription_url'],order_id=order_id,source='direct_product',user_limit=result.get('user_limit'))
            upstream_id=result.get('custom_config_id')
            if not upstream_id:
                with root._get_conn() as conn:
                    source_cc=conn.execute('SELECT id FROM custom_configs WHERE order_id=? AND username=?',(purchase,result['username'])).fetchone()
                if source_cc:
                    upstream_id=source_cc['id']
            if upstream_id:
                with local_db._get_conn() as conn:
                    conn.execute('UPDATE custom_configs SET upstream_config_id=? WHERE id=?',(upstream_id,config_id))
            delivery_mode=result.get('_managed_delivery_mode') or original['delivery_mode']
            if upstream_id and not saved_services:
                with root._get_conn() as conn:
                    conn.execute('UPDATE custom_configs SET delivery_mode=? WHERE id=?',(delivery_mode,upstream_id))
            with local_db._get_conn() as conn:
                conn.execute('UPDATE custom_configs SET delivery_mode=?,panel_server_id=? WHERE id=?',(delivery_mode,proxy_id,config_id))
            mirrored.append({**result,'panel_server_id':proxy_id,'custom_config_id':config_id,'_managed_delivery_mode':delivery_mode})
        with root._get_conn() as conn:
            conn.execute("UPDATE managed_purchases SET status='completed' WHERE request_key=?",(f'{bot_id}:{order_id}',))
        return mirrored
    finally:
        root.close()


async def notify_root_sales(root, bot):
    """Retry pending sale notices without resending to admins already notified."""
    with root._get_conn() as conn:
        sales=conn.execute("SELECT p.request_key,p.bot_id,p.local_order_id,p.root_order_id,b.bot_username,b.owner_telegram_id,o.product_id,o.quantity,o.wallet_used,o.is_renewal,p.is_trial FROM managed_purchases p JOIN reseller_bots b ON b.id=p.bot_id JOIN orders o ON o.id=p.root_order_id WHERE p.status='completed' AND p.root_notified=0 ORDER BY p.root_order_id LIMIT 50").fetchall()
    admins=[uid for uid in root.list_admins() if root.is_senior_admin(uid)]
    for sale in sales:
        product=root.get_product(sale['product_id'])
        name=product['name'] if product else str(sale['product_id'])
        text=(f"🛒 {'تست' if sale['is_trial'] else ('تمدید' if sale['is_renewal'] else 'خرید')} از ربات نماینده\n"
              f"ربات: @{sale['bot_username']}\nمالک نماینده: {sale['owner_telegram_id']}\n"
              f"محصول: {name}\nتعداد: {sale['quantity']}\n"
              f"سفارش نماینده: {sale['local_order_id']}\nسفارش تأمین اصلی: {sale['root_order_id']}\n"
              f"کسر نقدی از کیف پول بالادست: {sale['wallet_used']:,} تومان")
        all_sent=bool(admins)
        for uid in admins:
            with root._get_conn() as conn:
                sent=conn.execute('SELECT 1 FROM managed_sale_notices WHERE request_key=? AND admin_id=?',(sale['request_key'],uid)).fetchone()
            if sent:continue
            try:
                await bot.send_message(uid,text,parse_mode=None)
            except Exception:
                all_sent=False
                continue
            with root._get_conn() as conn:
                conn.execute('INSERT OR IGNORE INTO managed_sale_notices(request_key,admin_id) VALUES(?,?)',(sale['request_key'],uid))
        if all_sent:
            with root._get_conn() as conn:
                conn.execute('UPDATE managed_purchases SET root_notified=1 WHERE request_key=?',(sale['request_key'],))


async def reconcile(root, manager=None):
    from database import Database
    for bot in root.list_reseller_bots(active_only=True):
        from database import Database
        local=Database(bot['db_path'])
        try:
            marked=local.get_setting('managed_reseller_bot_id','')
            if not marked and root.get_agent_tier(bot['owner_telegram_id']) == 'gold':
                await asyncio.to_thread(bootstrap,root,bot['id'],parent_id=dict(bot).get('managed_parent_bot_id'))
                marked=str(bot['id'])
            if marked:
                await asyncio.to_thread(discover_products,root,bot['id'])
                if manager:
                    instance=manager.instances.get(bot['bot_token'])
                    if instance and not instance.get('managed_reseller'):
                        await manager.stop_bot(bot['bot_token'])
                        await manager.start_bot(bot['bot_token'],bot['db_path'],bot['owner_telegram_id'],is_main_bot=False)
        except Exception as exc:
            logging.getLogger(__name__).warning('Managed catalog reconciliation failed for bot #%s (%s)',bot['id'],type(exc).__name__)
        finally:
            local.close()
    if manager:
        from config import BOT_TOKEN
        main_instance=manager.instances.get(BOT_TOKEN)
        if main_instance:
            await notify_root_sales(root,main_instance['bot'])
        with root._get_conn() as conn:
            notices=conn.execute('SELECT n.*,b.bot_token,b.owner_telegram_id FROM managed_notices n JOIN reseller_bots b ON b.id=n.bot_id WHERE n.delivered=0 ORDER BY n.id LIMIT 100').fetchall()
        for notice in notices:
            instance=manager.instances.get(notice['bot_token'])
            if not instance:
                continue
            bot_row=root.get_reseller_bot(notice['bot_id'])
            child=Database(bot_row['db_path'])
            try:recipients=set(child.list_admins())|{notice['owner_telegram_id']}
            finally:child.close()
            product=root.get_product(notice['product_id'])
            name=product['name'] if product else f"#{notice['product_id']}"
            text=(f"📦 محصول جدید «{name}» به فهرست بالادست اضافه شد. نرخ تأمین: {notice['new_price']:,} تومان. از بخش محصولات، دریافت لیست جدید را بزنید و قیمت فروش را تعیین کنید." if notice['old_price'] is None else f"🔔 نرخ تأمین «{name}» تغییر کرد: {notice['old_price']} ← {notice['new_price']} تومان. نرخ جدید در خریدهای بعدی اعمال می‌شود.")
            key=f"managed_notice_sent:{notice['id']}"
            sent=set(json.loads(root.get_setting(key,'[]')))
            for uid in recipients-sent:
                try:await instance['bot'].send_message(uid,text,parse_mode=None)
                except Exception:continue
                sent.add(uid);root.set_setting(key,json.dumps(sorted(sent)))
            if recipients<=sent:
                with root._get_conn() as conn:
                    conn.execute('UPDATE managed_notices SET delivered=1 WHERE id=?',(notice['id'],))



def refund_reservation(root, bot_id, local_order_id):
    """Refund a failed, unprovisioned supply attempt exactly once."""
    key=f'{bot_id}:{local_order_id}'
    with root._get_conn() as conn:
        saved=conn.execute('SELECT * FROM managed_purchases WHERE request_key=?',(key,)).fetchone()
        if not saved or saved['status'] != 'reserved':
            return False
        if conn.execute('SELECT 1 FROM custom_configs WHERE order_id=? LIMIT 1',(saved['root_order_id'],)).fetchone():
            return False
        charges=json.loads(saved['charges'])
    connection=sqlite3.connect(root.db_path,timeout=10)
    try:
        aliases={os.path.abspath(root.db_path):'main'}
        for path,owner,amount in charges:
            path=os.path.abspath(path)
            if path not in aliases:
                alias=f'edge{len(aliases)}'
                connection.execute(f'ATTACH DATABASE ? AS {alias}',(path,))
                aliases[path]=alias
        connection.execute('BEGIN IMMEDIATE')
        row=connection.execute('SELECT status,root_order_id FROM managed_purchases WHERE request_key=?',(key,)).fetchone()
        if not row or row[0] != 'reserved' or connection.execute('SELECT 1 FROM custom_configs WHERE order_id=? LIMIT 1',(row[1],)).fetchone():
            connection.rollback()
            return False
        for path,owner,amount in charges:
            alias=aliases[os.path.abspath(path)]
            connection.execute(f'UPDATE {alias}.users SET referral_credit=referral_credit+? WHERE telegram_id=?',(amount,owner))
        if saved['prepaid_qty']:
            product_id=connection.execute('SELECT product_id FROM orders WHERE id=?',(row[1],)).fetchone()[0]
            connection.execute('UPDATE reseller_product_credit SET qty_remaining=qty_remaining+? WHERE reseller_id=? AND product_id=?',(saved['prepaid_qty'],charges[0][1],product_id))
        connection.execute("UPDATE managed_purchases SET status='refunded' WHERE request_key=?",(key,))
        connection.execute("UPDATE orders SET status='rejected' WHERE id=?",(row[1],))
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


class ManagedProvider:
    """A local placeholder never grants permission to provision for free."""
    def __init__(self, provider, root_path=None, server_id=None):
        self.provider=provider
        self.root_path=root_path
        self.server_id=server_id
    def __getattr__(self,name):
        return getattr(self.provider,name)
    async def create_user(self,*args,**kwargs):
        from panel_providers import PanelError
        raise PanelError('ساخت سرویس نمایندگی باید از مسیر خرید محصولات مجاز انجام شود.')
    async def update_user(self,*args,**kwargs):
        from panel_providers import PanelError
        raise PanelError('افزایش حجم و مدت باید از مسیر تمدید با تسویه بالادست انجام شود.')
    async def revoke_credentials(self,username):
        result=await self.provider.revoke_credentials(username)
        if self.root_path and result.subscription_url:
            await asyncio.to_thread(self._update,username,{'subscription_url':result.subscription_url})
        return result
    async def set_enabled(self,username,enabled):
        await self.provider.set_enabled(username,enabled)
        if self.root_path:
            await asyncio.to_thread(self._update,username,{'enabled':int(bool(enabled))})
    async def delete_user(self,username):
        result=await self.provider.delete_user(username)
        if result and self.root_path:
            await asyncio.to_thread(self._update,username,{'status':'cancelled'})
        return result
    def _update(self,username,values):
        from database import Database
        root=Database(self.root_path)
        try:
            with root._get_conn() as conn:
                conn.execute('UPDATE custom_configs SET '+','.join(f'{key}=?' for key in values)+' WHERE panel_server_id=? AND username=?', [*values.values(),self.server_id,username])
        finally:
            root.close()


async def renew_service(local, order):
    from database import Database
    from renewal_engine import execute_renewal
    cc=local.get_custom_config_owned(order['renewal_target_id'],order['user_id'])
    if not cc or not cc['upstream_config_id']:
        raise AgencyError('این سرویس قدیمی به سفارش بالادست متصل نیست؛ پشتیبانی باید آن را بررسی کند.')
    root=Database(local.get_setting('managed_root_db_path',''))
    bot_id=int(local.get_setting('managed_reseller_bot_id','0'))
    try:
        upstream=root.get_custom_config_by_id(cc['upstream_config_id'])
        if not upstream or upstream['user_id'] != -bot_id:
            raise AgencyError('مالکیت سرویس بالادست معتبر نیست.')
        with root._get_conn() as conn:
            historical=conn.execute('SELECT status FROM managed_purchases WHERE request_key=?',(f"{bot_id}:{order['id']}",)).fetchone()
        if historical and historical['status']=='completed':
            with local._get_conn() as conn:
                conn.execute('UPDATE custom_configs SET volume_gb=?,duration_days=?,expires_at=?,user_limit=? WHERE id=?',(upstream['volume_gb'],upstream['duration_days'],upstream['expires_at'],upstream['user_limit'],cc['id']))
            return '✅ این تمدید قبلاً انجام شده است.'
        if historical and historical['status']=='applying':
            raise AgencyError('نتیجه تمدید قبلی نیاز به بررسی مدیر دارد؛ برای جلوگیری از تمدید تکراری دوباره اجرا نشد.')
        original_order=root.get_order(upstream['order_id'])
        product=root.get_product(original_order['product_id'])
        if not product:
            raise AgencyError('محصول اصلی این سرویس حذف شده است؛ پشتیبانی باید بررسی کند.')
        mode=order['renewal_mode']
        factor=Fraction(1,1)
        package_volume=Fraction(str(product['auto_provision_volume_gb'] or 0))
        package_days=int(product['duration_days'] or 0)
        add_volume=Fraction(str(order['renewal_add_volume_gb'] or 0))
        add_days=int(order['renewal_add_days'] or 0)
        if mode in ('full','volume') and package_volume>0 and add_volume<=0:
            raise AgencyError('حجم نامحدود در این محصول بالادست مجاز نیست.')
        if mode=='full' and package_days>0 and add_days<=0:
            raise AgencyError('مدت نامحدود در این محصول بالادست مجاز نیست.')
        if mode=='volume':
            if package_volume<=0:raise AgencyError('این محصول حجم نامحدود دارد.')
            factor=add_volume/package_volume
        elif mode=='time':
            if package_days<=0:raise AgencyError('این محصول مدت نامحدود دارد.')
            factor=Fraction(add_days,package_days)
            if root.get_setting(f"volume_template:{product['id']}",'')=='1':
                factor*=max(Fraction(1,1),Fraction(str(cc['volume_gb'] or 0)))
        elif mode=='full':
            factor=max(Fraction(1,1),add_volume/package_volume if package_volume>0 else Fraction(1,1),Fraction(add_days,package_days) if package_days>0 else Fraction(1,1))
        new_users=int(order['renewal_user_limit'] or cc['user_limit'] or product['base_users'] or 0)
        maximum=int(product['max_users'] or product['base_users'] or 0)
        if maximum and new_users>maximum:
            raise AgencyError('تعداد کاربر بیش از حد مجاز محصول بالادست است.')
        extra=max(0,new_users-int(product['base_users'] or 0))*int(product['extra_user_price'] or 0)*factor
        if mode=='users':
            extra=max(0,new_users-int(cc['user_limit'] or 0))*int(product['extra_user_price'] or 0)
            factor=Fraction(0,1)
        current_price=Fraction(rate(root,bot_id,product['id']))*factor+extra
        if int(order['base_price']) < (current_price.numerator+current_price.denominator-1)//current_price.denominator:
            raise AgencyError('مبلغ تمدید از نرخ تأمین جدید کمتر است؛ ابتدا قیمت‌ها را بروزرسانی کنید.')
        purchase=await asyncio.to_thread(reserve,root,bot_id,order['id'],product['id'],1,factor,extra)
        with root._get_conn() as conn:
            saved=conn.execute('SELECT status FROM managed_purchases WHERE request_key=?',(f"{bot_id}:{order['id']}",)).fetchone()
        if saved and saved['status']=='completed':
            return '✅ این تمدید قبلاً انجام شده است.'
        if saved and saved['status']=='applying':
            raise AgencyError('نتیجه تمدید قبلی نیاز به بررسی مدیر دارد؛ برای جلوگیری از تمدید تکراری دوباره اجرا نشد.')
        with root._get_conn() as conn:
            if conn.execute("UPDATE managed_purchases SET status='applying' WHERE request_key=? AND status='reserved'",(f"{bot_id}:{order['id']}",)).rowcount != 1:
                raise AgencyError('این تمدید هم‌اکنون در حال پردازش است.')
        fields={'is_renewal':1,'renewal_target_kind':'custom','renewal_target_id':upstream['id'],'renewal_mode':mode,
                'renewal_add_volume_gb':order['renewal_add_volume_gb'],'renewal_add_days':order['renewal_add_days'],
                'renewal_user_limit':order['renewal_user_limit']}
        with root._get_conn() as conn:conn.execute('UPDATE orders SET '+','.join(f'{k}=?' for k in fields)+' WHERE id=?', [*fields.values(),purchase])
        try:
            text=await execute_renewal(root,root.get_order(purchase))
        except Exception:
            # A renewal may have reached the remote panel; keep its reservation
            # for investigation rather than silently crediting an applied renewal.
            raise
        root.approve_renewal_order(purchase)
        fresh=root.get_custom_config_by_id(upstream['id'])
        with local._get_conn() as conn:
            conn.execute('UPDATE custom_configs SET volume_gb=?,duration_days=?,expires_at=?,user_limit=? WHERE id=?',(fresh['volume_gb'],fresh['duration_days'],fresh['expires_at'],fresh['user_limit'],cc['id']))
        with root._get_conn() as conn:conn.execute("UPDATE managed_purchases SET status='completed' WHERE request_key=?",(f"{bot_id}:{order['id']}",))
        return text
    finally:
        root.close()


def discover_products(root, bot_id):
    bot=root.get_reseller_bot(bot_id)
    if not bot: raise AgencyError('نماینده یافت نشد.')
    parent=dict(bot).get('managed_parent_bot_id')
    with root._get_conn() as conn:
        if parent:
            ids=[r[0] for r in conn.execute("SELECT product_id FROM managed_catalog WHERE bot_id=? AND allowed=1 AND selected=1 AND NOT EXISTS (SELECT 1 FROM settings WHERE key='volume_template:'||managed_catalog.product_id AND value='1')",(parent,))]
        else:
            ids=[r[0] for r in conn.execute("SELECT id FROM products WHERE is_active=1 AND is_auto_provision=1 AND provision_server_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM settings WHERE key='volume_template:'||products.id AND value='1')")]
        for pid in ids:
            if conn.execute("SELECT 1 FROM settings WHERE key=? AND value='1'",(f'volume_template:{pid}',)).fetchone():continue
            current=conn.execute('SELECT * FROM products WHERE id=? AND is_active=1',(pid,)).fetchone()
            if not current:continue
            added=conn.execute('INSERT OR IGNORE INTO managed_catalog(bot_id,product_id,allowed,selected) VALUES(?,?,1,0)',(bot_id,pid)).rowcount
            if added:
                product=dict(current)
                price=int(product.get('reseller_wholesale_price') if product.get('reseller_wholesale_price') is not None else product['price'])
                if parent:
                    upstream=conn.execute('SELECT buy_price FROM managed_catalog WHERE bot_id=? AND product_id=?',(parent,pid)).fetchone()
                    price=max(price,int(upstream['buy_price'] or 0)) if upstream else price
                conn.execute('INSERT INTO managed_notices(bot_id,product_id,old_price,new_price) VALUES(?,?,NULL,?)',(bot_id,pid,price))
    return sync_bot(root,bot_id)


def assign_discount(db, user_id, percent):
    percent=int(percent)
    if not 1<=percent<100 or not db.get_user(user_id):
        raise AgencyError('شناسه کاربر یا درصد تخفیف معتبر نیست.')
    if db.get_setting('managed_reseller_bot_id',''):
        with db._get_conn() as conn:
            products=conn.execute('SELECT price,managed_buy_price FROM products WHERE is_active=1 AND upstream_product_id IS NOT NULL').fetchall()
        for product in products:
            if product['price']*(100-percent)//100 < product['managed_buy_price']:
                raise AgencyError('این تخفیف قیمت محصول را پایین‌تر از نرخ خرید شما می‌برد.')
    db.set_user_reseller_tier(user_id,'silver',discount_percent=percent)
