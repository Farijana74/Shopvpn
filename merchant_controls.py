"""Per-shop prices and editable customer menu controls."""
import asyncio
import json
import re
from aiogram import F
from aiogram.fsm.state import State,StatesGroup
from aiogram.types import InlineKeyboardButton as B,InlineKeyboardMarkup as M

class ControlsFlow(StatesGroup):
    value=State()
    price_user=State()
    price_amount=State()

def personal_price(db,user_id,product_id):
    raw=db.get_setting(f'personal_price:{int(user_id)}:{int(product_id)}','')
    return int(raw) if raw else None

def supply_floor(db,product):
    floor=int(dict(product).get('managed_buy_price') or 0)
    if db.get_setting('managed_reseller_bot_id','') and dict(product).get('upstream_product_id'):
        from database import Database
        from managed_resellers import rate
        root=Database(db.get_setting('managed_root_db_path',''))
        try:floor=rate(root,int(db.get_setting('managed_reseller_bot_id')),product['upstream_product_id'],require_selected=False)
        finally:root.close()
    return floor

def set_personal_price(db,user_id,product_id,price):
    product=db.get_product(product_id)
    if not db.get_user(user_id) or not product:raise ValueError('کاربر یا محصول یافت نشد.')
    if price is not None:
        price=int(price)
        if price>2**63-1:raise ValueError('مبلغ واردشده بیش از حد مجاز است.')
        floor=supply_floor(db,product)
        if price<max(1,floor):raise ValueError(f'قیمت باید حداقل {max(1,floor):,} تومان و برابر یا بیشتر از نرخ تأمین فعلی باشد.')
    db.set_setting(f'personal_price:{int(user_id)}:{int(product_id)}','' if price is None else str(price))

