import unittest
from unittest.mock import AsyncMock, patch
from config_delivery import order_payment_summary, deliver_config_to_user

class WalletPaymentSummaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_managed_resellers import ManagedAgencyTests
        self.fixture=ManagedAgencyTests();self.fixture.setUp()
        self.db=self.fixture.root

    def tearDown(self):self.fixture.tearDown()

    async def test_wallet_only_shows_paid_and_actual_current_balance(self):
        self.db.deduct_wallet_credit(101,1500)
        order=self.db.create_order(101,self.fixture.pid,1500,wallet_used=1500)
        summary=await order_payment_summary(self.db,101,order,0,1)
        self.assertIn('مبلغ پرداخت‌شده: 1,500 تومان',summary)
        self.assertIn('کسرشده از کیف پول: 1,500 تومان',summary)
        self.assertIn('موجودی فعلی کیف پول: 8,500 تومان',summary)
        self.assertNotIn('پرداخت‌شده: 0 تومان',summary)
        self.assertNotIn('پرداخت از روش دیگر',summary)

    async def test_mixed_wallet_external_discount_and_quantity(self):
        self.db.deduct_wallet_credit(101,400)
        order=self.db.create_order(101,self.fixture.pid,2000,wallet_used=400,discount_amount=100,quantity=2)
        summary=await order_payment_summary(self.db,101,order,1500,2)
        for value in ['مبلغ پرداخت‌شده: 1,900 تومان','تخفیف: 100 تومان','کیف پول: 400 تومان','روش دیگر: 1,500 تومان','کیف پول: 9,600 تومان','2 عدد کانفیگ']:
            self.assertIn(value,summary)

    async def test_delivery_uses_order_data_not_zero_final_price(self):
        self.db.deduct_wallet_credit(101,600)
        order=self.db.create_order(101,self.fixture.pid,600,wallet_used=600)
        bot=AsyncMock()
        with patch('config_delivery.prepare_delivery',new=AsyncMock(return_value=('اطلاعات',[]))), \
             patch('config_delivery._build_qr_photo',return_value=b'png'), \
             patch('config_delivery.get_post_delivery_text',return_value='راهنما'):
            await deliver_config_to_user(bot,101,'plan','vless://x',final_price=0,order_id=order,db=self.db)
        guide=bot.send_message.await_args.args[1]
        self.assertIn('کسرشده از کیف پول: 600 تومان',guide)
        self.assertIn('موجودی فعلی کیف پول: 9,400 تومان',guide)
        self.assertNotIn('پرداخت‌شده: 0 تومان',guide)

    async def test_web_delivery_uses_same_order_summary(self):
        from admin_panel.config_delivery_web import deliver_config_to_user_web
        self.db.deduct_wallet_credit(101,500)
        order=self.db.create_order(101,self.fixture.pid,1200,wallet_used=500)
        with patch('admin_panel.config_delivery_web.prepare_delivery',new=AsyncMock(return_value=('اطلاعات',[]))), \
             patch('admin_panel.config_delivery_web.build_qr_bytes',return_value=b'png'), \
             patch('admin_panel.config_delivery_web.tg_send_photo',new=AsyncMock(return_value=True)), \
             patch('admin_panel.config_delivery_web.tg_send',new=AsyncMock(return_value=True)) as send:
            await deliver_config_to_user_web(101,'plan','vless://x',final_price=700,order_id=order,db=self.db,bot_token='test')
        guide=send.await_args.args[2]
        self.assertIn('کسرشده از کیف پول: 500 تومان',guide)
        self.assertIn('پرداخت از روش دیگر: 700 تومان',guide)
        self.assertIn('موجودی فعلی کیف پول: 9,500 تومان',guide)

    async def test_wrong_owner_does_not_disclose_wallet_balance(self):
        order=self.db.create_order(101,self.fixture.pid,1000,wallet_used=500)
        result=await order_payment_summary(self.db,999,order,500,1)
        self.assertNotIn('کیف پول',result)

    async def test_card_details_still_send_if_balance_lookup_fails(self):
        from types import SimpleNamespace
        from handlers_user import create_user_router
        self.db.set_setting('card_number','6037991234567890')
        self.db.set_setting('card_holder','Test')
        order=self.db.create_order(101,self.fixture.pid,1000,wallet_used=500)
        router=create_user_router(self.db,True)
        handler=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='cb_pay_card2card_order')
        message=SimpleNamespace(answer=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=101),message_id=8)))
        call=SimpleNamespace(from_user=SimpleNamespace(id=101),message=message,answer=AsyncMock())
        state=SimpleNamespace(get_data=AsyncMock(return_value={'order_id':order}))
        with patch.object(self.db,'get_wallet_credit',side_effect=RuntimeError('unavailable')):
            await handler(call,state)
        message.answer.assert_awaited_once()
        self.assertIn('کسرشده از کیف پول: 500 تومان',message.answer.await_args.args[0])
        self.assertIn('مبلغ (ریال)',message.answer.await_args.args[0])
