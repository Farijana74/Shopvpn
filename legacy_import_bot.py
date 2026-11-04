"""Legacy database replacement from the main bot's owner backup menu."""
import asyncio
import html
import os
from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.state import State,StatesGroup
from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton,FSInputFile
import legacy_import_jobs as jobs

class LegacyImportFlow(StatesGroup):
    file=State()
    confirm=State()

def summary(value):
    old=value.get('source_counts') or {};current=value.get('current_counts') or {}
    return ('📦 پیش‌نمایش مهاجرت از ربات قدیمی\n'
            f"اطلاعات قدیمی: {old.get('users',0)} کاربر، {old.get('orders',0)} سفارش، {old.get('custom_configs',0)} سرویس پنلی\n"
            f"اطلاعات فعلی که جایگزین می‌شوند: {current.get('users',0)} کاربر، {current.get('orders',0)} سفارش، {current.get('custom_configs',0)} سرویس پنلی\n\n"
            'اطلاعات فعلی حذف و با دیتابیس قدیمی جایگزین می‌شود. قبل از اجرا بکاپ تازه گرفته می‌شود. هنگام اجرا بات و پنل وب موقتاً قطع می‌شوند. حساب ورود مالک پنل فعلی حفظ می‌شود؛ پس از انتقال دوباره وارد پنل شوید.')

