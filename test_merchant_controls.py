import asyncio
import json
import types
import unittest
from unittest.mock import AsyncMock,patch
from pathlib import Path
import test_managed_resellers as fixtures
from merchant_controls import set_personal_price,prefix_uri
from managed_volume import configure,quote
from managed_resellers import sync_bot,provision,discover_products,AgencyError

class MerchantTests(unittest.TestCase):
    setUp=fixtures.ManagedAgencyTests.setUp
    tearDown=fixtures.ManagedAgencyTests.tearDown
    local_product=fixtures.ManagedAgencyTests.local_product

    def test_personal_price_is_per_product_user_and_shop(self):
        with self.local._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        p=self.local_product();set_personal_price(self.local,303,p['id'],150)
        self.assertEqual(self.local.get_tier_price_info(303,100,2,p['id'])['total_after'],300)
        self.assertEqual(self.local.get_tier_price_info(304,100,2,p['id'])['total_after'],200)
        self.assertEqual(self.root.get_tier_price_info(303,100,2,self.pid)['total_after'],200)
        with self.assertRaises(ValueError):set_personal_price(self.local,303,p['id'],99)
        set_personal_price(self.local,303,p['id'],None)
        self.assertEqual(self.local.get_tier_price_info(303,100,2,p['id'])['total_after'],200)

    def test_new_catalog_is_unselected_and_notice_is_durable_once(self):
        p=self.root.get_product(self.pid)
        new=self.root.add_product(p['category_id'],'جدید',200,is_auto_provision=True,auto_provision_volume_gb=10,provision_server_id=p['provision_server_id'])
        discover_products(self.root,self.aid);discover_products(self.root,self.aid)
        with self.root._get_conn() as c:
            self.assertEqual(c.execute('SELECT selected FROM managed_catalog WHERE bot_id=? AND product_id=?',(self.aid,new)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM managed_notices WHERE product_id=? AND old_price IS NULL',(new,)).fetchone()[0],1)

    def test_volume_quote_fixed_duration_cost_and_retry(self):
        p=self.root.get_product(self.pid)
        rules=configure(self.root,self.aid,p['provision_server_id'],5)
        self.local.set_setting('volume_sell_per_gb','8')
        with self.local._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        pid,price=quote(self.local,self.root,self.aid,303,10)
        self.assertEqual(price,80)
        product=self.local.get_product(pid)
        self.assertEqual(product['duration_days'],30)
        sync_bot(self.root,self.aid)
        self.assertEqual(self.local.get_product(pid)['auto_provision_volume_gb'],10)
        self.assertNotIn(pid,[p['id'] for p in self.local.get_products(product['category_id'],False)])
        order=self.local.create_order(303,pid,price)
        async def create(db,source,quantity,user_id,order_id):
            self.assertEqual(source['auto_provision_volume_gb'],10)
            self.assertEqual(source['duration_days'],30)
            name=db.get_order(order_id)['config_name'];self.assertTrue(name.startswith('agent-'))
            cc=db.add_custom_config(user_id,source['provision_server_id'],name,10,30,'https://example.invalid/sub',order_id=order_id,source='direct_product')
            return [dict(username=name,subscription_url='https://example.invalid/sub',volume_gb=10,duration_days=30,panel_server_id=source['provision_server_id'],custom_config_id=cc)]
        self.local.set_custom_config_prefix('agent')
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=create)) as mocked:
                await provision(self.local,product,1,303,order)
                await provision(self.local,product,1,303,order)
            self.assertEqual(mocked.await_count,1)
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),9950)
        configure(self.root,self.aid,p['provision_server_id'],5,False)
        with self.assertRaises(AgencyError):quote(self.local,self.root,self.aid,303,10)

    def test_display_prefix_preserves_connection(self):
        from urllib.parse import unquote
        uri='vless://uuid@example.com:443?security=tls#Original'
        result=prefix_uri(uri,'My shop ')
        self.assertEqual(result.split('#')[0],uri.split('#')[0])
        self.assertEqual(unquote(result.split('#')[1]),'My shop Original')
        self.assertEqual(prefix_uri(result,'My shop '),result)

    def test_volume_insufficient_funds_cannot_provision(self):
        source=self.root.get_product(self.pid)
        configure(self.root,self.aid,source['provision_server_id'],5000)
        with self.local._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        pid,price=quote(self.local,self.root,self.aid,303,10)
        product=self.local.get_product(pid);order=self.local.create_order(303,pid,price)
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock()) as provider:
                with self.assertRaises(AgencyError):await provision(self.local,product,1,303,order)
                provider.assert_not_awaited()
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),10000)

    def test_private_volume_quotes_and_templates_are_not_other_users_products(self):
        from merchant_controls import purchase_allowed
        source=self.root.get_product(self.pid)
        data=configure(self.root,self.aid,source['provision_server_id'],5)
        pid,price=quote(self.local,self.root,self.aid,303,10)
        self.assertTrue(purchase_allowed(self.local,pid,303))
        self.assertFalse(purchase_allowed(self.local,pid,404))
        self.assertFalse(purchase_allowed(self.root,data['product_id'],303))
        with self.assertRaises(ValueError):self.local.get_tier_price_info(404,price,1,pid)

    def test_catalog_notifications_reach_all_admins_without_duplicate_retry(self):
        from managed_resellers import reconcile
        self.local.add_admin(505)
        source=self.root.get_product(self.pid)
        pid=self.root.add_product(source['category_id'],'جدید',200,is_auto_provision=True,auto_provision_volume_gb=20,provision_server_id=source['provision_server_id'])
        attempts=[]
        async def send(uid,text,**kwargs):
            attempts.append(uid)
            if uid==505 and attempts.count(505)==1:raise RuntimeError('temporary delivery failure')
        manager=types.SimpleNamespace(instances={'123:parent':{'managed_reseller':True,'bot':types.SimpleNamespace(send_message=AsyncMock(side_effect=send))}})
        async def run():
            await reconcile(self.root,manager)
            await reconcile(self.root,manager)
        asyncio.run(run())
        self.assertEqual(attempts.count(101),1)
        self.assertEqual(attempts.count(505),2)
        with self.root._get_conn() as c:
            self.assertEqual(c.execute('SELECT delivered FROM managed_notices WHERE product_id=?',(pid,)).fetchone()[0],1)

    def test_new_product_selection_requires_retail_price_before_activation(self):
        from managed_reseller_ui import create_agency_router,AgencyFlow
        source=self.root.get_product(self.pid)
        pid=self.root.add_product(source['category_id'],'جدید',200,is_auto_provision=True,auto_provision_volume_gb=20,provision_server_id=source['provision_server_id'])
        discover_products(self.root,self.aid)
        router=create_agency_router(self.local,False)
        callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='agency_callback')
        receiver=next(h.callback for h in router.message.handlers if h.callback.__name__=='select_price_message')
        call=types.SimpleNamespace(data=f'agency:select:{pid}',from_user=types.SimpleNamespace(id=101),message=types.SimpleNamespace(edit_text=AsyncMock(),answer=AsyncMock()),answer=AsyncMock())
        state=AsyncMock();state.get_data.return_value={'agency_source_product':pid}
        message=types.SimpleNamespace(text='250',from_user=types.SimpleNamespace(id=101),answer=AsyncMock())
        async def run():
            await callback(call,state)
            state.set_state.assert_awaited_with(AgencyFlow.select_price)
            with self.root._get_conn() as c:self.assertEqual(c.execute('SELECT selected FROM managed_catalog WHERE bot_id=? AND product_id=?',(self.aid,pid)).fetchone()[0],0)
            await receiver(message,state)
        asyncio.run(run())
        with self.local._get_conn() as c:
            p=c.execute('SELECT price,is_active FROM products WHERE upstream_product_id=?',(pid,)).fetchone()
            self.assertEqual(tuple(p),(250,1))

    def test_support_admin_cannot_change_shop_prices(self):
        from aiogram import Router
        import merchant_controls
        self.local.add_admin(606,'support')
        router=Router();merchant_controls.register(router,self.local)
        callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='merchant_callback')
        call=types.SimpleNamespace(data='merchant:price',from_user=types.SimpleNamespace(id=606),message=types.SimpleNamespace(answer=AsyncMock()),answer=AsyncMock())
        asyncio.run(callback(call,AsyncMock()))
        call.message.answer.assert_not_awaited()
        self.assertTrue(call.answer.call_args.kwargs['show_alert'])
