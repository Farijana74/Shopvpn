"""Permission-checked bot UI for root catalogs and nested gold resellers."""
import asyncio
import os
from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from managed_resellers import AgencyError, bootstrap, configure, select_product, sync_bot, rate, chain, discover_products, assign_discount


class AgencyFlow(StatesGroup):
    price=State()
    description=State()
    grant=State()
    discount=State()
    token=State()
    owner=State()


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
                    rows=[[Button(text=('✅ ' if offers.get(p['id'],{}).get('allowed') else '☑️ ')+p['name'],callback_data=f"agency:allow:{bot_id}:{p['id']}")] for p in candidates]
                else:
                    rows=[]
                    for pid,offer in offers.items():
                        product=root.get_product(pid)
                        if not product or not offer['allowed']: continue
                        rows.append([Button(text=('✅ ' if offer['selected'] else '☑️ ')+product['name'],callback_data=f'agency:select:{pid}')])
                    with local._get_conn() as conn:
                        products=conn.execute('SELECT id,name,price,is_active FROM products WHERE upstream_product_id IS NOT NULL').fetchall()
                    rows += [[Button(text=f"ویرایش محصول: {p['name']} | {p['price']:,} تومان | {'فعال' if p['is_active'] else 'غیرفعال'}",callback_data=f"agency:edit:{p['id']}")] for p in products]
                prefix=f'agency:offers:{bot_id}' if action=='offers' else 'agency:products'
                requested=int(parts[3]) if action=='offers' and len(parts)>3 else (int(parts[2]) if action=='products' and len(parts)>2 else 0)
                total=len(rows)
                page=max(0,min(requested,max(0,(total-1)//40)))
                rows=rows[page*40:(page+1)*40]
                navigation=[]
                if page:navigation.append(Button(text='قبلی',callback_data=prefix+f':{page-1}'))
                if (page+1)*40<total:navigation.append(Button(text='بعدی',callback_data=prefix+f':{page+1}'))
                if navigation:rows.append(navigation)
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
                await asyncio.to_thread(select_product,root,parent_id,pid,not bool(offer['selected']))
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
                rows=[[Button(text='قیمت فروش',callback_data=f"agency:price:{product['id']}")],
                      [Button(text='توضیحات',callback_data=f"agency:description:{product['id']}")],
                      [Button(text='غیرفعال کن' if product['managed_local_enabled'] else 'فعال کن',callback_data=f"agency:toggle:{product['id']}")],
                      [Button(text='⬅️ محصولات',callback_data='agency:products')]]
                await display(call,notice+f"ویرایش محصول: {product['name']}\nوضعیت فعلی: {status}\nنرخ تأمین: {product['managed_buy_price']} تومان\nقیمت فروش: {product['price']} تومان",rows)
            elif action in ('price','description'):
                await state.set_state(AgencyFlow.price if action=='price' else AgencyFlow.description)
                await state.update_data(agency_product=int(parts[2]))
                await display(call,'قیمت فروش جدید (تومان) را بفرستید؛ کمتر از نرخ تأمین مجاز نیست.' if action=='price' else 'توضیحات جدید را بفرستید.',[[Button(text='انصراف',callback_data='agency:home')]])
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
