import asyncio
import types
import unittest
from unittest.mock import AsyncMock,patch
from customer_messages import customer_error,TRIAL_USED,UNAVAILABLE
from managed_resellers import AgencyError

class CustomerTextTests(unittest.TestCase):
    def test_trial_limit_does_not_explain_internal_topology(self):
        result=customer_error(AgencyError('شما قبلاً در ربات اصلی یا یکی از نماینده‌های این زنجیره تست گرفته‌اید.'))
        self.assertEqual(result,TRIAL_USED)
        for word in ('زنجیره','نماینده','ربات اصلی'):self.assertNotIn(word,result)
    def test_internal_financial_and_provider_errors_are_not_exposed(self):
        for text in ('اعتبار کیف پول یکی از نمایندگان زنجیره کافی نیست','محصول بالادست حذف شده است','database locked /root/secret.db token=secret','پنل اصلی قطع است'):
            result=customer_error(AgencyError(text))
            self.assertEqual(result,UNAVAILABLE)
            self.assertNotIn(text,result)
    def test_customer_volume_bounds_are_preserved(self):
        text='حجم باید بین 5 تا 100 گیگ باشد.'
        self.assertEqual(customer_error(AgencyError(text)),text)
    def test_volume_prompt_only_contains_customer_price_and_terms(self):
        from managed_native_volume import ask_volume
        local=types.SimpleNamespace(get_setting=lambda key,default='':{'volume_sell_per_gb':'9600','managed_reseller_bot_id':'1'}.get(key,default))
        state=AsyncMock();state.get_data.return_value={'native_plan_id':1}
        message=types.SimpleNamespace(chat=types.SimpleNamespace(id=10),answer=AsyncMock())
        with patch('managed_native_volume.rules',return_value={'name':'حجم دلخواه','min_gb':1,'max_gb':1000}),patch('volume_personal_prices.personal_volume_rate',return_value=None):
            asyncio.run(ask_volume(message,state,local,object(),'hub'))
        text=message.answer.call_args.args[0]
        self.assertIn('9,600 تومان',text)
        for word in ('تأمین','نماینده','ربات اصلی','نرخ فروش','بالادست'):self.assertNotIn(word,text)

    def test_admin_settings_keep_management_diagnostics(self):
        from aiogram import Router
        from unittest.mock import Mock
        from managed_volume import register
        root=types.SimpleNamespace(get_custom_config_products=lambda active:[],get_custom_config_settings=lambda:{'enabled':False},get_setting=lambda key,default='':default,close=Mock())
        local=types.SimpleNamespace(is_senior_admin=lambda uid:True,get_setting=lambda key,default='':{'managed_root_db_path':'root.db','managed_reseller_bot_id':'1'}.get(key,default))
        router=Router()
        with patch('database.Database',return_value=root):register(router,local,False)
        save=next(h.callback for h in router.message.handlers if h.callback.__name__=='save_settings')
        state=AsyncMock();state.get_data.return_value={'volume_setting_field':'price'}
        message=types.SimpleNamespace(text='100',from_user=types.SimpleNamespace(id=1),answer=AsyncMock())
        asyncio.run(save(message,state))
        self.assertIn('ربات اصلی',message.answer.call_args.args[0])
