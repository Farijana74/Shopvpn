"""Thirty-day volume quotes using the existing managed purchase ledger."""
import asyncio
import json
from aiogram import F
from aiogram.fsm.state import State,StatesGroup
from aiogram.types import InlineKeyboardButton as B,InlineKeyboardMarkup as M
from managed_resellers import AgencyError,chain,rate

class VolumeFlow(StatesGroup):
    volume=State()
    settings=State()

def policy(root,bot_id):
    return json.loads(root.get_setting(f'volume_policy:{int(bot_id)}','{}'))

def configure(root,bot_id,panel_id,price,enabled=True):
    from database import Database
    bot=root.get_reseller_bot(bot_id);server=root.get_panel_server(panel_id)
    if not bot or not server or not server['is_active'] or dict(server).get('managed_source_server_id'):raise AgencyError('نماینده یا پنل فعال یافت نشد.')
    if int(price)<1:raise AgencyError('نرخ هر گیگ باید مثبت باشد.')
    child=Database(bot['db_path'])
    try:
        if not child.get_setting('managed_reseller_bot_id',''):raise AgencyError('ابتدا نمایندگی طلایی این ربات را فعال کنید.')
    finally:child.close()
    old=policy(root,bot_id);pid=old.get('product_id')
    if not pid:
        with root._get_conn() as conn:
            category=conn.execute('SELECT id FROM categories ORDER BY id LIMIT 1').fetchone()
            cid=category[0] if category else conn.execute("INSERT INTO categories(name) VALUES('حجم دلخواه')").lastrowid
        pid=root.add_product(cid,f'تأمین حجم دلخواه #{bot_id}',int(price),duration_days=30,is_auto_provision=True,auto_provision_volume_gb=1,provision_server_id=panel_id)
        root.set_setting(f'volume_template:{pid}','1')
    members=chain(root,bot_id)
    with root._get_conn() as conn:
        conn.execute('UPDATE products SET price=?,reseller_wholesale_price=?,provision_server_id=?,duration_days=30,is_active=? WHERE id=?',(price,price,panel_id,int(enabled),pid))
        for member in members:
            conn.execute('INSERT INTO managed_catalog(bot_id,product_id,allowed,selected) VALUES(?,?,?,1) ON CONFLICT(bot_id,product_id) DO UPDATE SET allowed=excluded.allowed,selected=1',(member['id'],pid,int(enabled)))
    data={'enabled':bool(enabled),'product_id':pid,'price':int(price),'panel_id':panel_id}
    root.set_setting(f'volume_policy:{bot_id}',json.dumps(data))
    if old.get('price') is not None and int(old['price'])!=int(price):
        with root._get_conn() as conn:
            conn.execute('INSERT INTO managed_notices(bot_id,product_id,old_price,new_price) VALUES(?,?,?,?)',(bot_id,pid,int(old['price']),int(price)))
    local=Database(bot['db_path'])
    try:local.set_setting('custom_config_enabled','1' if enabled else '0')
    finally:local.close()
    return data

def quote(local,root,bot_id,user_id,gb):
    data=policy(root,bot_id)
    if not data.get('enabled') or not 1<=gb<=1000:raise AgencyError('حجم دلخواه غیرفعال است یا حجم خارج از بازه ۱ تا ۱۰۰۰ گیگ است.')
    cost=rate(root,bot_id,data['product_id'])
    selling=max(cost,int(local.get_setting('volume_sell_per_gb',str(cost))))
    source=root.get_product(data['product_id']);server=root.get_panel_server(source['provision_server_id'])
    with local._get_conn() as conn:
        category=conn.execute('SELECT id FROM categories WHERE is_active=1 ORDER BY id LIMIT 1').fetchone()
        cid=category[0] if category else conn.execute("INSERT INTO categories(name) VALUES('حجم دلخواه')").lastrowid
        proxy=conn.execute('SELECT id FROM panel_servers WHERE managed_source_server_id=?',(server['id'],)).fetchone()
        proxy_id=proxy[0] if proxy else conn.execute('INSERT INTO panel_servers(name,panel_type,api_url,managed_source_server_id,managed_root_db_path) VALUES(?,?,?,?,?)',('حجم دلخواه',server['panel_type'],'https://managed.invalid',server['id'],root.db_path)).lastrowid
        pid=conn.execute('INSERT INTO products(category_id,name,price,duration_days,is_auto_provision,auto_provision_volume_gb,provision_server_id,upstream_product_id,managed_buy_price,delivery_mode) VALUES(?,?,?,30,1,?,?,?,?,?)',(cid,f'حجم دلخواه {gb} گیگ / ۳۰ روز',selling*gb,gb,proxy_id,source['id'],cost*gb,source['delivery_mode'])).lastrowid
    local.set_setting(f'volume_quote:{pid}',json.dumps({'user_id':user_id,'gb':gb,'bot_id':bot_id}))
    local.set_setting(f'volume_template:{pid}','1')
    return pid,selling*gb

