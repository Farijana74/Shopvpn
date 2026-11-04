"""Expose the main shop's existing custom-volume plans to managed shops."""
import json
import uuid
from managed_resellers import AgencyError,chain


def plans(root):
    result=[]
    configured=root.get_custom_config_products(True)
    if configured:
        for row in configured:
            plan=dict(row);server=root.get_panel_server(plan['panel_server_id'])
            if server and server['is_active'] and not dict(server).get('managed_source_server_id'):
                result.append(plan)
    elif root.get_custom_config_settings()['enabled']:
        settings=root.get_custom_config_settings()
        server=root.get_panel_server_for_usage('custom_config')
        if server and server['is_active'] and root.get_pricing_tiers():
            result.append(dict(id=0,name=root.get_setting('btn_custom_config_text','حجم دلخواه'),panel_server_id=server['id'],min_gb=settings['min_gb'],max_gb=settings['max_gb'],pricing_mode='tiered',description='',icon='🛠'))
    return result


def rules(local,root,bot_id,plan_id):
    source=next((p for p in plans(root) if p['id']==plan_id),None)
    if source is None:raise AgencyError('این پلن حجم دلخواه در ربات اصلی غیرفعال یا حذف شده است.')
    for member in chain(root,bot_id):
        if root.get_setting(f"volume_native_access:{member['id']}",'1')!='1':raise AgencyError('دسترسی حجم دلخواه نماینده توسط ربات اصلی غیرفعال است.')
    if local.get_setting('volume_local_enabled','1')!='1':raise AgencyError('حجم دلخواه این فروشگاه غیرفعال است.')
    minimum=max(1,int(source['min_gb']),int(local.get_setting('volume_min_gb',str(source['min_gb']))))
    maximum=int(source['max_gb'])
    if minimum>maximum:raise AgencyError('حداقل حجم فروشگاه از حداکثر این پلن بیشتر است.')
    return {**source,'min_gb':minimum,'max_gb':maximum}


def available(local,root,bot_id):
    for plan in plans(root):
        try:rules(local,root,bot_id,plan['id']);return True
        except AgencyError:continue
    return False


def source_unit(root,plan_id,gb):
    if gb<1:raise AgencyError('حجم باید عدد مثبت باشد.')
    total=root.calc_custom_config_product_price(plan_id,gb) if plan_id else root.calc_custom_config_price(gb)
    if total<=0 or total%gb:raise AgencyError('نرخ تأمین این حجم در ربات اصلی معتبر نیست.')
    return total//gb


def costs(root,bot_id,plan_id,gb):
    base=source_unit(root,plan_id,gb);members=chain(root,bot_id)
    first=root.get_setting(f"volume_supply_price:{members[0]['id']}:{plan_id}",'')
    if first:base=int(first)
    current=base;edges=[]
    for member in members:
        custom=root.get_setting(f"volume_supply_price:{member['id']}:{plan_id}",'')
        current=max(current,int(custom)) if custom else current
        edges.append((member['id'],current))
    return base,edges


def refresh(local,root,bot_id,template_id,plan_id,gb):
    source=rules(local,root,bot_id,plan_id)
    if not source['min_gb']<=gb<=source['max_gb']:raise AgencyError(f"حجم باید بین {source['min_gb']} تا {source['max_gb']} گیگ باشد.")
    saved=json.loads(root.get_setting(f'volume_native_source:{template_id}','{}'))
    if saved!={'bot_id':bot_id,'plan_id':plan_id}:raise AgencyError('مبدأ پیش‌فاکتور حجم دلخواه معتبر نیست.')
    base,edges=costs(root,bot_id,plan_id,gb)
    with root._get_conn() as conn:
        conn.execute('UPDATE products SET price=?,reseller_wholesale_price=?,provision_server_id=?,duration_days=30,is_active=1 WHERE id=?',(base,base,source['panel_server_id'],template_id))
        for edge,price in edges:
            conn.execute('UPDATE managed_catalog SET buy_price=? WHERE bot_id=? AND product_id=?',(price,edge,template_id))
    return source


