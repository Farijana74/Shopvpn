import types
import unittest
from unittest.mock import AsyncMock,patch
from aiogram.types import Message,Chat,User,ReplyKeyboardMarkup,KeyboardButton
from aiogram.dispatcher.event.bases import UNHANDLED
from datetime import datetime
from reply_menu_compat import ReplyMenuCompatibilityMiddleware,label_key,menu_snapshot

class ReplyMenuTests(unittest.IsolatedAsyncioTestCase):
    def event(self,text):
        return Message(message_id=1,date=datetime.now(),chat=Chat(id=5,type='private'),from_user=User(id=5,is_bot=False,first_name='User'),text=text)
    async def run_case(self,text,labels,state=None,active=None,result='handled'):
        keyboard=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t)] for t in labels])
        db=types.SimpleNamespace(get_text=lambda k,d:d,get_setting=lambda k,d='':{'btn_buy':'🚀 خرید کانفیگ'}.get(k,d))
        state=state or AsyncMock();state.get_state.return_value=active
        handler=AsyncMock(return_value=result)
        with patch('keyboards.menu_for_user',return_value=keyboard),patch('aiogram.types.Message.answer',new_callable=AsyncMock) as send:
            data={'state':state,'raw_state':active}
            result=await ReplyMenuCompatibilityMiddleware(db)(handler,self.event(text),data)
        return handler,send,state,result
    async def test_old_emoji_runs_current_button_and_refreshes_keyboard(self):
        handler,send,state,result=await self.run_case('🛒 خرید کانفیگ',['🚀 خرید کانفیگ'])
        self.assertEqual(handler.call_args.args[0].text,'🚀 خرید کانفیگ')
        self.assertEqual(handler.await_count,1);send.assert_awaited_once();state.clear.assert_awaited_once()
    async def test_premium_placeholder_and_zwj_are_ignored(self):
        self.assertEqual(label_key('👨‍💻 خرید کانفیگ'),label_key('🚀 خرید کانفیگ'))
        self.assertEqual(label_key('1️⃣ خرید کانفیگ'),label_key('خرید کانفیگ'))
        self.assertEqual(label_key('🅰 خرید کانفیگ'),label_key('خرید کانفیگ'))
        self.assertEqual(label_key('ℹ️ خرید کانفیگ'),label_key('خرید کانفیگ'))
    async def test_ambiguous_label_refreshes_without_guessing(self):
        handler,send,state,result=await self.run_case('🛒 خرید کانفیگ',['🚀 خرید کانفیگ','✅ خرید کانفیگ'])
        handler.assert_not_awaited();send.assert_awaited_once()
    async def test_first_current_button_refreshes_cached_icons(self):
        handler,send,state,result=await self.run_case('🚀 خرید کانفیگ',['🚀 خرید کانفیگ'])
        handler.assert_awaited_once();send.assert_awaited_once()
    async def test_hidden_admin_button_is_never_forwarded(self):
        handler,send,state,result=await self.run_case('🔧 پنل مدیریت',['🚀 خرید کانفیگ'])
        handler.assert_not_awaited();send.assert_awaited_once()

    async def test_old_button_exits_numeric_flow_before_routing(self):
        handler,send,state,result=await self.run_case('🛒 خرید کانفیگ',['🚀 خرید کانفیگ'],active='VolumeFlow:volume')
        state.clear.assert_awaited_once()
        self.assertIsNone(handler.call_args.args[1]['raw_state'])
        self.assertEqual(handler.call_args.args[0].text,'🚀 خرید کانفیگ')

    async def test_arbitrary_form_input_is_preserved(self):
        handler,send,state,result=await self.run_case('۱۵',['🚀 خرید کانفیگ'],active='VolumeFlow:volume')
        self.assertEqual(handler.call_args.args[0].text,'۱۵')
        state.clear.assert_not_awaited();send.assert_not_awaited()

    async def test_unknown_idle_text_refreshes_menu_only_when_unhandled(self):
        handler,send,state,result=await self.run_case('دکمه قدیمی نامشخص',['🚀 خرید کانفیگ'],result=UNHANDLED)
        handler.assert_awaited_once();send.assert_awaited_once()

    async def test_admin_label_edit_is_not_mistaken_for_navigation(self):
        handler,send,state,result=await self.run_case('🛒 خرید کانفیگ',['🚀 خرید کانفیگ'],active='AdminEditButtons:waiting_text')
        self.assertEqual(handler.call_args.args[0].text,'🛒 خرید کانفیگ')
        state.clear.assert_not_awaited();send.assert_not_awaited()

    async def test_icon_only_change_refreshes_once_per_revision(self):
        keyboard=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='خرید',icon_custom_emoji_id='111')]])
        db=types.SimpleNamespace(get_text=lambda k,d:d,get_setting=lambda k,d='':d)
        middleware=ReplyMenuCompatibilityMiddleware(db)
        state=AsyncMock();state.get_state.return_value=None
        handler=AsyncMock(return_value='handled')
        with patch('keyboards.menu_for_user',return_value=keyboard),patch('aiogram.types.Message.answer',new_callable=AsyncMock) as send:
            await middleware(handler,self.event('خرید'),{'state':state})
            await middleware(handler,self.event('خرید'),{'state':state})
            self.assertEqual(send.await_count,1)
            keyboard.keyboard[0][0].icon_custom_emoji_id='222'
            await middleware(handler,self.event('خرید'),{'state':state})
            await middleware(handler,self.event('خرید'),{'state':state})
            self.assertEqual(send.await_count,2)
        self.assertEqual(handler.await_count,4)
