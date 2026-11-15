"""Single-message trial links with native code blocks, help and return."""
import asyncio
import html
import logging

from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import dedicated_sub
from config_delivery import build_qr_bytes, collect_service_info, strip_html, _delivery_flags, panel_individual_links
from delivery_copy import link_sections
from merchant_controls import prefix_uri


def _length(text):
    return len(strip_html(text).encode('utf-16-le')) // 2


async def send_trial(bot, db, user_id, links, *, plan_name='', amount='', username=''):
    if isinstance(links, str):
        links = [links]
    links = [link for link in links if link]
    if not links:
        raise ValueError('لینک کانفیگ تست موجود نیست.')
    prefix = db.get_setting('shop_display_prefix', '')
    visible_links = []
    sub_on, individual_on = _delivery_flags(db, is_test=True)
    for link in links:
        if link.startswith(('http://', 'https://')):
            extracted = []
            if individual_on:
                try:
                    panel_configs = await panel_individual_links(db, user_id, link)
                    svc = {'configs': panel_configs} if panel_configs else await collect_service_info(link, include_info=False)
                except Exception:
                    logging.getLogger(__name__).warning('Could not extract test configurations for user %s',user_id)
                    svc = {'configs': []}
                if svc['configs']:
                    extracted = [prefix_uri(c, prefix) for c in svc['configs']]
                    visible_links.extend(extracted)
                if extracted and not sub_on:
                    continue
            link = await asyncio.to_thread(dedicated_sub.dedicated_link_for_upstream, db, link)
        visible_links.append(prefix_uri(link, prefix))
    lines = ['🧪 <b>کانفیگ تست شما آماده است</b>']
    if plan_name:
        lines.append(f'📦 پلن: {html.escape(str(plan_name))}')
    if amount:
        lines.append(f'📊 مشخصات: {html.escape(str(amount))}')
    if username:
        lines.append(f'👤 نام کاربری: <code>{html.escape(str(username))}</code>')
    caption = '\n'.join(lines)
    configs = [link for link in visible_links if not link.startswith(('http://', 'https://', 'happ://'))]
    subscriptions = [link for link in visible_links if link.startswith(('http://', 'https://'))]
    happs = [link for link in visible_links if link.startswith('happ://')]
    qr_link = subscriptions[0] if subscriptions else visible_links[0]
    sections = link_sections(configs, subscriptions, happs, code_links=True)
    if sections:
        caption += '\n\n' + sections
    success = db.get_setting('test_success_message', '') or ''
    if success.strip():
        try:
            success = success.format(plan_name=plan_name, amount=amount, username=username)
        except (KeyError, ValueError):
            pass
        caption += '\n\n' + success
    back = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📖 پیام راهنما', callback_data='trial:help')],
        [InlineKeyboardButton(text='🏠 بازگشت به منو', callback_data='trial:main_menu')],
    ])
    buttons = back
    long_body = _length(caption) > 1024
    try:
        qr = await asyncio.to_thread(build_qr_bytes, qr_link, db)
        try:
            await bot.send_photo(user_id, BufferedInputFile(qr, filename='test-qr.png'),
                                 caption='🧪 کد QR لینک تست' if long_body else caption,
                                 parse_mode='HTML', reply_markup=None if long_body else buttons)
        except Exception:
            await bot.send_photo(user_id, BufferedInputFile(qr, filename='test-qr.png'),
                                 caption='🧪 کد QR لینک تست' if long_body else strip_html(caption),
                                 parse_mode=None, reply_markup=None if long_body else buttons)
    except Exception:
        logging.getLogger(__name__).warning('Could not send test QR for user %s; sending text instead', user_id, exc_info=True)
        if not long_body:
            await bot.send_message(user_id, strip_html(caption), parse_mode=None, reply_markup=buttons)
    if _length(caption) > 4096:
        await bot.send_document(user_id, BufferedInputFile(strip_html(caption).encode('utf-8'), filename='test-configs.txt'),
                                caption='🧪 همهٔ لینک‌های کانفیگ تست در فایل پیوست قرار دارند.', reply_markup=buttons)
    elif long_body:
        try:
            await bot.send_message(user_id, caption, parse_mode='HTML', reply_markup=buttons)
        except Exception:
            await bot.send_message(user_id, strip_html(caption), parse_mode=None, reply_markup=buttons)
