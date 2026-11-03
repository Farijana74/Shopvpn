import types
import unittest
from unittest.mock import AsyncMock
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from admin_navigation import replace_view
from keyboards import admin_categories_kb

class AdminNavigationTests(unittest.IsolatedAsyncioTestCase):
    def call(self):
        return types.SimpleNamespace(message=types.SimpleNamespace(edit_text=AsyncMock(),answer=AsyncMock(),delete=AsyncMock()))

    async def test_open_categories_does_not_delete_original(self):
        call=self.call()
        markup=admin_categories_kb([{'id':1,'name':'ایران','is_active':1,'icon_custom_emoji_id':''}])
        self.assertIsNone(markup.inline_keyboard[0][0].icon_custom_emoji_id)
        self.assertTrue(await replace_view(call,'دسته‌بندی‌ها',markup))
        call.message.delete.assert_not_awaited()
        call.message.answer.assert_not_awaited()
        self.assertEqual(call.message.edit_text.call_args.kwargs['reply_markup'],markup)

    async def test_failed_edit_and_send_preserve_existing_menu(self):
        call=self.call()
        call.message.edit_text.side_effect=TelegramBadRequest(method=EditMessageText(chat_id=1,message_id=2,text='x'),message='DOCUMENT_INVALID')
        call.message.answer.side_effect=RuntimeError('transport unavailable')
        self.assertFalse(await replace_view(call,'دسته‌بندی‌ها'))
        call.message.delete.assert_not_awaited()

    async def test_replacement_is_sent_before_deletion(self):
        call=self.call();events=[]
        call.message.edit_text.side_effect=RuntimeError('not editable')
        async def send(*args,**kwargs):events.append('sent')
        async def delete(*args,**kwargs):events.append('deleted')
        call.message.answer.side_effect=send
        call.message.delete.side_effect=delete
        self.assertTrue(await replace_view(call,'دسته‌بندی‌ها'))
        self.assertEqual(events,['sent','deleted'])

    async def test_unchanged_menu_is_successful_and_kept(self):
        call=self.call()
        call.message.edit_text.side_effect=TelegramBadRequest(method=EditMessageText(chat_id=1,message_id=2,text='x'),message='message is not modified')
        self.assertTrue(await replace_view(call,'دسته‌بندی‌ها'))
        call.message.delete.assert_not_awaited()
        call.message.answer.assert_not_awaited()

    async def test_rejected_premium_icon_retries_without_erasing_menu(self):
        call=self.call()
        markup=admin_categories_kb([{'id':1,'name':'ایران','is_active':1,'icon_custom_emoji_id':'123'}])
        call.message.edit_text.side_effect=[TelegramBadRequest(method=EditMessageText(chat_id=1,message_id=2,text='x'),message='DOCUMENT_INVALID'),True]
        self.assertTrue(await replace_view(call,'دسته‌بندی‌ها',markup))
        self.assertIsNone(call.message.edit_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0].icon_custom_emoji_id)
        self.assertEqual(markup.inline_keyboard[0][0].icon_custom_emoji_id,'123')
        call.message.delete.assert_not_awaited()
        call.message.answer.assert_not_awaited()
