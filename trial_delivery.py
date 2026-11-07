"""Compact test configuration delivery with a single navigation button."""
import asyncio
import html
import logging

from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import dedicated_sub
from config_delivery import build_qr_bytes, collect_service_info, strip_html
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
    for link in links:
        if link.startswith(('http://', 'https://')):
            if db.get_setting('deliver_sub_link_enabled', '1') == '0' and db.get_setting('deliver_individual_configs_enabled', '1') != '0':
                svc = await collect_service_info(link)
                if svc['configs']:
                    visible_links.extend(prefix_uri(c, prefix) for c in svc['configs'])
                    continue
            link = await asyncio.to_thread(dedicated_sub.dedicated_link_for_upstream, db, link)
        visible_links.append(prefix_uri(link, prefix))
    qr_link = visible_links[0]
    lines = ['🧪 <b>کانفیگ تست شما آماده است</b>']
    if plan_name:
        lines.append(f'📦 پلن: {html.escape(str(plan_name))}')
    if amount:
        lines.append(f'📊 مشخصات: {html.escape(str(amount))}')
    if username:
        lines.append(f'👤 نام کاربری: <code>{html.escape(str(username))}</code>')
    caption = '\n'.join(lines)
    blocks = []
    for link in visible_links:
        title = '📲 لینک Happ' if link.startswith('happ://') else '🔗 لینک اتصال'
        blocks.append(f'{title}:\n<code>{html.escape(link)}</code>')
    success = db.get_setting('test_success_message', '') or ''
    if success.strip():
        try:
            success = success.format(plan_name=plan_name, amount=amount, username=username)
        except (KeyError, ValueError):
            pass
        blocks.append(success)
    extras = []
    for block in blocks:
        if not extras and _length(caption + '\n\n' + block) <= 1024:
            caption += '\n\n' + block
        else:
            extras.append(block)
    back = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📖 پیام راهنما', callback_data='trial:help')],
        [InlineKeyboardButton(text='🏠 بازگشت به منو', callback_data='trial:main_menu')],
    ])
    try:
        qr = await asyncio.to_thread(build_qr_bytes, qr_link, db)
        try:
            await bot.send_photo(user_id, BufferedInputFile(qr, filename='test-qr.png'), caption=caption,
                                 parse_mode='HTML', reply_markup=None if extras else back)
        except Exception:
            await bot.send_photo(user_id, BufferedInputFile(qr, filename='test-qr.png'), caption=strip_html(caption),
                                 parse_mode=None, reply_markup=None if extras else back)
    except Exception:
        logging.getLogger(__name__).warning('Could not send test QR for user %s; sending text instead', user_id, exc_info=True)
        await bot.send_message(user_id, strip_html(caption), parse_mode=None, reply_markup=None if extras else back)
    for index, block in enumerate(extras):
        markup = back if index == len(extras) - 1 else None
        if _length(block) <= 4096:
            try:
                await bot.send_message(user_id, block, parse_mode='HTML', reply_markup=markup)
            except Exception:
                await bot.send_message(user_id, strip_html(block), parse_mode=None, reply_markup=markup)
        else:
            # Telegram cannot place an oversized link in a single message.
            plain = strip_html(block)
            chunks = [plain[i:i + 1900] for i in range(0, len(plain), 1900)]
            for i, chunk in enumerate(chunks):
                await bot.send_message(user_id, chunk, parse_mode=None,
                                       reply_markup=markup if i == len(chunks) - 1 else None)
