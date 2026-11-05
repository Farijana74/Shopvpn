import asyncio
import types
import unittest
from unittest.mock import AsyncMock,patch
from aiogram import Router
from managed_volume import register

class VolumeNavigationTests(unittest.TestCase):
    def make_router(self):
        db=types.SimpleNamespace(get_menu_order=lambda:['btn_buy','btn_wallet'],get_setting=lambda key,default='':{'btn_buy':'🛒 خرید کانفیگ','btn_wallet':'کیف پول'}.get(key,default))
        router=Router();register(router,db,False)
        return router

    def test_reply_menu_leaves_volume_state_for_buy_handler(self):
        router=self.make_router()
        state=AsyncMock();message=types.SimpleNamespace(text='🛒 خرید کانفیگ')
        seen=[]
        async def buy(message):seen.append(message.text)
        router.message.register(buy)
        async def run():
            await router.message.trigger(message,state=state,raw_state='VolumeFlow:volume')
        asyncio.run(run())
        state.clear.assert_awaited_once()
        self.assertEqual(seen,['🛒 خرید کانفیگ'])

    def test_non_numeric_input_is_friendly_and_does_not_open_database(self):
        router=self.make_router()
        handler=next(h.callback for h in router.message.handlers if h.callback.__name__=='receive_volume')
        message=types.SimpleNamespace(text='حجم دلخواه',answer=AsyncMock())
        with patch('database.Database') as database:
            asyncio.run(handler(message,AsyncMock()))
        database.assert_not_called()
        self.assertIn('عدد صحیح',message.answer.call_args.args[0])

    def test_photo_instead_of_volume_is_handled(self):
        handler=next(h.callback for h in self.make_router().message.handlers if h.callback.__name__=='receive_volume')
        message=types.SimpleNamespace(text=None,answer=AsyncMock())
        asyncio.run(handler(message,AsyncMock()))
        self.assertIn('عدد صحیح',message.answer.call_args.args[0])
