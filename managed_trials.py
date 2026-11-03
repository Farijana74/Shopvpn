"""Root-controlled trials with one durable claim per customer and wallet billing."""
import asyncio
import json
from managed_resellers import AgencyError

POLICY_KEY = 'managed_trial_policy'


def trial_policy(root):
    try:
        value=json.loads(root.get_setting(POLICY_KEY,'{}'))
        return value if isinstance(value,dict) else {}
    except (ValueError,TypeError):return {}


def configure_trials(root, product_id, volume_mb, hours, cost):
    values=(product_id,volume_mb,hours,cost)
    if any(not isinstance(v,int) or isinstance(v,bool) for v in values) or not 1<=volume_mb<=1048576 or not 1<=hours<=8760 or cost<0:
        raise AgencyError('حجم و مدت باید مثبت و هزینه به تومان صفر یا بیشتر باشد.')
    product=root.get_product(product_id)
    if not product or not product['is_active'] or not product['is_auto_provision'] or not product['provision_server_id'] or product['provision_ssh_node_id']:
        raise AgencyError('محصول پایه باید VPN فعال و متصل به پنل اصلی باشد.')
    category=root.get_category(product['category_id'])
    server=root.get_panel_server(product['provision_server_id'])
    if (category and not category['is_active']) or not server or not server['is_active']:
        raise AgencyError('دسته‌بندی و پنل محصول پایه باید فعال باشند.')
    policy=dict(enabled=True,product_id=product_id,volume_mb=volume_mb,hours=hours,cost=cost)
    root.set_setting(POLICY_KEY,json.dumps(policy))
    return policy


def _claim(local,user_id,policy):
    with local._get_conn() as conn:
        old=conn.execute('SELECT * FROM managed_trials WHERE user_id=?',(user_id,)).fetchone()
        if old and old['status']=='completed':return dict(old)
        if old and old['status']=='processing':
            raise AgencyError('درخواست تست شما در حال پردازش است؛ دوباره درخواست ندهید. اگر متوقف مانده، پشتیبانی باید بررسی کند.')
        product=conn.execute('SELECT id FROM products WHERE upstream_product_id=?',(policy['product_id'],)).fetchone()
        if not product:raise AgencyError('محصول پایه تست هنوز برای این نماینده دریافت نشده است.')
        changed=conn.execute('UPDATE users SET test_used=test_used+1 WHERE telegram_id=? AND test_used<1',(user_id,)).rowcount
        if not changed:raise AgencyError('هر کاربر فقط یک بار می‌تواند سرویس تست دریافت کند.')
        order_id=conn.execute("INSERT INTO orders(user_id,product_id,status,base_price,final_price,quantity,user_limit) VALUES(?,?,'processing',0,0,1,1)",(user_id,product['id'])).lastrowid
        encoded=json.dumps(policy)
        conn.execute("INSERT INTO managed_trials(user_id,order_id,policy,status) VALUES(?,?,?,'processing') ON CONFLICT(user_id) DO UPDATE SET order_id=excluded.order_id,policy=excluded.policy,status='processing'",(user_id,order_id,encoded))
        return dict(order_id=order_id,policy=encoded,status='processing')


async def provide_trial(local,user_id):
    from database import Database
    from managed_resellers import provision,refund_reservation
    root=Database(local.get_setting('managed_root_db_path',''))
    bot_id=int(local.get_setting('managed_reseller_bot_id','0'))
    try:
        policy=trial_policy(root)
        if not policy.get('enabled'):raise AgencyError('سرویس تست نماینده‌ها از ربات اصلی غیرفعال است.')
        claim=await asyncio.to_thread(_claim,local,user_id,policy)
        policy=json.loads(claim['policy'])
        with local._get_conn() as conn:
            product=conn.execute('SELECT * FROM products WHERE upstream_product_id=?',(policy['product_id'],)).fetchone()
            saved=conn.execute('SELECT * FROM custom_configs WHERE order_id=?',(claim['order_id'],)).fetchall()
        if claim['status']=='completed':
            if not saved:raise AgencyError('سرویس تست قبلی حذف شده است؛ پشتیبانی باید بررسی کند.')
            results=[dict(username=c['username'],subscription_url=c['subscription_url'],panel_server_id=c['panel_server_id'],custom_config_id=c['id'],volume_gb=c['volume_gb'],duration_days=c['duration_days'],_managed_delivery_mode=c['delivery_mode']) for c in saved]
            return product,results
        try:
            results=await provision(local,product,1,user_id,claim['order_id'],trial_policy=policy)
        except Exception as exc:
            with root._get_conn() as conn:
                existing=conn.execute('SELECT 1 FROM custom_configs c JOIN managed_purchases p ON p.root_order_id=c.order_id WHERE p.request_key=?',(f"{bot_id}:{claim['order_id']}",)).fetchone()
            if existing:
                raise AgencyError('سرویس بالادست ثبت شده ولی تکمیل تست نیاز به بررسی پشتیبانی دارد؛ هزینه دوباره کسر نمی‌شود.') from exc
            await asyncio.to_thread(refund_reservation,root,bot_id,claim['order_id'])
            with local._get_conn() as conn:
                conn.execute("UPDATE managed_trials SET status='failed' WHERE user_id=?",(user_id,))
                conn.execute('UPDATE users SET test_used=MAX(test_used-1,0) WHERE telegram_id=?',(user_id,))
                conn.execute("UPDATE orders SET status='rejected' WHERE id=?",(claim['order_id'],))
            if isinstance(exc,AgencyError):raise AgencyError(str(exc)) from None
            raise AgencyError('ساخت تست ناموفق بود؛ هزینه و سهمیه برگردانده شد. دوباره تلاش کنید.') from None
        with local._get_conn() as conn:
            conn.execute("UPDATE custom_configs SET source='test' WHERE order_id=?",(claim['order_id'],))
            conn.execute("UPDATE orders SET status='approved' WHERE id=?",(claim['order_id'],))
            conn.execute("UPDATE managed_trials SET status='completed' WHERE user_id=?",(user_id,))
        with root._get_conn() as conn:
            conn.execute("UPDATE custom_configs SET source='test' WHERE order_id=(SELECT root_order_id FROM managed_purchases WHERE request_key=?)",(f"{bot_id}:{claim['order_id']}",))
        return product,results
    finally:root.close()
