import asyncio
import ast
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import test_managed_resellers as fixtures
from database import Database
from user_financials import count_orders, order_cards, history_text


class UserFinancialTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ManagedAgencyTests()
        self.fixture.setUp()
        self.root, self.local = self.fixture.root, self.fixture.local
        with self.local._get_conn() as conn:
            conn.execute("INSERT INTO users(telegram_id,first_name) VALUES(303,'<customer>')")
            conn.execute("INSERT INTO users(telegram_id) VALUES(404)")

    def tearDown(self):
        self.fixture.tearDown()

    def order(self, db=None, user=303, status='approved', price=100, product=None, **kwargs):
        db = db or self.local
        if product is None:
            product = self.fixture.local_product()['id'] if db is self.local else self.fixture.pid
        oid = db.create_order(user, product, price, **kwargs)
        with db._get_conn() as conn:
            conn.execute('UPDATE orders SET status=? WHERE id=?', (status,oid))
        return oid

    def test_personal_and_shop_orders_are_separate_and_count_orders_not_units(self):
        self.order(self.root,101,quantity=3)
        self.order(self.local,303,quantity=5)
        self.order(self.local,404,status='pending')
        stats = self.root.get_user_full_stats(101)
        self.assertEqual(stats['order_counts']['total'],1)
        self.assertEqual(stats['shop_order_summary']['total'],2)
        self.assertEqual(stats['shop_order_summary']['approved'],1)
        cards = order_cards(stats)
        self.assertEqual(cards.count('<blockquote>'),2)
        self.assertIn('سفارش‌های شخصی',cards)
        self.assertIn('سفارش‌های بات',cards)

    def test_personal_counts_include_renewals_deleted_products_and_wallet_payments(self):
        legacy = self.order(price=200,wallet_used=180,discount_amount=20)
        with self.local._get_conn() as conn:
            conn.execute('UPDATE orders SET base_price=NULL WHERE id=?',(legacy,))
        self.order(product=999999,price=300)
        oid = self.local.create_renewal_order(303,'custom',99,'full',10,30,50)
        self.local.approve_renewal_order(oid)
        stats = self.local.get_user_full_stats(303)
        self.assertEqual(stats['approved_orders'],3)
        self.assertEqual(stats['total_spent'],530)

    def test_internal_supply_and_trials_are_excluded(self):
        normal = self.order()
        internal = self.order()
        trial = self.order()
        with self.local._get_conn() as conn:
            conn.execute('UPDATE orders SET managed_internal=1 WHERE id=?',(internal,))
            conn.execute("INSERT INTO managed_trials(user_id,order_id,policy,status) VALUES(303,?,'{}','completed')",(trial,))
        # Old failed attempts may no longer be the user's current trial row.
        from managed_trials import _billing_product
        billing = _billing_product(self.local)
        self.order(product=billing,status='rejected',price=0)
        delivered_trial = self.order(price=0)
        self.local.add_custom_config(303,self.fixture.local_product()['provision_server_id'],
                                     'old_trial',1,1,'https://example.invalid/test',order_id=delivered_trial,source='test')
        stats = self.local.get_user_full_stats(303)
        self.assertEqual(stats['order_counts']['total'],1)
        rows = self.local.get_user_financial_history(303,kind='order')['items']
        self.assertEqual([r['id'] for r in rows],[normal])

    def test_shop_access_includes_direct_child_but_excludes_sibling(self):
        child = self.fixture.child()
        self.order(self.local,202)
        summary = self.local.get_owned_shop_order_summary(202)
        self.assertEqual([s['id'] for s in summary['shops']],[child])
        self.assertEqual(summary['total'],0)
        # The root owner of another top-level shop must not expose it here.
        sibling = self.root.register_reseller_bot('123:other','sibling',202,'other',str(Path(self.fixture.temp.name)/'sibling.db'))
        from managed_resellers import bootstrap
        bootstrap(self.root,sibling,[self.fixture.pid])
        summary = self.local.get_owned_shop_order_summary(202)
        self.assertEqual([s['id'] for s in summary['shops']],[child])
        self.assertEqual(len(self.root.get_owned_shop_order_summary(202)['shops']),2)

    def test_missing_shop_database_is_reported_without_creating_file(self):
        path = Path(self.fixture.temp.name)/'missing.db'
        self.root.register_reseller_bot('123:missing','missing',101,'missing',str(path))
        stats = self.root.get_user_full_stats(101)
        self.assertEqual(stats['shop_order_summary']['unavailable'],1)
        self.assertFalse(path.exists())
        self.assertIn('آمار کامل نیست',order_cards(stats))

    def test_history_includes_orders_topups_and_wallet_balance_changes(self):
        self.order(wallet_used=25,discount_amount=10)
        self.local.create_topup(303,70)
        self.local.add_wallet_credit(303,500,'admin_adjust','<script>note</script>')
        history = self.local.get_user_financial_history(303)
        self.assertEqual({r['event_kind'] for r in history['items']},{'order','topup','wallet'})
        wallet = next(r for r in history['items'] if r['event_kind']=='wallet')
        self.assertEqual(wallet['amount'],500)
        self.assertEqual(wallet['balance_after']-wallet['balance_before'],500)
        text = history_text(303,history)
        self.assertIn('&lt;script&gt;',text)
        self.assertNotIn('<script>',text)
        self.assertIn('قبل',text)

    def test_history_filters_and_pagination_have_no_duplicates(self):
        for _ in range(14):
            self.order()
        self.order(user=404)
        seen=[]
        for page in range(3):
            data=self.local.get_user_financial_history(303,page,'order',6)
            self.assertEqual(data['total'],14)
            seen.extend(r['id'] for r in data['items'])
        self.assertEqual(len(set(seen)),14)
        self.assertEqual(len(seen),14)
        data=self.local.get_user_financial_history(303,999999,'order',6)
        self.assertEqual(data['page'],2)
        self.assertEqual(len(data['items']),2)
        self.assertEqual(self.local.get_user_financial_history(303,kind='topup')['total'],0)
        with self.assertRaises(ValueError):
            self.local.get_user_financial_history(303,kind='invalid')

    def test_status_breakdown_and_empty_user(self):
        for status in ['approved','pending','processing','rejected','expired']:
            self.order(status=status)
        counts=self.local.get_user_full_stats(303)['order_counts']
        self.assertEqual((counts['total'],counts['approved'],counts['pending'],counts['rejected'],counts['expired']),(5,1,2,1,1))
        stats=self.local.get_user_full_stats(404)
        self.assertEqual(stats['order_counts']['total'],0)
        self.assertEqual(order_cards(stats).count('<blockquote>'),1)

    def router(self, main=False):
        from handlers_admin import create_admin_router
        return create_admin_router(self.root if main else self.local,main)

    def call(self, actor=101, data='adm_user_tx:303:all:0'):
        return types.SimpleNamespace(data=data,from_user=types.SimpleNamespace(id=actor),
            message=types.SimpleNamespace(text='profile',edit_text=AsyncMock(),answer=AsyncMock(),delete=AsyncMock()),answer=AsyncMock())

    def test_history_button_exists_in_private_and_report_profiles(self):
        import keyboards
        for fn in [keyboards.user_full_stats_kb,keyboards.user_group_stats_kb]:
            buttons=[b.callback_data for row in fn(303).inline_keyboard for b in row]
            self.assertIn('adm_user_tx:303:all:0',buttons)

    def test_reseller_admin_can_read_local_transactions_and_navigate(self):
        for _ in range(8):
            self.order()
        router=self.router()
        cb=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='cb_admin_user_transactions')
        call=self.call()
        asyncio.run(cb(call))
        text=call.message.edit_text.call_args.args[0]
        self.assertIn('8 رکورد',text)
        buttons=[b.callback_data for row in call.message.edit_text.call_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertIn('adm_user_tx:303:all:1',buttons)

    def test_history_rejects_unauthorized_actor_and_invalid_callbacks(self):
        router=self.router()
        cb=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='cb_admin_user_transactions')
        for actor,data in [(404,'adm_user_tx:303:all:0'),(101,'adm_user_tx:303:all:-1'),(101,'adm_user_tx:303:invalid:0'),(101,'adm_user_tx:999:all:0')]:
            call=self.call(actor,data)
            asyncio.run(cb(call))
            call.message.edit_text.assert_not_awaited()
            call.message.answer.assert_not_awaited()
            call.answer.assert_awaited()

    def test_main_admin_has_the_same_history_route(self):
        self.order(self.root,101)
        cb=next(h.callback for h in self.router(True).callback_query.handlers if h.callback.__name__=='cb_admin_user_transactions')
        call=self.call(1,'adm_user_tx:101:order:0')
        asyncio.run(cb(call))
        self.assertIn('سفارش #',call.message.edit_text.call_args.args[0])

    def test_profile_escapes_names_and_has_two_distinct_order_cards(self):
        cb=next(h.callback for h in self.router(True).callback_query.handlers if h.callback.__name__=='cb_quick_action_profile')
        call=self.call(1,'qa_profile:101')
        call.message.reply=AsyncMock()
        with self.root._get_conn() as conn:
            conn.execute("UPDATE users SET first_name='<owner>' WHERE telegram_id=101")
        asyncio.run(cb(call))
        text=call.message.reply.call_args.args[0]
        self.assertIn('&lt;owner&gt;',text)
        self.assertEqual(text.count('<blockquote>'),2)

    def test_web_history_route_enforces_permissions_and_tenant_scope(self):
        # Register the real routes/dependency in an isolated ASGI app, without
        # importing the production server and initializing operational files.
        import httpx
        from fastapi import FastAPI, Request, Depends, Query, HTTPException
        source=ast.parse((Path(__file__).parent/'admin_panel/server.py').read_text())
        names={'require_permission','api_user_detail','api_user_transactions'}
        nodes=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in names]
        app=FastAPI()
        async def auth(request: Request):
            role=request.headers.get('x-test-role')
            if not role:
                raise HTTPException(401,'login required')
            return {'role':role,'permissions':[],'tenant':'local'}
        ns=dict(app=app,db=self.local,Depends=Depends,Query=Query,HTTPException=HTTPException,
                get_current_admin=auth,tr=lambda s:s,MAIN_TENANT_ONLY_PERMISSIONS={'resellers'},
                TENANT_BLOCKED_PERMISSIONS={'resellers'},rows_to_list=lambda rows:[dict(r) for r in rows])
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'isolated_admin_routes','exec'),ns)
        self.order()
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                url='/api/users/303/transactions'
                self.assertEqual((await client.get(url)).status_code,401)
                self.assertEqual((await client.get(url,headers={'x-test-role':'support'})).status_code,403)
                headers={'x-test-role':'owner'}
                response=await client.get(url+'?kind=order',headers=headers)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['total'],1)
                profile=(await client.get('/api/users/303',headers=headers)).json()
                self.assertEqual(profile['order_counts']['total'],1)
                self.assertIn('shop_order_summary',profile)
                self.assertEqual((await client.get(url+'?kind=invalid',headers=headers)).status_code,400)
                self.assertEqual((await client.get(url+'?page=-1',headers=headers)).status_code,422)
                ns['db']=self.root
                self.assertEqual((await client.get(url,headers=headers)).status_code,404)
        asyncio.run(run())
