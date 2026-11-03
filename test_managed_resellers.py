import asyncio
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from database import Database
from managed_resellers import bootstrap, configure, sync_bot, select_product, rate, reserve, refund_reservation, AgencyError


class ManagedAgencyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.worker=patch.dict('sys.modules',{'bulk_gifts':types.SimpleNamespace(ensure_worker=lambda db:None)})
        self.worker.start()
        self.scan=patch.object(Database,'_sync_text_registry',lambda self:None)
        self.scan.start()
        self.root=Database(str(Path(self.temp.name)/'root.db'))
        self.root.init_db(1)
        with self.root._get_conn() as conn:
            conn.execute('INSERT INTO users(telegram_id,referral_credit) VALUES(101,10000)')
        category=self.root.add_category('اروپا')
        server=self.root.add_panel_server('source','3xui','https://example.invalid','user','secret')
        self.pid=self.root.add_product(category,'سرویس',100,description='root',duration_days=30,is_auto_provision=True,auto_provision_volume_gb=10,provision_server_id=server)
        self.aid=self.root.register_reseller_bot('123:parent','parent',101,'parent',str(Path(self.temp.name)/'parent.db'))
        bootstrap(self.root,self.aid,[self.pid])
        self.local=Database(self.root.get_reseller_bot(self.aid)['db_path'])

    def tearDown(self):
        self.local.close();self.root.close()
        self.scan.stop();self.worker.stop();self.temp.cleanup()

    def local_product(self):
        with self.local._get_conn() as conn:return dict(conn.execute('SELECT * FROM products WHERE upstream_product_id=?',(self.pid,)).fetchone())

    def test_selected_product_is_imported_without_panel_secrets(self):
        product=self.local_product()
        self.assertEqual(product['duration_days'],30)
        self.assertEqual(product['managed_buy_price'],100)
        server=self.local.get_panel_server(product['provision_server_id'])
        self.assertFalse(server['api_password'])
        self.assertTrue(server['managed_source_server_id'])

    def test_only_two_tiers_are_offered(self):
        self.assertEqual({r['code'] for r in self.root.list_reseller_tiers(True)},{'gold','silver'})

    def test_reseller_cannot_add_own_panel_or_product(self):
        with self.assertRaises(ValueError):self.local.add_panel_server('bad','3xui','https://bad.invalid','x','x')
        with self.assertRaises(ValueError):self.local.add_product(1,'bad',1)
        self.assertFalse(self.local.is_full_access_bot(False))

    def test_retail_price_description_and_status_survive_sync(self):
        product=self.local_product()
        self.local.edit_product(product['id'],price=150,description='mine')
        self.local.toggle_product(product['id'])
        sync_bot(self.root,self.aid);sync_bot(self.root,self.aid)
        updated=self.local_product()
        self.assertEqual(updated['price'],150)
        self.assertEqual(updated['description'],'mine')
        self.assertEqual(updated['is_active'],0)

    def test_technical_changes_and_below_cost_price_rejected(self):
        product=self.local_product()
        with self.assertRaises(ValueError):self.local.edit_product(product['id'],price=99)
        with self.assertRaises(ValueError):self.local.edit_product(product['id'],duration_days=90)

    def test_upstream_price_updates_floor_and_creates_notice(self):
        self.root.edit_product(self.pid,price=200)
        sync_bot(self.root,self.aid)
        self.assertEqual(self.local_product()['managed_buy_price'],200)
        self.assertGreaterEqual(self.local_product()['price'],200)
        with self.root._get_conn() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM managed_notices').fetchone()[0],1)

    def test_root_can_revoke_product_and_new_sales_fail_immediately(self):
        configure(self.root,self.aid,self.pid,allowed=False)
        self.assertEqual(self.local_product()['is_active'],0)
        with self.assertRaises(AgencyError):rate(self.root,self.aid,self.pid)

    def test_reserve_is_idempotent_and_refund_is_idempotent(self):
        first=reserve(self.root,self.aid,5,self.pid,2)
        self.assertEqual(reserve(self.root,self.aid,5,self.pid,2),first)
        self.assertEqual(self.root.get_wallet_credit(101),9800)
        self.assertTrue(refund_reservation(self.root,self.aid,5))
        self.assertFalse(refund_reservation(self.root,self.aid,5))
        self.assertEqual(self.root.get_wallet_credit(101),10000)
        self.assertEqual(reserve(self.root,self.aid,5,self.pid,2),first)
        self.assertEqual(self.root.get_wallet_credit(101),9800)

    def child(self):
        with self.local._get_conn() as conn:conn.execute('INSERT INTO users(telegram_id,referral_credit) VALUES(202,1000)')
        bid=self.root.register_reseller_bot('123:child','child',202,'child',str(Path(self.temp.name)/'child.db'))
        bootstrap(self.root,bid,[self.pid],parent_id=self.aid)
        return bid

    def test_child_rate_has_parent_purchase_floor(self):
        child=self.child()
        with self.assertRaises(AgencyError):configure(self.root,child,self.pid,buy_price=99,actor_bot_id=self.aid)
        configure(self.root,child,self.pid,buy_price=150,actor_bot_id=self.aid)
        self.assertEqual(rate(self.root,child,self.pid),150)

    def test_nested_sale_debits_both_levels_exactly_once(self):
        child=self.child()
        configure(self.root,child,self.pid,buy_price=150,actor_bot_id=self.aid)
        reserve(self.root,child,9,self.pid,2)
        reserve(self.root,child,9,self.pid,2)
        self.assertEqual(self.root.get_wallet_credit(101),9800)
        self.assertEqual(self.local.get_wallet_credit(202),700)

    def test_insufficient_child_funds_rolls_back_root_debit(self):
        child=self.child()
        with self.local._get_conn() as conn:conn.execute('UPDATE users SET referral_credit=1 WHERE telegram_id=202')
        with self.assertRaises(AgencyError):reserve(self.root,child,9,self.pid,1)
        self.assertEqual(self.root.get_wallet_credit(101),10000)
        self.assertEqual(self.local.get_wallet_credit(202),1)

    def test_root_revocation_propagates_to_child(self):
        child=self.child()
        configure(self.root,self.aid,self.pid,allowed=False)
        with self.assertRaises(AgencyError):rate(self.root,child,self.pid)

    def test_cycle_is_rejected(self):
        child=self.child()
        with self.assertRaises(AgencyError):bootstrap(self.root,self.aid,[],parent_id=child)

    def test_prepaid_inventory_is_used_before_wallet_and_restored_on_failure(self):
        self.root.set_reseller_product_credit(101,self.pid,3)
        reserve(self.root,self.aid,15,self.pid,2)
        self.assertEqual(self.root.get_wallet_credit(101),10000)
        self.assertEqual(self.root.get_reseller_product_credit(101,self.pid),1)
        self.assertTrue(refund_reservation(self.root,self.aid,15))
        self.assertEqual(self.root.get_reseller_product_credit(101,self.pid),3)
        self.assertFalse(refund_reservation(self.root,self.aid,15))
        self.assertEqual(self.root.get_wallet_credit(101),10000)

    def test_new_products_are_discovered_unselected_and_denials_are_preserved(self):
        from managed_resellers import discover_products
        source=self.root.get_product(self.pid)
        new=self.root.add_product(source['category_id'],'جدید',120,is_auto_provision=True,auto_provision_volume_gb=5,provision_server_id=source['provision_server_id'])
        configure(self.root,self.aid,self.pid,allowed=False)
        discover_products(self.root,self.aid)
        with self.root._get_conn() as conn:
            offer=conn.execute('SELECT allowed,selected FROM managed_catalog WHERE bot_id=? AND product_id=?',(self.aid,new)).fetchone()
            self.assertEqual(tuple(offer),(1,0))
        with self.assertRaises(AgencyError):rate(self.root,self.aid,self.pid)
        with self.assertRaises(AgencyError):rate(self.root,self.aid,new)
        select_product(self.root,self.aid,new,True)
        self.assertEqual(rate(self.root,self.aid,new),120)

    def test_parent_deselection_cannot_be_bypassed_by_child(self):
        child=self.child()
        select_product(self.root,self.aid,self.pid,False)
        with self.assertRaises(AgencyError):select_product(self.root,child,self.pid,True)

    def test_discount_agency_cannot_sell_below_purchase_cost(self):
        from managed_resellers import assign_discount
        with self.local._get_conn() as conn:conn.execute('INSERT INTO users(telegram_id) VALUES(303)')
        product=self.local_product()
        self.local.edit_product(product['id'],price=150)
        with self.assertRaises(AgencyError):assign_discount(self.local,303,50)
        assign_discount(self.local,303,20)
        self.root.edit_product(self.pid,price=200)
        sync_bot(self.root,self.aid)
        fresh=self.local_product()
        info=self.local.get_tier_price_info(303,fresh['price'],1)
        self.assertGreaterEqual(info['total_after'],200)

    def test_managed_products_cannot_be_deleted_or_reconfigured(self):
        product=self.local_product()
        with self.assertRaises(ValueError):self.local.delete_product(product['id'])
        with self.assertRaises(ValueError):self.local.delete_category(product['category_id'])
        with self.assertRaises(ValueError):self.local.set_product_user_limit(product['id'],1,50)

    def test_reusing_order_for_different_product_is_rejected(self):
        source=self.root.get_product(self.pid)
        new=self.root.add_product(source['category_id'],'دیگر',200,is_auto_provision=True,auto_provision_volume_gb=5,provision_server_id=source['provision_server_id'])
        configure(self.root,self.aid,new)
        reserve(self.root,self.aid,17,self.pid,1)
        with self.assertRaises(AgencyError):reserve(self.root,self.aid,17,new,1)
        self.assertEqual(self.root.get_wallet_credit(101),9900)

    def test_actual_supply_mirrors_service_and_retries_without_double_debit(self):
        from managed_resellers import provision
        import selectors
        original=selectors.EpollSelector.select
        selectors.EpollSelector.select=lambda self,timeout=None:original(self,0.05 if timeout is None else min(timeout,0.05))
        try:
            with self.local._get_conn() as conn:conn.execute('INSERT INTO users(telegram_id) VALUES(303)')
            product=self.local_product()
            order=self.local.create_order(303,product['id'],100)
            async def create(db,source,quantity,user_id,order_id):
                with db._get_conn() as conn:saved=conn.execute('SELECT id FROM custom_configs WHERE order_id=?',(order_id,)).fetchone()
                config_id=saved[0] if saved else db.add_custom_config(user_id,source['provision_server_id'],'client',10,30,'https://example.invalid/sub',order_id=order_id,source='direct_product')
                return [dict(username='client',subscription_url='https://example.invalid/sub',volume_gb=10,duration_days=30,panel_server_id=source['provision_server_id'],custom_config_id=config_id)]
            async def run():
                with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=create)):
                    first=await provision(self.local,product,1,303,order)
                    second=await provision(self.local,product,1,303,order)
                self.assertEqual(first[0]['custom_config_id'],second[0]['custom_config_id'])
            asyncio.run(run())
            self.assertEqual(self.root.get_wallet_credit(101),9900)
            configs=self.local.get_custom_configs_for_user(303)
            self.assertEqual(len(configs),1)
            self.assertTrue(configs[0]['upstream_config_id'])
        finally:
            selectors.EpollSelector.select=original

    def test_custom_volume_label_aliases_are_consistent(self):
        self.root.set_setting('btn_custom_config','حجم انتخابی من')
        self.assertEqual(self.root.get_setting('btn_custom_config_text'),'حجم انتخابی من')
        self.root.set_setting('btn_custom_config_text','ترافیک دلخواه')
        self.assertEqual(self.root.get_setting('btn_custom_config'),'ترافیک دلخواه')
        sync_bot(self.root,self.aid)
        self.assertEqual(self.local.get_setting('btn_custom_config_text'),'ترافیک دلخواه')

    def test_parent_cannot_manage_unrelated_reseller(self):
        other=self.root.register_reseller_bot('123:other','other',404,'other',str(Path(self.temp.name)/'other.db'))
        bootstrap(self.root,other,[self.pid])
        with self.assertRaises(AgencyError):configure(self.root,other,self.pid,allowed=False,actor_bot_id=self.aid)

    def test_fractional_supply_uses_exact_rounding_and_no_prepaid_units(self):
        from fractions import Fraction
        self.root.set_reseller_product_credit(101,self.pid,3)
        reserve(self.root,self.aid,19,self.pid,1,Fraction(1,3))
        self.assertEqual(self.root.get_wallet_credit(101),9966)
        self.assertEqual(self.root.get_reseller_product_credit(101,self.pid),3)

    def test_extra_user_supply_fee_is_charged_in_addition_to_prepaid_base(self):
        from fractions import Fraction
        self.root.set_reseller_product_credit(101,self.pid,1)
        reserve(self.root,self.aid,20,self.pid,1,Fraction(1,1),50)
        self.assertEqual(self.root.get_wallet_credit(101),9950)
        self.assertEqual(self.root.get_reseller_product_credit(101,self.pid),0)
        refund_reservation(self.root,self.aid,20)
        self.assertEqual(self.root.get_wallet_credit(101),10000)
        self.assertEqual(self.root.get_reseller_product_credit(101,self.pid),1)

    def test_agency_ui_limits_parent_transaction_view(self):
        from managed_reseller_ui import create_agency_router
        other=self.root.register_reseller_bot('123:outside','outside',404,'outside',str(Path(self.temp.name)/'outside.db'))
        with self.root._get_conn() as conn:
            conn.execute("INSERT INTO managed_purchases(request_key,bot_id,local_order_id,root_order_id,charges,status) VALUES('outside',?,991,991,'[]','applying')",(other,))
        reserve(self.root,self.aid,22,self.pid,1)
        router=create_agency_router(self.local,False)
        callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='agency_callback')
        call=types.SimpleNamespace(data='agency:transactions',from_user=types.SimpleNamespace(id=101),message=types.SimpleNamespace(edit_text=AsyncMock(),answer=AsyncMock()),answer=AsyncMock())
        asyncio.run(callback(call,AsyncMock()))
        text=call.message.edit_text.call_args.args[0]
        self.assertIn('22',text)
        self.assertNotIn('991',text)
        call.from_user.id=909
        call.message.edit_text.reset_mock()
        asyncio.run(callback(call,AsyncMock()))
        call.message.edit_text.assert_not_called()

    def test_completed_renewal_retry_ignores_later_product_revocation(self):
        from managed_resellers import renew_service
        with self.local._get_conn() as conn:conn.execute('INSERT INTO users(telegram_id) VALUES(303)')
        purchase=reserve(self.root,self.aid,23,self.pid,1)
        source=self.root.get_product(self.pid)
        upstream=self.root.add_custom_config(-self.aid,source['provision_server_id'],'renewed',20,60,'https://example.invalid/sub',order_id=purchase,source='direct_product')
        local_product=self.local_product()
        local_cc=self.local.add_custom_config(303,local_product['provision_server_id'],'renewed',10,30,'https://example.invalid/sub',source='direct_product')
        with self.local._get_conn() as conn:conn.execute('UPDATE custom_configs SET upstream_config_id=? WHERE id=?',(upstream,local_cc))
        with self.root._get_conn() as conn:
            conn.execute("UPDATE managed_purchases SET status='completed' WHERE request_key=?",(f'{self.aid}:23',))
        configure(self.root,self.aid,self.pid,allowed=False)
        order={'id':23,'renewal_target_id':local_cc,'user_id':303}
        result=asyncio.run(renew_service(self.local,order))
        self.assertIn('قبلاً',result)
        self.assertEqual(self.local.get_custom_config_by_id(local_cc)['volume_gb'],20)
        self.assertEqual(self.root.get_wallet_credit(101),9900)

    def test_grants_preserve_negotiated_rate_and_owner_selection(self):
        configure(self.root,self.aid,self.pid,buy_price=150)
        select_product(self.root,self.aid,self.pid,False)
        configure(self.root,self.aid,self.pid,buy_price=160)
        with self.root._get_conn() as conn:
            row=conn.execute('SELECT * FROM managed_catalog WHERE bot_id=? AND product_id=?',(self.aid,self.pid)).fetchone()
        self.assertFalse(row['selected'])
        configure(self.root,self.aid,self.pid,allowed=False)
        configure(self.root,self.aid,self.pid,allowed=True)
        self.assertEqual(rate(self.root,self.aid,self.pid,require_selected=False),160)

    def test_new_gold_imports_all_products_despite_prepaid_selection(self):
        category=self.root.add_category('آسیا')
        source=self.root.get_product(self.pid)
        new=self.root.add_product(category,'آسیا',120,is_auto_provision=True,auto_provision_volume_gb=5,provision_server_id=source['provision_server_id'])
        self.root.set_reseller_product_credit(101,self.pid,1)
        self.root.set_setting('reseller_fixed_product_ids',str(self.pid))
        bid=self.root.register_reseller_bot('123:all','all',101,'all',str(Path(self.temp.name)/'all.db'))
        bootstrap(self.root,bid)
        local=Database(self.root.get_reseller_bot(bid)['db_path'])
        try:
            with local._get_conn() as conn:
                rows=conn.execute('SELECT p.upstream_product_id,p.is_active,c.name FROM products p JOIN categories c ON c.id=p.category_id WHERE upstream_product_id IS NOT NULL').fetchall()
            self.assertEqual({r[0] for r in rows},{self.pid,new})
            self.assertTrue(all(r[1] for r in rows))
            self.assertEqual({r[2] for r in rows},{'اروپا','آسیا'})
            select_product(self.root,bid,new,False)
            bootstrap(self.root,bid)
            with local._get_conn() as conn:
                self.assertEqual(conn.execute('SELECT is_active FROM products WHERE upstream_product_id=?',(new,)).fetchone()[0],0)
        finally:local.close()

    def test_child_automatically_inherits_all_available_parent_products(self):
        source=self.root.get_product(self.pid)
        new=self.root.add_product(source['category_id'],'جدید',120,is_auto_provision=True,auto_provision_volume_gb=5,provision_server_id=source['provision_server_id'])
        configure(self.root,self.aid,new)
        child=self.root.register_reseller_bot('123:autochild','autochild',202,'child',str(Path(self.temp.name)/'autochild.db'))
        bootstrap(self.root,child,parent_id=self.aid)
        self.assertEqual(rate(self.root,child,new),120)
        self.assertEqual(rate(self.root,child,self.pid),100)

    def test_specific_reseller_price_does_not_change_sibling(self):
        other=self.root.register_reseller_bot('123:sibling','sibling',404,'other',str(Path(self.temp.name)/'sibling.db'))
        bootstrap(self.root,other)
        configure(self.root,self.aid,self.pid,buy_price=150)
        self.assertEqual(rate(self.root,self.aid,self.pid),150)
        self.assertEqual(rate(self.root,other,self.pid),100)
        self.assertEqual(self.root.get_product(self.pid)['price'],100)
        first=self.child()
        second=self.root.register_reseller_bot('123:secondchild','secondchild',505,'second',str(Path(self.temp.name)/'secondchild.db'))
        bootstrap(self.root,second,parent_id=self.aid)
        configure(self.root,first,self.pid,buy_price=190,actor_bot_id=self.aid)
        self.assertEqual(rate(self.root,first,self.pid),190)
        self.assertEqual(rate(self.root,second,self.pid),150)

    def test_invalid_quantities_do_not_debit(self):
        for quantity in (0,-1,True,1.5):
            with self.assertRaises(AgencyError):reserve(self.root,self.aid,25,self.pid,quantity)
        self.assertEqual(self.root.get_wallet_credit(101),10000)


if __name__=='__main__':unittest.main()