def controls(job_id,ready=False):
    rows=[]
    if ready:rows.append([InlineKeyboardButton(text='ادامه و تأیید نهایی',callback_data='legacyui:confirm:'+job_id)])
    rows.append([InlineKeyboardButton(text='وضعیت مهاجرت',callback_data='legacyui:status:'+job_id)])
    if ready:rows.append([InlineKeyboardButton(text='انصراف',callback_data='legacyui:cancel:'+job_id)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def register(router,db,is_main_bot):
    def allowed(uid):return is_main_bot and db.is_owner(uid)
    def principal(uid):return 'bot:'+str(uid)

    @router.message(StateFilter(LegacyImportFlow),F.text.in_({'انصراف','❌ انصراف','/cancel'}))
    async def cancel_text(message,state):
        if not allowed(message.from_user.id):return
        data=await state.get_data()
        if data.get('legacy_job'):
            try:await asyncio.to_thread(jobs.cancel_job,data['legacy_job'],principal(message.from_user.id))
            except jobs.MigrationError:pass
        await state.clear()
        await message.answer('مهاجرت لغو شد؛ دیتابیس فعلی تغییر نکرد.')

    @router.callback_query(F.data.startswith('legacyui:'))
    async def legacy_callback(call,state):
        if not allowed(call.from_user.id):return await call.answer('فقط مالک بات اصلی دسترسی دارد.',show_alert=True)
        parts=call.data.split(':');action=parts[1]
        try:
            if action=='start':
                await state.clear();await state.set_state(LegacyImportFlow.file)
                await call.message.answer('فایل بکاپ ربات قدیمی (.db، .sqlite یا .zip) را به صورت فایل بفرستید، یا دیتابیس v2ray_bot همین سرور را انتخاب کنید.\nبا تأیید نهایی، تمام اطلاعات فعلی جایگزین می‌شود.',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='آپلود فایل بکاپ ربات قدیمی',callback_data='legacyui:upload')],[InlineKeyboardButton(text='خواندن ربات قدیمی همین سرور',callback_data='legacyui:local')],[InlineKeyboardButton(text='بازگشت',callback_data='adm_backup_menu')]]))
            elif action=='upload':
                await state.clear();await state.set_state(LegacyImportFlow.file)
                await call.message.answer('فایل بکاپ ربات قدیمی (.db، .sqlite یا .zip) را به صورت فایل ارسال کنید. ابتدا پیش‌نمایش نمایش داده می‌شود؛ جایگزینی فقط پس از تأیید REPLACE انجام می‌شود.')
            elif action=='local':
                await call.answer('در حال بررسی؛ فعلاً تغییری اعمال نمی‌شود.')
                value=await asyncio.to_thread(jobs.new_job,principal(call.from_user.id),True)
                result=await asyncio.to_thread(jobs.prepare_job,value['job_id'],principal(call.from_user.id))
                if result['status']!='ready':return await call.message.answer(html.escape(result.get('error','بررسی ناموفق بود.')))
                await state.clear();await call.message.answer(summary(result),reply_markup=controls(value['job_id'],True));return
            elif action=='confirm':
                value=await asyncio.to_thread(jobs.load,parts[2],principal(call.from_user.id))
                if value['status']!='ready':raise jobs.MigrationError('درخواست آماده تأیید نیست.')
                await state.update_data(legacy_job=parts[2]);await state.set_state(LegacyImportFlow.confirm)
                await call.message.answer('برای تأیید حذف اطلاعات فعلی و جایگزینی دیتابیس قدیمی، فقط REPLACE را ارسال کنید. برای لغو «انصراف» بفرستید.')
            elif action=='cancel':
                await asyncio.to_thread(jobs.cancel_job,parts[2],principal(call.from_user.id));await state.clear()
                await call.message.answer('مهاجرت لغو شد؛ دیتابیس فعلی تغییر نکرد.')
            elif action=='status':
                value=await asyncio.to_thread(jobs.load,parts[2],principal(call.from_user.id))
                labels={'ready':'آماده تأیید','queued':'در صف اجرا','running':'در حال اجرا','completed':'تکمیل شد','failed':'ناموفق','cancelled':'لغو شد'}
                text='وضعیت مهاجرت: '+labels.get(value['status'],value['status'])
                if value.get('error'):text+='\n'+html.escape(value['error'])
                rows=controls(parts[2],value['status']=='ready').inline_keyboard
                if value.get('backup_available'):rows.append([InlineKeyboardButton(text='دریافت بکاپ قبل از مهاجرت',callback_data='legacyui:backup:'+parts[2])])
                await call.message.answer(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            elif action=='backup':
                await asyncio.to_thread(jobs.load,parts[2],principal(call.from_user.id))
                path=jobs.folder(parts[2])/'execution'/'before.db'
                if not path.is_file():raise jobs.MigrationError('بکاپ هنوز آماده نشده است.')
                try:await call.message.answer_document(FSInputFile(path,filename='before-legacy-import.db'))
                except Exception:await call.message.answer('ارسال بکاپ در تلگرام ممکن نشد؛ نسخه پشتیبان روی سرور محفوظ است. حجم فایل و محدودیت ارسال تلگرام را بررسی کنید.')
            await call.answer()
        except (jobs.MigrationError,IndexError) as exc:await call.answer(str(exc),show_alert=True)

    @router.message(LegacyImportFlow.file,F.document)
    async def legacy_upload(message,state):
        if not allowed(message.from_user.id):return
        doc=message.document
        if not (doc.file_name or '').lower().endswith(jobs.UPLOAD_SUFFIXES):return await message.answer('فایل بکاپ .db، .sqlite یا .zip ارسال کنید.')
        if doc.file_size and doc.file_size>jobs.MAX_UPLOAD:return await message.answer('حجم فایل زیاد است؛ از پنل وب استفاده کنید.')
        value=await asyncio.to_thread(jobs.new_job,principal(message.from_user.id));path=jobs.folder(value['job_id'])/'source.db'
        try:
            path.touch(mode=0o600)
            remote=await message.bot.get_file(doc.file_id)
            await message.bot.download_file(remote.file_path,destination=str(path))
            if path.stat().st_size>jobs.MAX_UPLOAD:raise jobs.MigrationError('حجم فایل بیشتر از حد مجاز است.')
            await message.answer('در حال بررسی فایل؛ دیتابیس فعلی تغییر نمی‌کند.')
            await asyncio.to_thread(jobs.unpack_backup,value['job_id'],principal(message.from_user.id))
            result=await asyncio.to_thread(jobs.prepare_job,value['job_id'],principal(message.from_user.id))
            if result['status']!='ready':return await message.answer(html.escape(result.get('error','فایل قابل تبدیل نیست.')))
            await state.clear();await message.answer(summary(result),reply_markup=controls(value['job_id'],True))
        except Exception as exc:
            await message.answer(html.escape(str(exc)) if isinstance(exc,jobs.MigrationError) else 'دریافت فایل ممکن نشد؛ دوباره امتحان کنید یا از پنل وب استفاده کنید.')

    @router.message(LegacyImportFlow.confirm)
    async def legacy_confirm(message,state):
        if not allowed(message.from_user.id):return
        if (message.text or '').strip()!='REPLACE':return await message.answer('برای تأیید، REPLACE و برای لغو، انصراف را بفرستید.')
        data=await state.get_data()
        try:
            result=await asyncio.to_thread(jobs.queue_job,data.get('legacy_job'),principal(message.from_user.id),'REPLACE')
            await state.clear()
            await message.answer('مهاجرت در صف اجرا قرار گرفت. بات و پنل موقتاً قطع می‌شوند و نتیجه برای مالک ارسال می‌شود.',reply_markup=controls(result['job_id']))
        except jobs.MigrationError as exc:await message.answer(html.escape(str(exc)))
