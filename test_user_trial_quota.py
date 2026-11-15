import asyncio
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import AsyncMock, patch

from database import Database
from managed_trials import provide_trial
from managed_resellers import AgencyError
from direct_panel_provision import ProvisionError


class TrialQuotaTests(unittest.TestCase):
    def setUp(self):
        from test_managed_trials import ManagedTrialTests
        self.fixture=ManagedTrialTests();self.fixture.setUp()
        self.root,self.local=self.fixture.root,self.fixture.local

    def tearDown(self):self.fixture.tearDown()

    def root_user(self,used=0):
        with self.root._get_conn() as conn:
            conn.execute('INSERT INTO users(telegram_id,test_used) VALUES(303,?)',(used,))

    def test_standard_user_has_default_plus_exact_extra_quota(self):
        self.root_user()
        self.root.grant_extra_test_slots(303,2)
        self.assertEqual(self.root.get_test_quota_status(303)['remaining'],3)
        for _ in range(3):
            self.assertTrue(self.root.can_request_test(303))
            self.assertTrue(self.root.try_reserve_test_slot(303,1))
            self.assertFalse(self.root.can_request_test(303)) # no parallel delivery
            self.root.complete_test_slot(303)
        self.assertFalse(self.root.can_request_test(303))
        self.assertFalse(self.root.try_reserve_test_slot(303,1))
        self.assertEqual(self.root.get_user(303)['test_used'],3)

    def test_already_used_user_gets_exactly_granted_tests(self):
        self.root_user(used=1)
        self.root.grant_extra_test_slots(303,2)
        for _ in range(2):
            self.assertTrue(self.root.try_reserve_test_slot(303,1))
            self.root.complete_test_slot(303)
        self.assertFalse(self.root.try_reserve_test_slot(303,1))
        self.assertEqual(self.root.get_user(303)['test_used'],3)

    def test_managed_repeat_trials_charge_each_success_once(self):
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.fixture.create)) as provider:
                await provide_trial(self.local,303)
                self.root.grant_extra_test_slots(303,2)
                await provide_trial(self.local,303)
                await provide_trial(self.local,303)
                with self.assertRaises(AgencyError):await provide_trial(self.local,303)
                self.assertEqual(provider.await_count,3)
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),9979)
        self.assertEqual(self.local.get_user(303)['test_used'],3)
        with self.local._get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders WHERE user_id=303 AND status='approved'").fetchone()[0],3)

    def test_grant_is_shared_between_root_and_reseller(self):
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.fixture.create)):
                await provide_trial(self.local,303)
            self.root_user()
            self.root.grant_extra_test_slots(303,1)
            self.assertTrue(self.root.try_reserve_test_slot(303,1))
            self.root.complete_test_slot(303)
            with self.assertRaises(AgencyError):await provide_trial(self.local,303)
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),9993)
        self.assertEqual(self.local.get_test_quota_status(303)['remaining'],0)

    def test_failed_extra_trial_restores_quota_wallet_and_usage(self):
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.fixture.create)):
                await provide_trial(self.local,303)
            self.root.grant_extra_test_slots(303,1)
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=ProvisionError('offline'))):
                with self.assertRaises(AgencyError):await provide_trial(self.local,303)
            self.assertEqual(self.root.get_wallet_credit(101),9993)
            self.assertEqual(self.local.get_user(303)['test_used'],1)
            self.assertEqual(self.root.get_test_quota_status(303)['extra_remaining'],1)
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.fixture.create)):
                await provide_trial(self.local,303)
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),9986)
        self.assertEqual(self.local.get_user(303)['test_used'],2)
        self.assertEqual(self.root.get_test_quota_status(303)['remaining'],0)

    def test_concurrent_extra_reservations_have_one_winner_and_release_restores_one(self):
        self.root_user(used=1)
        self.root.grant_extra_test_slots(303,1)
        def reserve(_):
            db=Database(self.root.db_path)
            try:return db.try_reserve_test_slot(303,1)
            finally:db.close()
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(reserve,range(2))),[False,True])
        self.root.release_test_slot(303)
        self.root.release_test_slot(303)
        self.assertEqual(self.root.get_test_quota_status(303)['extra_remaining'],1)
        self.assertTrue(self.root.try_reserve_test_slot(303,1))
        self.root.complete_test_slot(303)
        self.assertFalse(self.root.try_reserve_test_slot(303,1))

    def test_duplicate_admin_delivery_does_not_grant_twice(self):
        self.root.grant_extra_test_slots(303,3,request_key='admin:1:10')
        self.root.grant_extra_test_slots(303,3,request_key='admin:1:10')
        self.assertEqual(self.root.get_test_quota_status(303)['extra_remaining'],3)
        with self.assertRaises(ValueError):self.root.grant_extra_test_slots(303,4,request_key='admin:1:10')
        for amount in [0,-1,True,1.5,1000001]:
            with self.assertRaises(ValueError):self.root.grant_extra_test_slots(303,amount)

    def test_old_claim_schema_migrates_without_losing_usage(self):
        self.root_user(used=1)
        with self.root._get_conn() as conn:
            conn.execute('CREATE TABLE chain_trial_claims(user_id INTEGER PRIMARY KEY,owner TEXT NOT NULL,status TEXT NOT NULL)')
            conn.execute("INSERT INTO chain_trial_claims VALUES(?,?,'completed')",(303,self.root.db_path))
        self.root.grant_extra_test_slots(303,1)
        self.assertTrue(self.root.try_reserve_test_slot(303,1))
        self.root.release_test_slot(303)
        self.assertEqual(self.root.get_user(303)['test_used'],1)
        self.assertEqual(self.root.get_test_quota_status(303)['extra_remaining'],1)

    def test_admin_ui_grants_quota_and_rejects_invalid_or_unauthorized_changes(self):
        import types
        from handlers_admin import create_admin_router
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.memory import MemoryStorage
        from aiogram.fsm.storage.base import StorageKey
        from states import AdminUserManage
        router=create_admin_router(self.root,True)
        callbacks={h.callback.__name__:h.callback for h in router.callback_query.handlers}
        handlers={h.callback.__name__:h.callback for h in router.message.handlers}
        state=FSMContext(MemoryStorage(),StorageKey(bot_id=1,chat_id=1,user_id=1))
        message=types.SimpleNamespace(text='-1',message_id=100,chat=types.SimpleNamespace(id=1),from_user=types.SimpleNamespace(id=1),answer=AsyncMock())
        call=types.SimpleNamespace(data='adm_user_trial_quota:303',from_user=message.from_user,message=message,answer=AsyncMock())
        async def run():
            await callbacks['cb_admin_user_trial_quota'](call,state)
            self.assertEqual(await state.get_state(),AdminUserManage.waiting_test_quota_count.state)
            await handlers['process_test_quota_count'](message,state)
            self.assertEqual(self.root.get_test_quota_status(303)['extra_remaining'],0)
            message.text='3'
            await handlers['process_test_quota_count'](message,state)
            self.assertEqual(self.root.get_test_quota_status(303)['extra_remaining'],3)
            self.assertIsNone(await state.get_state())
            call.from_user=types.SimpleNamespace(id=101)
            await callbacks['cb_admin_user_trial_quota'](call,state)
            self.assertIsNone(await state.get_state())
            local_router=create_admin_router(self.local,False)
            local_cb=next(h.callback for h in local_router.callback_query.handlers if h.callback.__name__=='cb_admin_user_trial_quota')
            await local_cb(call,state)
            self.assertIsNone(await state.get_state())
            self.assertEqual(self.root.get_test_quota_status(303)['extra_remaining'],3)
        asyncio.run(run())

    def test_extra_failure_keeps_prior_history_even_if_usage_was_reset(self):
        self.root_user(used=1)
        self.root.grant_extra_test_slots(303,1)
        self.assertTrue(self.root.try_reserve_test_slot(303,1))
        self.root.release_test_slot(303)
        with self.root._get_conn() as conn:conn.execute('UPDATE users SET test_used=0 WHERE telegram_id=303')
        self.assertEqual(self.root.get_test_quota_status(303)['remaining'],1)
        self.assertTrue(self.root.try_reserve_test_slot(303,1))
        self.root.complete_test_slot(303)
        self.assertFalse(self.root.try_reserve_test_slot(303,1))
