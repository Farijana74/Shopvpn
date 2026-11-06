"""Thirty-day volume quotes using the existing managed purchase ledger."""
import asyncio
import json
from aiogram import F
from aiogram.fsm.state import State,StatesGroup
from aiogram.types import InlineKeyboardButton as B,InlineKeyboardMarkup as M
from managed_resellers import AgencyError,chain,rate

class VolumeFlow(StatesGroup):
    volume=State()
    plan=State()
    username=State()
    settings=State()

def policy(root,bot_id):
    return json.loads(root.get_setting(f'volume_policy:{int(bot_id)}','{}'))

def configure(root,bot_id,panel_id,price,enabled=True,*,policy_key=None):
    from database import Database
    bot=root.get_reseller_bot(bot_id);server=root.get_panel_server(panel_id)
    if not bot or not server or not server['is_active'] or dict(server).get('managed_source_server_id'):raise AgencyError('نماینده یا پنل فعال یافت نشد.')
    if int(price)<1:raise AgencyError('نرخ هر گیگ باید مثبت باشد.')
    child=Database(bot['db_path'])
    try:
        if not child.get_setting('managed_reseller_bot_id',''):raise AgencyError('ابتدا نمایندگی طلایی این ربات را فعال کنید.')
    finally:child.close()
    policy_key=policy_key or f'volume_policy:{bot_id}'
    old=json.loads(root.get_setting(policy_key,'{}'));pid=old.get('product_id')
    if not pid:
        with root._get_conn() as conn:
            category=conn.execute('SELECT id FROM categories ORDER BY id LIMIT 1').fetchone()
            cid=category[0] if category else conn.execute("INSERT INTO categories(name) VALUES('حجم دلخواه')").lastrowid
        with root._get_conn() as conn:
            pid=conn.execute('INSERT INTO products(category_id,name,price,duration_days,is_auto_provision,auto_provision_volume_gb,provision_server_id) VALUES(?,?,?,30,1,1,?)',(cid,f'تأمین حجم دلخواه #{bot_id}',int(price),panel_id)).lastrowid
            conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(f'volume_template:{pid}','1'))
        root._settings_cache=None
    members=chain(root,bot_id)
    with root._get_conn() as conn:
        conn.execute('UPDATE products SET price=?,reseller_wholesale_price=?,provision_server_id=?,duration_days=30,is_active=? WHERE id=?',(price,price,panel_id,int(enabled),pid))
        for member in members:
            conn.execute('INSERT INTO managed_catalog(bot_id,product_id,allowed,selected) VALUES(?,?,?,1) ON CONFLICT(bot_id,product_id) DO UPDATE SET allowed=excluded.allowed,selected=1',(member['id'],pid,int(enabled)))
    data={'enabled':bool(enabled),'product_id':pid,'price':int(price),'panel_id':panel_id}
    root.set_setting(policy_key,json.dumps(data))
    if old.get('price') is not None and int(old['price'])!=int(price):
        with root._get_conn() as conn:
            conn.execute('INSERT INTO managed_notices(bot_id,product_id,old_price,new_price) VALUES(?,?,?,?)',(bot_id,pid,int(old['price']),int(price)))
    local=Database(bot['db_path'])
    try:local.set_setting('custom_config_enabled','1' if enabled else '0')
    finally:local.close()
    return data