async def start(message,state,local):
    from database import Database
    root=Database(local.get_setting('managed_root_db_path',''))
    try:
        data=policy(root,int(local.get_setting('managed_reseller_bot_id','0')))
        if not data.get('enabled'):
            from menu_restore import main_menu_markup
            return await message.answer('حجم دلخواه فعلاً فعال نیست.',reply_markup=main_menu_markup(local,message.from_user.id))
        cost=rate(root,int(local.get_setting('managed_reseller_bot_id')),data['product_id'])
        selling=max(cost,int(local.get_setting('volume_sell_per_gb',str(cost))))
        await state.set_state(VolumeFlow.volume)
        await message.answer(f'حجم دلخواه را از ۱ تا ۱۰۰۰ گیگ وارد کنید.\nمدت ثابت: ۳۰ روز\nقیمت هر گیگ: {selling:,} تومان\nبرای لغو «انصراف» بفرستید.')
    finally:root.close()

def register(router,local,is_main_bot):
    from database import Database
    @router.callback_query(F.data=='volume:settings')
    async def settings(call,state):
        if not local.is_senior_admin(call.from_user.id):return await call.answer('دسترسی ندارید.',show_alert=True)
        if not is_main_bot and not local.get_setting('managed_reseller_bot_id',''):
            return await call.answer('این تنظیم برای نمایندگی طلایی مدیریت‌شده است.',show_alert=True)
        await state.set_state(VolumeFlow.settings)
        if is_main_bot:
            bots=local.list_reseller_bots(active_only=False);panels=local.get_panel_servers(True)
            text='شناسه نماینده، شناسه پنل و نرخ تأمین هر گیگ را با فاصله بفرستید. مثال: 2 1 5000\nبرای غیرفعال‌سازی همان سه عدد و در انتها 0 بفرستید.\nنماینده‌ها:\n'+'\n'.join(f"{b['id']}: @{b['bot_username']}" for b in bots)+'\nپنل‌ها:\n'+'\n'.join(f"{p['id']}: {p['name']}" for p in panels)
        else:text='قیمت فروش هر گیگ حجم دلخواه را به تومان بفرستید. هزینه تأمین جدا محاسبه می‌شود؛ مدت ثابت ۳۰ روز است.'
        await call.message.answer(text[:4000]);await call.answer()
    @router.message(VolumeFlow.settings)
    async def save_settings(message,state):
        if not local.is_senior_admin(message.from_user.id):return
        text=(message.text or '').strip()
        try:
            if text in ('انصراف','/cancel'):pass
            elif is_main_bot:
                parts=[int(x) for x in text.split()]
                if len(parts) not in (3,4):raise AgencyError('سه عدد لازم است: نماینده، پنل، نرخ هر گیگ.')
                await asyncio.to_thread(configure,local,*parts[:3],enabled=bool(parts[3]) if len(parts)==4 else True)
            else:
                root=Database(local.get_setting('managed_root_db_path',''))
                try:
                    data=policy(root,int(local.get_setting('managed_reseller_bot_id')))
                    if not data.get('enabled'):raise AgencyError('ابتدا ربات اصلی باید حجم دلخواه را فعال کند.')
                    floor=rate(root,int(local.get_setting('managed_reseller_bot_id')),data['product_id'])
                    if int(text)<floor:raise AgencyError(f'حداقل نرخ فروش هر گیگ {floor:,} تومان است.')
                    local.set_setting('volume_sell_per_gb',str(int(text)))
                finally:root.close()
            await state.clear()
            from menu_restore import main_menu_markup
            await message.answer('✅ تنظیمات ذخیره شد.',reply_markup=main_menu_markup(local,message.from_user.id))
        except ValueError as exc:await message.answer(str(exc))
    @router.message(VolumeFlow.volume)
    async def receive_volume(message,state):
        from menu_restore import main_menu_markup
        if (message.text or '').strip() in ('انصراف','/cancel'):
            await state.clear();return await message.answer('لغو شد.',reply_markup=main_menu_markup(local,message.from_user.id))
        root=Database(local.get_setting('managed_root_db_path',''))
        try:
            pid,price=await asyncio.to_thread(quote,local,root,int(local.get_setting('managed_reseller_bot_id')),message.from_user.id,int(message.text))
            await state.clear()
            await message.answer(f'قیمت سفارش: {price:,} تومان\nمدت: ۳۰ روز',reply_markup=M(inline_keyboard=[[B(text='ادامه خرید و پرداخت',callback_data=f'prod:{pid}')],[B(text='بازگشت',callback_data='acct:main_menu')]]))
        except (ValueError,TypeError) as exc:await message.answer(str(exc))
        finally:root.close()
