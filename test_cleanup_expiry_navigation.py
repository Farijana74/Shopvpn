import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cleanup_loop import EXPIRY_WARNING_TEXT, _notify_user


class CleanupExpiryButtonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_managed_resellers import ManagedAgencyTests
        from handlers_user import create_user_router
        self.fixture=ManagedAgencyTests();self.fixture.setUp()
        self.db=self.fixture.root
        self.router=create_user_router(self.db,True)
        self.state=SimpleNamespace(clear=AsyncMock())
        self.message=SimpleNamespace(text=EXPIRY_WARNING_TEXT,chat=SimpleNamespace(id=101),message_id=12,answer=AsyncMock())
        self.call=SimpleNamespace(data='prod:999999',from_user=SimpleNamespace(id=101),message=self.message,answer=AsyncMock())

    def tearDown(self):self.fixture.tearDown()

    async def test_new_warning_uses_existing_category_route(self):
        with patch('cleanup_loop.send_telegram',new=AsyncMock()) as send:
            await _notify_user(AsyncMock(),self.db,101,EXPIRY_WARNING_TEXT,999999,expiry_warning=True)
        button=send.await_args.kwargs['reply_markup'].inline_keyboard[0][0]
        self.assertEqual(button.callback_data,'cleanup:buy')
        self.call.data=button.callback_data
        await self.router.callback_query.trigger(self.call,state=self.state,raw_state=None,bot=AsyncMock())
        self.state.clear.assert_not_awaited()
        categories=self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard
        self.assertTrue(any(b.callback_data.startswith('cat:') for row in categories for b in row if b.callback_data))

    async def test_old_warning_opens_categories_even_if_product_missing(self):
        with patch.object(self.db,'get_product',return_value=None) as get_product:
            await self.router.callback_query.trigger(self.call,state=self.state,raw_state=None,bot=AsyncMock())
        get_product.assert_not_called()
        self.state.clear.assert_not_awaited()
        self.message.answer.assert_awaited_once()
        self.call.answer.assert_awaited_once()

    async def test_old_translated_warning_uses_same_category_route(self):
        self.message.text='⚠️ translated reminder'
        with patch('notification_i18n.localized',return_value=self.message.text), \
             patch.object(self.db,'get_product',return_value=None) as get_product:
            await self.router.callback_query.trigger(self.call,state=self.state,raw_state=None,bot=AsyncMock())
        get_product.assert_not_called()
        self.state.clear.assert_not_awaited()
        self.message.answer.assert_awaited_once()

    async def test_old_english_warning_still_works_after_language_change(self):
        from i18n import tr
        self.message.text=tr(EXPIRY_WARNING_TEXT,'en')
        with patch('notification_i18n.localized',side_effect=RuntimeError('unused')), \
             patch.object(self.db,'get_product',return_value=None) as get_product:
            await self.router.callback_query.trigger(self.call,state=self.state,raw_state=None,bot=AsyncMock())
        get_product.assert_not_called()
        self.state.clear.assert_not_awaited()
        self.message.answer.assert_awaited_once()

    async def test_unrelated_warning_does_not_hijack_product_link(self):
        self.message.text='⚠️ unrelated notice'
        with patch('notification_i18n.localized',return_value='⚠️ translated reminder'), \
             patch.object(self.db,'get_product',return_value=None) as get_product:
            await self.router.callback_query.trigger(self.call,state=self.state,raw_state=None,bot=AsyncMock())
        get_product.assert_called_once_with(999999)
        self.state.clear.assert_not_awaited()
        self.assertTrue(self.call.answer.await_args.kwargs['show_alert'])

    async def test_other_product_button_keeps_existing_product_lookup(self):
        self.message.text='پیام عادی محصول'
        with patch.object(self.db,'get_product',return_value=None) as get_product:
            await self.router.callback_query.trigger(self.call,state=self.state,raw_state=None,bot=AsyncMock())
        get_product.assert_called_once_with(999999)
        self.state.clear.assert_not_awaited()
        self.call.answer.assert_awaited_once()
        self.assertTrue(self.call.answer.await_args.kwargs['show_alert'])

    async def test_other_cleanup_notices_and_missing_product_stay_unchanged(self):
        with patch('cleanup_loop.send_telegram',new=AsyncMock()) as send:
            await _notify_user(AsyncMock(),self.db,101,'سرویس حذف شد',7)
            await _notify_user(AsyncMock(),self.db,101,EXPIRY_WARNING_TEXT,None,expiry_warning=True)
        self.assertEqual(send.await_args_list[0].kwargs['reply_markup'].inline_keyboard[0][0].callback_data,'prod:7')
        self.assertIsNone(send.await_args_list[1].kwargs['reply_markup'])
