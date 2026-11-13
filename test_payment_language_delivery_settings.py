import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

from config_delivery import _delivery_flags, prepare_delivery, deliver_config_to_user
from extra_gateway_user import present_invoice
from trial_delivery import send_trial


class SettingsDB:
    def __init__(self, values=None):
        self.values = values or {}
    def get_setting(self, key, default=''):
        return self.values.get(key, default)
    def set_setting(self, key, value):
        self.values[key] = value


class PaymentButtonTests(unittest.IsolatedAsyncioTestCase):
    def target(self, chat='private', business=None):
        return types.SimpleNamespace(chat=types.SimpleNamespace(id=10,type=chat),
                                     business_connection_id=business,answer=AsyncMock())
    async def test_zarinpal_uses_webapp_in_every_invoice_context_and_keeps_check_button(self):
        for kind in ('order','wallet_topup','reseller_request'):
            target = self.target()
            result = dict(invoice_id=42,payment_url='https://shop.invalid/api/pay-start/zarinpal/A123?b=5',meta={},payable_amount=100)
            await present_invoice(target,AsyncMock(),'zarinpal',result,kind,'invoice',100)
            rows = target.answer.await_args.kwargs['reply_markup'].inline_keyboard
            self.assertIsNone(rows[0][0].url)
            self.assertEqual(rows[0][0].web_app.url,result['payment_url'])
            self.assertEqual(rows[-1][0].callback_data,'xgw_check:42')

    async def test_unsupported_contexts_keep_payable_invoice(self):
        for chat,business,url in [('group',None,'https://shop.invalid/pay'),
                                  ('private','connection','https://shop.invalid/pay'),
                                  ('private',None,'http://shop.invalid/pay')]:
            target=self.target(chat,business)
            await present_invoice(target,AsyncMock(),'zarinpal',dict(invoice_id=1,payment_url=url,meta={}), 'order','invoice',100)
            button=target.answer.await_args.kwargs['reply_markup'].inline_keyboard[0][0]
            self.assertEqual(button.url,url)
            self.assertIsNone(button.web_app)

    async def test_other_gateway_and_stars_behaviour_is_unchanged(self):
        target=self.target()
        await present_invoice(target,AsyncMock(),'tetra98',dict(invoice_id=9,payment_url='https://pay.invalid',meta={'bot_url':'https://t.me/paybot'}),'order','invoice',100)
        rows=target.answer.await_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(rows[0][0].url,'https://pay.invalid')
        self.assertEqual(rows[1][0].url,'https://t.me/paybot')
        bot=AsyncMock()
        await present_invoice(target,bot,'tgstars',dict(invoice_id=1,payable_amount=5),'order','invoice',100)
        bot.send_invoice.assert_awaited_once()


