import asyncio
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from inline_navigation import InlineNavigationMiddleware, send_navigation
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from menu_restore import main_menu_markup, restore_main_menu


class MenuRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_referral_is_separate_and_original_menu_is_not_edited(self):
        event=types.SimpleNamespace(data='mm:btn_referral',message=types.SimpleNamespace(chat=types.SimpleNamespace(id=1),message_id=2))
        send=AsyncMock(return_value=True)
        async def handler(event,data):await send_navigation(SendMessage(chat_id=1,text='Referral'),send)
        await InlineNavigationMiddleware()(handler,event,{})
        self.assertIsInstance(send.call_args.args[0],SendMessage)

    async def test_completed_purchase_can_restore_inline_menu(self):
        bot=MagicMock(send_message=AsyncMock())
        db=types.SimpleNamespace(db_path='/tmp/representative.db',get_text=lambda key,default:'منوی دلخواه')
        markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='خرید',callback_data='mm:btn_buy')]])
        with patch('keyboards.inline_menu_for_user',return_value=markup):
            await restore_main_menu(bot,db,10)
        self.assertEqual(bot.send_message.call_args.kwargs['reply_markup'],markup)
        self.assertEqual(bot.send_message.call_args.args[1],'منوی دلخواه')

    async def test_navigation_error_keeps_inline_menu(self):
        from aiogram.methods import EditMessageText
        markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='خرید',callback_data='mm:btn_buy')]])
        db=types.SimpleNamespace()
        event=types.SimpleNamespace(data='mm:btn_buy',from_user=types.SimpleNamespace(id=1),message=types.SimpleNamespace(chat=types.SimpleNamespace(id=1),message_id=2))
        send=AsyncMock(return_value=True)
        async def handler(event,data):
            await send_navigation(SendMessage(chat_id=1,text='موجود نیست'),send)
        with patch('menu_restore.main_menu_markup',return_value=markup):
            await InlineNavigationMiddleware(db)(handler,event,{})
        method=send.call_args.args[0]
        self.assertIsInstance(method,EditMessageText)
        self.assertEqual(method.reply_markup,markup)

    async def test_reply_menu_is_preserved_when_inline_menu_is_disabled(self):
        db=types.SimpleNamespace(db_path='/tmp/representative.db')
        with patch('keyboards.inline_menu_for_user',return_value=None),patch('keyboards.menu_for_user',return_value='reply'):
            self.assertEqual(main_menu_markup(db,10),'reply')


if __name__=='__main__':unittest.main()
