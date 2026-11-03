"""Reuse the originating menu message for one navigation response per click."""
import asyncio
import contextvars
from dataclasses import dataclass

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage, EditMessageText
from aiogram.types import InlineKeyboardMarkup


@dataclass
class Navigation:
    chat_id: int
    message_id: int
    used: bool = False
    task: object = None


_navigation = contextvars.ContextVar('inline_navigation', default=None)
# Transactions, deliveries, broadcasts and user-authored messages keep their own messages.
_PREFIXES = ('mm:', 'acct:', 'cat:', 'prod:', 'mo_v:', 'mo_back', 'mo_refresh:',
             'tut', 'contact_', 'wallet_menu', 'wallet_back', 'tickets_mine',
             'ticket_view:', 'ticket_dept:', 'ticket_reply:', 'tickets_new',
             'reseller_menu', 'reseller_stats', 'reseller_fixed:', 'reseller_new_config', 'rt:', 'adm_cat:', 'adm_categories',
             'language:', 'wallet_history', 'svc_rename:', 'svc_transfer:', 'svc_users_pick:',
             'svc_renew_pick:', 'svc_tutorial', 'pay_crypto', 'pay_abangateway', 'pay_blupal',
             'pay_noapay', 'pay_customgw:', 'pay_zarinpal', 'pay_aqayepardakht', 'pay_tetra98',
             'pay_cubepay', 'pay_nowpayments', 'pay_tgstars',
             'adm_cat_rename:', 'gift_open_store', 'custom_config_start')


class InlineNavigationMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        message = getattr(event, 'message', None)
        callback = getattr(event, 'data', '') or ''
        if callback == 'mm:btn_referral':
            return await handler(event, data)
        if not message or not callback.startswith(_PREFIXES):
            return await handler(event, data)
        token = _navigation.set(Navigation(message.chat.id, message.message_id, task=asyncio.current_task()))
        try:
            return await handler(event, data)
        finally:
            _navigation.reset(token)


async def send_navigation(method, send):
    context = _navigation.get()
    if context is None or context.task is not asyncio.current_task() or str(getattr(method, 'chat_id', '')) != str(context.chat_id):
        return await send(method)
    if isinstance(method, EditMessageText) and method.message_id == context.message_id:
        context.used = True
    elif isinstance(method, SendMessage) and not context.used and not method.reply_parameters:
        markup = method.reply_markup
        if markup is None or isinstance(markup, InlineKeyboardMarkup):
            context.used = True
            edit = EditMessageText(chat_id=context.chat_id, message_id=context.message_id,
                                   text=method.text, parse_mode=method.parse_mode,
                                   entities=method.entities, link_preview_options=method.link_preview_options,
                                   reply_markup=markup)
            try:
                return await send(edit)
            except TelegramBadRequest as exc:
                if 'message is not modified' in str(exc).lower():
                    return True
                # An expired/deleted/media menu can still be opened with a new message.
                if not any(error in str(exc).lower() for error in (
                    'message to edit not found', "message can't be edited", 'no text in the message')):
                    raise
    return await send(method)


def without_custom_icons(method):
    markup = getattr(method, 'reply_markup', None)
    if not isinstance(markup, InlineKeyboardMarkup):
        return None
    if not any(button.icon_custom_emoji_id is not None for row in markup.inline_keyboard for button in row):
        return None
    result = method.model_copy(deep=True)
    for row in result.reply_markup.inline_keyboard:
        for button in row:
            button.icon_custom_emoji_id = None
    return result
