import asyncio
import unittest
import types
from unittest.mock import AsyncMock,patch
import test_merchant_controls as fixtures
from managed_native_volume import plans,available,rules,quote
from managed_resellers import sync_bot,provision,AgencyError

class NativeVolumeTests(unittest.TestCase):
    setUp=fixtures.MerchantTests.setUp
    tearDown=fixtures.MerchantTests.tearDown

    def plan(self,**kwargs):
        server=self.root.get_product(self.pid)['provision_server_id']
        return self.root.create_custom_config_product('حجم دلخواه اصلی',server,min_gb=5,max_gb=200,flat_price_per_gb=10,**kwargs)

    def test_existing_main_plan_appears_automatically_and_reseller_minimum(self):
        pid=self.plan()
        sync_bot(self.root,self.aid)
        self.local._settings_cache=None
        self.assertEqual(self.local.get_setting('custom_config_enabled'),'1')
        self.assertTrue(available(self.local,self.root,self.aid))
        self.assertEqual(rules(self.local,self.root,self.aid,pid)['min_gb'],5)
        self.local.set_setting('volume_min_gb','12')
        self.assertEqual(rules(self.local,self.root,self.aid,pid)['min_gb'],12)
        with self.assertRaises(AgencyError):quote(self.local,self.root,self.aid,303,11,pid)
        self.local.set_setting('volume_min_gb','1')
        self.assertEqual(rules(self.local,self.root,self.aid,pid)['min_gb'],5)
        self.root.update_custom_config_product(pid,is_active=0)
        sync_bot(self.root,self.aid);self.local._settings_cache=None
        self.assertEqual(self.local.get_setting('custom_config_enabled'),'0')
        with self.assertRaises(AgencyError):quote(self.local,self.root,self.aid,303,20,pid)

    def test_inherited_plan_uses_existing_ledger_and_current_main_price(self):
        native_id=self.plan()
        with self.local._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        self.local.set_setting('volume_sell_per_gb','20')
        pid,price=quote(self.local,self.root,self.aid,303,10,native_id,'myclient')
        self.assertEqual(price,200)
        product=self.local.get_product(pid)
        order=self.local.create_order(303,pid,price)
        self.root.update_custom_config_product(native_id,flat_price_per_gb=12)
        async def create(db,source,quantity,user_id,order_id):
            self.assertEqual(source['auto_provision_volume_gb'],10)
            self.assertEqual(source['duration_days'],30)
            name=db.get_order(order_id)['config_name'];self.assertTrue(name.startswith('myclient'))
            cc=db.add_custom_config(user_id,source['provision_server_id'],name,10,30,'https://example.invalid/sub',order_id=order_id,source='direct_product')
            return [dict(username=name,subscription_url='https://example.invalid/sub',volume_gb=10,duration_days=30,panel_server_id=source['provision_server_id'],custom_config_id=cc)]
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=create)) as provider:
                await provision(self.local,product,1,303,order)
                await provision(self.local,product,1,303,order)
                self.assertEqual(provider.await_count,1)
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),9880)

    def test_representative_specific_supply_rate_and_global_restriction(self):
        pid=self.plan()
        self.root.set_setting(f'volume_supply_price:{self.aid}:{pid}','4')
        local_id,price=quote(self.local,self.root,self.aid,303,10,pid)
        self.assertEqual(price,40)
        self.assertEqual(self.local.get_product(local_id)['managed_buy_price'],40)
        self.root.set_setting(f'volume_native_access:{self.aid}','0')
        self.assertFalse(available(self.local,self.root,self.aid))
        with self.assertRaises(AgencyError):quote(self.local,self.root,self.aid,303,10,pid)

    def test_main_plans_use_the_same_customer_picker(self):
        from managed_volume import start,VolumeFlow,register
        from aiogram import Router
        first=self.plan();second=self.plan()
        state=AsyncMock();message=types.SimpleNamespace(from_user=types.SimpleNamespace(id=303),answer=AsyncMock())
        async def run():
            await start(message,state,self.local)
            state.set_state.assert_awaited_with(VolumeFlow.plan)
        asyncio.run(run())
        buttons=message.answer.call_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual([b.callback_data for row in buttons for b in row if b.callback_data.startswith('volume:native:')],[f'volume:native:{first}',f'volume:native:{second}'])

    def test_tiered_main_plan_keeps_quotes_independent(self):
        pid=self.plan(pricing_mode='tiered')
        with self.root._get_conn() as c:
            c.execute('INSERT INTO custom_config_product_pricing_tiers(product_id,from_gb,to_gb,price_per_gb) VALUES(?,?,?,?)',(pid,5,19,12))
            c.execute('INSERT INTO custom_config_product_pricing_tiers(product_id,from_gb,to_gb,price_per_gb) VALUES(?,?,?,?)',(pid,20,200,8))
        first,total=quote(self.local,self.root,self.aid,303,10,pid)
        second,total2=quote(self.local,self.root,self.aid,303,30,pid)
        self.assertEqual((total,total2),(120,240))
        a=self.local.get_product(first);b=self.local.get_product(second)
        self.assertNotEqual(a['upstream_product_id'],b['upstream_product_id'])
        self.assertEqual(self.root.get_product(a['upstream_product_id'])['reseller_wholesale_price'],12)
        self.assertEqual(self.root.get_product(b['upstream_product_id'])['reseller_wholesale_price'],8)

    def test_legacy_main_volume_settings_also_appear_without_a_new_product(self):
        server=self.root.get_product(self.pid)['provision_server_id']
        with self.root._get_conn() as c:c.execute('UPDATE panel_servers SET used_for_custom_config=1 WHERE id=?',(server,))
        self.root.set_setting('custom_config_enabled','1')
        self.root.set_setting('custom_config_min_gb','7')
        self.root.set_setting('custom_config_max_gb','90')
        self.root.add_pricing_tier(1,None,6)
        sync_bot(self.root,self.aid);self.local._settings_cache=None
        self.assertEqual(self.local.get_setting('custom_config_enabled'),'1')
        self.assertEqual(plans(self.root)[0]['id'],0)
        self.assertEqual(rules(self.local,self.root,self.aid,0)['min_gb'],7)
        with self.assertRaises(AgencyError):quote(self.local,self.root,self.aid,303,6,0)
        pid,total=quote(self.local,self.root,self.aid,303,10,0)
        self.assertEqual(total,60)

    def test_minimum_can_be_saved_from_representative_admin(self):
        from aiogram import Router
        from managed_volume import register
        self.plan()
        router=Router();register(router,self.local,False)
        edit=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='edit_setting')
        save=next(h.callback for h in router.message.handlers if h.callback.__name__=='save_settings')
        state=AsyncMock();state.get_data.return_value={'volume_setting_field':'min'}
        call=types.SimpleNamespace(data='volume:edit:min',from_user=types.SimpleNamespace(id=101),message=types.SimpleNamespace(answer=AsyncMock()),answer=AsyncMock())
        message=types.SimpleNamespace(text='15',from_user=types.SimpleNamespace(id=101),answer=AsyncMock())
        async def run():
            await edit(call,state)
            await save(message,state)
            self.assertEqual(self.local.get_setting('volume_min_gb'),'15')
            message.text='2'
            await save(message,state)
            self.assertEqual(self.local.get_setting('volume_min_gb'),'15')
        asyncio.run(run())

    def test_nested_native_volume_debits_each_wholesale_level(self):
        from pathlib import Path
        from database import Database
        from managed_resellers import bootstrap
        native_id=self.plan()
        with self.local._get_conn() as c:c.execute('INSERT INTO users(telegram_id,referral_credit) VALUES(202,10000)')
        child_id=self.root.register_reseller_bot('123:child','child',202,'child',str(Path(self.temp.name)/'child.db'))
        bootstrap(self.root,child_id,[self.pid],parent_id=self.aid)
        child=Database(self.root.get_reseller_bot(child_id)['db_path'])
        try:
            with child._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
            self.root.set_setting(f'volume_supply_price:{child_id}:{native_id}','15')
            child.set_setting('volume_sell_per_gb','20')
            pid,total=quote(child,self.root,child_id,303,10,native_id)
            order=child.create_order(303,pid,total)
            async def create(db,source,quantity,user_id,order_id):
                name=db.get_order(order_id)['config_name']
                cc=db.add_custom_config(user_id,source['provision_server_id'],name,10,30,'https://example.invalid/sub',order_id=order_id,source='direct_product')
                return [dict(username=name,subscription_url='https://example.invalid/sub',volume_gb=10,duration_days=30,panel_server_id=source['provision_server_id'],custom_config_id=cc)]
            async def run():
                with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=create)):
                    await provision(child,child.get_product(pid),1,303,order)
            asyncio.run(run())
            self.assertEqual(self.root.get_wallet_credit(101),9900)
            self.assertEqual(self.local.get_wallet_credit(202),9850)
        finally:child.close()

    def test_quote_backings_never_appear_as_regular_products(self):
        from database import Database
        from merchant_controls import purchase_allowed
        native_id=self.plan()
        reader=Database(self.root.db_path)
        try:
            expected={p['id'] for p in reader.get_all_products()}
            reader.get_setting('cache_warmup','')
            pid,total=quote(self.local,self.root,self.aid,303,10,native_id)
            source=self.local.get_product(pid)['upstream_product_id']
            self.assertEqual({p['id'] for p in reader.get_all_products()},expected)
            self.assertFalse(purchase_allowed(reader,source,303))
        finally:reader.close()