def register(router,db):
    def markup():
        return M(inline_keyboard=[[B(text='تنظیم حجم دلخواه نمایندگان / نرخ فروش',callback_data='volume:settings')],[B(text='قیمت اختصاصی کاربر و محصول',callback_data='merchant:price')],[B(text='پیشوند نام کاربری پنل',callback_data='merchant:username')],[B(text='پیشوند نام نمایشی کانفیگ',callback_data='merchant:display')],[B(text='تغییر متن منوی اصلی',callback_data='merchant:menu')],[B(text='بازگشت',callback_data='adm_back_panel')]])
    def product_list(user_id,page=0):
        products=db.get_all_products();page=max(0,min(page,max(0,(len(products)-1)//12)))
        rows=[]
        for product in products[page*12:(page+1)*12]:
            custom=personal_price(db,user_id,product['id'])
            price=custom if custom is not None else product['price']
            rows.append([B(text=f"{product['name']} | {price:,} تومان",callback_data=f"merchant:pick:{user_id}:{product['id']}"),B(text="تغییر قیمت",callback_data=f"merchant:pick:{user_id}:{product['id']}")])
        nav=[]
        if page:nav.append(B(text='قبلی',callback_data=f'merchant:list:{user_id}:{page-1}'))
        if (page+1)*12<len(products):nav.append(B(text='بعدی',callback_data=f'merchant:list:{user_id}:{page+1}'))
        if nav:rows.append(nav)
        rows.append([B(text='انتخاب کاربر دیگر',callback_data='merchant:price')])
        rows.append([B(text='بازگشت',callback_data='merchant:home')])
        return M(inline_keyboard=rows)

    @router.callback_query(F.data.startswith('merchant:'))
    async def merchant_callback(call,state):
        if not db.is_senior_admin(call.from_user.id):return await call.answer('دسترسی ندارید.',show_alert=True)
        action=call.data.split(':')[1]
        if action=='home':
            await state.clear()
            await call.message.answer('تنظیمات فروشگاه؛ این تنظیمات مخصوص همین ربات است.',reply_markup=markup())
        elif action=='price':
            await state.clear();await state.set_state(ControlsFlow.price_user)
            await call.message.answer('آیدی عددی کاربر را بفرستید تا محصولات و قیمت‌های مخصوص همین کاربر نمایش داده شوند.',reply_markup=M(inline_keyboard=[[B(text='بازگشت',callback_data='merchant:home')]]))
        elif action in ('list','pick'):
            try:
                parts=call.data.split(':');user_id=int(parts[2]);data=await state.get_data()
                if data.get('merchant_user')!=user_id or not db.get_user(user_id):raise ValueError('ابتدا کاربر را از بخش قیمت اختصاصی انتخاب کنید.')
                if action=='list':
                    await state.set_state(ControlsFlow.price_user)
                    await call.message.answer(f'محصولات و قیمت‌های کاربر {user_id}:',reply_markup=await asyncio.to_thread(product_list,user_id,int(parts[3])))
                else:
                    pid=int(parts[3]);product=await asyncio.to_thread(db.get_product,pid)
                    if not product:raise ValueError('محصول یافت نشد.')
                    floor=await asyncio.to_thread(supply_floor,db,product)
                    current=personal_price(db,user_id,pid)
                    price=current if current is not None else product['price']
                    await state.update_data(merchant_product=pid)
                    await state.set_state(ControlsFlow.price_amount)
                    import html
                    await call.message.answer(f"تغییر قیمت «{html.escape(product['name'])}» برای کاربر {user_id}\nقیمت فعلی: {price:,} تومان\nحداقل مجاز: {max(1,floor):,} تومان\nقیمت جدید را به تومان بفرستید؛ برای برگشت به قیمت عمومی «حذف» را بفرستید.",reply_markup=M(inline_keyboard=[[B(text='فهرست محصولات کاربر',callback_data=f'merchant:list:{user_id}:0')]]))
            except (ValueError,IndexError) as exc:return await call.answer(str(exc),show_alert=True)
        else:
            prompts={'username':'پیشوند نام کاربری پنل را با حروف انگلیسی، عدد یا آندرلاین بفرستید؛ برای پاک کردن «حذف» بفرستید.','display':'پیشوند نام نمایشی کانفیگ را ارسال کنید؛ برای پاک کردن «حذف» بفرستید.','menu':'متن دلخواه به جای «📋 منو:» را ارسال کنید.'}
            if action not in prompts:return await call.answer()
            await state.set_state(ControlsFlow.value);await state.update_data(merchant_action=action)
            await call.message.answer(prompts[action])
        await call.answer()
    @router.message(ControlsFlow.price_user)
    async def price_user(message,state):
        if not db.is_senior_admin(message.from_user.id):return
        text=(message.text or '').strip()
        if text in ('انصراف','/cancel'):
            await state.clear();return await message.answer('لغو شد.',reply_markup=markup())
        if not text.isdecimal():return await message.answer('فقط آیدی عددی کاربر را بفرستید.')
        user_id=int(text)
        if not 0<user_id<2**63:return await message.answer('آیدی عددی کاربر معتبر نیست.')
        if not await asyncio.to_thread(db.get_user,user_id):return await message.answer('این کاربر در همین ربات پیدا نشد. آیدی را بررسی کنید.')
        await state.update_data(merchant_user=user_id)
        await message.answer(f'محصولات و قیمت‌های کاربر {user_id}:',reply_markup=await asyncio.to_thread(product_list,user_id))

    @router.message(ControlsFlow.price_amount)
    async def price_amount(message,state):
        if not db.is_senior_admin(message.from_user.id):return
        text=(message.text or '').strip();data=await state.get_data()
        if text in ('انصراف','/cancel'):
            await state.clear();return await message.answer('لغو شد.',reply_markup=markup())
        if text!='حذف' and not text.isdecimal():return await message.answer('قیمت را فقط به عدد و به تومان بفرستید، یا برای حذف نرخ اختصاصی «حذف» بنویسید.')
        try:
            user_id=data['merchant_user'];pid=data['merchant_product']
            await asyncio.to_thread(set_personal_price,db,user_id,pid,None if text=='حذف' else int(text))
            await state.set_state(ControlsFlow.price_user)
            await message.answer('✅ قیمت این کاربر به قیمت عمومی برگشت.' if text=='حذف' else '✅ قیمت اختصاصی این محصول برای کاربر ذخیره شد.',reply_markup=await asyncio.to_thread(product_list,user_id))
        except (ValueError,KeyError) as exc:await message.answer(str(exc))

    @router.message(ControlsFlow.value)
    async def merchant_value(message,state):
        if not db.is_senior_admin(message.from_user.id):return
        text=(message.text or '').strip();action=(await state.get_data()).get('merchant_action')
        if text in ('انصراف','/cancel'):
            await state.clear();return await message.answer('لغو شد.',reply_markup=markup())
        try:
            if action=='price':
                await state.set_state(ControlsFlow.price_user)
                return await price_user(message,state)
            elif action=='username':
                value='' if text=='حذف' else text
                if value and not re.fullmatch(r'[A-Za-z0-9_]{1,20}',value):raise ValueError('پیشوند باید حداکثر ۲۰ حرف انگلیسی، عدد یا آندرلاین باشد.')
                db.set_custom_config_prefix(value)
            elif action=='display':
                if len(text)>80:raise ValueError('پیشوند حداکثر ۸۰ کاراکتر باشد.')
                db.set_setting('shop_display_prefix','' if text=='حذف' else text)
            elif action=='menu':
                if not text or len(text)>2000:raise ValueError('متن باید بین ۱ تا ۲۰۰۰ کاراکتر باشد.')
                db.set_text('handlers_user.auto_f368dac9',text)
            await state.clear();await message.answer('✅ ذخیره شد.',reply_markup=markup())
        except ValueError as exc:await message.answer(str(exc))


def prefix_uri(uri,prefix):
    """Rename display metadata only; preserve transport and credentials."""
    import base64
    from urllib.parse import quote,unquote
    if not prefix or uri.startswith('happ://'):return uri
    if uri.startswith('vmess://'):
        try:
            value=uri[8:];obj=json.loads(base64.b64decode(value+'='*(-len(value)%4)))
            name=str(obj.get('ps') or '')
            if not name.startswith(prefix):obj['ps']=prefix+name
            return 'vmess://'+base64.b64encode(json.dumps(obj,ensure_ascii=False).encode()).decode()
        except (ValueError,TypeError):return uri
    if '://' not in uri:return uri
    base,sep,name=uri.partition('#');name=unquote(name)
    return base+'#'+quote(name if name.startswith(prefix) else prefix+name,safe='')


def purchase_allowed(db,product_id,user_id):
    with db._get_conn() as conn:
        hidden=conn.execute("SELECT value FROM settings WHERE key=?",(f'volume_template:{int(product_id)}',)).fetchone()
        if not hidden or hidden[0]!='1':return True
        saved=conn.execute("SELECT value FROM settings WHERE key=?",(f'volume_quote:{int(product_id)}',)).fetchone()
    return bool(saved) and json.loads(saved[0]).get('user_id')==user_id


def priced_products(db,products,user_id):
    result=[]
    for product in products:
        product=dict(product)
        override=personal_price(db,user_id,product['id'])
        if override is not None:
            product['price']=max(override,int(product.get('managed_buy_price') or 0))
        result.append(product)
    return result