def quote(local,root,bot_id,user_id,gb,plan_id,username=None):
    from managed_volume import configure,quote as backing_quote
    source=rules(local,root,bot_id,plan_id)
    if not source['min_gb']<=gb<=source['max_gb']:raise AgencyError(f"حجم باید بین {source['min_gb']} تا {source['max_gb']} گیگ باشد.")
    base,edges=costs(root,bot_id,plan_id,gb)
    # A private backing product gives each quote independent tier/rate revalidation.
    data=configure(root,bot_id,source['panel_server_id'],base,policy_key=f'volume_native_quote:{uuid.uuid4().hex}')
    root.set_setting(f"volume_native_source:{data['product_id']}",json.dumps({'bot_id':bot_id,'plan_id':plan_id}))
    refresh(local,root,bot_id,data['product_id'],plan_id,gb)
    local_id,total=backing_quote(local,root,bot_id,user_id,gb,data_override={**data,'min_gb':source['min_gb'],'max_gb':source['max_gb']},extras={'native_plan_id':plan_id,'username':username})
    with local._get_conn() as conn:
        conn.execute('UPDATE products SET name=?,description=? WHERE id=?',(f"{source['name']} | {gb} گیگ / ۳۰ روز",source.get('description',''),local_id))
    with root._get_conn() as conn:
        conn.execute('UPDATE products SET name=? WHERE id=?',(f"{source['name']} / نماینده #{bot_id}",data['product_id']))
    return local_id,total

async def begin(message,state,local,root):
    from aiogram.types import InlineKeyboardButton as B,InlineKeyboardMarkup as M
    from managed_volume import VolumeFlow
    from menu_restore import main_menu_markup
    bot_id=int(local.get_setting('managed_reseller_bot_id'))
    choices=[]
    for plan in plans(root):
        try:choices.append(rules(local,root,bot_id,plan['id']))
        except AgencyError:continue
    await state.clear()
    if not choices:return await message.answer('حجم دلخواه فعلاً در دسترس نیست.',reply_markup=main_menu_markup(local,message.from_user.id))
    if len(choices)==1:return await ask_username(message,state,choices[0]['id'])
    await state.set_state(VolumeFlow.plan)
    await message.answer('🛠 حجم دلخواه\nیکی از پلن‌های ربات اصلی را انتخاب کنید:',reply_markup=M(inline_keyboard=[[B(text=p['name'],callback_data=f"volume:native:{p['id']}")] for p in choices]+[[B(text='بازگشت',callback_data='acct:main_menu')]]))

async def ask_username(message,state,plan_id):
    from aiogram.types import InlineKeyboardButton as B,InlineKeyboardMarkup as M
    from managed_volume import VolumeFlow
    await state.update_data(native_plan_id=plan_id)
    await state.set_state(VolumeFlow.username)
    await message.answer('نام دلخواه کانفیگ را ارسال کنید؛ حروف انگلیسی، عدد و آندرلاین، بین ۳ تا ۲۰ کاراکتر. پیشوند فروشگاه خودکار اضافه می‌شود.',reply_markup=M(inline_keyboard=[[B(text='نام تصادفی',callback_data='volume:random')],[B(text='بازگشت',callback_data='acct:main_menu')]]))

async def ask_volume(message,state,local,root,username):
    import html
    from managed_volume import VolumeFlow
    plan_id=(await state.get_data())['native_plan_id']
    source=rules(local,root,int(local.get_setting('managed_reseller_bot_id')),plan_id)
    await state.update_data(native_username=username)
    await state.set_state(VolumeFlow.volume)
    selling=local.get_setting('volume_sell_per_gb','')
    note=f'نرخ فروش هر گیگ: {int(selling):,} تومان (حداقل برابر نرخ تأمین همان حجم)' if selling else 'قیمت‌گذاری بر اساس پلن حجم دلخواه ربات اصلی است؛ مبلغ نهایی بعد از انتخاب حجم نمایش داده می‌شود.'
    await message.answer(f"🛠 {html.escape(source['name'])}\nنام کانفیگ: {html.escape(username)}\nحجم را بین {source['min_gb']} تا {source['max_gb']} گیگ وارد کنید.\nمدت ثابت: ۳۰ روز\n{note}\nبرای لغو «انصراف» بفرستید.")
