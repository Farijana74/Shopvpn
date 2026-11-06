import asyncio
import types
import unittest
from unittest.mock import AsyncMock
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
import test_merchant_controls as fixtures
from merchant_controls import register
from managed_native_volume import quote
from volume_personal_prices import set_rate,personal_volume_rate,tier_info

class VolumePriceTests(unittest.TestCase):
    setUp=fixtures.MerchantTests.setUp
    tearDown=fixtures.MerchantTests.tearDown
    def plan(self,**kwargs):
        server=self.root.get_product(self.pid)['provision_server_id']
        return self.root.create_custom_config_product('پلن اصلی',server,min_gb=5,max_gb=100,flat_price_per_gb=10,**kwargs)

    def test_picker_includes_volume_and_saves_user_rate(self):
        pid=self.plan()
        with self.local._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        router=Router();register(router,self.local)
        callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='merchant_callback')
        enter=next(h.callback for h in router.message.handlers if h.callback.__name__=='price_user')
        save=next(h.callback for h in router.message.handlers if h.callback.__name__=='price_amount')
        state=FSMContext(MemoryStorage(),StorageKey(bot_id=1,chat_id=101,user_id=101))
        message=types.SimpleNamespace(text='303',from_user=types.SimpleNamespace(id=101),answer=AsyncMock())
        call=types.SimpleNamespace(data=f'merchant:pick:303:v{pid}',from_user=types.SimpleNamespace(id=101),message=types.SimpleNamespace(answer=AsyncMock()),answer=AsyncMock())
        async def run():
            await enter(message,state)
            rows=message.answer.call_args.kwargs['reply_markup'].inline_keyboard
            self.assertTrue(any(b.callback_data==call.data for row in rows for b in row))
            await callback(call,state)
            message.text='9';await save(message,state)
            self.assertIsNone(personal_volume_rate(self.local,303,pid))
            message.text='15';await save(message,state)
            self.assertEqual(personal_volume_rate(self.local,303,pid),15)
        asyncio.run(run())
        local_pid,total=quote(self.local,self.root,self.aid,303,10,pid)
        self.assertEqual(total,150)
        self.assertEqual(self.local.get_tier_price_info(303,total,1,local_pid)['total_after'],150)
        other_pid,other_total=quote(self.local,self.root,self.aid,404,10,pid)
        self.assertEqual(other_total,100)
        set_rate(self.local,303,pid,None)
        self.assertIsNone(personal_volume_rate(self.local,303,pid))

    def test_main_rate_is_separate_from_representative_and_discounts(self):
        pid=self.plan()
        set_rate(self.root,101,pid,7)
        self.assertEqual(personal_volume_rate(self.root,101,pid),7)
        self.assertIsNone(personal_volume_rate(self.local,101,pid))
        self.assertEqual(tier_info(self.root,101,70,pid)['total_after'],70)
        self.assertEqual(self.root.calc_custom_config_product_price(pid,10),100)

    def test_flat_override_cannot_undercut_any_upstream_tier(self):
        pid=self.plan(pricing_mode='tiered')
        with self.root._get_conn() as c:
            c.execute('INSERT INTO custom_config_product_pricing_tiers(product_id,from_gb,to_gb,price_per_gb) VALUES(?,?,?,?)',(pid,5,19,12))
            c.execute('INSERT INTO custom_config_product_pricing_tiers(product_id,from_gb,to_gb,price_per_gb) VALUES(?,?,?,?)',(pid,20,100,8))
            c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        with self.local._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        with self.assertRaises(ValueError):set_rate(self.local,303,pid,10)
        set_rate(self.local,303,pid,14)
        local_id,total=quote(self.local,self.root,self.aid,303,30,pid)
        self.assertEqual(total,420)

    def test_main_customer_volume_step_uses_override_in_order_total(self):
        import ast
        from pathlib import Path
        pid=self.plan();set_rate(self.root,101,pid,7)
        tree=ast.parse(Path('handlers_user.py').read_text())
        function=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='custom_config_receive_volume')
        function.decorator_list=[]
        finalize=AsyncMock()
        namespace=dict(asyncio=asyncio,db=self.root,Message=object,FSMContext=object,tr=lambda t:t,_custom_config_finalize_order=finalize)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),'volume_flow_test','exec'),namespace)
        state=AsyncMock();state.get_data.return_value={'product_id':pid}
        message=types.SimpleNamespace(text='10',from_user=types.SimpleNamespace(id=101),answer=AsyncMock())
        asyncio.run(namespace['custom_config_receive_volume'](message,state))
        self.assertEqual(state.update_data.call_args.kwargs['custom_price'],70)
        finalize.assert_awaited_once()
