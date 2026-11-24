import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_managed_resellers as fixtures
from user_config_prefix import get_prefix, set_prefix


class UserConfigPrefixTests(unittest.TestCase):
    setUp = fixtures.ManagedAgencyTests.setUp
    tearDown = fixtures.ManagedAgencyTests.tearDown

    def test_per_user_default_override_and_validation(self):
        self.assertEqual(get_prefix(self.root, 101), '101')
        self.assertEqual(set_prefix(self.root, 101, 'my123'), 'my123')
        self.assertEqual(get_prefix(self.root, 102), '102')
        for invalid in ('bad name', 'a/b', 'نام', 'a' * 33):
            with self.assertRaises(ValueError):
                set_prefix(self.root, 101, invalid)
        self.assertEqual(get_prefix(self.root, 101), 'my123')
        self.assertEqual(set_prefix(self.root, 101, ''), '101')

    def test_direct_purchase_uses_customer_prefix_and_keeps_reserved_name(self):
        from direct_panel_provision import provision_direct
        from test_threexui_hwid_limit import provider_session
        set_prefix(self.root, 101, 'Customer')
        order = self.root.create_order(101, self.pid, 100)
        provider, session = provider_session()
        with patch('direct_panel_provision.get_provider', return_value=provider):
            asyncio.run(provision_direct(self.root, self.root.get_product(self.pid), 1, 101, order))
        self.assertEqual(session.post.call_args.kwargs['json']['client']['email'], 'Customer_1')
        self.assertEqual(self.root.get_order(order)['config_name'], 'Customer_1')

    def test_global_prefix_changes_for_new_orders_but_respects_personal_prefix(self):
        from user_config_prefix import reserve_order_name
        self.root.add_or_update_user(101, 'buyer', 'Buyer')
        self.root.set_custom_config_prefix('old')
        first = self.root.create_order(101, self.pid, 100)
        self.assertEqual(reserve_order_name(self.root, 101, first), 'old_1')
        self.root.set_custom_config_prefix('new')
        second = self.root.create_order(101, self.pid, 100)
        self.assertEqual(reserve_order_name(self.root, 101, second), 'new_1')
        self.assertEqual(reserve_order_name(self.root, 101, first), 'old_1')
        set_prefix(self.root, 101, 'personal')
        self.assertEqual(get_prefix(self.root, 101), 'personal')
        self.assertEqual(set_prefix(self.root, 101, ''), 'new')
        self.root.set_custom_config_prefix('')
        self.assertEqual(get_prefix(self.root, 101), 'buyer')

    def test_shared_prefix_starts_after_previous_orders_for_all_users(self):
        from user_config_prefix import reserve_order_name
        self.root.add_or_update_user(202, 'second', 'Second')
        self.root.set_custom_config_prefix('shop')
        self.root.create_order(101, self.pid, 100, config_name='shop_75')
        first = self.root.create_order(101, self.pid, 100)
        second = self.root.create_order(202, self.pid, 100)
        self.assertEqual(reserve_order_name(self.root, 101, first), 'shop_76')
        self.assertEqual(reserve_order_name(self.root, 202, second), 'shop_77')
        self.assertEqual(reserve_order_name(self.root, 101, first), 'shop_76')

    def test_global_start_number_is_safe_and_only_affects_new_orders(self):
        from user_config_prefix import global_sequence_start, reserve_order_name, set_global_sequence_start
        self.root.set_custom_config_prefix('shop')
        old = self.root.create_order(101, self.pid, 100, config_name='shop_10')
        self.assertEqual(global_sequence_start(self.root, 'shop'), 11)
        with self.assertRaises(ValueError):
            set_global_sequence_start(self.root, 'shop', 10)
        self.assertEqual(set_global_sequence_start(self.root, 'shop', 1000), 1000)
        first = self.root.create_order(101, self.pid, 100)
        second = self.root.create_order(101, self.pid, 100)
        self.assertEqual(reserve_order_name(self.root, 101, first), 'shop_1000')
        self.assertEqual(reserve_order_name(self.root, 101, second), 'shop_1001')
        self.assertEqual(self.root.get_order(old)['config_name'], 'shop_10')
        with self.assertRaises(ValueError):
            set_global_sequence_start(self.root, 'shop', 1000)
        self.root.set_custom_config_prefix('other')
        with self.assertRaises(ValueError):
            set_global_sequence_start(self.root, 'shop', 2000)

    def test_shared_prefix_advances_when_panel_reports_taken_name(self):
        from direct_panel_provision import provision_direct
        from panel_providers import PanelUsernameTakenError
        self.root.set_custom_config_prefix('shop')
        order = self.root.create_order(101, self.pid, 100)
        async def create(name, *args, **kwargs):
            if name in ('shop_1', 'shop_2'):
                raise PanelUsernameTakenError('already exists')
            return types.SimpleNamespace(username=name, subscription_url='https://example.invalid/sub')
        provider = types.SimpleNamespace(create_user=AsyncMock(side_effect=create), supports_user_limit=False)
        with patch('direct_panel_provision.get_provider', return_value=provider):
            result = asyncio.run(provision_direct(self.root, self.root.get_product(self.pid), 1, 101, order))
        self.assertEqual(result[0]['username'], 'shop_3')
        self.assertEqual(self.root.get_order(order)['config_name'], 'shop_3')
        self.assertEqual([call.args[0] for call in provider.create_user.await_args_list], ['shop_1', 'shop_2', 'shop_3'])

    def test_shared_prefix_is_atomic_for_different_users_and_connections(self):
        from concurrent.futures import ThreadPoolExecutor
        from database import Database
        from user_config_prefix import reserve_order_name
        self.root.add_or_update_user(202, 'second', 'Second')
        self.root.set_custom_config_prefix('shop')
        first = self.root.create_order(101, self.pid, 100)
        second = self.root.create_order(202, self.pid, 100)
        other = Database(self.root.db_path)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                names = list(pool.map(lambda item: reserve_order_name(*item), [(self.root, 101, first), (other, 202, second)]))
            self.assertEqual(set(names), {'shop_1', 'shop_2'})
        finally:
            other.close()

    def test_username_default_and_sequential_names_survive_retries_and_prefix_change(self):
        from user_config_prefix import reserve_order_name
        self.root.add_or_update_user(101, 'john_doe', 'John')
        self.assertEqual(get_prefix(self.root, 101), 'john_doe')
        first = self.root.create_order(101, self.pid, 100)
        second = self.root.create_order(101, self.pid, 100)
        self.assertEqual(reserve_order_name(self.root, 101, first), 'john_doe_1')
        self.assertEqual(reserve_order_name(self.root, 101, first), 'john_doe_1')
        set_prefix(self.root, 101, 'custom')
        self.assertEqual(reserve_order_name(self.root, 101, second), 'custom_2')
        self.assertEqual(reserve_order_name(self.root, 101, first), 'john_doe_1')

    def test_concurrent_orders_receive_different_numbers(self):
        from concurrent.futures import ThreadPoolExecutor
        from user_config_prefix import reserve_order_name
        self.root.add_or_update_user(101, 'buyer', 'Buyer')
        orders = [self.root.create_order(101, self.pid, 100) for _ in range(5)]
        with ThreadPoolExecutor(max_workers=5) as pool:
            names = list(pool.map(lambda order: reserve_order_name(self.root, 101, order), orders))
        self.assertEqual(set(names), {f'buyer_{i}' for i in range(1, 6)})

    def test_concurrent_orders_across_database_connections_are_unique(self):
        from concurrent.futures import ThreadPoolExecutor
        from database import Database
        from user_config_prefix import reserve_order_name
        self.root.add_or_update_user(101, 'buyer', 'Buyer')
        orders = [self.root.create_order(101, self.pid, 100) for _ in range(2)]
        other = Database(self.root.db_path)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                names = list(pool.map(lambda pair: reserve_order_name(pair[0], 101, pair[1]), [(self.root, orders[0]), (other, orders[1])]))
            self.assertEqual(set(names), {'buyer_1', 'buyer_2'})
        finally:
            other.close()

    def test_manual_wallet_increase_sends_the_user_a_notice(self):
        from handlers_admin import create_admin_router
        router = create_admin_router(self.root, True)
        handler = next(h.callback for h in router.message.handlers if h.callback.__name__ == 'process_admin_user_wallet')
        message = types.SimpleNamespace(text='5000', from_user=types.SimpleNamespace(id=1), answer=AsyncMock(), bot=types.SimpleNamespace(send_message=AsyncMock()))
        state = types.SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={'target_user_id': 101}))
        asyncio.run(handler(message, state))
        message.bot.send_message.assert_awaited_once()
        self.assertEqual(message.bot.send_message.await_args.args[0], 101)
        self.assertIn('5,000', message.bot.send_message.await_args.args[1])

    def test_trial_uses_customer_prefix(self):
        from test_config_provision import provision_test_plan
        set_prefix(self.root, 101, 'Customer')
        server_id = self.root.get_product(self.pid)['provision_server_id']
        plan = dict(panel_server_id=server_id, volume_mb=1024, duration_hours=24, name_prefix='old', id=1)
        provider = types.SimpleNamespace(create_user=AsyncMock(side_effect=lambda name, *args: types.SimpleNamespace(username=name, subscription_url='https://example.invalid/sub')))
        with patch('test_config_provision.get_provider', return_value=provider):
            result = asyncio.run(provision_test_plan(self.root, plan, 101))
        self.assertTrue(result['username'].startswith('Customer'))

    def test_user_and_admin_can_set_prefix_for_the_right_user(self):
        from handlers_user import create_user_router
        from handlers_admin import create_admin_router
        user_router = create_user_router(self.root, True)
        admin_router = create_admin_router(self.root, True)
        user_handler = next(h.callback for h in user_router.message.handlers if h.callback.__name__ == 'receive_account_config_prefix')
        admin_handler = next(h.callback for h in admin_router.message.handlers if h.callback.__name__ == 'process_admin_user_prefix')
        user_message = types.SimpleNamespace(text='userPrefix', from_user=types.SimpleNamespace(id=101), answer=AsyncMock())
        admin_message = types.SimpleNamespace(text='adminPrefix', from_user=types.SimpleNamespace(id=1), answer=AsyncMock())
        user_state = types.SimpleNamespace(clear=AsyncMock())
        admin_state = types.SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={'prefix_user_id': 101}))
        async def run():
            await user_handler(user_message, user_state)
            self.assertEqual(get_prefix(self.root, 101), 'userPrefix')
            await admin_handler(admin_message, admin_state)
        asyncio.run(run())
        self.assertEqual(get_prefix(self.root, 101), 'adminPrefix')
