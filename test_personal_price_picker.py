import asyncio
import types
import unittest
from unittest.mock import AsyncMock
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
import test_merchant_controls as fixtures
from merchant_controls import register,ControlsFlow,personal_price

class PricePickerTests(unittest.TestCase):
    setUp=fixtures.MerchantTests.setUp
    tearDown=fixtures.MerchantTests.tearDown
    local_product=fixtures.MerchantTests.local_product

    def test_user_first_picker_saves_and_checks_live_supply_floor(self):
        with self.local._get_conn() as c:c.execute('INSERT INTO users(telegram_id) VALUES(303)')
        router=Router();register(router,self.local)
        callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='merchant_callback')
        user_handler=next(h.callback for h in router.message.handlers if h.callback.__name__=='price_user')
        amount_handler=next(h.callback for h in router.message.handlers if h.callback.__name__=='price_amount')
        state=FSMContext(MemoryStorage(),StorageKey(bot_id=1,chat_id=101,user_id=101))
        call=types.SimpleNamespace(data='merchant:price',from_user=types.SimpleNamespace(id=101),message=types.SimpleNamespace(answer=AsyncMock()),answer=AsyncMock())
        message=types.SimpleNamespace(text='303',from_user=types.SimpleNamespace(id=101),answer=AsyncMock())
        pid=self.local_product()['id']
        async def run():
            await callback(call,state)
            self.assertEqual(await state.get_state(),ControlsFlow.price_user.state)
            await user_handler(message,state)
            buttons=message.answer.call_args.kwargs['reply_markup'].inline_keyboard
            self.assertTrue(any(b.callback_data==f'merchant:pick:303:{pid}' for row in buttons for b in row))
            call.data=f'merchant:pick:303:{pid}'
            await callback(call,state)
            self.assertEqual(await state.get_state(),ControlsFlow.price_amount.state)
            # Cost increases after opening the price editor, before saving.
            self.root.edit_product(self.pid,price=150)
            message.text='120';await amount_handler(message,state)
            self.assertIsNone(personal_price(self.local,303,pid))
            self.assertEqual(await state.get_state(),ControlsFlow.price_amount.state)
            message.text='180';await amount_handler(message,state)
            self.assertEqual(personal_price(self.local,303,pid),180)
            self.assertEqual(await state.get_state(),ControlsFlow.price_user.state)
            call.data=f'merchant:pick:303:{pid}';await callback(call,state)
            message.text='حذف';await amount_handler(message,state)
            self.assertIsNone(personal_price(self.local,303,pid))
            self.root.edit_product(self.pid,price=80)
            call.data=f'merchant:pick:303:{pid}';await callback(call,state)
            message.text='85';await amount_handler(message,state)
            self.assertEqual(personal_price(self.local,303,pid),85)
        asyncio.run(run())

    def test_unknown_user_and_stale_product_buttons_do_not_change_prices(self):
        router=Router();register(router,self.local)
        user_handler=next(h.callback for h in router.message.handlers if h.callback.__name__=='price_user')
        callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='merchant_callback')
        state=FSMContext(MemoryStorage(),StorageKey(bot_id=1,chat_id=101,user_id=101))
        message=types.SimpleNamespace(text='999999',from_user=types.SimpleNamespace(id=101),answer=AsyncMock())
        call=types.SimpleNamespace(data=f'merchant:pick:999999:{self.local_product()["id"]}',from_user=types.SimpleNamespace(id=101),answer=AsyncMock(),message=types.SimpleNamespace(answer=AsyncMock()))
        async def run():
            await user_handler(message,state)
            self.assertNotIn('merchant_user',await state.get_data())
            await callback(call,state)
            self.assertTrue(call.answer.call_args.kwargs['show_alert'])
        asyncio.run(run())

    def test_main_bot_uses_the_same_picker(self):
        router=Router();register(router,self.root)
        callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='merchant_callback')
        user_handler=next(h.callback for h in router.message.handlers if h.callback.__name__=='price_user')
        amount_handler=next(h.callback for h in router.message.handlers if h.callback.__name__=='price_amount')
        state=FSMContext(MemoryStorage(),StorageKey(bot_id=1,chat_id=1,user_id=1))
        call=types.SimpleNamespace(data='merchant:price',from_user=types.SimpleNamespace(id=1),message=types.SimpleNamespace(answer=AsyncMock()),answer=AsyncMock())
        message=types.SimpleNamespace(text='101',from_user=types.SimpleNamespace(id=1),answer=AsyncMock())
        async def run():
            await callback(call,state);await user_handler(message,state)
            call.data=f'merchant:pick:101:{self.pid}';await callback(call,state)
            message.text='50';await amount_handler(message,state)
            self.assertEqual(personal_price(self.root,101,self.pid),50)
            self.assertEqual(self.root.get_tier_price_info(101,100,1,self.pid)['total_after'],50)
        asyncio.run(run())