class DeliverySettingTests(unittest.IsolatedAsyncioTestCase):
    async def send(self, db, links='https://example.invalid/sub', service=None):
        bot=AsyncMock()
        info=AsyncMock(return_value=service or {'configs':['vless://test']})
        with patch('trial_delivery.collect_service_info',info), \
             patch('trial_delivery.dedicated_sub.dedicated_link_for_upstream',side_effect=lambda db,url:url), \
             patch('trial_delivery.build_qr_bytes',return_value=b'png'):
            await send_trial(bot,db,42,links)
        text=bot.send_photo.await_args.kwargs['caption']+'\n'.join(str(c.args[1]) for c in bot.send_message.await_args_list)
        return text,info

    async def test_explicit_test_setting_sends_individuals_even_when_purchase_disabled(self):
        db=SettingsDB({'deliver_individual_configs_enabled':'0','deliver_test_individual_configs_enabled':'1'})
        text,info=await self.send(db)
        self.assertIn('vless://test',text)
        self.assertIn('https://example.invalid/sub',text)
        self.assertEqual(_delivery_flags(db),(True,False))
        self.assertEqual(_delivery_flags(db,is_test=True),(True,True))

    async def test_tests_can_disable_individuals_while_purchase_keeps_them(self):
        db=SettingsDB({'deliver_individual_configs_enabled':'1','deliver_test_individual_configs_enabled':'0'})
        text,info=await self.send(db)
        info.assert_not_awaited()
        self.assertNotIn('vless://test',text)
        self.assertIn('https://example.invalid/sub',text)
        self.assertEqual(_delivery_flags(db),(True,True))
        self.assertEqual(_delivery_flags(db,is_test=True),(True,False))

    async def test_legacy_trial_layout_is_preserved_until_explicitly_configured(self):
        text,info=await self.send(SettingsDB())
        info.assert_not_awaited()
        self.assertNotIn('vless://test',text)
        text,info=await self.send(SettingsDB({'deliver_sub_link_enabled':'0'}))
        self.assertIn('vless://test',text)
        self.assertNotIn('https://example.invalid/sub',text)

    async def test_happ_and_extraction_failure_keep_a_usable_test_link(self):
        db=SettingsDB({'deliver_test_individual_configs_enabled':'0'})
        text,info=await self.send(db,'happ://crypt5/test')
        self.assertIn('happ://crypt5/test',text)
        info.assert_not_awaited()
        db.set_setting('deliver_test_individual_configs_enabled','1')
        bot=AsyncMock()
        with patch('trial_delivery.collect_service_info',new=AsyncMock(side_effect=RuntimeError('offline'))), \
             patch('trial_delivery.dedicated_sub.dedicated_link_for_upstream',side_effect=lambda db,url:url), \
             patch('trial_delivery.build_qr_bytes',return_value=b'png'):
            await send_trial(bot,db,42,'https://example.invalid/sub')
        self.assertIn('https://example.invalid/sub',bot.send_photo.await_args.kwargs['caption'])

    async def test_shared_delivery_builder_respects_test_and_purchase_flags(self):
        db=SettingsDB({'deliver_individual_configs_enabled':'0','deliver_test_individual_configs_enabled':'1'})
        service=dict(is_sub=True,configs=['vless://test'],sub_url='https://example.invalid/sub',name='test',protocol='vless',volume=None,duration=None)
        with patch('config_delivery.collect_service_info',new=AsyncMock(return_value=service)), \
             patch('config_delivery.dedicated_sub.dedicated_link_for_upstream',side_effect=lambda db,url:url):
            paid=await prepare_delivery('https://example.invalid/sub','test',1,1,db=db)
            trial=await prepare_delivery('https://example.invalid/sub','test',1,1,db=db,is_test=True)
            self.assertNotIn('vless://test',paid[0]+''.join(paid[1]))
            self.assertIn('vless://test',trial[0]+''.join(trial[1]))
            bot=AsyncMock()
            with patch('config_delivery._build_qr_photo',return_value=b'png'):
                await deliver_config_to_user(bot,42,'test','https://example.invalid/sub',db=db,is_test=True)
            self.assertIn('vless://test',bot.send_photo.await_args.kwargs['caption'])
            from admin_panel.config_delivery_web import deliver_config_to_user_web
            with patch('admin_panel.config_delivery_web.build_qr_bytes',return_value=b'png'), \
                 patch('admin_panel.config_delivery_web.tg_send_photo',new=AsyncMock(return_value=True)) as photo, \
                 patch('admin_panel.config_delivery_web.tg_send',new=AsyncMock(return_value=True)):
                await deliver_config_to_user_web(42,'test','https://example.invalid/sub',db=db,bot_token='mock',is_test=True)
            self.assertIn('vless://test',photo.await_args.args[4])

    async def test_web_settings_accept_split_and_reject_empty_delivery(self):
        from extra_settings_schema import save_values,SettingsValidationError
        db=SettingsDB()
        save_values(db,'web',{'deliver_individual_configs_enabled':'0','deliver_test_individual_configs_enabled':'1','language_selection_enabled':'0'})
        self.assertEqual(db.values['language_selection_enabled'],'0')
        self.assertEqual(_delivery_flags(db),(True,False))
        self.assertEqual(_delivery_flags(db,True),(True,True))
        with self.assertRaises(SettingsValidationError):
            save_values(db,'web',{'deliver_sub_link_enabled':'0','deliver_test_individual_configs_enabled':'0'})


class LanguageMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_forced_persian_overrides_saved_and_telegram_language_and_restores_context(self):
        from bot_manager import LanguageMiddleware
        from i18n import get_language,language_scope
        db=SettingsDB({'language_selection_enabled':'0'})
        db.get_user=lambda user_id:{'language_code':'en'}
        async def handler(event,data):
            return get_language()
        with language_scope('en'):
            result=await LanguageMiddleware(db)(handler,object(),{'event_from_user':types.SimpleNamespace(id=42,language_code='en')})
            self.assertEqual(result,'fa')
            self.assertEqual(get_language(),'en')
        db.values.clear()
        result=await LanguageMiddleware(db)(handler,object(),{'event_from_user':types.SimpleNamespace(id=42,language_code='en')})
        self.assertEqual(result,'en')


class LanguageAndAdminIntegrationTests(unittest.TestCase):
    def setUp(self):
        from test_managed_resellers import ManagedAgencyTests
        self.fixture=ManagedAgencyTests();self.fixture.setUp()
        self.root,self.local=self.fixture.root,self.fixture.local
        self.root.add_or_update_user(303,'user','user')
        self.root.set_setting('main_menu_reply_enabled','0')
        self.root.set_setting('main_menu_inline_enabled','0')
    def tearDown(self):
        self.fixture.tearDown()
    def user_router(self):
        from handlers_user import create_user_router
        return create_user_router(self.root,True)
    def message(self,text='/start'):
        return types.SimpleNamespace(text=text,from_user=types.SimpleNamespace(id=303,username='user',first_name='user',language_code='en'),
            chat=types.SimpleNamespace(id=303,type='private'),answer=AsyncMock(),answer_photo=AsyncMock())
    def state(self):
        from test_trial_wizard import MemoryState
        return MemoryState()
    def start(self,message,state):
        router=self.user_router()
        cb=next(h.callback for h in router.message.handlers if h.callback.__name__=='cmd_start')
        asyncio.run(cb(message,state,AsyncMock()))

    def test_start_without_language_prompt_keeps_welcome_and_does_not_erase_preferences(self):
        self.root.set_user_language(303,'en')
        with self.root._get_conn() as conn:
            conn.execute('UPDATE users SET language_selected=0 WHERE telegram_id=303')
        self.root.set_setting('language_selection_enabled','0')
        self.assertEqual(self.root.get_user_language(303),'fa')
        message,state=self.message(),self.state()
        self.start(message,state)
        self.assertFalse(state.data.get('pending_welcome'))
        self.assertIn(self.root.get_setting('welcome_text'),[c.args[0] for c in message.answer.await_args_list])
        self.assertEqual(self.root.get_user(303)['language_code'],'en')
        self.root.set_setting('language_selection_enabled','1')
        self.assertEqual(self.root.get_user_language(303),'en')
        message,state=self.message(),self.state()
        self.start(message,state)
        self.assertTrue(state.data['pending_welcome'])

    def test_no_prompt_does_not_bypass_terms_or_lose_deeplink(self):
        self.root.set_setting('language_selection_enabled','0')
        self.root.set_setting('terms_enabled','1');self.root.set_setting('terms_text','TERMS')
        message,state=self.message('/start buy'),self.state()
        self.start(message,state)
        self.assertTrue(state.data['pending_terms'])
        self.assertIn('__open_buy__',state.data['pending_post_start_actions'])
        self.assertEqual(message.answer.await_args.args[0],'TERMS')

    def test_first_start_with_language_disabled_still_requires_terms(self):
        with self.root._get_conn() as conn:
            conn.execute('DELETE FROM users WHERE telegram_id=303')
        self.root.set_setting('language_selection_enabled','0')
        self.root.set_setting('terms_enabled','1');self.root.set_setting('terms_text','FIRST TERMS')
        message,state=self.message(),self.state()
        self.start(message,state)
        self.assertTrue(state.data['pending_terms'])
        self.assertEqual(message.answer.await_args.args[0],'FIRST TERMS')
        self.assertNotIn(self.root.get_setting('welcome_text'),[c.args[0] for c in message.answer.await_args_list])

    def test_new_signup_report_is_not_lost_when_language_prompt_is_disabled(self):
        with self.root._get_conn() as conn:
            conn.execute('DELETE FROM users WHERE telegram_id=303')
        self.root.set_setting('language_selection_enabled','0')
        message,state=self.message(),self.state()
        with patch('handlers_user.report_router.send_text',new=AsyncMock()) as report:
            self.start(message,state)
        self.assertEqual(report.await_count,1)
        self.assertEqual(report.await_args.args[2],'signup')
        self.assertIn(self.root.get_setting('welcome_text'),[c.args[0] for c in message.answer.await_args_list])

    def test_stale_language_callback_cannot_enable_english_while_locked(self):
        self.root.set_setting('language_selection_enabled','0')
        router=self.user_router()
        cb=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='cb_language')
        call=types.SimpleNamespace(data='language:en',from_user=types.SimpleNamespace(id=303),
            message=types.SimpleNamespace(answer=AsyncMock()),answer=AsyncMock())
        asyncio.run(cb(call,self.state(),AsyncMock()))
        self.assertEqual(self.root.get_user(303)['language_code'],'fa')

    def test_admin_can_toggle_each_setting_without_changing_other_tenant_or_purchase(self):
        from handlers_admin import create_admin_router
        import keyboards
        router=create_admin_router(self.root,True)
        handlers={h.callback.__name__:h.callback for h in router.callback_query.handlers}
        call=types.SimpleNamespace(data='',from_user=types.SimpleNamespace(id=1),
            message=types.SimpleNamespace(text='menu',edit_text=AsyncMock(),answer=AsyncMock(),delete=AsyncMock()),answer=AsyncMock())
        asyncio.run(handlers['cb_admin_language_selection_toggle'](call))
        self.assertEqual(self.root.get_setting('language_selection_enabled'),'0')
        self.assertEqual(self.local.get_setting('language_selection_enabled','1'),'1')
        self.root.set_setting('deliver_individual_configs_enabled','0')
        asyncio.run(handlers['cb_admin_deliver_test_individual_toggle'](call))
        self.assertEqual(self.root.get_setting('deliver_test_individual_configs_enabled'),'1')
        self.assertEqual(self.root.get_setting('deliver_individual_configs_enabled'),'0')
        asyncio.run(handlers['cb_admin_deliver_test_individual_toggle'](call))
        self.assertEqual(self.root.get_setting('deliver_test_individual_configs_enabled'),'0')
        buttons=[b.callback_data for row in keyboards.delivery_settings_kb(self.root).inline_keyboard for b in row]
        self.assertIn('adm_deliver_test_individual_toggle',buttons)
        self.root.set_setting('deliver_test_individual_configs_enabled','1')
        self.root.set_setting('deliver_individual_configs_enabled','1')
        self.root.set_setting('deliver_sub_link_enabled','0')
        asyncio.run(handlers['cb_admin_deliver_test_individual_toggle'](call))
        self.assertEqual(self.root.get_setting('deliver_test_individual_configs_enabled'),'1')
        asyncio.run(handlers['cb_admin_deliver_individual_toggle'](call))
        self.assertEqual(self.root.get_setting('deliver_individual_configs_enabled'),'1')
        call.from_user.id=303
        asyncio.run(handlers['cb_admin_language_selection_toggle'](call))
        self.assertEqual(self.root.get_setting('language_selection_enabled'),'0')
