"""Permission-checked bot UI for root catalogs and nested gold resellers."""
import asyncio
import html
import os
from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from managed_resellers import AgencyError, bootstrap, configure, select_product, sync_bot, rate, chain, discover_products, assign_discount


class AgencyFlow(StatesGroup):
    price=State()
    select_price=State()
    description=State()
    name=State()
    grant=State()
    discount=State()
    token=State()
    owner=State()
    trials=State()
    trial_hours=State()
    trial_cost=State()
    trial_panel=State()
    trial_confirm=State()


def create_agency_router(local, is_main_bot):
    from config import DB_PATH, RESELLER_DBS_DIR
    from database import Database
    router=Router()
    root=local if is_main_bot else Database(local.get_setting('managed_root_db_path',DB_PATH))
    parent_id=None if is_main_bot else int(local.get_setting('managed_reseller_bot_id','0') or 0)

    def authorized(user):
        return local.is_admin(user) and (is_main_bot or bool(parent_id))

    def own_bot(bot_id):
        bot=root.get_reseller_bot(bot_id)
        if not bot:
            raise AgencyError('نماینده یافت نشد.')
        if not is_main_bot and dict(bot).get('managed_parent_bot_id') != parent_id and bot_id != parent_id:
            raise AgencyError('دسترسی به این نماینده ندارید.')
        return bot

    async def display(call, text, rows):
        markup=Markup(inline_keyboard=rows)
        try:
            await call.message.edit_text(text,reply_markup=markup)
        except Exception:
            await call.message.answer(text,reply_markup=markup)

    def home_rows():
        rows=[]
        if parent_id:
            rows += [[Button(text='📦 محصولات و دریافت لیست جدید',callback_data='agency:products')]]
        rows += [[Button(text='٪ نمایندگی تخفیف درصدی',callback_data='agency:discount')]]
        rows += [[Button(text='🧾 تراکنش‌های تأمین',callback_data='agency:transactions')],
                 [Button(text='👥 نماینده‌ها',callback_data='agency:bots')],
                 [Button(text='➕ ثبت نماینده طلایی',callback_data='agency:add')]]
        if is_main_bot:
            rows += [[Button(text='💰 تعیین نرخ پایه تأمین محصول',callback_data='agency:wholesale')]]
            rows += [[Button(text='🧪 تنظیم تست نماینده‌ها',callback_data='agency:trials')]]
        rows += [[Button(text='⬅️ منوی اصلی',callback_data='acct:main_menu')]]
        return rows

    @router.message(StateFilter(AgencyFlow), F.text.in_({'❌ انصراف','انصراف','/cancel'}))
    async def cancel_agency(message,state):
        await state.clear()
        from menu_restore import main_menu_markup
        markup=await asyncio.to_thread(main_menu_markup,local,message.from_user.id)
        await message.answer('عملیات لغو شد. از منو انتخاب کنید:',reply_markup=markup)

    @router.message(Command('agency'))
    async def agency_command(message,state):
        if authorized(message.from_user.id):
            await state.clear()
            await message.answer('مدیریت نمایندگی طلایی',reply_markup=Markup(inline_keyboard=home_rows()))

    @router.callback_query(F.data.startswith('agency:'))
    async def agency_callback(call,state):
        if not authorized(call.from_user.id):
            return await call.answer('دسترسی مجاز نیست.',show_alert=True)
        parts=call.data.split(':')
        action=parts[1]
        try:
            if action=='home':
                await state.clear()
                await display(call,'مدیریت نمایندگی طلایی',home_rows())
            elif action in ('trials','trials_off','trial_panels','trial_panel','trial_save'):
                if not is_main_bot or not root.is_senior_admin(call.from_user.id):raise AgencyError('این تنظیم فقط در اختیار مدیر ارشد ربات اصلی است.')
                from managed_trials import trial_policy,configure_panel_trials,POLICY_KEY
                import json
                if action in ('trials','trials_off'):
                    await state.clear()
                    policy=trial_policy(root)
                    if action=='trials_off':
                        policy['enabled']=False
                        await asyncio.to_thread(root.set_setting,POLICY_KEY,json.dumps(policy))
                        await display(call,'تست نماینده‌ها غیرفعال شد.',[[Button(text='تنظیم دوباره',callback_data='agency:trials')],[Button(text='بازگشت',callback_data='agency:home')]])
                    else:
                        await state.set_state(AgencyFlow.trials)
                        await display(call,'حجم هر سرویس تست چند مگابایت باشد؟\nمثلاً 512 برای نیم گیگابایت.',[[Button(text='غیرفعال‌کردن تست نماینده‌ها',callback_data='agency:trials_off')],[Button(text='انصراف',callback_data='agency:home')]])
                elif action=='trial_panels':
                    if await state.get_state() not in (AgencyFlow.trial_panel.state,AgencyFlow.trial_confirm.state):raise AgencyError('ابتدا تنظیم تست را از ابتدا شروع کنید.')
                    await state.set_state(AgencyFlow.trial_panel)
                    servers=await asyncio.to_thread(root.get_panel_servers,True)
                    servers=[s for s in servers if not dict(s).get('managed_source_server_id')]
                    if not servers:raise AgencyError('هیچ پنل فعالی در ربات اصلی ثبت نشده است.')
                    page=max(0,int(parts[2]) if len(parts)>2 else 0)
                    rows=[[Button(text=s['name'],callback_data=f"agency:trial_panel:{s['id']}")] for s in servers[page*30:(page+1)*30]]
                    nav=[]
                    if page:nav.append(Button(text='قبلی',callback_data=f'agency:trial_panels:{page-1}'))
                    if (page+1)*30<len(servers):nav.append(Button(text='بعدی',callback_data=f'agency:trial_panels:{page+1}'))
                    if nav:rows.append(nav)
                    rows.append([Button(text='انصراف',callback_data='agency:home')])
                    await display(call,'سرویس تست روی کدام پنل ساخته شود؟',rows)
                elif action=='trial_panel':
                    if await state.get_state()!=AgencyFlow.trial_panel.state:raise AgencyError('ابتدا مراحل تنظیم تست را انجام دهید.')
                    server=await asyncio.to_thread(root.get_panel_server,int(parts[2]))
                    if not server or not server['is_active'] or dict(server).get('managed_source_server_id'):raise AgencyError('پنل انتخاب‌شده فعال نیست.')
                    data=await state.get_data()
                    await state.update_data(trial_panel_id=server['id'])
                    await state.set_state(AgencyFlow.trial_confirm)
                    await display(call,f"تنظیم تست نماینده‌ها:\nحجم: {data['trial_volume']} مگابایت\nمدت: {data['trial_hours']} ساعت\nهزینه هر تست: {data['trial_cost']:,} تومان\nپنل: {server['name']}\n\nتست برای مشتری رایگان است؛ هزینه از کیف پول زنجیره نماینده کسر می‌شود.",[[Button(text='تأیید و فعال‌سازی',callback_data='agency:trial_save')],[Button(text='تغییر پنل',callback_data='agency:trial_panels')],[Button(text='انصراف',callback_data='agency:home')]])
                else:
                    if await state.get_state()!=AgencyFlow.trial_confirm.state:raise AgencyError('تنظیم تست آماده تأیید نیست؛ از ابتدا شروع کنید.')
                    data=await state.get_data()
                    await asyncio.to_thread(configure_panel_trials,root,data['trial_panel_id'],data['trial_volume'],data['trial_hours'],data['trial_cost'])
                    await state.clear()
                    await display(call,'✅ تنظیم تست نماینده‌ها ذخیره و فعال شد.',home_rows())
            elif action=='transactions':
                with root._get_conn() as conn:
                    if is_main_bot:
                        purchases=conn.execute('SELECT bot_id,local_order_id,root_order_id,status FROM managed_purchases ORDER BY root_order_id DESC LIMIT 20').fetchall()
                    else:
                        purchases=conn.execute('SELECT p.bot_id,p.local_order_id,p.root_order_id,p.status FROM managed_purchases p JOIN reseller_bots b ON b.id=p.bot_id WHERE b.id=? OR b.managed_parent_bot_id=? ORDER BY p.root_order_id DESC LIMIT 20',(parent_id,parent_id)).fetchall()
                labels={'reserved':'رزروشده','applying':'نیازمند بررسی نتیجه پنل','completed':'انجام‌شده','refunded':'برگشت هزینه'}
                lines=['۲۰ تراکنش اخیر تأمین:']
                for purchase in purchases:
                    lines.append(f"نماینده {purchase['bot_id']} · سفارش {purchase['local_order_id']} · تأمین {purchase['root_order_id']}: {labels.get(purchase['status'],purchase['status'])}")
                if not purchases:lines.append('تراکنشی ثبت نشده است.')
                lines.append('وضعیت نیازمند بررسی: پیش از هر اقدام دستی، نتیجه تمدید در پنل اصلی بررسی شود؛ اجرای دوباره خودکار مسدود است.')
                await display(call,'\n'.join(lines),[[Button(text='⬅️ بازگشت',callback_data='agency:home')]])
            elif action=='bots':
                bots=root.list_reseller_bots()
                bots=[b for b in bots if is_main_bot or dict(b).get('managed_parent_bot_id')==parent_id]
                rows=[[Button(text='@'+b['bot_username'],callback_data=f"agency:bot:{b['id']}")] for b in bots]
                rows.append([Button(text='⬅️ بازگشت',callback_data='agency:home')])
                await display(call,'نماینده را انتخاب کنید:',rows)
            elif action=='bot':
                bot_id=int(parts[2]); bot=own_bot(bot_id)
                rows=[[Button(text='📋 انتخاب محصولات مجاز',callback_data=f'agency:offers:{bot_id}')],
                      [Button(text='💵 تعیین نرخ خرید نماینده',callback_data=f'agency:grant:{bot_id}')],
                      [Button(text='⬅️ بازگشت',callback_data='agency:bots')]]
                await display(call,'مدیریت @'+bot['bot_username']+'\nنرخ خریدی که اینجا تعیین می‌کنید فقط برای همین نماینده است و نرخ سایر نماینده‌ها را تغییر نمی‌دهد.',rows)
            elif action in ('offers','products'):
                bot_id=int(parts[2]) if action=='offers' else parent_id
                own_bot(bot_id)
                if action=='products':
                    await state.clear()
                    await asyncio.to_thread(discover_products,root,bot_id)
                with root._get_conn() as conn:
                    offers={r['product_id']:dict(r) for r in conn.execute('SELECT * FROM managed_catalog WHERE bot_id=?',(bot_id,))}
                if action=='offers':
                    if is_main_bot:
                        
                        with root._get_conn() as conn:
                            candidates=[dict(p) for p in conn.execute('SELECT * FROM products WHERE is_auto_provision=1 AND provision_server_id IS NOT NULL')]
                    else:
                        with root._get_conn() as conn:
                            ids=[r[0] for r in conn.execute('SELECT product_id FROM managed_catalog WHERE bot_id=? AND allowed=1 AND selected=1',(parent_id,))]
                        candidates=[dict(root.get_product(pid)) for pid in ids if root.get_product(pid)]
                    candidates=[p for p in candidates if root.get_setting(f"volume_template:{p['id']}",'')!='1']
                    rows=[[Button(text=('مجاز | ' if offers.get(p['id'],{}).get('allowed') else 'غیرمجاز | ')+p['name'],callback_data=f"agency:allow:{bot_id}:{p['id']}")] for p in candidates]
                else:
                    rows=[]
                    for pid,offer in offers.items():
                        if root.get_setting(f'volume_template:{pid}','')=='1':continue
                        product=root.get_product(pid)
                        if not product or not offer['allowed']: continue
                        try:cost_label=f"{rate(root,bot_id,pid,require_selected=False):,} تومان"
                        except AgencyError:cost_label='ناموجود در بالادست'
                        rows.append([Button(text=('انتخاب‌شده | ' if offer['selected'] else 'جدید | ')+product['name']+f" | تأمین: {cost_label}",callback_data=f'agency:select:{pid}')])
                    with local._get_conn() as conn:
                        products=conn.execute('SELECT id,name,price,is_active FROM products WHERE upstream_product_id IS NOT NULL').fetchall()
                    products=[p for p in products if local.get_setting(f"volume_template:{p['id']}",'')!='1']
                    from product_emoji import get as product_emoji
                    rows += [[Button(text=f"ویرایش محصول: {product_emoji(local,p).get('label',p['name'])} | {p['price']:,} تومان | {'فعال' if p['is_active'] else 'غیرفعال'}",callback_data=f"agency:edit:{p['id']}", icon_custom_emoji_id=product_emoji(local,p).get('icon'))] for p in products]
                prefix=f'agency:offers:{bot_id}' if action=='offers' else 'agency:products'
                requested=int(parts[3]) if action=='offers' and len(parts)>3 else (int(parts[2]) if action=='products' and len(parts)>2 else 0)
                total=len(rows)
                page=max(0,min(requested,max(0,(total-1)//40)))
                rows=rows[page*40:(page+1)*40]
                navigation=[]
                if page:navigation.append(Button(text='قبلی',callback_data=prefix+f':{page-1}'))
                if (page+1)*40<total:navigation.append(Button(text='بعدی',callback_data=prefix+f':{page+1}'))
                if navigation:rows.append(navigation)
                if action=='products':rows.append([Button(text='🔄 دریافت لیست جدید محصولات',callback_data='agency:products')])
                rows.append([Button(text='⬅️ بازگشت',callback_data='agency:home')])
                await display(call,'محصولات مجاز را انتخاب کنید؛ تغییر مشخصات فنی فقط از ربات اصلی انجام می‌شود.',rows)
            elif action=='allow':
                bot_id,pid=map(int,parts[2:4]);own_bot(bot_id)
                with root._get_conn() as conn:
                    offer=conn.execute('SELECT * FROM managed_catalog WHERE bot_id=? AND product_id=?',(bot_id,pid)).fetchone()
                if not root.get_reseller_bot(bot_id): raise AgencyError('نماینده یافت نشد.')
                # Initialize the managed marker before the first synchronization.
                from database import Database
                child=Database(root.get_reseller_bot(bot_id)['db_path'])
                marked=child.get_setting('managed_reseller_bot_id','');child.close()
                if not marked: await asyncio.to_thread(bootstrap,root,bot_id,[])
                await asyncio.to_thread(configure,root,bot_id,pid,allowed=not bool(offer and offer['allowed']),selected=True,actor_bot_id=parent_id)
                await display(call,'✅ دسترسی محصول تغییر کرد.',[[Button(text='ادامه انتخاب',callback_data=f'agency:offers:{bot_id}')]])
            elif action=='select':
                pid=int(parts[2])
                with root._get_conn() as conn:
                    offer=conn.execute('SELECT selected FROM managed_catalog WHERE bot_id=? AND product_id=?',(parent_id,pid)).fetchone()
                if not offer: raise AgencyError('محصول یافت نشد.')
                if not offer['selected']:
                    cost=rate(root,parent_id,pid,require_selected=False)
                    await state.set_state(AgencyFlow.select_price);await state.update_data(agency_source_product=pid)
                    await display(call,f"قیمت فروش «{root.get_product(pid)['name']}» را به تومان ارسال کنید.\nنرخ تأمین فعلی: {cost:,} تومان",[[Button(text='انصراف',callback_data='agency:products')]])
                    return await call.answer()
                await asyncio.to_thread(select_product,root,parent_id,pid,False)
                await display(call,'✅ انتخاب ذخیره و محصولات بروزرسانی شد.',[[Button(text='ادامه',callback_data='agency:products')]])
            elif action in ('edit','toggle'):
                if not parent_id: raise AgencyError('این بخش برای فروشگاه نماینده است.')
                product=local.get_product(int(parts[2]))
                if not product or not product['upstream_product_id']: raise AgencyError('محصول یافت نشد.')
                notice=''
                if action=='toggle':
                    enabled=await asyncio.to_thread(local.toggle_product,product['id'])
                    notice='✅ محصول اکنون فعال شد.\n\n' if enabled else '✅ محصول اکنون غیرفعال شد.\n\n'
                    product=local.get_product(product['id'])
                status='فعال' if product['is_active'] else 'غیرفعال'
                rows=[[Button(text='تغییر نام محصول',callback_data=f"agency:name:{product['id']}")],
                      [Button(text='قیمت فروش',callback_data=f"agency:price:{product['id']}")],
                      [Button(text='توضیحات',callback_data=f"agency:description:{product['id']}")],
                      [Button(text='غیرفعال کن' if product['managed_local_enabled'] else 'فعال کن',callback_data=f"agency:toggle:{product['id']}")],
                      [Button(text='⬅️ محصولات',callback_data='agency:products')]]
                from product_emoji import get as product_emoji
                product_name=product_emoji(local,product).get('html',html.escape(product['name']))
                await display(call,notice+f"ویرایش محصول: {product_name}\nوضعیت فعلی: {status}\nنرخ تأمین: {product['managed_buy_price']} تومان\nقیمت فروش: {product['price']} تومان",rows)
            elif action in ('price','description','name'):
                await state.set_state({'price':AgencyFlow.price,'description':AgencyFlow.description,'name':AgencyFlow.name}[action])
                await state.update_data(agency_product=int(parts[2]))
                await display(call,{'price':'قیمت فروش جدید (تومان) را بفرستید؛ کمتر از نرخ تأمین مجاز نیست.','description':'توضیحات جدید را بفرستید.','name':'نام جدید محصول را بفرستید.'}[action],[[Button(text='انصراف',callback_data='agency:home')]])
            elif action in ('grant','wholesale'):
                if action=='wholesale' and not is_main_bot: raise AgencyError('دسترسی مجاز نیست.')
                if action=='grant': own_bot(int(parts[2]))
                await state.set_state(AgencyFlow.grant)
                await state.update_data(agency_target=int(parts[2]) if action=='grant' else 0)
                await display(call,('این نرخ فقط برای نماینده انتخاب‌شده اعمال می‌شود.\n' if action=='grant' else 'این نرخ پایه برای همه نماینده‌هاست.\n')+'شناسه محصول و نرخ تأمین به تومان را با فاصله بفرستید؛ مثلاً: 12 150000',[[Button(text='انصراف',callback_data='agency:home')]])
            elif action=='discount':
                await state.set_state(AgencyFlow.discount)
                await display(call,'شناسه کاربر و درصد تخفیف را بفرستید؛ مثلاً: 12345678 10',[[Button(text='انصراف',callback_data='agency:home')]])
            elif action=='add':
                await state.set_state(AgencyFlow.token)
                await display(call,'توکن ربات نماینده جدید را بفرستید. مالک باید قبلاً این ربات را /start کرده باشد.',[[Button(text='انصراف',callback_data='agency:home')]])
            await call.answer()
        except (AgencyError,ValueError,IndexError,TypeError) as exc:
            await call.answer(str(exc),show_alert=True)

    @router.message(StateFilter(AgencyFlow.trials,AgencyFlow.trial_hours,AgencyFlow.trial_cost))
    async def trial_settings_message(message,state):
        if not is_main_bot or not authorized(message.from_user.id) or not root.is_senior_admin(message.from_user.id):return
        try:
            value=int((message.text or '').strip())
            current=await state.get_state()
            cancel=Markup(inline_keyboard=[[Button(text='انصراف',callback_data='agency:home')]])
            if current==AgencyFlow.trials.state:
                if not 1<=value<=1048576:raise AgencyError('حجم را به مگابایت، عددی مثبت تا 1048576 بفرستید.')
                await state.update_data(trial_volume=value)
                await state.set_state(AgencyFlow.trial_hours)
                await message.answer('مدت اعتبار تست چند ساعت باشد؟ مثلاً 24.',reply_markup=cancel)
            elif current==AgencyFlow.trial_hours.state:
                if not 1<=value<=8760:raise AgencyError('مدت را به ساعت، عددی مثبت تا 8760 بفرستید.')
                await state.update_data(trial_hours=value)
                await state.set_state(AgencyFlow.trial_cost)
                await message.answer('بابت هر تست چند تومان از موجودی نماینده کم شود؟ برای تست رایگان، 0 بفرستید.',reply_markup=cancel)
            else:
                if value<0:raise AgencyError('هزینه باید صفر یا بیشتر باشد.')
                await state.update_data(trial_cost=value)
                await state.set_state(AgencyFlow.trial_panel)
                servers=await asyncio.to_thread(root.get_panel_servers,True)
                servers=[s for s in servers if not dict(s).get('managed_source_server_id')]
                rows=[[Button(text=s['name'],callback_data=f"agency:trial_panel:{s['id']}")] for s in servers[:30]]
                if len(servers)>30:rows.append([Button(text='بعدی',callback_data='agency:trial_panels:1')])
                rows.append([Button(text='انصراف',callback_data='agency:home')])
                await message.answer('سرویس تست روی کدام پنل ساخته شود؟' if servers else 'پنل فعالی ثبت نشده است. ابتدا پنل را در ربات اصلی اضافه کنید.',reply_markup=Markup(inline_keyboard=rows))
        except ValueError as exc:
            await message.answer(str(exc) if isinstance(exc,AgencyError) else 'لطفاً فقط یک عدد بفرستید.')

    @router.message(AgencyFlow.select_price)
    async def select_price_message(message,state):
        if not authorized(message.from_user.id) or not parent_id:return
        try:
            value=int((message.text or '').strip())
            pid=(await state.get_data())['agency_source_product']
            minimum=rate(root,parent_id,pid,require_selected=False)
            if value<minimum:raise AgencyError(f'قیمت فروش حداقل {minimum:,} تومان باشد.')
            # Create an unselected mirror first; it cannot be sold before pricing succeeds.
            await asyncio.to_thread(sync_bot,root,parent_id)
            with local._get_conn() as conn:
                row=conn.execute('SELECT id FROM products WHERE upstream_product_id=?',(pid,)).fetchone()
            if not row:raise AgencyError('محصول مجاز نیست یا حذف شده است.')
            await asyncio.to_thread(local.edit_product,row['id'],price=value)
            await asyncio.to_thread(select_product,root,parent_id,pid,True)
            await state.clear()
            await message.answer('✅ محصول با قیمت فروش انتخاب‌شده اضافه شد.',reply_markup=Markup(inline_keyboard=home_rows()))
        except ValueError as exc:await message.answer(str(exc))

    @router.message(AgencyFlow.price)
    async def price_message(message,state):
        if not authorized(message.from_user.id):return
        try:
            price=int((message.text or '').strip())
            if price<0:raise AgencyError('قیمت نامعتبر است.')
            product_id=(await state.get_data())['agency_product']
            await asyncio.to_thread(local.edit_product,product_id,price=price)
            await state.clear()
            await message.answer('✅ قیمت فروش ذخیره شد.',reply_markup=Markup(inline_keyboard=home_rows()))
        except ValueError as exc:await message.answer(str(exc))

    @router.message(AgencyFlow.name)
    async def name_message(message,state):
        if not authorized(message.from_user.id) or not parent_id:return
        name=(message.text or '').strip()
        if not name or len(name)>120:return await message.answer('نام محصول باید بین ۱ تا ۱۲۰ کاراکتر باشد.')
        try:
            product_id=(await state.get_data())['agency_product']
            await asyncio.to_thread(local.edit_product,product_id,name=name)
            from product_emoji import save as save_product_emoji
            await asyncio.to_thread(save_product_emoji,local,product_id,message,name)
            await state.clear()
            await message.answer('✅ نام محصول تغییر کرد.',reply_markup=Markup(inline_keyboard=home_rows()))
        except ValueError as exc:await message.answer(str(exc))

    @router.message(AgencyFlow.description)
    async def description_message(message,state):
        if not authorized(message.from_user.id):return
        text=(message.text or '').strip()
        if len(text)>2000:return await message.answer('توضیحات حداکثر ۲۰۰۰ کاراکتر است.')
        product_id=(await state.get_data())['agency_product']
        await asyncio.to_thread(local.edit_product,product_id,description=text)
        await state.clear()
        await message.answer('✅ توضیحات ذخیره شد.',reply_markup=Markup(inline_keyboard=home_rows()))

    @router.message(AgencyFlow.grant)
    async def grant_message(message,state):
        if not authorized(message.from_user.id):return
        try:
            pid,price=map(int,(message.text or '').split())
            target=(await state.get_data())['agency_target']
            if price<0:raise AgencyError('نرخ نامعتبر است.')
            if target:
                own_bot(target)
                await asyncio.to_thread(configure,root,target,pid,buy_price=price,actor_bot_id=parent_id)
            else:
                if not is_main_bot or not root.get_product(pid):raise AgencyError('محصول یافت نشد.')
                with root._get_conn() as conn:conn.execute('UPDATE products SET reseller_wholesale_price=? WHERE id=?',(price,pid))
            await state.clear()
            await message.answer('✅ نرخ ذخیره شد؛ در خریدهای بعدی اعمال می‌شود.',reply_markup=Markup(inline_keyboard=home_rows()))
        except ValueError as exc:await message.answer(str(exc))

    @router.message(AgencyFlow.discount)
    async def discount_message(message,state):
        if not authorized(message.from_user.id):return
        try:
            owner,percent=map(int,(message.text or '').split())
            await asyncio.to_thread(assign_discount,local,owner,percent)
            await state.clear()
            await message.answer('✅ نمایندگی تخفیف درصدی در همین ربات فعال شد.',reply_markup=Markup(inline_keyboard=home_rows()))
        except ValueError as exc:await message.answer(str(exc))

    @router.message(AgencyFlow.token)
    async def token_message(message,state):
        if not authorized(message.from_user.id):return
        token=(message.text or '').strip()
        try:
            async with Bot(token) as bot: me=await bot.get_me()
            if root.get_reseller_bot_by_token(token):raise AgencyError('این ربات قبلاً ثبت شده است.')
            await state.update_data(agency_token=token,agency_username=me.username)
            await state.set_state(AgencyFlow.owner)
            await message.answer('شناسه عددی مالک ربات را بفرستید؛ برای تایید مالکیت به خودش پیام داده می‌شود.')
        except Exception:await message.answer('توکن معتبر نیست یا ارتباط با تلگرام برقرار نشد.')

    @router.message(AgencyFlow.owner)
    async def owner_message(message,state):
        if not authorized(message.from_user.id):return
        try:
            owner=int((message.text or '').strip())
            if not local.get_user(owner):raise AgencyError('مالک باید ابتدا همین ربات را /start کند.')
            data=await state.get_data()
            with root._get_conn() as conn:
                invite=conn.execute('INSERT INTO managed_invites(parent_bot_id,owner_id,bot_token,bot_username) VALUES(?,?,?,?)',(parent_id,owner,data['agency_token'],data['agency_username'])).lastrowid
            await message.bot.send_message(owner,'ثبت ربات نمایندگی به نام شما پیشنهاد شده است. آیا تایید می‌کنید؟',reply_markup=Markup(inline_keyboard=[[Button(text='تایید مالکیت',callback_data=f'agencyjoin:{invite}')]]))
            await state.clear()
            await message.answer('درخواست تایید برای مالک ارسال شد.',reply_markup=Markup(inline_keyboard=home_rows()))
        except (ValueError,KeyError) as exc:await message.answer(str(exc))

    @router.callback_query(F.data.startswith('agencyjoin:'))
    async def accept_join(call):
        invite_id=int(call.data.split(':')[1])
        with root._get_conn() as conn:
            invite=conn.execute('SELECT * FROM managed_invites WHERE id=?',(invite_id,)).fetchone()
        if not invite or invite['owner_id']!=call.from_user.id or invite['completed']:
            return await call.answer('درخواست معتبر نیست.',show_alert=True)
        if invite['parent_bot_id'] != parent_id:
            return await call.answer('این دعوت مربوط به این فروشگاه نیست.',show_alert=True)
        if parent_id and len(chain(root,parent_id))>=8:
            return await call.answer('حداکثر عمق نمایندگی رسیده است.',show_alert=True)
        path=os.path.join(RESELLER_DBS_DIR,invite['bot_username']+'.db')
        if os.path.exists(path):return await call.answer('مسیر این ربات موجود است؛ مدیر باید آن را بررسی کند.',show_alert=True)
        os.makedirs(RESELLER_DBS_DIR,exist_ok=True)
        bot_id=root.register_reseller_bot(invite['bot_token'],invite['bot_username'],invite['owner_id'],str(invite['owner_id']),path)
        await asyncio.to_thread(bootstrap,root,bot_id,parent_id=parent_id)
        with root._get_conn() as conn:conn.execute('UPDATE managed_invites SET completed=1 WHERE id=?',(invite_id,))
        await call.message.edit_text('✅ نمایندگی ثبت شد. همه محصولات مجاز و دسته‌های مربوط اضافه و فعال شدند. با /agency می‌توانید محصولات را فعال یا غیرفعال کنید. هزینه هر فروش از کیف پول زنجیره کسر می‌شود.')
        await call.answer()
    return router
