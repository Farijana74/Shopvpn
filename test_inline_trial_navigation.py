import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.methods import SendMessage, EditMessageText
from inline_navigation import InlineNavigationMiddleware, send_navigation


class InlineTrialNavigationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_managed_resellers import ManagedAgencyTests
        from handlers_user import create_user_router
        self.fixture=ManagedAgencyTests();self.fixture.setUp()
        self.db=self.fixture.root
        router=create_user_router(self.db,True)
        self.callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='cb_main_menu_inline')
        self.plan_callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='cb_user_test_plan_pick')
        self.sender=AsyncMock(return_value=True)
        self.bot=AsyncMock()
        async def output(chat_id,text,**kwargs):
            return await send_navigation(SendMessage(chat_id=chat_id,text=text,**kwargs),self.sender)
        self.bot.send_message.side_effect=output
        self.user=SimpleNamespace(id=101,first_name='User',full_name='User',username='user')
        async def answer(text,**kwargs):return await output(101,text,**kwargs)
        self.message=SimpleNamespace(chat=SimpleNamespace(id=101),message_id=7,from_user=self.user,bot=self.bot,answer=AsyncMock(side_effect=answer),delete=AsyncMock())
        self.message.model_copy=MagicMock(return_value=self.message)
        self.event=SimpleNamespace(data='mm:btn_test',from_user=self.user,message=self.message,answer=AsyncMock())
        self.state=SimpleNamespace(get_state=AsyncMock(return_value=None),clear=AsyncMock())

    def tearDown(self):self.fixture.tearDown()

    async def click(self):
        async def handler(event,data):await self.callback(event,self.state,self.bot)
        await InlineNavigationMiddleware(self.db)(handler,self.event,{})

    def assert_new_messages(self):
        for call in self.sender.await_args_list:
            self.assertIsInstance(call.args[0],SendMessage)
            self.assertNotIsInstance(call.args[0],EditMessageText)

    async def test_plan_selection_appears_in_new_message(self):
        plans=[dict(id=i,name=f'پلن {i}',volume_mb=1024,duration_hours=24) for i in (1,2)]
        with patch.object(self.db,'can_request_test',return_value=True), \
             patch.object(self.db,'get_test_config_plans',return_value=plans):
            await self.click()
        self.sender.assert_awaited_once()
        self.assert_new_messages()
        markup=self.sender.await_args.args[0].reply_markup
        self.assertEqual([row[0].callback_data for row in markup.inline_keyboard],['user_test_plan:1','user_test_plan:2'])

    async def test_disabled_and_exhausted_messages_are_new(self):
        self.db.set_setting('test_enabled','0')
        await self.click()
        self.db.set_setting('test_enabled','1')
        with patch.object(self.db,'can_request_test',return_value=False):await self.click()
        self.assertEqual(self.sender.await_count,2)
        self.assert_new_messages()

    async def test_long_trial_result_and_qr_are_new_without_changing_quota_flow(self):
        plan=dict(id=1,name='تست',volume_mb=1024,duration_hours=24)
        configs=['vless://'+str(i)+'x'*180 for i in range(9)]
        result={'subscription_url':'https://example.invalid/sub','username':'trial1'}
        with patch.object(self.db,'can_request_test',return_value=True), \
             patch.object(self.db,'get_test_config_plans',return_value=[plan]), \
             patch.object(self.db,'try_reserve_test_slot',return_value=True) as reserve, \
             patch.object(self.db,'complete_test_slot') as complete, \
             patch('handlers_user.is_volume_credit_bot',return_value=False), \
             patch('handlers_user.provision_test_plan',new=AsyncMock(return_value=result)), \
             patch('handlers_user.report_router.notify_test_config',new=AsyncMock()), \
             patch('trial_delivery.panel_individual_links',new=AsyncMock(return_value=configs)), \
             patch('trial_delivery.dedicated_sub.dedicated_link_for_upstream',side_effect=lambda db,url:url), \
             patch('trial_delivery.build_qr_bytes',return_value=b'png'):
            await self.click()
        self.bot.send_photo.assert_awaited_once()
        self.sender.assert_awaited_once()
        self.assert_new_messages()
        self.assertTrue(all(c in self.sender.await_args.args[0].text for c in configs))
        self.assertEqual(self.sender.await_args.args[0].reply_markup.inline_keyboard[-1][0].callback_data,'trial:main_menu')
        reserve.assert_called_once();complete.assert_called_once_with(101)

    async def test_insufficient_stock_keeps_release_and_sends_new_message(self):
        with patch.object(self.db,'can_request_test',return_value=True), \
             patch.object(self.db,'get_test_config_plans',return_value=[]), \
             patch.object(self.db,'try_reserve_test_slot',return_value=True), \
             patch.object(self.db,'take_unused_test_config',return_value=None), \
             patch.object(self.db,'release_test_slot') as release:
            await self.click()
        release.assert_called_once_with(101)
        self.sender.assert_awaited_once()
        self.assert_new_messages()

    async def test_after_plan_selection_qr_and_result_are_new(self):
        plan=dict(id=1,name='تست',volume_mb=1024,duration_hours=24,is_active=1)
        configs=['vless://'+str(i)+'x'*180 for i in range(9)]
        result={'subscription_url':'https://example.invalid/sub','username':'trial1'}
        self.event.data='user_test_plan:1'
        async def handler(event,data):await self.plan_callback(event)
        with patch.object(self.db,'get_test_config_plan',return_value=plan), \
             patch.object(self.db,'try_reserve_test_slot',return_value=True), \
             patch.object(self.db,'complete_test_slot'), \
             patch('handlers_user.is_volume_credit_bot',return_value=False), \
             patch('handlers_user.provision_test_plan',new=AsyncMock(return_value=result)), \
             patch('handlers_user.report_router.notify_test_config',new=AsyncMock()), \
             patch('trial_delivery.panel_individual_links',new=AsyncMock(return_value=configs)), \
             patch('trial_delivery.dedicated_sub.dedicated_link_for_upstream',side_effect=lambda db,url:url), \
             patch('trial_delivery.build_qr_bytes',return_value=b'png'):
            await InlineNavigationMiddleware(self.db)(handler,self.event,{})
        self.bot.send_photo.assert_awaited_once()
        self.sender.assert_awaited_once()
        self.assert_new_messages()
        self.assertTrue(all(c in self.sender.await_args.args[0].text for c in configs))
