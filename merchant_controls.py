"""Per-shop prices and editable customer menu controls."""
import asyncio
import json
import re
from aiogram import F
from aiogram.fsm.state import State,StatesGroup
from aiogram.types import InlineKeyboardButton as B,InlineKeyboardMarkup as M

class ControlsFlow(StatesGroup):
    value=State()

def personal_price(db,user_id,product_id):
    raw=db.get_setting(f'personal_price:{int(user_id)}:{int(product_id)}','')
    return int(raw) if raw else None

def set_personal_price(db,user_id,product_id,price):
    product=db.get_product(product_id)
    if not db.get_user(user_id) or not product:raise ValueError('کاربر یا محصول یافت نشد.')
    if price is not None:
        price=int(price)
        floor=int(dict(product).get('managed_buy_price') or 0)
        if price<max(1,floor):raise ValueError('قیمت باید مثبت و حداقل برابر نرخ تأمین محصول باشد.')
    db.set_setting(f'personal_price:{int(user_id)}:{int(product_id)}','' if price is None else str(price))

def register(router,db):
    def markup():
        return M(inline_keyboard=[[B(text='تنظیم حجم دلخواه نمایندگان / نرخ فروش',callback_data='volume:settings')],[B(text='قیمت اختصاصی کاربر و محصول',callback_data='merchant:price')],[B(text='پیشوند نام کاربری پنل',callback_data='merchant:username')],[B(text='پیشوند نام نمایشی کانفیگ',callback_data='merchant:display')],[B(text='تغییر متن منوی اصلی',callback_data='merchant:menu')],[B(text='بازگشت',callback_data='adm_back_panel')]])
    @router.callback_query(F.data.startswith('merchant:'))
    async def merchant_callback(call,state):
        if not db.is_senior_admin(call.from_user.id):return await call.answer('دسترسی ندارید.',show_alert=True)
        action=call.data.split(':')[1]
        if action=='home':
            await call.message.answer('تنظیمات فروشگاه؛ این تنظیمات مخصوص همین ربات است.',reply_markup=markup())
        else:
            prompts={'price':'شناسه عددی کاربر، شناسه محصول و قیمت تومان را با فاصله ارسال کنید. مثال: 123456 7 150000\nبرای حذف قیمت اختصاصی: 123456 7 حذف','username':'پیشوند نام کاربری پنل را با حروف انگلیسی، عدد یا آندرلاین بفرستید؛ برای پاک کردن «حذف» بفرستید.','display':'پیشوند نام نمایشی کانفیگ را ارسال کنید؛ برای پاک کردن «حذف» بفرستید.','menu':'متن دلخواه به جای «📋 منو:» را ارسال کنید.'}
            if action not in prompts:return await call.answer()
            await state.set_state(ControlsFlow.value);await state.update_data(merchant_action=action)
            if action=='price':
                products=db.get_all_products()
                text='\n'.join(f"{p['id']}: {p['name']} | {p['price']:,} تومان" for p in products)
                await call.message.answer((prompts[action]+'\n\n'+text)[:4000])
            else:await call.message.answer(prompts[action])
        await call.answer()
    @router.message(ControlsFlow.value)
    async def merchant_value(message,state):
        if not db.is_senior_admin(message.from_user.id):return
        text=(message.text or '').strip();action=(await state.get_data()).get('merchant_action')
        if text in ('انصراف','/cancel'):
            await state.clear();return await message.answer('لغو شد.',reply_markup=markup())
        try:
            if action=='price':
                parts=text.split()
                if len(parts)!=3:raise ValueError('شناسه کاربر، شناسه محصول و قیمت را با فاصله بفرستید.')
                await asyncio.to_thread(set_personal_price,db,int(parts[0]),int(parts[1]),None if parts[2]=='حذف' else int(parts[2]))
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
    if db.get_setting(f'volume_template:{int(product_id)}','')!='1':return True
    quote=json.loads(db.get_setting(f'volume_quote:{int(product_id)}','{}'))
    return quote.get('user_id')==user_id


def priced_products(db,products,user_id):
    result=[]
    for product in products:
        product=dict(product)
        override=personal_price(db,user_id,product['id'])
        if override is not None:
            product['price']=max(override,int(product.get('managed_buy_price') or 0))
        result.append(product)
    return result
