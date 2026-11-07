import asyncio
import types
import unittest
from unittest.mock import AsyncMock,patch
import test_managed_resellers as agency_tests
from database import Database
from managed_trials import configure_trials,provide_trial
from managed_resellers import AgencyError,sync_bot
from direct_panel_provision import ProvisionError

class ManagedTrialTests(unittest.TestCase):
    def setUp(self):
        self.f=agency_tests.ManagedAgencyTests();self.f.setUp()
        self.root,self.local=self.f.root,self.f.local
        configure_trials(self.root,self.f.pid,256,6,7)
        with self.local._get_conn() as conn:conn.execute('INSERT INTO users(telegram_id) VALUES(303)')

    def tearDown(self):self.f.tearDown()

    async def create(self,db,source,quantity,user_id,order_id):
        self.assertEqual(source['auto_provision_volume_gb'],0.25)
        self.assertEqual(source['duration_days'],0.25)
        name=f'trial{order_id}'
        cid=db.add_custom_config(user_id,source['provision_server_id'],name,source['auto_provision_volume_gb'],source['duration_days'],'https://example.invalid/sub',order_id=order_id,source='direct_product')
        return [dict(username=name,subscription_url='https://example.invalid/sub',volume_gb=source['auto_provision_volume_gb'],duration_days=source['duration_days'],panel_server_id=source['provision_server_id'],custom_config_id=cid)]

    def test_cost_once_and_second_request_is_denied(self):
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.create)) as remote:
                _,first=await provide_trial(self.local,303)
                with self.assertRaises(AgencyError):await provide_trial(self.local,303)
                self.assertEqual(remote.await_count,1)
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),9993)
        self.assertEqual(self.local.get_user(303)['test_used'],1)
        self.assertEqual(self.local.get_user(303)['referral_credit'],0)
        with self.root._get_conn() as conn:
            row=conn.execute('SELECT is_trial FROM managed_purchases').fetchone()
        self.assertEqual(row[0],1)

    def test_failed_provision_refunds_money_and_slot_then_retry_charges_once(self):
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=ProvisionError('offline'))):
                with self.assertRaises(AgencyError):await provide_trial(self.local,303)
            self.assertEqual(self.root.get_wallet_credit(101),10000)
            self.assertEqual(self.local.get_user(303)['test_used'],0)
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.create)):
                await provide_trial(self.local,303)
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),9993)

    def test_insufficient_wallet_does_not_create_or_consume_test(self):
        with self.root._get_conn() as conn:conn.execute('UPDATE users SET referral_credit=0 WHERE telegram_id=101')
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock()) as remote:
                with self.assertRaises(AgencyError):await provide_trial(self.local,303)
                remote.assert_not_awaited()
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),0)
        self.assertEqual(self.local.get_user(303)['test_used'],0)

    def test_zero_fee_does_not_consume_prepaid_product_inventory(self):
        configure_trials(self.root,self.f.pid,256,6,0)
        self.root.set_reseller_product_credit(101,self.f.pid,3)
        with self.root._get_conn() as conn:conn.execute('UPDATE users SET referral_credit=0 WHERE telegram_id=101')
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.create)):
                await provide_trial(self.local,303)
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),0)
        self.assertEqual(self.root.get_reseller_product_credit(101,self.f.pid),3)

    def test_nested_trials_charge_flat_fee_at_each_wallet_edge(self):
        child=self.f.child()
        local=Database(self.root.get_reseller_bot(child)['db_path'])
        try:
            with local._get_conn() as conn:conn.execute('INSERT INTO users(telegram_id) VALUES(303)')
            async def run():
                with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.create)):
                    await provide_trial(local,303)
            asyncio.run(run())
            self.assertEqual(self.root.get_wallet_credit(101),9993)
            self.assertEqual(self.local.get_wallet_credit(202),993)
        finally:local.close()

    def test_parallel_clicks_do_not_charge_or_provision_twice(self):
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.create)) as remote:
                results=await asyncio.gather(provide_trial(self.local,303),provide_trial(self.local,303),return_exceptions=True)
                self.assertTrue(any(not isinstance(r,Exception) for r in results))
                self.assertEqual(remote.await_count,1)
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),9993)

    def test_trial_matches_main_normal_delivery_even_if_paid_product_is_happ(self):
        from happ_delivery import get_product_delivery_links
        with self.root._get_conn() as conn:conn.execute("UPDATE products SET delivery_mode='happ' WHERE id=?",(self.f.pid,))
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.create)):
                product,items=await provide_trial(self.local,303)
            provider=types.SimpleNamespace(get_happ_link=AsyncMock(return_value='happ://crypt5/test-trial'))
            with patch('happ_delivery.get_provider',return_value=provider):
                links=await get_product_delivery_links(self.local,product,items)
            self.assertEqual(links,['https://example.invalid/sub'])
            provider.get_happ_link.assert_not_awaited()
            self.assertEqual(self.local.get_custom_config_by_id(items[0]['custom_config_id'])['delivery_mode'],'normal')
        asyncio.run(run())

    def test_referral_label_and_root_trial_toggle_sync(self):
        root_label=self.root.get_setting('btn_referral')
        sync_bot(self.root,self.f.aid)
        self.assertEqual(self.local.get_setting('btn_referral'),'🤝 دعوت دوستان')
        self.assertEqual(self.root.get_setting('btn_referral'),root_label)
        self.assertEqual(self.local.get_setting('test_enabled'),'1')
        self.local.set_setting('btn_referral','دعوت به فروشگاه من')
        self.root.set_setting('managed_trial_policy','{}')
        sync_bot(self.root,self.f.aid)
        self.local.close()
        self.assertEqual(self.local.get_setting('test_enabled'),'0')
        self.assertEqual(self.local.get_setting('btn_referral'),'دعوت به فروشگاه من')

    def test_panel_trial_does_not_inherit_happ_from_paid_product(self):
        from managed_trials import configure_panel_trials
        from happ_delivery import get_product_delivery_links
        with self.root._get_conn() as conn:conn.execute("UPDATE products SET delivery_mode='happ' WHERE id=?",(self.f.pid,))
        panel=self.root.get_product(self.f.pid)['provision_server_id']
        policy=configure_panel_trials(self.root,panel,256,6,7)
        self.assertEqual(policy['delivery_mode'],'normal')
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=self.create)):
                product,items=await provide_trial(self.local,303)
            provider=types.SimpleNamespace(get_happ_link=AsyncMock())
            with patch('happ_delivery.get_provider',return_value=provider):
                links=await get_product_delivery_links(self.local,product,items)
            self.assertEqual(links,['https://example.invalid/sub'])
            provider.get_happ_link.assert_not_awaited()
        asyncio.run(run())
