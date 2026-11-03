import asyncio
import types
import unittest
from unittest.mock import AsyncMock,patch
import test_managed_resellers as agency_tests
from managed_trials import POLICY_KEY,configure_panel_trials,provide_trial,trial_policy
from managed_reseller_ui import create_agency_router,AgencyFlow

class MemoryState:
    def __init__(self):self.current=None;self.data={}
    async def clear(self):self.current=None;self.data={}
    async def set_state(self,value):self.current=value.state
    async def get_state(self):return self.current
    async def update_data(self,**kwargs):self.data.update(kwargs)
    async def get_data(self):return self.data.copy()

class TrialWizardTests(unittest.TestCase):
    def setUp(self):
        self.f=agency_tests.ManagedAgencyTests();self.f.setUp()
        self.root,self.local=self.f.root,self.f.local
        self.panel=self.root.add_panel_server('پنل بدون محصول','3xui','https://example.invalid','user','secret')
    def tearDown(self):self.f.tearDown()

    def test_steps_choose_panel_then_confirm_without_product_id(self):
        router=create_agency_router(self.root,True)
        callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='agency_callback')
        receive=next(h.callback for h in router.message.handlers if h.callback.__name__=='trial_settings_message')
        call=types.SimpleNamespace(data='agency:trials',from_user=types.SimpleNamespace(id=1),message=types.SimpleNamespace(edit_text=AsyncMock(),answer=AsyncMock()),answer=AsyncMock())
        message=types.SimpleNamespace(from_user=types.SimpleNamespace(id=1),answer=AsyncMock(),text='')
        state=MemoryState()
        async def run():
            await callback(call,state)
            self.assertEqual(state.current,AgencyFlow.trials.state)
            message.text='invalid';await receive(message,state)
            self.assertEqual(state.current,AgencyFlow.trials.state)
            for value,expected in [('256',AgencyFlow.trial_hours),('6',AgencyFlow.trial_cost),('7',AgencyFlow.trial_panel)]:
                message.text=value;await receive(message,state)
                self.assertEqual(state.current,expected.state)
            markup=message.answer.call_args.kwargs['reply_markup']
            self.assertTrue(any(b.text=='پنل بدون محصول' for row in markup.inline_keyboard for b in row))
            self.assertFalse(trial_policy(self.root).get('enabled'))
            call.data=f'agency:trial_panel:{self.panel}'
            await callback(call,state)
            self.assertEqual(state.current,AgencyFlow.trial_confirm.state)
            self.assertIn('پنل بدون محصول',call.message.edit_text.call_args.args[0])
            self.assertFalse(trial_policy(self.root).get('enabled'))
            call.data='agency:trial_save';await callback(call,state)
            self.assertIsNone(state.current)
            policy=trial_policy(self.root)
            self.assertEqual((policy['panel_id'],policy['volume_mb'],policy['hours'],policy['cost']),(self.panel,256,6,7))
            await callback(call,state)  # old confirmation button cannot reapply settings
            self.assertTrue(call.answer.call_args.kwargs['show_alert'])
        asyncio.run(run())

    def test_panel_trial_needs_no_product_or_catalog_permission_and_bills_once(self):
        configure_panel_trials(self.root,self.panel,256,6,7)
        before=[c['id'] for c in self.local.get_categories(False)]
        with self.local._get_conn() as conn:conn.execute('INSERT INTO users(telegram_id) VALUES(303)')
        async def create(db,source,quantity,user_id,order_id):
            self.assertEqual(source['provision_server_id'],self.panel)
            self.assertEqual((source['auto_provision_volume_gb'],source['duration_days']),(0.25,0.25))
            cid=db.add_custom_config(user_id,self.panel,'paneltrial',0.25,0.25,'https://example.invalid/sub',order_id=order_id,source='direct_product')
            return [dict(username='paneltrial',subscription_url='https://example.invalid/sub',panel_server_id=self.panel,volume_gb=0.25,duration_days=0.25,custom_config_id=cid)]
        async def run():
            with patch('direct_panel_provision.provision_direct',new=AsyncMock(side_effect=create)) as remote:
                _,first=await provide_trial(self.local,303)
                _,second=await provide_trial(self.local,303)
                self.assertEqual(remote.await_count,1)
                self.assertEqual(first[0]['custom_config_id'],second[0]['custom_config_id'])
            proxy=self.local.get_panel_server(first[0]['panel_server_id'])
            self.assertEqual(proxy['managed_source_server_id'],self.panel)
        asyncio.run(run())
        self.assertEqual(self.root.get_wallet_credit(101),9993)
        self.assertEqual([c['id'] for c in self.local.get_categories(False)],before)
        self.assertEqual(len(self.root.get_categories(False)),1)
