import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from states import BuyFlow


class ProductCheckoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_managed_resellers import ManagedAgencyTests
        from handlers_user import create_user_router
        self.fixture=ManagedAgencyTests();self.fixture.setUp()
        self.db=self.fixture.root
        with self.db._get_conn() as conn:
            conn.execute('UPDATE users SET referral_credit=0 WHERE telegram_id=101')
        cat=self.db.add_category('خرید')
        self.pid=self.db.add_product(cat,'پلن تست',200000)
        self.db.add_configs(self.pid,['vless://test-a','vless://test-b','vless://test-c'])
        self.db.set_setting('card_number','6037991234567890')
        self.db.set_setting('card_holder','Test')
        self.router=create_user_router(self.db,True)
        self.state=FSMContext(MemoryStorage(),StorageKey(bot_id=1,chat_id=101,user_id=101))
        self.user=types.SimpleNamespace(id=101,first_name='Test',full_name='Test',username='test')
        self.bot=AsyncMock();self.bot.token='123:test'
        self.message=types.SimpleNamespace(chat=types.SimpleNamespace(id=101),message_id=7,from_user=self.user,
            edit_text=AsyncMock(),answer=AsyncMock(),delete=AsyncMock())
        self.message.answer.return_value=self.message

    def tearDown(self):
        self.fixture.tearDown()

    async def invoke(self,name,data=None,**extra):
        handler=next(h.callback for h in self.router.callback_query.handlers if h.callback.__name__==name)
        call=types.SimpleNamespace(data=data,from_user=self.user,message=self.message,answer=AsyncMock())
        await handler(call,self.state,**extra)
        return call

    async def enter(self, method='pay_card2card'):
        await self.invoke('cb_product',f'prod:{self.pid}')
        draft=(await self.state.get_data())['product_checkout']
        await self.invoke('cb_checkout_method',f"checkout_method:{draft['token']}:{method}")
        return (await self.state.get_data())['product_checkout']

    def orders(self):
        with self.db._get_conn() as c:return c.execute('SELECT * FROM orders WHERE user_id=101').fetchall()

    async def code(self,token,value):
        await self.invoke('cb_checkout_code',f'checkout_code:{token}')
        handler=next(h.callback for h in self.router.message.handlers if h.callback.__name__=='process_checkout_code')
        self.message.text=value
        await handler(self.message,self.state)

    async def test_product_opens_methods_without_quantity_or_charge(self):
        await self.invoke('cb_product',f'prod:{self.pid}')
        rows=self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
        callbacks=[b.callback_data for r in rows for b in r]
        self.assertTrue(any(':pay_card2card' in x for x in callbacks))
        self.assertFalse(any(x.startswith(('qty_','enter_code:','buy_start:')) for x in callbacks))
        self.assertEqual(await self.state.get_state(),BuyFlow.choosing_payment.state)
        self.assertEqual(len(self.orders()),0)

    async def test_discount_code_is_not_offered_or_applied_to_product(self):
        cid=self.db.create_discount_code('SAVE',percent=25,max_uses=1)
        draft=await self.enter()
        self.assertEqual(len(self.orders()),0)
        buttons=self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
        self.assertFalse(any(b.callback_data.startswith('checkout_code:') for r in buttons for b in r))
        self.assertEqual(await self.state.get_state(),BuyFlow.checkout_review.state)
        self.assertEqual(self.db.get_discount_code_by_id(cid)['used_count'],0)
        await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        order=self.orders()[0]
        self.assertEqual(order['quantity'],1)
        self.assertEqual(order['discount_amount'],0)
        self.assertEqual(order['final_price'],200000)
        self.assertEqual(self.db.get_discount_code_by_id(cid)['used_count'],0)
        card=self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0]
        self.assertEqual(card[1].copy_text.text,'2000000')
        self.assertEqual(await self.state.get_state(),BuyFlow.waiting_receipt.state)

    async def test_owned_box_product_prize_can_still_be_redeemed(self):
        self.db._ensure_engage_tables()
        cid = self.db.create_discount_code('BOXFREE123', percent=100, max_uses=1, source='lootbox_prize', product_id=self.pid)
        with self.db._get_conn() as conn:
            conn.execute("INSERT INTO user_lootboxes(user_id,type_id,status,prize_kind,prize_code) VALUES(101,1,'opened','product','BOXFREE123')")
        draft = await self.enter()
        buttons = self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
        self.assertTrue(any((b.callback_data or '').startswith('checkout_code:') for row in buttons for b in row))
        await self.code(draft['token'], 'BOXFREE123')
        with patch('handlers_user.deliver_config_to_user',new=AsyncMock()) as delivery, \
             patch('handlers_user.check_and_notify_low_stock',new=AsyncMock()), \
             patch('handlers_user.report_router.send_text',new=AsyncMock(return_value=None)):
            await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        self.assertEqual(self.orders()[0]['status'], 'approved')
        self.assertEqual(self.orders()[0]['final_price'], 0)
        self.assertEqual(self.db.get_discount_code_by_id(cid)['used_count'], 1)
        delivery.assert_awaited_once()

    async def test_double_confirmation_deducts_wallet_and_creates_only_once(self):
        self.db.add_wallet_credit(101,30000)
        draft=await self.enter()
        await asyncio.gather(*(self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot) for _ in range(2)))
        orders=self.orders()
        self.assertEqual(len(orders),1)
        self.assertEqual(orders[0]['wallet_used'],30000)
        self.assertEqual(orders[0]['final_price'],170000)
        self.assertEqual(self.db.get_wallet_credit(101),0)

    async def test_invalid_code_does_not_create_order_or_consume_wallet(self):
        self.db.add_wallet_credit(101,30000)
        draft=await self.enter()
        await self.code(draft['token'],'NOPE')
        self.assertEqual(await self.state.get_state(),BuyFlow.checkout_review.state)
        self.assertFalse(self.orders())
        self.assertEqual(self.db.get_wallet_credit(101),30000)
        await self.invoke('cb_checkout_review',f"checkout_review:{draft['token']}")
        self.assertEqual(await self.state.get_state(),BuyFlow.checkout_review.state)

    async def test_stale_token_cannot_submit_another_draft(self):
        draft=await self.enter()
        await self.invoke('cb_product',f'prod:{self.pid}')
        await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        self.assertFalse(self.orders())

    async def test_price_change_requires_new_confirmation(self):
        draft=await self.enter()
        self.db.edit_product(self.pid,price=250000)
        await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        self.assertFalse(self.orders())
        self.assertEqual(await self.state.get_state(),BuyFlow.checkout_review.state)
        self.assertIn('250,000',self.message.edit_text.await_args.args[0])

    async def test_discount_below_gateway_minimum_requires_method_change(self):
        self.db.set_setting('min_amount_card','100000')
        self.db.create_discount_code('BIG',percent=75)
        draft=await self.enter()
        await self.code(draft['token'],'BIG')
        await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        self.assertEqual(self.orders()[0]['final_price'], 200000)

    async def test_cancel_draft_does_not_spend_or_claim(self):
        self.db.add_wallet_credit(101,30000)
        cid=self.db.create_discount_code('SAVE',percent=25)
        draft=await self.enter()
        await self.code(draft['token'],'SAVE')
        await self.invoke('cb_cancel_flow','cancel_flow')
        self.assertFalse(self.orders())
        self.assertEqual(self.db.get_wallet_credit(101),30000)
        self.assertEqual(self.db.get_discount_code_by_id(cid)['used_count'],0)
        self.assertIsNone(await self.state.get_state())

    async def test_wallet_covered_order_keeps_original_delivery_path(self):
        self.db.add_wallet_credit(101,200000)
        draft=await self.enter('checkout_credit')
        with patch('handlers_user.deliver_config_to_user',new=AsyncMock()) as delivery, \
             patch('handlers_user.check_and_notify_low_stock',new=AsyncMock()), \
             patch('handlers_user.report_router.send_text',new=AsyncMock(return_value=None)):
            await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        self.assertEqual(len(self.orders()),1)
        self.assertEqual(self.orders()[0]['status'],'approved')
        self.assertEqual(self.db.get_wallet_credit(101),0)
        delivery.assert_awaited_once()

    async def test_auto_product_keeps_name_step_then_selected_payment(self):
        self.pid=self.fixture.pid
        draft=await self.enter()
        await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        self.assertEqual(await self.state.get_state(),BuyFlow.waiting_config_name.state)
        self.assertFalse(self.orders())
        await self.invoke('cb_buy_random_config_name','custom_config_random_username',bot=self.bot)
        self.assertEqual(len(self.orders()),1)
        self.assertEqual(await self.state.get_state(),BuyFlow.waiting_receipt.state)
        self.assertEqual(self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0][1].copy_text.text,'1000')

    async def test_gateway_invoice_created_only_after_final_confirmation(self):
        self.db.create_discount_code('SAVE',percent=25)
        invoice=AsyncMock(return_value={'invoice_id':1,'payment_url':'https://example.invalid/pay'})
        with patch('handlers_user.extra_gateway_payment.available_keys',return_value=['zarinpal']), \
             patch('extra_gateway_user.egp.is_available',return_value=True), \
             patch('extra_gateway_user.egp.create_invoice_for',new=invoice), \
             patch('extra_gateway_user.present_invoice',new=AsyncMock()) as present:
            draft=await self.enter('pay_zarinpal')
            invoice.assert_not_awaited()
            await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        invoice.assert_awaited_once()
        self.assertEqual(invoice.await_args.args[6],200000)
        present.assert_awaited_once()
        self.assertEqual(self.orders()[0]['final_price'],200000)

    async def test_deactivated_product_is_not_charged(self):
        draft=await self.enter()
        self.db.toggle_product(self.pid)
        await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        self.assertFalse(self.orders())

    async def test_deeplink_opens_same_payment_step(self):
        handler=next(h.callback for h in self.router.message.handlers if h.callback.__name__=='cmd_start')
        # Access the actual deep-link opener used by /start without executing unrelated welcome logic.
        from inspect import getclosurevars
        opener=getclosurevars(handler).nonlocals['_open_product_from_deeplink']
        await opener(self.message,self.state,self.pid)
        self.assertEqual(await self.state.get_state(),BuyFlow.choosing_payment.state)
        self.assertFalse(self.orders())

    async def test_invoice_presentation_failure_does_not_refund_payable_order(self):
        self.db.add_wallet_credit(101,30000)
        with patch('handlers_user.extra_gateway_payment.available_keys',return_value=['zarinpal']), \
             patch('extra_gateway_user.egp.is_available',return_value=True), \
             patch('extra_gateway_user.egp.create_invoice_for',new=AsyncMock(return_value={'invoice_id':1})), \
             patch('extra_gateway_user.present_invoice',new=AsyncMock(side_effect=RuntimeError('Telegram unavailable'))):
            draft=await self.enter('pay_zarinpal')
            await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        self.assertEqual(len(self.orders()),1)
        self.assertEqual(self.orders()[0]['status'],'pending')
        self.assertEqual(self.orders()[0]['wallet_used'],30000)
        self.assertEqual(self.db.get_wallet_credit(101),0)

    async def test_insufficient_credit_does_not_create_an_unpayable_order(self):
        await self.invoke('cb_product',f'prod:{self.pid}')
        draft=(await self.state.get_data())['product_checkout']
        call=await self.invoke('cb_checkout_method',f"checkout_method:{draft['token']}:checkout_credit")
        self.assertFalse(self.orders())
        self.assertEqual(await self.state.get_state(),BuyFlow.choosing_payment.state)
        self.assertTrue(any(args.kwargs.get('show_alert') for args in call.answer.await_args_list))

    async def test_discount_cannot_replace_payment_gateway_for_product(self):
        self.db.set_setting('card_to_card_enabled','0')
        cid=self.db.create_discount_code('FREE',percent=100,max_uses=1)
        call=await self.invoke('cb_product',f'prod:{self.pid}')
        self.assertFalse(self.orders())
        self.assertTrue(any(args.kwargs.get('show_alert') for args in call.answer.await_args_list))
        self.assertEqual(self.db.get_discount_code_by_id(cid)['used_count'],0)

    async def test_stale_checkout_cannot_override_another_active_flow(self):
        from states import WalletTopup
        draft=await self.enter()
        await self.state.set_state(WalletTopup.waiting_receipt)
        await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        self.assertFalse(self.orders())
        self.assertEqual(await self.state.get_state(),WalletTopup.waiting_receipt.state)

    async def test_checkout_displays_current_wallet_and_predicted_deduction(self):
        self.db.add_wallet_credit(101,30000)
        await self.invoke('cb_product',f'prod:{self.pid}')
        text=self.message.edit_text.await_args.args[0]
        self.assertIn('موجودی کیف پول: 30,000 تومان',text)
        self.assertIn('استفاده از کیف پول: 30,000 تومان',text)
        self.assertIn('مبلغ نهایی قابل پرداخت: 170,000 تومان',text)
        self.assertEqual(self.db.get_wallet_credit(101),30000)
        self.assertFalse(self.orders())

    async def test_mixed_card_details_show_deduction_and_remaining_balance(self):
        self.db.add_wallet_credit(101,30000)
        draft=await self.enter()
        await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        text=self.message.answer.await_args.args[0]
        self.assertIn('کسرشده از کیف پول: 30,000 تومان',text)
        self.assertIn('موجودی فعلی کیف پول: 0 تومان',text)
        self.assertIn('مبلغ (ریال): `1700000`',text)
        self.assertEqual(self.orders()[0]['final_price'],170000)

    async def test_wallet_option_only_appears_when_final_amount_is_fully_covered(self):
        for balance in (0, 199999, 200000, 250000):
            with self.subTest(balance=balance):
                with self.db._get_conn() as conn:
                    conn.execute('UPDATE users SET referral_credit=? WHERE telegram_id=?',(balance,101))
                await self.invoke('cb_product',f'prod:{self.pid}')
                buttons=[button for row in self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for button in row]
                wallet=[button for button in buttons if button.callback_data and button.callback_data.endswith(':checkout_credit')]
                self.assertEqual(bool(wallet),balance>=200000)
                if balance<200000:
                    self.assertTrue(any(button.callback_data and button.callback_data.endswith(':pay_card2card') for button in buttons))
                self.assertEqual(self.db.get_wallet_credit(101),balance)
                self.assertFalse(self.orders())

    async def test_balance_drop_before_confirmation_returns_to_available_methods_without_charge(self):
        self.db.add_wallet_credit(101,200000)
        draft=await self.enter('checkout_credit')
        self.db.deduct_wallet_credit(101,1)
        await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        self.assertEqual(await self.state.get_state(),BuyFlow.choosing_payment.state)
        buttons=[b.callback_data for row in self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertFalse(any(value and value.endswith(':checkout_credit') for value in buttons))
        self.assertTrue(any(value and value.endswith(':pay_card2card') for value in buttons))
        self.assertEqual(self.db.get_wallet_credit(101),199999)
        self.assertFalse(self.orders())

    async def test_discount_does_not_make_wallet_sufficient_for_product(self):
        self.db.add_wallet_credit(101,100000)
        self.db.create_discount_code('HALF',percent=50)
        draft=await self.enter('pay_card2card')
        await self.invoke('cb_checkout_back',f"checkout_back:{draft['token']}")
        buttons=[b for row in self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertFalse(any(b.callback_data and b.callback_data.endswith(':checkout_credit') for b in buttons))
        self.assertEqual(self.db.get_wallet_credit(101),100000)
        self.assertFalse(self.orders())

    async def test_partial_discount_without_gateway_does_not_create_order(self):
        self.db.set_setting('card_to_card_enabled','0')
        self.db.create_discount_code('HALF',percent=50)
        call=await self.invoke('cb_product',f'prod:{self.pid}')
        self.assertFalse(self.orders())
        self.assertTrue(any(args.kwargs.get('show_alert') for args in call.answer.await_args_list))
        self.assertEqual(self.db.get_wallet_credit(101),0)

    async def test_full_discount_without_wallet_does_not_show_wallet_label(self):
        self.db.create_discount_code('FREE',percent=100)
        draft=await self.enter('pay_card2card')
        await self.invoke('cb_checkout_back',f"checkout_back:{draft['token']}")
        buttons=[b for row in self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertFalse(any(b.callback_data and b.callback_data.endswith(':checkout_credit') for b in buttons))
        self.assertFalse(self.orders())
        self.assertEqual(self.db.get_wallet_credit(101),0)

    async def test_postpaid_credit_remains_available_under_its_own_label(self):
        with self.db._get_conn() as conn:
            conn.execute('UPDATE users SET referral_credit=0, credit_limit=250000 WHERE telegram_id=101')
        await self.invoke('cb_product',f'prod:{self.pid}')
        buttons=[b for row in self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        selection=next(b for b in buttons if b.callback_data and b.callback_data.endswith(':checkout_credit'))
        self.assertNotIn('کیف پول',selection.text)
        self.assertIn('اعتبار',selection.text)
        self.assertFalse(self.orders())
        self.assertEqual(self.db.get_wallet_credit(101),0)

    async def test_postpaid_purchase_still_completes_and_records_debt(self):
        with self.db._get_conn() as conn:
            conn.execute('UPDATE users SET referral_credit=0, credit_limit=250000 WHERE telegram_id=101')
        draft=await self.enter('checkout_credit')
        self.assertIn('اعتبار',draft['method_label'])
        with patch('handlers_user.deliver_config_to_user',new=AsyncMock()) as delivery, \
             patch('handlers_user.check_and_notify_low_stock',new=AsyncMock()), \
             patch('handlers_user.report_router.send_text',new=AsyncMock(return_value=None)):
            await self.invoke('cb_checkout_pay',f"checkout_pay:{draft['token']}",bot=self.bot)
        self.assertEqual(len(self.orders()),1)
        self.assertEqual(self.orders()[0]['status'],'approved')
        self.assertEqual(self.db.get_wallet_credit(101),-200000)
        delivery.assert_awaited_once()
