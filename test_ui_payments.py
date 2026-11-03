import asyncio
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI
from aiogram.methods import SendMessage, EditMessageText
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from inline_navigation import InlineNavigationMiddleware, send_navigation, without_custom_icons
from button_emoji import button_icon, icon_key, validate_icon
from payment_urls import payment_base, return_url
from public_payment_routes import public_payment_router
import extra_gateway_clients as clients
import keyboards
import button_registry
import blupal_payment
import blupal_client


class Settings:
    def __init__(self):
        self.settings = {}
    def get_setting(self, key, default=''):
        return self.settings.get(key, default)
    def set_setting(self, key, value):
        self.settings[key] = value
    def count_active_custom_config_products(self):
        return 0
    def is_full_access_bot(self, value):
        return True


class ButtonTests(unittest.TestCase):
    def test_category_labels_have_no_automatic_folder(self):
        markup = keyboards.categories_kb(Settings(), [{'id': 1, 'name': 'اروپا'}])
        self.assertEqual(markup.inline_keyboard[0][0].text, 'اروپا')

    def test_category_admin_offers_rename(self):
        markup = keyboards.admin_categories_kb([{'id': 1, 'name': 'test', 'is_active': 1}])
        self.assertIn('adm_cat_rename:1', [button.callback_data for row in markup.inline_keyboard for button in row])

    def test_premium_icon_can_be_saved_rendered_and_removed(self):
        db = Settings()
        button_registry.update_item(db, 'main_menu', 'btn_buy', icon_custom_emoji_id='1234567890123456789')
        button = keyboards._styled_inline(db, 'خرید', 'mm:btn_buy', 'btn_buy_style')
        self.assertEqual(button.icon_custom_emoji_id, '1234567890123456789')
        button_registry.update_item(db, 'main_menu', 'btn_buy', icon_custom_emoji_id='')
        self.assertIsNone(button_icon(db, 'btn_buy_style'))

    def test_invalid_emoji_is_rejected_before_saving(self):
        db = Settings()
        with self.assertRaises(ValueError):
            button_registry.update_item(db, 'main_menu', 'btn_buy', icon_custom_emoji_id='<tg-emoji>')
        self.assertEqual(db.settings, {})

    def test_user_button_uses_matching_setting_key(self):
        db = Settings()
        db.set_setting(icon_key('svc_detail', 'sv_qr'), '12345')
        self.assertEqual(keyboards._ubtn(db, 'sv_qr', 'QR', 'svc_qr:x1').icon_custom_emoji_id, '12345')

    def test_icon_fallback_preserves_original_markup(self):
        original = SendMessage(chat_id=1, text='Menu', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='Buy', callback_data='buy', icon_custom_emoji_id='123')]]))
        fallback = without_custom_icons(original)
        self.assertIsNone(fallback.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id)
        self.assertEqual(original.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id, '123')


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    def event(self, callback='mm:btn_buy'):
        return types.SimpleNamespace(data=callback, message=types.SimpleNamespace(chat=types.SimpleNamespace(id=1), message_id=20))

    async def test_click_edits_first_response_and_keeps_followup(self):
        send = AsyncMock(return_value=True)
        async def handler(event, data):
            await send_navigation(SendMessage(chat_id=1, text='Categories'), send)
            await send_navigation(SendMessage(chat_id=1, text='Receipt'), send)
        await InlineNavigationMiddleware()(handler, self.event(), {})
        self.assertIsInstance(send.call_args_list[0].args[0], EditMessageText)
        self.assertEqual(send.call_args_list[0].args[0].message_id, 20)
        self.assertIsInstance(send.call_args_list[1].args[0], SendMessage)

    async def test_other_chat_notification_is_never_edited(self):
        send = AsyncMock(return_value=True)
        async def handler(event, data):
            await send_navigation(SendMessage(chat_id=2, text='Admin notice'), send)
        await InlineNavigationMiddleware()(handler, self.event(), {})
        self.assertIsInstance(send.call_args.args[0], SendMessage)

    async def test_existing_edit_does_not_replace_receipt(self):
        send = AsyncMock(return_value=True)
        async def handler(event, data):
            await send_navigation(EditMessageText(chat_id=1, message_id=20, text='Menu'), send)
            await send_navigation(SendMessage(chat_id=1, text='Receipt'), send)
        await InlineNavigationMiddleware()(handler, self.event(), {})
        self.assertIsInstance(send.call_args_list[1].args[0], SendMessage)

    async def test_transaction_callbacks_keep_delivery_message(self):
        send = AsyncMock(return_value=True)
        async def handler(event, data):
            await send_navigation(SendMessage(chat_id=1, text='Configuration delivered'), send)
        await InlineNavigationMiddleware()(handler, self.event('order_approve:1'), {})
        self.assertIsInstance(send.call_args.args[0], SendMessage)

    async def test_payment_invoice_menu_reuses_current_message(self):
        for callback in ('pay_zarinpal', 'pay_blupal', 'pay_crypto'):
            with self.subTest(callback=callback):
                send = AsyncMock(return_value=True)
                async def handler(event, data):
                    await send_navigation(SendMessage(chat_id=1, text='Payment invoice'), send)
                await InlineNavigationMiddleware()(handler, self.event(callback), {})
                self.assertIsInstance(send.call_args.args[0], EditMessageText)

    async def test_background_notification_never_reuses_menu(self):
        send = AsyncMock(return_value=True)
        async def handler(event, data):
            await asyncio.create_task(send_navigation(SendMessage(chat_id=1, text='Background delivery'), send))
            await send_navigation(SendMessage(chat_id=1, text='Menu'), send)
        await InlineNavigationMiddleware()(handler, self.event(), {})
        self.assertIsInstance(send.call_args_list[0].args[0], SendMessage)
        self.assertIsInstance(send.call_args_list[1].args[0], EditMessageText)

    async def test_deleted_menu_falls_back_to_new_message(self):
        send = AsyncMock(side_effect=[TelegramBadRequest(method=EditMessageText(chat_id=1,message_id=20,text='Menu'), message='message to edit not found'), True])
        async def handler(event, data):
            await send_navigation(SendMessage(chat_id=1, text='Menu'), send)
        await InlineNavigationMiddleware()(handler, self.event(), {})
        self.assertEqual(send.await_count, 2)
        self.assertIsInstance(send.call_args.args[0], SendMessage)


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_zarinpal_verify_converts_toman_to_rial(self):
        with patch('extra_gateway_clients._request', new=AsyncMock(return_value=(200, {'data': {'code': 100}}))) as request:
            self.assertEqual(await clients.zarinpal_verify('merchant', 25000, 'authority'), clients.PAID)
        self.assertEqual(request.call_args.kwargs['json_body']['amount'], 250000)

    async def test_domain_error_shows_actual_callback_domain(self):
        with patch('extra_gateway_clients._request', new=AsyncMock(return_value=(400, {'errors': {'code': -14}}))):
            with self.assertRaisesRegex(clients.GatewayError, 'pay.example.com'):
                await clients.zarinpal_create('merchant', 20000, 'https://pay.example.com/return', 'order', '1')

    def test_standalone_panel_is_valid_payment_base(self):
        self.assertEqual(payment_base('', 'https://panel.example.com/'), 'https://panel.example.com')
        self.assertEqual(payment_base('https://api.example.com', 'https://panel.example.com', 'https://pay.example.com'), 'https://pay.example.com')
        self.assertIn('b=tenant%26other', return_url('https://example.com', 'zarinpal', 'tenant&other'))
        with self.assertRaises(ValueError):
            payment_base('', '', 'https://user:password@example.com/?a=b')

    async def test_blupal_error_never_claims_payment(self):
        db=MagicMock()
        db.get_setting.return_value='key'
        row={'invoice_id':'11','status':'pending','amount_rial':1000}
        with patch('blupal_client.get_invoice', new=AsyncMock(return_value={'invoice_id':'11','status':'PAID','amount':1})):
            self.assertTrue((await blupal_payment.try_verify_and_finalize(db,row)).startswith('error:'))
        db.claim_blupal_invoice.assert_not_called()

    async def test_nested_blupal_response_is_normalized(self):
        response=MagicMock(status=200)
        response.json=AsyncMock(return_value={'success':True,'data':{'invoice':{'invoice_id':'11','status':'PAID'}}})
        request=MagicMock()
        request.__aenter__=AsyncMock(return_value=response)
        request.__aexit__=AsyncMock(return_value=False)
        session=MagicMock()
        session.request.return_value=request
        cm=MagicMock()
        cm.__aenter__=AsyncMock(return_value=session)
        cm.__aexit__=AsyncMock(return_value=False)
        with patch('blupal_client.aiohttp.ClientSession', return_value=cm):
            result=await blupal_client.get_invoice('key','11')
        self.assertEqual(result['invoice_id'],'11')

    async def test_public_payment_routes_work_without_miniapp(self):
        tenant=types.SimpleNamespace(db=MagicMock(), bot_token='123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk')
        tenant.db.get_blupal_invoice_by_invoice_id.return_value={'invoice_id':'11','kind':'order','ref_id':3,'user_id':1}
        app=FastAPI()
        app.include_router(public_payment_router(lambda slug: tenant))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://panel.example.com') as client:
            authority='A'+'0'*35
            response=await client.get('/api/pay-start/zarinpal/'+authority)
            self.assertEqual(response.status_code,200)
            self.assertIn('https://payment.zarinpal.com/pg/StartPay/'+authority,response.text)
            self.assertEqual(response.headers['referrer-policy'],'origin')
            self.assertEqual((await client.get('/api/pay-start/zarinpal/not-valid')).status_code,400)
            with patch('blupal_payment.try_verify_and_finalize', new=AsyncMock(return_value='not_paid_yet')) as verify, patch('blupal_payment.finalize_paid_order', new=AsyncMock()) as finalize:
                response=await client.post('/api/webhooks/blupal',json={'invoice_id':'11','status':'PAID'})
            self.assertEqual(response.json()['status'],'not_paid_yet')
            verify.assert_awaited_once()
            finalize.assert_not_awaited()


class PublicWebhookDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_paid_invoice_uses_shared_delivery(self):
        invoice = {'invoice_id': '11', 'kind': 'order', 'ref_id': 3, 'user_id': 1}
        db = MagicMock()
        db.get_blupal_invoice_by_invoice_id.return_value = invoice
        tenant = types.SimpleNamespace(db=db, bot_token='123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk')
        app = FastAPI()
        app.include_router(public_payment_router(lambda slug: tenant))
        bot = MagicMock()
        bot.__aenter__ = AsyncMock(return_value=bot)
        bot.__aexit__ = AsyncMock(return_value=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://panel.example.com') as client:
            with patch('aiogram.Bot', return_value=bot), patch('blupal_payment.try_verify_and_finalize', new=AsyncMock(return_value='verified_now')), patch('blupal_payment.finalize_paid_order', new=AsyncMock(return_value='✅ delivered')) as finalize:
                response = await client.post('/api/webhooks/blupal', json={'invoice_id': '11'})
            self.assertEqual(response.status_code, 200)
            finalize.assert_awaited_once_with(db, bot, 3)

    async def test_gateway_failure_does_not_acknowledge_paid(self):
        db = MagicMock()
        db.get_blupal_invoice_by_invoice_id.return_value = {'invoice_id': '11'}
        tenant = types.SimpleNamespace(db=db)
        app = FastAPI()
        app.include_router(public_payment_router(lambda slug: tenant))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://panel.example.com') as client:
            with patch('blupal_payment.try_verify_and_finalize', new=AsyncMock(return_value='error:network')):
                response = await client.post('/api/webhooks/blupal', json={'invoice_id': '11', 'status': 'PAID'})
        self.assertEqual(response.status_code, 502)

    async def test_zarinpal_callback_uses_configured_public_domain(self):
        import extra_gateway_payment as gateway
        db = MagicMock()
        values = {'zarinpal_payment_enabled':'1', 'zarinpal_merchant_id':'merchant', 'zarinpal_callback_base_url':'https://pay.example.com'}
        db.get_setting.side_effect = lambda key, default='': values.get(key, default)
        db.get_pending_extra_invoice_for_ref.return_value = None
        db.create_extra_invoice.return_value = 12
        authority = 'A'+'0'*35
        db.get_extra_invoice.return_value = {'id':12,'meta':'{}','payment_url':'https://pay.example.com/api/pay-start/zarinpal/'+authority+'?b=', 'payable_amount':20000,'gateway':'zarinpal'}
        with patch.object(gateway, 'API_BASE_URL', ''), patch.object(gateway, 'ADMIN_PANEL_URL', 'https://panel.example.com'), patch('extra_gateway_clients.zarinpal_create', new=AsyncMock(return_value={'remote_id':authority,'payment_url':'https://payment.zarinpal.com/pg/StartPay/'+authority,'meta':{}})) as create:
            result = await gateway.create_invoice_for(db, '', 1, 'zarinpal', 'order', 3, 20000, 'order')
        self.assertEqual(create.call_args.args[2], 'https://pay.example.com/api/pay-return/zarinpal?b=')
        self.assertIn('https://pay.example.com/api/pay-start/zarinpal/', result['payment_url'])


if __name__ == '__main__':
    unittest.main()
