import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from payment_delivery import finalize_paid_order, finalize_paid_topup

class OnlinePaymentReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_managed_resellers import ManagedAgencyTests
        self.fixture=ManagedAgencyTests();self.fixture.setUp()
        self.db=self.fixture.root
        cat=self.db.add_category('report test')
        self.pid=self.db.add_product(cat,'خرید',100)
        self.db.add_configs(self.pid,['vless://config'])
        self.bot=AsyncMock()
        self.bot.token='123:test'

    def tearDown(self):self.fixture.tearDown()

    async def test_successful_order_without_notifier_reports_once_even_on_retry(self):
        oid=self.db.create_order(101,self.pid,100)
        with patch('online_payment_reports.report_router.report',new=AsyncMock(return_value=True)) as report, \
             patch('payment_delivery.deliver_config_to_user',new=AsyncMock()), \
             patch('payment_delivery.check_and_notify_low_stock',new=AsyncMock()):
            await finalize_paid_order(self.db,self.bot,oid)
            await finalize_paid_order(self.db,self.bot,oid)
        report.assert_awaited_once()
        self.assertEqual(report.await_args.args[2],'purchase')
        self.assertIn(f'سفارش #{oid}',report.await_args.args[3])
        self.assertIn('100 تومان',report.await_args.args[3])
        self.assertEqual(self.db.get_order(oid)['status'],'approved')

    async def test_existing_notifier_is_used_without_duplicate_report(self):
        oid=self.db.create_order(101,self.pid,100)
        notifier=AsyncMock()
        with patch('online_payment_reports.report_router.report',new=AsyncMock()) as report, \
             patch('payment_delivery.deliver_config_to_user',new=AsyncMock()), \
             patch('payment_delivery.check_and_notify_low_stock',new=AsyncMock()):
            await finalize_paid_order(self.db,self.bot,oid,notify_admins_fn=notifier)
        notifier.assert_awaited_once_with(self.bot,oid)
        report.assert_not_awaited()

    async def test_paid_order_with_no_stock_reports_failure_and_stays_pending(self):
        oid=self.db.create_order(101,self.pid,100,quantity=2)
        with patch('online_payment_reports.report_router.report',new=AsyncMock(return_value=True)) as report:
            result=await finalize_paid_order(self.db,self.bot,oid)
        self.assertTrue(result.startswith('⚠️'))
        report.assert_awaited_once()
        self.assertEqual(report.await_args.args[2],'gateway')
        self.assertEqual(self.db.get_order(oid)['status'],'pending')

    async def test_online_topup_reports_once_and_is_not_credited_twice(self):
        topup=self.db.create_topup(101,500)
        with patch('online_payment_reports.report_router.report',new=AsyncMock(return_value=True)) as report:
            await finalize_paid_topup(self.db,topup,bot=self.bot,payment_method='blupal')
            await finalize_paid_topup(self.db,topup,bot=self.bot,payment_method='blupal')
        report.assert_awaited_once()
        self.assertEqual(report.await_args.args[2],'finance')
        self.assertIn('blupal',report.await_args.args[3])
        self.assertEqual(self.db.get_wallet_credit(101),10500)

    async def test_reporting_failure_does_not_undo_successful_topup(self):
        topup=self.db.create_topup(101,500)
        with patch('online_payment_reports.report_router.report',new=AsyncMock(side_effect=RuntimeError('offline'))):
            result=await finalize_paid_topup(self.db,topup,bot=self.bot)
        self.assertTrue(result.startswith('✅'))
        self.assertEqual(self.db.get_wallet_credit(101),10500)
        self.assertEqual(self.db.get_topup(topup)['status'],'approved')

    async def test_extra_gateway_reseller_reports_once_after_atomic_completion(self):
        from extra_gateway_payment import _finalize_reseller
        db=MagicMock()
        db.complete_reseller_request_gateway_payment.side_effect=[True,False]
        db.get_reseller_request.return_value={'user_id':42,'tier_code':None,'status':'completed'}
        invoice={'ref_id':3,'user_id':42,'amount_toman':900,'gateway':'zarinpal'}
        with patch('online_payment_reports.report_router.report',new=AsyncMock(return_value=True)) as report:
            await _finalize_reseller(db,self.bot,invoice)
            await _finalize_reseller(db,self.bot,invoice)
        report.assert_awaited_once()
        self.assertEqual(report.await_args.args[2],'reseller')
        self.assertIn('900 تومان',report.await_args.args[3])

    async def test_payment_exception_reports_without_changing_approved_order(self):
        oid=self.db.create_order(101,self.pid,100)
        with patch('online_payment_reports.report_router.report',new=AsyncMock(return_value=True)) as report, \
             patch('payment_delivery.deliver_config_to_user',new=AsyncMock(side_effect=RuntimeError('offline'))), \
             patch('payment_delivery.check_and_notify_low_stock',new=AsyncMock()):
            with self.assertRaises(RuntimeError):await finalize_paid_order(self.db,self.bot,oid)
        report.assert_awaited_once()
        self.assertEqual(report.await_args.args[2],'gateway')
        self.assertEqual(self.db.get_order(oid)['status'],'approved')

    async def test_failed_extra_gateway_fulfillment_reports_and_keeps_retry(self):
        import extra_gateway_payment as egp
        db=MagicMock()
        invoice={'id':7,'kind':'order','ref_id':3,'user_id':42,'gateway':'zarinpal'}
        with patch.object(egp,'check_invoice',new=AsyncMock(return_value='verified_now')), \
             patch.object(egp,'deliver',new=AsyncMock(side_effect=RuntimeError('offline'))), \
             patch.object(egp,'_notify_admins',new=AsyncMock()) as notify:
            status,_=await egp.process_invoice(db,self.bot,invoice)
        self.assertTrue(status.startswith('error:'))
        db.release_extra_invoice.assert_called_once_with(7)
        notify.assert_awaited_once()
        self.assertIn('تأیید شد',notify.await_args.args[2])

    async def test_miniapp_reseller_completion_reports_once(self):
        import miniapp.server as server
        db=MagicMock()
        db.complete_reseller_request_gateway_payment.side_effect=[True,False]
        db.get_reseller_request.return_value={'user_id':42,'tier_code':None,'status':'completed'}
        invoice={'ref_id':3,'user_id':42,'amount_toman':900}
        tenant=SimpleNamespace(bot_token='fake')
        session=MagicMock()
        session.__aenter__=AsyncMock(return_value=session)
        session.__aexit__=AsyncMock(return_value=None)
        session.post=AsyncMock()
        with patch('online_payment_reports.report_router.report_raw',new=AsyncMock(return_value=True)) as report, \
             patch('miniapp.server.aiohttp.ClientSession',return_value=session):
            self.assertTrue(await server._complete_reseller_gateway_payment(db,tenant,invoice))
            self.assertFalse(await server._complete_reseller_gateway_payment(db,tenant,invoice))
        report.assert_awaited_once()
        self.assertEqual(report.await_args.args[2],'reseller')
        self.assertIn('900 تومان',report.await_args.args[3])

    async def test_extra_gateway_does_not_add_second_fulfillment_failure_report(self):
        import extra_gateway_payment as egp
        invoice={'ref_id':3,'user_id':42,'amount_toman':900,'gateway':'zarinpal','kind':'order'}
        db=MagicMock()
        db.get_order.return_value={'status':'pending'}
        with patch.object(egp.abangateway_payment,'finalize_paid_order',new=AsyncMock(return_value='⚠️ failure')) as finalize, \
             patch.object(egp,'_notify_admins',new=AsyncMock()) as notify:
            await egp.deliver(db,self.bot,invoice)
        self.assertFalse(finalize.await_args.kwargs['report_payment_failures'])
        notify.assert_awaited_once()

    async def test_online_order_report_reaches_admin_when_group_not_configured(self):
        from online_payment_reports import order_completed
        oid=self.db.create_order(101,self.pid,100)
        with patch('report_router.send_text',new=AsyncMock(return_value=None)):
            await order_completed(self.db,self.bot,oid)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_message.await_args.args[0],1)
        self.assertIn(f'سفارش #{oid}',self.bot.send_message.await_args.args[1])

    async def test_reseller_status_checks_dispatch_correct_type_for_all_three_gateways(self):
        import importlib
        from handlers_user import create_user_router
        from handlers_admin import create_admin_router
        user_router=create_user_router(self.db,True)
        admin_router=create_admin_router(self.db,True)
        gateways=[('abangateway','aban'),('blupal','blupal'),('noapay','noapay')]
        invoice={'id':1,'user_id':101,'kind':'reseller_request','ref_id':3,'amount_toman':900}
        for module_name,callback_name in gateways:
            module=importlib.import_module(module_name+'_payment')
            for admin in (False,True):
                router=admin_router if admin else user_router
                name=f'cb_check_{module_name}_invoice' if admin else f'cb_check_{module_name}'
                data=f'check_{module_name}_invoice:1' if admin else f'check_{callback_name}:1'
                call=SimpleNamespace(data=data,from_user=SimpleNamespace(id=1 if admin else 101),message=SimpleNamespace(answer=AsyncMock()),answer=AsyncMock())
                handler=next(h.callback for h in router.callback_query.handlers if h.callback.__name__==name)
                with self.subTest(gateway=module_name,admin=admin), \
                     patch.object(self.db,f'get_{module_name}_invoice',return_value=invoice), \
                     patch.object(module,'try_verify_and_finalize',new=AsyncMock(return_value='verified_now')), \
                     patch.object(module,'finalize_paid_order',new=AsyncMock()) as order, \
                     patch('extra_gateway_payment._finalize_reseller',new=AsyncMock(return_value='')) as reseller:
                    await handler(call,self.bot)
                    reseller.assert_awaited_once_with(self.db,self.bot,invoice)
                    order.assert_not_awaited()
                    self.assertIn('پرداخت نمایندگی ثبت شد',call.message.answer.await_args.args[0])

    async def test_failed_existing_notifier_falls_back_without_undoing_order(self):
        oid=self.db.create_order(101,self.pid,100)
        notifier=AsyncMock(side_effect=RuntimeError('offline'))
        with patch('online_payment_reports.report_router.report',new=AsyncMock(return_value=True)) as report, \
             patch('payment_delivery.deliver_config_to_user',new=AsyncMock()), \
             patch('payment_delivery.check_and_notify_low_stock',new=AsyncMock()):
            result=await finalize_paid_order(self.db,self.bot,oid,notify_admins_fn=notifier)
        self.assertTrue(result.startswith('✅'))
        notifier.assert_awaited_once_with(self.bot,oid)
        report.assert_awaited_once()
        self.assertEqual(self.db.get_order(oid)['status'],'approved')

    async def test_paid_custom_order_awaiting_fulfillment_notifies_admin(self):
        import miniapp.server as server
        db=MagicMock()
        order={'id':3,'user_id':42,'status':'pending','is_renewal':0,'is_custom_config':1,'final_price':900}
        db.get_order.return_value=order
        invoice={'kind':'order','ref_id':3,'user_id':42,'amount_toman':900}
        tenant=SimpleNamespace(bot_token='fake')
        session=MagicMock()
        session.__aenter__=AsyncMock(return_value=session)
        session.__aexit__=AsyncMock(return_value=None)
        session.post=AsyncMock()
        with patch('online_payment_reports.report_router.report_raw',new=AsyncMock(return_value=True)) as report, \
             patch('miniapp.server.aiohttp.ClientSession',return_value=session):
            await server._complete_generic_gateway_payment(db,tenant,invoice)
        report.assert_awaited_once()
        self.assertEqual(report.await_args.args[2],'purchase')
        self.assertIn('تأیید شد',report.await_args.args[3])
        self.assertIn('هنوز تکمیل نشده',report.await_args.args[3])
        db.claim_order.assert_not_called()

    async def test_existing_order_notification_describes_wallet_and_gateway_correctly(self):
        import inspect
        from handlers_user import create_user_router
        router=create_user_router(self.db,True)
        callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='cb_check_blupal')
        notify=inspect.getclosurevars(callback).nonlocals['_notify_admins_of_order']
        ids=[self.db.create_order(101,self.pid,100,wallet_used=100),self.db.create_order(101,self.pid,100)]
        with self.db._get_conn() as conn:
            conn.executemany("UPDATE orders SET status='approved' WHERE id=?",[(oid,) for oid in ids])
        sent=SimpleNamespace(chat=SimpleNamespace(id=-100),message_id=9)
        with patch('report_router.send_text',new=AsyncMock(return_value=sent)) as report:
            await notify(self.bot,ids[0])
            await notify(self.bot,ids[1])
        first=report.await_args_list[0].args[3]
        second=report.await_args_list[1].args[3]
        self.assertIn('پرداخت کامل از کیف پول',first)
        self.assertNotIn('پرداخت کامل از کیف پول',second)
        self.assertIn('به‌طور خودکار تایید',second)