def quote(local,root,bot_id,user_id,gb,*,data_override=None,extras=None):
    data=data_override or policy(root,bot_id)
    minimum=max(int(data.get('min_gb',1)),int(local.get_setting('volume_min_gb','1')))
    maximum=int(data.get('max_gb',1000))
    if not data.get('enabled') or not minimum<=gb<=maximum:raise AgencyError(f'حجم دلخواه غیرفعال است یا حجم خارج از بازه {minimum} تا {maximum} گیگ است.')
    cost=rate(root,bot_id,data['product_id'])
    from volume_personal_prices import personal_volume_rate
    override=personal_volume_rate(local,user_id,extras['native_plan_id']) if extras and extras.get('native_plan_id') is not None else None
    selling=max(cost,override if override is not None else int(local.get_setting('volume_sell_per_gb',str(cost))))
    source=root.get_product(data['product_id']);server=root.get_panel_server(source['provision_server_id'])
    with local._get_conn() as conn:
        category=conn.execute('SELECT id FROM categories WHERE is_active=1 ORDER BY id LIMIT 1').fetchone()
        cid=category[0] if category else conn.execute("INSERT INTO categories(name) VALUES('حجم دلخواه')").lastrowid
        proxy=conn.execute('SELECT id FROM panel_servers WHERE managed_source_server_id=?',(server['id'],)).fetchone()
        proxy_id=proxy[0] if proxy else conn.execute('INSERT INTO panel_servers(name,panel_type,api_url,managed_source_server_id,managed_root_db_path) VALUES(?,?,?,?,?)',('حجم دلخواه',server['panel_type'],'https://managed.invalid',server['id'],root.db_path)).lastrowid
        pid=conn.execute('INSERT INTO products(category_id,name,price,duration_days,is_auto_provision,auto_provision_volume_gb,provision_server_id,upstream_product_id,managed_buy_price,delivery_mode) VALUES(?,?,?,30,1,?,?,?,?,?)',(cid,f'حجم دلخواه {gb} گیگ / ۳۰ روز',selling*gb,gb,proxy_id,source['id'],cost*gb,source['delivery_mode'])).lastrowid
        conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(f'volume_quote:{pid}',json.dumps({'user_id':user_id,'gb':gb,'bot_id':bot_id,**(extras or {})})))
        conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(f'volume_template:{pid}','1'))
        if override is not None:conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(f'personal_price:{user_id}:{pid}',str(selling*gb)))
    local._settings_cache=None
    return pid,selling*gb

async def start(message,state,local):
    from database import Database
    root=Database(local.get_setting('managed_root_db_path',''))
    try:
        from managed_native_volume import begin
        return await begin(message,state,local,root)
    finally:root.close()

