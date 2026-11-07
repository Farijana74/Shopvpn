import unittest
import types
from unittest.mock import AsyncMock, patch

from trial_delivery import send_trial


class FakeDB:
    def __init__(self, values=None):
        self.values = values or {}

    def get_setting(self, key, default=''):
        return self.values.get(key, default)


class TrialDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_links_fit_in_one_photo_with_help_and_back(self):
        bot = AsyncMock()
        db = FakeDB({'test_post_delivery_text': 'راهنما', 'test_success_message': 'تبریک {username}'})
        links = ['https://example.invalid/sub', 'happ://crypt5/abc123']
        with patch('trial_delivery.dedicated_sub.dedicated_link_for_upstream', side_effect=lambda db, url: url), patch('trial_delivery.build_qr_bytes', return_value=b'png'):
            await send_trial(bot, db, 42, links, plan_name='تست', amount='۱ گیگ', username='user1')
        bot.send_photo.assert_awaited_once()
        bot.send_message.assert_not_awaited()
        args = bot.send_photo.await_args.kwargs
        self.assertTrue(all(link in args['caption'] for link in links))
        self.assertIn('تبریک user1', args['caption'])
        self.assertNotIn('راهنما', args['caption'])
        rows = args['reply_markup'].inline_keyboard
        self.assertEqual([r[0].callback_data for r in rows], ['trial:help', 'trial:main_menu'])

    async def test_oversized_link_is_delivered_in_extra_message(self):
        bot = AsyncMock()
        link = 'happ://crypt5/' + 'a' * 1100
        with patch('trial_delivery.build_qr_bytes', return_value=b'png'):
            await send_trial(bot, FakeDB(), 42, link)
        bot.send_photo.assert_awaited_once()
        self.assertIsNone(bot.send_photo.await_args.kwargs['reply_markup'])
        bot.send_message.assert_awaited_once()
        self.assertIn(link, bot.send_message.await_args.args[1])
        self.assertEqual(bot.send_message.await_args.kwargs['reply_markup'].inline_keyboard[-1][0].callback_data, 'trial:main_menu')

    async def test_subscription_disabled_keeps_individual_config(self):
        bot = AsyncMock()
        db = FakeDB({'deliver_sub_link_enabled': '0', 'deliver_individual_configs_enabled': '1'})
        with patch('trial_delivery.collect_service_info', new_callable=AsyncMock, return_value={'configs': ['vless://individual']}), patch('trial_delivery.build_qr_bytes', return_value=b'png'):
            await send_trial(bot, db, 42, 'https://example.invalid/sub')
        self.assertIn('vless://individual', bot.send_photo.await_args.kwargs['caption'])
        self.assertNotIn('https://example.invalid/sub', bot.send_photo.await_args.kwargs['caption'])


class TrialButtonsTests(unittest.TestCase):
    from test_managed_resellers import ManagedAgencyTests as fixture
    setUp = fixture.setUp
    tearDown = fixture.tearDown

    def test_guide_and_return_callbacks(self):
        import asyncio
        from handlers_user import create_user_router
        self.root.set_setting('test_post_delivery_text', 'راهنمای تست')
        router = create_user_router(self.root, True)
        callbacks = {h.callback.__name__: h.callback for h in router.callback_query.handlers}
        call = types.SimpleNamespace(from_user=types.SimpleNamespace(id=1), bot=AsyncMock(),
                                     message=types.SimpleNamespace(answer=AsyncMock()), answer=AsyncMock())
        async def run():
            await callbacks['cb_trial_help'](call)
            self.assertEqual(call.message.answer.await_args.args[0], 'راهنمای تست')
            self.assertEqual(call.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data, 'trial:main_menu')
            with patch('menu_restore.restore_main_menu', new_callable=AsyncMock) as restore:
                await callbacks['cb_trial_main_menu'](call)
                restore.assert_awaited_once_with(call.bot, self.root, 1)
        asyncio.run(run())
