"""Refund safety during wholesale supply and delivery retries."""
import asyncio
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import test_managed_resellers as fixtures
from database import Database
from managed_resellers import AgencyError, provision, reserve, refund_reservation
from payment_delivery import finalize_paid_order
from happ_delivery import HappDeliveryError


class ManagedRefundTests(unittest.TestCase):
    setUp = fixtures.ManagedAgencyTests.setUp
    tearDown = fixtures.ManagedAgencyTests.tearDown
    local_product = fixtures.ManagedAgencyTests.local_product

    def order(self):
        with self.local._get_conn() as conn:
            conn.execute('INSERT OR IGNORE INTO users(telegram_id,referral_credit) VALUES(303,1000)')
        self.local.deduct_wallet_credit(303, 100)
        return self.local.create_order(303, self.local_product()['id'], 100, wallet_used=100)

    async def create(self, db, source, quantity, user_id, order_id):
        name = db.get_order(order_id)['config_name']
        cc = db.add_custom_config(user_id, source['provision_server_id'], name, 10, 30,
                                  'https://example.invalid/sub', order_id=order_id, source='direct_product')
        return [dict(username=name, subscription_url='https://example.invalid/sub', volume_gb=10,
                     duration_days=30, panel_server_id=source['provision_server_id'], custom_config_id=cc)]

    def test_inflight_supply_blocks_other_connection_refund(self):
        oid = self.order()
        other = Database(self.local.db_path)
        async def work(*args, **kwargs):
            self.assertFalse(other.reject_order(oid, 'admin'))
            self.assertEqual(other.get_wallet_credit(303), 900)
            raise AgencyError('not reserved')
        try:
            with patch('managed_resellers._provision', new=AsyncMock(side_effect=work)):
                with self.assertRaises(AgencyError):
                    asyncio.run(provision(self.local, self.local_product(), 1, 303, oid))
            self.assertTrue(other.reject_order(oid))
            self.assertEqual(other.get_wallet_credit(303), 1000)
            self.assertFalse(other.reject_order(oid))
            self.assertEqual(other.get_wallet_credit(303), 1000)
        finally:
            other.close()

    def test_completed_supply_blocks_refund_and_can_retry(self):
        oid = self.order()
        with patch('direct_panel_provision.provision_direct', new=AsyncMock(side_effect=self.create)) as create:
            first = asyncio.run(provision(self.local, self.local_product(), 1, 303, oid))
            self.assertFalse(self.local.reject_order(oid, 'admin'))
            self.assertFalse(self.local.reject_order(oid, 'user_cancel'))
            second = asyncio.run(provision(self.local, self.local_product(), 1, 303, oid))
            self.assertEqual(first[0]['custom_config_id'], second[0]['custom_config_id'])
            self.assertEqual(create.await_count, 1)
        self.assertEqual(self.local.get_wallet_credit(303), 900)
        self.assertEqual(self.root.get_wallet_credit(101), 9900)
        self.assertTrue(self.local.approve_order_auto(oid))

    def test_happ_failure_then_retry_delivers_without_refund_or_double_charge(self):
        oid = self.order()
        with patch('direct_panel_provision.provision_direct', new=AsyncMock(side_effect=self.create)) as create:
            with patch('payment_delivery.get_product_delivery_links', new=AsyncMock(side_effect=HappDeliveryError('temporary'))):
                asyncio.run(finalize_paid_order(self.local, AsyncMock(), oid))
            self.assertEqual(self.local.get_order(oid)['status'], 'pending')
            self.assertFalse(self.local.reject_order(oid))
            with patch('payment_delivery.get_product_delivery_links', new=AsyncMock(return_value=['https://example.invalid/sub'])), \
                 patch('payment_delivery.deliver_config_to_user', new=AsyncMock()):
                asyncio.run(finalize_paid_order(self.local, AsyncMock(), oid))
            self.assertEqual(create.await_count, 1)
        self.assertEqual(self.local.get_order(oid)['status'], 'approved')
        self.assertEqual(self.local.get_wallet_credit(303), 900)
        self.assertEqual(self.root.get_wallet_credit(101), 9900)

    def test_definite_failure_allows_customer_refund_after_supply_refund(self):
        oid = self.order()
        with patch('direct_panel_provision.provision_direct', new=AsyncMock(side_effect=AgencyError('not created'))):
            with self.assertRaises(AgencyError):
                asyncio.run(provision(self.local, self.local_product(), 1, 303, oid))
        self.assertEqual(self.root.get_wallet_credit(101), 10000)
        self.assertTrue(self.local.reject_order(oid))
        self.assertEqual(self.local.get_wallet_credit(303), 1000)

    def test_mirror_failure_blocks_refund_and_can_retry(self):
        oid = self.order()
        with patch('direct_panel_provision.provision_direct', new=AsyncMock(side_effect=self.create)) as create:
            with patch.object(self.local, 'add_custom_config', side_effect=RuntimeError('mirror failed')):
                with self.assertRaises(RuntimeError):
                    asyncio.run(provision(self.local, self.local_product(), 1, 303, oid))
            self.assertFalse(self.local.reject_order(oid))
            result = asyncio.run(provision(self.local, self.local_product(), 1, 303, oid))
            self.assertTrue(result[0]['custom_config_id'])
            self.assertEqual(create.await_count, 1)
        self.assertEqual(self.local.get_wallet_credit(303), 900)
        self.assertEqual(self.root.get_wallet_credit(101), 9900)

    def test_legacy_reservation_blocks_refund_until_supply_is_refunded(self):
        oid = self.order()
        reserve(self.root, self.aid, oid, self.pid, 1)
        self.assertFalse(self.local.reject_order(oid))
        self.assertTrue(refund_reservation(self.root, self.aid, oid))
        self.assertTrue(self.local.reject_order(oid))
        self.assertEqual(self.local.get_wallet_credit(303), 1000)

    def test_local_service_alone_blocks_refund(self):
        oid = self.order()
        product = self.local_product()
        self.local.add_custom_config(303, product['provision_server_id'], 'existing', 10, 30,
                                     'https://example.invalid/sub', order_id=oid, source='direct_product')
        self.assertFalse(self.local.reject_order(oid))
        self.assertEqual(self.local.get_wallet_credit(303), 900)

    def test_rejected_order_cannot_start_supply(self):
        oid = self.order()
        self.assertTrue(self.local.reject_order(oid))
        with patch('direct_panel_provision.provision_direct', new=AsyncMock()) as create:
            with self.assertRaises(AgencyError):
                asyncio.run(provision(self.local, self.local_product(), 1, 303, oid))
            create.assert_not_awaited()
        self.assertEqual(self.root.get_wallet_credit(101), 10000)

    def test_expiry_skips_funded_order_and_refunds_unfunded_order(self):
        funded, unfunded = self.order(), self.order()
        reserve(self.root, self.aid, funded, self.pid, 1)
        with self.local._get_conn() as conn:
            conn.execute("UPDATE orders SET discount_code_id=999,created_at='2000-01-01' WHERE id IN (?,?)", (funded, unfunded))
        self.assertEqual(self.local.expire_stale_discount_orders(1), [unfunded])
        self.assertEqual(self.local.get_order(funded)['status'], 'pending')
        self.assertEqual(self.local.get_order(unfunded)['status'], 'expired')
        self.assertEqual(self.local.get_wallet_credit(303), 900)

    def test_unavailable_root_blocks_refund_without_creating_database(self):
        oid = self.order()
        path = Path(self.temp.name) / 'missing.db'
        self.local.set_setting('managed_root_db_path', str(path))
        self.assertFalse(self.local.reject_order(oid))
        self.assertFalse(path.exists())
        self.assertEqual(self.local.get_wallet_credit(303), 900)

    def test_main_shop_refund_unchanged(self):
        self.root.add_wallet_credit(101, -100)
        oid = self.root.create_order(101, self.pid, 100, wallet_used=100)
        self.assertTrue(self.root.reject_order(oid))
        self.assertEqual(self.root.get_wallet_credit(101), 10000)

    def test_overlapping_failed_attempt_does_not_clear_active_guard(self):
        oid = self.order()
        async def run():
            started, finish = asyncio.Event(), asyncio.Event()
            async def work(*args, **kwargs):
                if not started.is_set():
                    started.set()
                    await finish.wait()
                raise AgencyError('not reserved')
            with patch('managed_resellers._provision', new=AsyncMock(side_effect=work)):
                task = asyncio.create_task(provision(self.local, self.local_product(), 1, 303, oid))
                await started.wait()
                with self.assertRaises(AgencyError):
                    await provision(self.local, self.local_product(), 1, 303, oid)
                self.assertFalse(self.local.reject_order(oid))
                finish.set()
                with self.assertRaises(AgencyError):
                    await task
            self.assertTrue(self.local.reject_order(oid))
        asyncio.run(run())

    def test_cancelled_task_preserves_guard_while_thread_can_commit(self):
        oid = self.order()
        finish, done = threading.Event(), threading.Event()
        async def run():
            started = asyncio.Event()
            loop = asyncio.get_running_loop()
            def worker():
                loop.call_soon_threadsafe(started.set)
                try:
                    if not finish.wait(5):
                        raise RuntimeError('test timeout')
                    reserve(self.root, self.aid, oid, self.pid, 1)
                finally:
                    done.set()
            async def work(*args, **kwargs):
                await asyncio.to_thread(worker)
            with patch('managed_resellers._provision', new=AsyncMock(side_effect=work)):
                task = asyncio.create_task(provision(self.local, self.local_product(), 1, 303, oid))
                try:
                    await started.wait()
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertFalse(self.local.reject_order(oid))
                    self.assertEqual(self.local.get_wallet_credit(303), 900)
                finally:
                    finish.set()
                    self.assertTrue(await asyncio.to_thread(done.wait, 5))
            self.assertEqual(self.root.get_wallet_credit(101), 9900)
        asyncio.run(run())