def register(router,local,is_main_bot):
    from database import Database
    async def flow_input(message,state):
        # Reply-menu buttons belong to their navigation handlers, not numeric/name input.
        import keyboards
        from i18n import tr
        text=(message.text or '').strip()
        labels=[local.get_setting(key,'') for key in local.get_menu_order()]
        labels += [keyboards.LANGUAGE_BTN_TEXT,keyboards.MINIAPP_BTN_TEXT]
        menu={label.strip() for label in labels if label}
        menu.update(tr(label,'en').strip() for label in labels if label)
        if text and (text in menu or text.startswith('/')):
            await state.clear()
            return False
        return True
    @router.callback_query(F.data=='volume:settings')
    async def settings(call,state):
        if not local.is_senior_admin(call.from_user.id):return await call.answer('دسترسی ندارید.',show_alert=True)
        if not is_main_bot and not local.get_setting('managed_reseller_bot_id',''):
            return await call.answer('این تنظیم برای نمایندگی طلایی مدیریت‌شده است.',show_alert=True)
        await state.clear()
        await state.set_state(VolumeFlow.settings)
        if is_main_bot:
            bots=local.list_reseller_bots(active_only=False);panels=local.get_panel_servers(True)
            text='شناسه نماینده، شناسه پنل و نرخ تأمین هر گیگ را با فاصله بفرستید. مثال: 2 1 5000\nبرای غیرفعال‌سازی همان سه عدد و در انتها 0 بفرستید.\nنماینده‌ها:\n'+'\n'.join(f"{b['id']}: @{b['bot_username']}" for b in bots)+'\nپنل‌ها:\n'+'\n'.join(f"{p['id']}: {p['name']}" for p in panels)
            from managed_native_volume import plans
            native=plans(local)
            if not native:
                await state.clear()
                await call.message.answer('ابتدا همان بخش حجم دلخواه ربات اصلی را فعال و قیمت‌گذاری کنید؛ پلن‌های آن خودکار برای نماینده‌ها نمایش داده می‌شوند.',reply_markup=M(inline_keyboard=[[B(text='تنظیمات حجم دلخواه ربات اصلی',callback_data='adm_custom_config_settings')]]))
                return await call.answer()
            if native:
                await state.update_data(volume_settings_native=True)
                text='همان پلن‌های حجم دلخواه ربات اصلی خودکار برای نماینده‌ها نمایش داده می‌شوند.\nبرای نرخ اختصاصی تأمین: شناسه نماینده، شناسه پلن و نرخ هر گیگ را بفرستید. برای قطع دسترسی نماینده، در انتها 0 بفرستید.\nنماینده‌ها:\n'+'\n'.join(f"{b['id']}: @{b['bot_username']}" for b in bots)+'\nپلن‌های حجم دلخواه:\n'+'\n'.join(f"{p['id']}: {p['name']}" for p in native)
        else:
            await state.clear()
            text=f"🛠 تنظیمات حجم دلخواه\nحداقل اختصاصی فروشگاه: {local.get_setting('volume_min_gb','مطابق ربات اصلی')} گیگ\nنرخ فروش: {local.get_setting('volume_sell_per_gb','مطابق ربات اصلی')} تومان/گیگ\nمدت ثابت: ۳۰ روز"
            await call.message.answer(text,reply_markup=M(inline_keyboard=[[B(text='تنظیم حداقل حجم',callback_data='volume:edit:min')],[B(text='تنظیم نرخ فروش هر گیگ',callback_data='volume:edit:price')],[B(text='فعال / غیرفعال کردن',callback_data='volume:edit:toggle')],[B(text='بازگشت',callback_data='adm_back_panel')]]))
            return await call.answer()
        await call.message.answer(text[:4000]);await call.answer()
    @router.callback_query(F.data.startswith('volume:edit:'))
    async def edit_setting(call,state):
        if not local.is_senior_admin(call.from_user.id) or is_main_bot:return await call.answer('دسترسی ندارید.',show_alert=True)
        field=call.data.rsplit(':',1)[1]
        if field=='toggle':
            enabled=local.get_setting('volume_local_enabled','1')!='1'
            local.set_setting('volume_local_enabled','1' if enabled else '0')
            root=Database(local.get_setting('managed_root_db_path',''))
            try:
                from managed_native_volume import available
                effective=await asyncio.to_thread(available,local,root,int(local.get_setting('managed_reseller_bot_id')))
                local.set_setting('custom_config_enabled','1' if effective else '0')
            finally:root.close()
            text='✅ حجم دلخواه فعال شد.' if effective else ('نمایش از سمت شما مجاز شد؛ برای نمایش به مشتری، پلن و دسترسی ربات اصلی نیز باید فعال باشند.' if enabled else '✅ حجم دلخواه غیرفعال شد.')
            await call.message.answer(text)
        elif field in ('min','price'):
            await state.set_state(VolumeFlow.settings)
            await state.update_data(volume_setting_field=field)
            await call.message.answer('حداقل حجم را به گیگابایت بفرستید. حداقل ربات اصلی نیز رعایت می‌شود.' if field=='min' else 'نرخ فروش هر گیگ را به تومان بفرستید.')
        await call.answer()

    @router.callback_query(F.data.startswith('volume:native:'),VolumeFlow.plan)
    async def choose_native(call,state):
        from managed_native_volume import rules,ask_username
        root=Database(local.get_setting('managed_root_db_path',''))
        try:
            plan_id=int(call.data.rsplit(':',1)[1])
            rules(local,root,int(local.get_setting('managed_reseller_bot_id')),plan_id)
            await ask_username(call.message,state,plan_id);await call.answer()
        except ValueError as exc:await call.answer(str(exc),show_alert=True)
        finally:root.close()

    @router.callback_query(F.data=='volume:random',VolumeFlow.username)
    async def random_username(call,state):
        import secrets
        from managed_native_volume import ask_volume
        root=Database(local.get_setting('managed_root_db_path',''))
        try:
            prefix=local.get_custom_config_prefix()
            name=(prefix+'-' if prefix else '')+'u'+secrets.token_hex(4)
            await ask_volume(call.message,state,local,root,name);await call.answer()
        except ValueError as exc:await call.answer(str(exc),show_alert=True)
        finally:root.close()

    @router.message(VolumeFlow.username,flow_input)
    async def receive_username(message,state):
        import re
        from managed_native_volume import ask_volume
        text=(message.text or '').strip()
        if text in ('انصراف','/cancel'):
            await state.clear();return await message.answer('لغو شد.',reply_markup=M(inline_keyboard=[[B(text='منوی اصلی',callback_data='acct:main_menu')]]))
        if not re.fullmatch(r'[A-Za-z0-9_]{3,20}',text):return await message.answer('نام باید شامل ۳ تا ۲۰ حرف انگلیسی، عدد یا آندرلاین باشد.')
        root=Database(local.get_setting('managed_root_db_path',''))
        try:
            prefix=local.get_custom_config_prefix()
            await ask_volume(message,state,local,root,(prefix+'-' if prefix else '')+text)
        except ValueError as exc:await message.answer(str(exc))
        finally:root.close()

    @router.message(VolumeFlow.settings,flow_input)
    async def save_settings(message,state):
        if not local.is_senior_admin(message.from_user.id):return
        text=(message.text or '').strip()
        try:
            if text in ('انصراف','/cancel'):pass
            elif is_main_bot:
                parts=[int(x) for x in text.split()]
                if len(parts) not in (3,4):raise AgencyError('سه عدد لازم است: نماینده، پنل، نرخ هر گیگ.')
                if (await state.get_data()).get('volume_settings_native'):
                    from managed_native_volume import plans
                    bot_id,plan_id,price=parts[:3]
                    if not local.get_reseller_bot(bot_id) or plan_id not in {p['id'] for p in plans(local)} or price<1:raise AgencyError('نماینده، پلن یا نرخ واردشده معتبر نیست.')
                    local.set_setting(f'volume_native_access:{bot_id}','0' if len(parts)==4 and parts[3]==0 else '1')
                    local.set_setting(f'volume_supply_price:{bot_id}:{plan_id}',str(price))
                else:await asyncio.to_thread(configure,local,*parts[:3],enabled=bool(parts[3]) if len(parts)==4 else True)
            else:
                root=Database(local.get_setting('managed_root_db_path',''))
                try:
                    if (await state.get_data()).get('volume_setting_field')=='min':
                        value=int(text)
                        from managed_native_volume import plans
                        choices=plans(root)
                        low=min((int(p['min_gb']) for p in choices),default=1)
                        high=max((int(p['max_gb']) for p in choices),default=1000)
                        if not low<=value<=high:raise AgencyError(f'حداقل حجم باید بین {low} تا {high} گیگ باشد.')
                        local.set_setting('volume_min_gb',str(value))
                        await state.clear()
                        return await message.answer('✅ حداقل حجم ذخیره شد.',reply_markup=M(inline_keyboard=[[B(text='حجم دلخواه',callback_data='volume:settings')]]))
                    from managed_native_volume import plans,costs
                    choices=plans(root)
                    if choices:
                        value=int(text)
                        floor=max(costs(root,int(local.get_setting('managed_reseller_bot_id')),p['id'],max(int(p['min_gb']),int(local.get_setting('volume_min_gb','1'))))[1][-1][1] for p in choices)
                        if value<floor:raise AgencyError(f'نرخ فروش حداقل {floor:,} تومان باشد.')
                        local.set_setting('volume_sell_per_gb',str(value));await state.clear()
                        return await message.answer('✅ نرخ فروش ذخیره شد.',reply_markup=M(inline_keyboard=[[B(text='حجم دلخواه',callback_data='volume:settings')]]))
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
    @router.message(VolumeFlow.volume,flow_input)
    async def receive_volume(message,state):
        from menu_restore import main_menu_markup
        if (message.text or '').strip() in ('انصراف','/cancel'):
            await state.clear();return await message.answer('لغو شد.',reply_markup=main_menu_markup(local,message.from_user.id))
        text=(message.text or '').strip()
        if not text.isdecimal():return await message.answer('لطفاً حجم را فقط با عدد صحیح مثبت به گیگابایت وارد کنید؛ مثلاً ۱۰. برای خروج «انصراف» بفرستید.')
        root=Database(local.get_setting('managed_root_db_path',''))
        try:
            data=await state.get_data()
            if data.get('native_plan_id') is not None:
                from managed_native_volume import quote as native_quote
                pid,price=await asyncio.to_thread(native_quote,local,root,int(local.get_setting('managed_reseller_bot_id')),message.from_user.id,int(text),data['native_plan_id'],data.get('native_username'))
            else:pid,price=await asyncio.to_thread(quote,local,root,int(local.get_setting('managed_reseller_bot_id')),message.from_user.id,int(text))
            await state.clear()
            await message.answer(f'قیمت سفارش: {price:,} تومان\nمدت: ۳۰ روز',reply_markup=M(inline_keyboard=[[B(text='ادامه خرید و پرداخت',callback_data=f'prod:{pid}')],[B(text='بازگشت',callback_data='acct:main_menu')]]))
        except (ValueError,TypeError) as exc:await message.answer(str(exc))
        finally:root.close()
