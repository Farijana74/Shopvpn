import unittest
import asyncio
import types
from unittest.mock import AsyncMock, patch
from concurrent.futures import ThreadPoolExecutor

import test_managed_resellers as fixtures
from wallet_coupon import quote, reserve, WalletCouponError


class WalletCouponTests(unittest.TestCase):
    setUp = fixtures.ManagedAgencyTests.setUp
    tearDown = fixtures.ManagedAgencyTests.tearDown

    def test_quote_and_reservation_store_pay_and_credit_separately(self):
        code_id = self.root.create_discount_code('SAVE', percent=10, max_uses=1, min_purchase=100000)
        with self.assertRaises(WalletCouponError):
            quote(self.root, 'SAVE', 101, 99999)
        result = quote(self.root, 'SAVE', 101, 100000)
        self.assertEqual((result['pay_amount'], result['credit_amount']), (90000, 100000))
        topup = self.root.get_topup(reserve(self.root, 'SAVE', 101, 100000))
        self.assertEqual((topup['amount'], topup['credit_amount'], topup['discount_code_id']), (90000, 100000, code_id))
        with self.assertRaises(WalletCouponError):
            reserve(self.root, 'SAVE', 101, 100000)

    def test_concurrent_single_use_code_is_reserved_once(self):
        self.root.create_discount_code('ONCE', percent=20, max_uses=1)
        def attempt(_):
            try:
                return reserve(self.root, 'ONCE', 101, 100000)
            except WalletCouponError:
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(4)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_approval_credits_original_amount_once(self):
        self.root.create_discount_code('SAVE', percent=10, max_uses=1)
        before = self.root.get_wallet_credit(101)
        topup_id = reserve(self.root, 'SAVE', 101, 100000)
        from wallet_coupon import begin_payment
        self.assertFalse(self.root.approve_topup(topup_id))
        self.assertTrue(begin_payment(self.root, topup_id, 101, 90000, 'crypto'))
        self.assertFalse(self.root.approve_topup(topup_id))
        self.assertTrue(self.root.approve_topup(topup_id, verified_payment=True))
        self.assertFalse(self.root.approve_topup(topup_id, verified_payment=True))
        self.assertEqual(self.root.get_wallet_credit(101), before + 100000)

    def test_rejected_topup_releases_one_reserved_use(self):
        self.root.create_discount_code('SAVE', percent=10, max_uses=1)
        topup_id = reserve(self.root, 'SAVE', 101, 100000)
        self.assertTrue(self.root.reject_topup(topup_id))
        self.assertFalse(self.root.reject_topup(topup_id))
        self.assertEqual(quote(self.root, 'SAVE', 101, 100000)['pay_amount'], 90000)

    def test_card_coupon_requires_receipt_before_approval(self):
        from wallet_coupon import begin_payment
        self.root.create_discount_code('SAVE', percent=10, max_uses=1)
        before = self.root.get_wallet_credit(101)
        topup_id = reserve(self.root, 'SAVE', 101, 100000)
        self.assertTrue(begin_payment(self.root, topup_id, 101, 90000, 'card'))
        self.assertFalse(self.root.approve_topup(topup_id))
        self.root.set_topup_receipt(topup_id, 'receipt-file')
        self.assertTrue(self.root.approve_topup(topup_id))
        self.assertFalse(self.root.approve_topup(topup_id))
        self.assertEqual(self.root.get_wallet_credit(101), before + 100000)

    def test_started_gateway_coupon_cannot_be_released_before_verification(self):
        from wallet_coupon import begin_payment
        self.root.create_discount_code('SAVE', percent=10, max_uses=1)
        topup_id = reserve(self.root, 'SAVE', 101, 100000)
        self.assertTrue(begin_payment(self.root, topup_id, 101, 90000, 'zarinpal'))
        self.assertFalse(self.root.reject_topup(topup_id))
        self.assertFalse(self.root.approve_topup(topup_id))
        self.assertTrue(self.root.approve_topup(topup_id, verified_payment=True))

    def test_bot_coupon_flow_sets_payable_and_blocks_second_method(self):
        from handlers_user import create_user_router
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.memory import MemoryStorage
        from aiogram.fsm.storage.base import StorageKey
        from states import WalletTopup
        db = self.root
        db.create_discount_code('SAVE', percent=10, max_uses=1, min_purchase=100000)
        db.set_setting('card_to_card_enabled', '1')
        router = create_user_router(db, True)
        messages = {h.callback.__name__: h.callback for h in router.message.handlers}
        callbacks = {h.callback.__name__: h.callback for h in router.callback_query.handlers}
        user = types.SimpleNamespace(id=101, first_name='test', username='test')
        message = types.SimpleNamespace(from_user=user, text='100000', answer=AsyncMock(), bot=AsyncMock())
        call = types.SimpleNamespace(from_user=user, message=types.SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()), answer=AsyncMock())
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=101, user_id=101))
        async def run():
            await state.set_state(WalletTopup.waiting_amount)
            await messages['process_topup_amount'](message, state)
            await callbacks['wallet_coupon_enter'](call, state)
            message.text = 'SAVE'
            await messages['wallet_coupon_receive'](message, state)
            data = await state.get_data()
            self.assertEqual(data['topup_amount'], 90000)
            topup = db.get_topup(data['coupon_topup_id'])
            self.assertEqual(topup['credit_amount'], 100000)
            from wallet_coupon import topup_for_payment
            self.assertEqual(topup_for_payment(db, 101, data, 90000, 'card'), topup['id'])
            with self.assertRaises(WalletCouponError):
                topup_for_payment(db, 101, data, 90000, 'crypto')
        asyncio.run(run())

    def test_product_prize_is_not_a_wallet_coupon(self):
        self.root.create_discount_code('GIFT', percent=100, product_id=self.pid, source='lootbox_prize')
        with self.assertRaises(WalletCouponError):
            quote(self.root, 'GIFT', 101, 100000)

    def test_product_prize_remains_available_in_both_catalogs(self):
        from miniapp.server import api_catalog
        from web_store.routes import _web_catalog_payload
        self.root.create_discount_code('GIFT', percent=100, product_id=self.pid, source='lootbox_prize')
        for catalog in (api_catalog((101, self.root, None)), _web_catalog_payload(self.root)):
            products = [p for category in catalog for p in category['products']]
            self.assertTrue(next(p for p in products if p['id'] == self.pid)['has_product_prize'])

    def test_payment_method_is_claimed_once_across_connections(self):
        from database import Database
        from wallet_coupon import begin_payment
        self.root.create_discount_code('SAVE', percent=10, max_uses=1)
        topup_id = reserve(self.root, 'SAVE', 101, 100000)
        other = Database(self.root.db_path)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda pair: begin_payment(pair[0], topup_id, 101, 90000, pair[1]), [(self.root, 'card'), (other, 'crypto')]))
            self.assertEqual(results.count(True), 1)
        finally:
            other.close()

    def test_single_use_reservation_is_atomic_across_database_connections(self):
        from database import Database
        self.root.create_discount_code('ONCE', percent=20, max_uses=1)
        other = Database(self.root.db_path)
        def attempt(db):
            try:
                return reserve(db, 'ONCE', 101, 100000)
            except WalletCouponError:
                return None
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, (self.root, other)))
            self.assertEqual(sum(value is not None for value in results), 1)
        finally:
            other.close()

    def test_reminder_minimum_is_snapshotted_in_new_code(self):
        self.root.set_setting('wallet_coupon_min_volume', '120000')
        code, _, _, _ = self.root.generate_volume_discount_code(101)
        self.assertEqual(self.root.get_discount_code(code)['min_purchase'], 120000)
        self.root.set_setting('wallet_coupon_min_volume', '200000')
        self.assertEqual(self.root.get_discount_code(code)['min_purchase'], 120000)

    def test_old_schema_is_migrated_without_losing_topups(self):
        topup_id = self.root.create_topup(101, 50000)
        with self.root._get_conn() as conn:
            conn.execute('DROP INDEX IF EXISTS idx_discount_product_prize')
            conn.execute('DROP INDEX IF EXISTS idx_wallet_coupon_pending')
            for column in ('credit_amount', 'discount_code_id', 'discount_amount', 'payment_started', 'payment_method'):
                conn.execute('ALTER TABLE wallet_topups DROP COLUMN ' + column)
            conn.execute('ALTER TABLE discount_redemptions DROP COLUMN topup_id')
            conn.execute('ALTER TABLE discount_codes DROP COLUMN source')
        self.root.close()
        self.root.init_db(1)
        row = self.root.get_topup(topup_id)
        self.assertEqual(row['amount'], 50000)
        self.assertIsNone(row['credit_amount'])

    def test_miniapp_topup_separates_payable_and_credit(self):
        from miniapp.server import api_topup_request, TopupCreate, _guard_coupon_topup_payment
        from fastapi import BackgroundTasks
        self.root.create_discount_code('SAVE', percent=10, max_uses=1, min_purchase=100000)
        with patch('miniapp.server._card_info', return_value={}), patch('miniapp.server._payment_flags', return_value={}):
            result = api_topup_request(TopupCreate(amount=100000, discount_code='SAVE'), BackgroundTasks(), (101, self.root, types.SimpleNamespace()))
        self.assertEqual((result['pay_amount'], result['credit_amount']), (90000, 100000))
        topup = self.root.get_topup(result['topup_id'])
        _guard_coupon_topup_payment(self.root, topup, 101, 'card')
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            _guard_coupon_topup_payment(self.root, topup, 101, 'crypto')

    def test_abandoned_unstarted_coupon_is_released_but_started_is_not(self):
        from wallet_coupon import begin_payment
        self.root.create_discount_code('ABANDONED', percent=10, max_uses=1)
        first = reserve(self.root, 'ABANDONED', 101, 100000)
        with self.root._get_conn() as conn:
            conn.execute("UPDATE wallet_topups SET created_at='2000-01-01 00:00:00' WHERE id=?", (first,))
        second = reserve(self.root, 'ABANDONED', 101, 100000)
        self.assertTrue(begin_payment(self.root, second, 101, 90000, 'crypto'))
        with self.root._get_conn() as conn:
            conn.execute("UPDATE wallet_topups SET created_at='2000-01-01 00:00:00' WHERE id=?", (second,))
        with self.assertRaises(WalletCouponError):
            reserve(self.root, 'ABANDONED', 101, 100000)
        self.assertEqual(self.root.get_topup(second)['status'], 'pending')

    def test_miniapp_full_coupon_credits_once_without_gateway(self):
        from miniapp.server import api_topup_request, TopupCreate
        from fastapi import BackgroundTasks
        self.root.create_discount_code('FREE', percent=100, max_uses=1)
        before = self.root.get_wallet_credit(101)
        result = api_topup_request(TopupCreate(amount=100000, discount_code='FREE'), BackgroundTasks(), (101, self.root, types.SimpleNamespace()))
        self.assertEqual(result['status'], 'approved')
        self.assertEqual(self.root.get_wallet_credit(101), before + 100000)
        self.assertFalse(self.root.approve_topup(result['topup_id']))

    def test_miniapp_reminder_settings_store_minimum_without_overwriting_other_sources(self):
        from miniapp.server import VolumeReminderSettingsUpdate, api_admin_set_volume_reminder_settings
        self.root.set_setting('wallet_coupon_min_wheel', '60000')
        body = VolumeReminderSettingsUpdate(enabled=True, mode='percent', percent=80, gb_left=2,
                                            discount_percent=10, discount_expiry_hours=24, coupon_min=100000)
        api_admin_set_volume_reminder_settings(body, (1, self.root, types.SimpleNamespace()))
        self.assertEqual(self.root.get_setting('wallet_coupon_min_volume'), '100000')
        self.assertEqual(self.root.get_setting('wallet_coupon_min_wheel'), '60000')
