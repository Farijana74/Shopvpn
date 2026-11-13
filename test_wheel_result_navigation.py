import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.methods import SendMessage, EditMessageText
from inline_navigation import InlineNavigationMiddleware, send_navigation, separate_messages


class WheelNavigationTests(unittest.TestCase):
    def setUp(self):
        from test_managed_resellers import ManagedAgencyTests
        from handlers_user import create_user_router
        self.fixture=ManagedAgencyTests();self.fixture.setUp()
        self.root=self.fixture.root
        self.root.set_setting('wheel_enabled','1')
        router=create_user_router(self.root,True)
        self.wheel=next(h.callback for h in router.message.handlers if h.callback.__name__=='wheel_of_fortune')

    def tearDown(self):
        self.fixture.tearDown()

    def run_wheel(self, prize=None, provision_error=None, dice_error=False):
        from handlers_user import TestPlanProvisionError
        send=AsyncMock(return_value=True)
        async def output(chat_id,text,**kwargs):
            return await send_navigation(SendMessage(chat_id=chat_id,text=text,**kwargs),send)
        bot=AsyncMock()
        bot.send_message.side_effect=output
        if dice_error:
            bot.send_dice.side_effect=RuntimeError('animation unavailable')
        async def answer(text,**kwargs):
            return await output(101,text,**kwargs)
        user=types.SimpleNamespace(id=101,full_name='Customer',first_name='Customer',username='customer')
        message=types.SimpleNamespace(chat=types.SimpleNamespace(id=101),from_user=user,bot=bot,answer=AsyncMock(side_effect=answer))
        event=types.SimpleNamespace(data='mm:btn_wheel',from_user=user,
            message=types.SimpleNamespace(chat=types.SimpleNamespace(id=101),message_id=7))
        async def handler(event,data):
            await self.wheel(message,bot)
        result=dict(subscription_url='vless://wheel-prize',username='wheel-user')
        provision=AsyncMock(side_effect=TestPlanProvisionError('offline') if provision_error else None,return_value=result)
        with patch.object(self.root,'pick_wheel_prize',return_value=prize) as pick, \
             patch('handlers_user.provision_test_plan',new=provision), \
             patch('handlers_user.asyncio.sleep',new=AsyncMock()), \
             patch('handlers_user.report_router.notify_prize',new=AsyncMock()), \
             patch('handlers_user.report_router.report',new=AsyncMock()), \
             patch('config_delivery.prepare_delivery',new=AsyncMock(return_value=('Prize config',[]))), \
             patch('config_delivery._build_qr_photo',return_value=b'png'):
            asyncio.run(InlineNavigationMiddleware(self.root)(handler,event,{}))
        return send,bot,pick,provision

    def assert_new_results(self,send):
        self.assertTrue(send.await_args_list)
        for call in send.await_args_list:
            method=call.args[0]
            self.assertIsInstance(method,SendMessage)
            buttons=[b.callback_data for row in method.reply_markup.inline_keyboard for b in row]
            self.assertEqual(buttons,['trial:main_menu'])

    def test_discount_is_a_new_message_with_return_and_actual_code_is_created(self):
        send,bot,pick,provision=self.run_wheel(dict(kind='discount',discount_percent=15))
        self.assert_new_results(send)
        send.assert_awaited_once()
        with self.root._get_conn() as conn:
            rows=conn.execute("SELECT code,percent FROM discount_codes WHERE source='wheel'").fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['percent'],15)
        self.assertIn(rows[0]['code'],send.await_args.args[0].text)
        self.assertIsNotNone(self.root.get_user(101)['last_wheel_spin_at'])
        provision.assert_not_awaited()

    def test_config_prize_and_delivery_keep_return_buttons_and_new_messages(self):
        prize=dict(id=3,kind='config',discount_percent=0,title='VPN prize',name_prefix='gift',
                   panel_server_id=self.root.get_product(self.fixture.pid)['provision_server_id'],volume_mb=128,duration_hours=2)
        send,bot,pick,provision=self.run_wheel(prize)
        self.assert_new_results(send)
        self.assertIn('VPN prize',send.await_args_list[0].args[0].text)
        bot.send_photo.assert_awaited_once()
        provision.assert_awaited_once()
        self.assertEqual(provision.await_args.kwargs['source'],'wheel')
        self.assertFalse(provision.await_args.kwargs['track_product'])
        self.assertIsNotNone(self.root.get_user(101)['last_wheel_spin_at'])

    def test_no_prize_is_also_a_new_message_with_return(self):
        send,bot,pick,provision=self.run_wheel(None)
        self.assert_new_results(send)
        send.assert_awaited_once()
        self.assertIn('شانس',send.await_args.args[0].text)
        self.assertIsNotNone(self.root.get_user(101)['last_wheel_spin_at'])

    def test_failed_animation_does_not_overwrite_menu_or_prevent_result(self):
        send,bot,pick,provision=self.run_wheel(None,dice_error=True)
        self.assertEqual(send.await_count,2)
        self.assertTrue(all(isinstance(c.args[0],SendMessage) for c in send.await_args_list))
        markup=send.await_args.args[0].reply_markup
        self.assertEqual([b.callback_data for row in markup.inline_keyboard for b in row],['trial:main_menu'])
        self.assertIsNotNone(self.root.get_user(101)['last_wheel_spin_at'])

    def test_failed_config_keeps_return_and_releases_spin(self):
        prize=dict(id=3,kind='config',title='VPN',name_prefix='gift',panel_server_id=1,volume_mb=128,duration_hours=2)
        send,bot,pick,provision=self.run_wheel(prize,provision_error=True)
        self.assert_new_results(send)
        send.assert_awaited_once()
        self.assertIsNone(self.root.get_user(101)['last_wheel_spin_at'])
        bot.send_photo.assert_not_awaited()

    def test_cooldown_and_disabled_wheel_do_not_award_another_prize(self):
        for disabled in (False,True):
            with self.subTest(disabled=disabled):
                self.root.record_wheel_spin(101)
                previous=self.root.get_user(101)['last_wheel_spin_at']
                self.root.set_setting('wheel_enabled','0' if disabled else '1')
                send,bot,pick,provision=self.run_wheel(dict(kind='discount',discount_percent=15))
                self.assert_new_results(send)
                pick.assert_not_called()
                bot.send_dice.assert_not_awaited()
                self.assertEqual(self.root.get_user(101)['last_wheel_spin_at'],previous)


class SeparateMessageScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_navigation_is_restored_after_separate_result_even_on_exception(self):
        for fail in (False,True):
            with self.subTest(fail=fail):
                send=AsyncMock(return_value=True)
                event=types.SimpleNamespace(data='mm:btn_buy',message=types.SimpleNamespace(chat=types.SimpleNamespace(id=1),message_id=2))
                async def handler(event,data):
                    try:
                        with separate_messages():
                            await send_navigation(SendMessage(chat_id=1,text='Result'),send)
                            if fail:
                                raise RuntimeError('test')
                    except RuntimeError:
                        pass
                    await send_navigation(SendMessage(chat_id=1,text='Navigation'),send)
                await InlineNavigationMiddleware()(handler,event,{})
                self.assertIsInstance(send.await_args_list[0].args[0],SendMessage)
                self.assertIsInstance(send.await_args_list[1].args[0],EditMessageText)
