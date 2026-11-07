import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from concurrent.futures import ThreadPoolExecutor
from database import Database
import test_managed_trials as fixtures
from managed_trials import provide_trial
from managed_resellers import bootstrap,AgencyError
from trial_chain import claim,release

class ChainTrialTests(unittest.TestCase):
    setUp=fixtures.ManagedTrialTests.setUp
    tearDown=fixtures.ManagedTrialTests.tearDown
    create=fixtures.ManagedTrialTests.create

    def other(self):
        path=Path(self.f.temp.name)/'other.db'
        bot=self.root.register_reseller_bot('123:other','other',404,'Other',str(path))
        bootstrap(self.root,bot,[self.f.pid])
        other=Database(str(path))
        with other._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        return other

    def test_parent_child_sibling_and_main_share_one_trial(self):
        other=self.other()
        child_id=self.f.child();child=Database(self.root.get_reseller_bot(child_id)['db_path'])
        with child._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.create)) as provider:
                await provide_trial(self.local,303)
                for db in (other,child):
                    with self.assertRaises(AgencyError):await provide_trial(db,303)
                self.assertFalse(self.root.try_reserve_test_slot(303,1))
                self.assertEqual(provider.await_count,1)
        try:asyncio.run(run())
        finally:other.close();child.close()

    def test_previous_main_trial_blocks_representative(self):
        with self.root._get_conn() as c:c.execute('INSERT INTO users(telegram_id,test_used) VALUES(303,1)')
        async def run():
            with self.assertRaises(AgencyError):await provide_trial(self.local,303)
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),10000)

    def test_old_representative_history_is_not_reset_by_upgrade(self):
        other=self.other()
        with self.local._get_conn() as c:c.execute('UPDATE users SET test_used=1 WHERE telegram_id=303')
        async def run():
            with self.assertRaises(AgencyError):await provide_trial(other,303)
        try:asyncio.run(run())
        finally:other.close()

    def test_concurrent_reservations_have_one_winner(self):
        other=self.other()
        paths=[self.local.db_path,other.db_path]
        def reserve(path):
            db=Database(self.root.db_path)
            try:
                try:claim(db,303,path);return True
                except AgencyError:return False
            finally:db.close()
        try:
            with ThreadPoolExecutor(2) as pool:result=list(pool.map(reserve,paths))
            self.assertEqual(sorted(result),[False,True])
        finally:other.close()

    def test_main_failed_trial_release_and_representative_claim(self):
        with self.root._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        self.assertTrue(self.root.try_reserve_test_slot(303,1))
        self.root.release_test_slot(303)
        self.assertTrue(claim(self.root,303,self.local.db_path))
        self.root.release_test_slot(303)
        self.assertEqual(self.root.get_user(303)['test_used'],1)

    def test_legacy_reseller_test_uses_same_primary_quota(self):
        other=self.other()
        other.set_setting('managed_reseller_bot_id','')
        other.set_setting('managed_root_db_path','')
        other.set_setting('miniapp_tenant_id','77')
        with self.root._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        try:
            with patch('config.DB_PATH',self.root.db_path):
                self.assertTrue(other.try_reserve_test_slot(303,1))
                self.assertFalse(self.root.try_reserve_test_slot(303,1))
                self.assertFalse(other.try_reserve_test_slot(303,1))
                other.release_test_slot(303)
                self.assertEqual(other.get_user(303)['test_used'],0)
                self.assertTrue(self.root.try_reserve_test_slot(303,1))
        finally:other.close()

    def test_successful_main_trial_cannot_be_released_again(self):
        with self.root._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        self.assertTrue(self.root.try_reserve_test_slot(303,1))
        self.root.complete_test_slot(303)
        self.root.release_test_slot(303)
        self.assertFalse(self.root.try_reserve_test_slot(303,1))
        with self.assertRaises(AgencyError):claim(self.root,303,self.local.db_path)

    def test_resetting_local_trial_history_does_not_create_a_second_test(self):
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.create)) as provider:
                await provide_trial(self.local,303)
                with self.local._get_conn() as conn:
                    conn.execute('DELETE FROM managed_trials WHERE user_id=303')
                    conn.execute('UPDATE users SET test_used=0 WHERE telegram_id=303')
                with self.assertRaises(AgencyError):await provide_trial(self.local,303)
                self.assertEqual(provider.await_count,1)
        asyncio.run(run())

    def test_trial_does_not_register_customer_in_main_bot(self):
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.create)):
                await provide_trial(self.local,303)
        asyncio.run(run())
        self.assertIsNone(self.root.get_user(303))
        self.root.add_or_update_user(303,'customer','Customer')
        self.assertFalse(self.root.try_reserve_test_slot(303,1))
