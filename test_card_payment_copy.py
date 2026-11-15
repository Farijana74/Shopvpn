import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import keyboards as kb


class CardPaymentCopyTests(unittest.IsolatedAsyncioTestCase):
    def handler(self, name, source='handlers_user.py', **extra):
        tree = ast.parse(Path(__file__).with_name(source).read_text())
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        node.decorator_list = []
        env = dict(CallbackQuery=object, Message=object, FSMContext=object, tr=lambda s:s, asyncio=asyncio, kb=kb, escape_md=lambda s: s,
                   _schedule_card_msg_autodelete=AsyncMock(), **extra)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'handlers_user.py', 'exec'), env)
        return env[name], env

    async def test_manual_details_copy_raw_card_and_rial(self):
        db = SimpleNamespace(get_setting=lambda k, default='': {'card_number': '۶۰۳۷-۹۹۱۲ ۳۴۵۶ ۷۸۹۰', 'card_holder': 'Test'}.get(k,default))
        fn, env = self.handler('_send_card2card_details', db=db)
        target = SimpleNamespace(answer=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2)))
        await fn(target, [], 123456)
        markup = target.answer.await_args.kwargs['reply_markup'].model_dump(exclude_none=True)
        buttons = markup['inline_keyboard'][0]
        self.assertEqual(buttons[0]['copy_text']['text'], '6037991234567890')
        self.assertEqual(buttons[1]['copy_text']['text'], '1234560')
        env['_schedule_card_msg_autodelete'].assert_awaited_once_with(1, 2)

    async def test_auto_details_preserve_exact_invoice_rial_and_status(self):
        invoice = dict(card_number='6037991234567890', card_holder='Test', amount_toman=123456,
                       amount_rial=1234567, invoice_id='abc')
        provider = SimpleNamespace(create_invoice=lambda *args: invoice, CardToCardError=RuntimeError)
        db = SimpleNamespace(get_setting=lambda k, default=None: default)
        fn, _ = self.handler('_send_card_auto_details', db=db, card_to_card_payment=provider)
        target = SimpleNamespace(answer=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2)))
        await fn(target, [], 'wallet_topup', 4, 1, 123400)
        rows = target.answer.await_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(rows[0][1].copy_text.text, '1234567')
        self.assertEqual(rows[1][0].callback_data, 'check_c2c:abc')

    async def test_auto_fallback_converts_invoice_toman(self):
        markup = kb.card_payment_copy_kb('6037 9912 3456 7890', 98765 * 10, invoice_id=9)
        self.assertEqual(markup.inline_keyboard[0][1].copy_text.text, '987650')
        self.assertEqual(markup.inline_keyboard[1][0].callback_data, 'check_c2c:9')

    async def test_receipt_button_is_full_width_green_and_prompt_is_new(self):
        from inline_navigation import InlineNavigationMiddleware, send_navigation
        from aiogram.methods import SendMessage
        markup = kb.card_payment_copy_kb('6037991234567890', 10000)
        receipt_row = markup.inline_keyboard[-1]
        self.assertEqual(len(receipt_row), 1)
        self.assertEqual(receipt_row[0].style, 'success')
        self.assertEqual(receipt_row[0].callback_data, 'card:send_receipt')
        fn, _ = self.handler('cb_card_send_receipt')
        sent = []
        async def answer(text):
            async def sender(method):
                sent.append(method)
            await send_navigation(SendMessage(chat_id=1, text=text), sender)
        call = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer=answer, chat=SimpleNamespace(id=1),message_id=2), data='card:send_receipt')
        from inline_navigation import _navigation, Navigation
        token=_navigation.set(Navigation(1,2,task=asyncio.current_task()))
        try:
            await fn(call)
            self.assertFalse(_navigation.get().used)
        finally:
            _navigation.reset(token)
        self.assertEqual(len(sent),1)
        self.assertIsInstance(sent[0],SendMessage)
        self.assertIn('رسید',sent[0].text)

    async def test_sheba_display_and_copy_are_optional(self):
        sheba='IR062960000000100324200001'
        db=SimpleNamespace(get_setting=lambda k, default='': {'card_number':'6037991234567890','card_holder':'Test','card_sheba':sheba}.get(k,default))
        fn,_=self.handler('_send_card2card_details',db=db)
        target=SimpleNamespace(answer=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=1),message_id=2)))
        await fn(target,[],1000)
        self.assertIn(sheba,target.answer.await_args.args[0])
        rows=target.answer.await_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(rows[1][0].copy_text.text,sheba)
        self.assertEqual(rows[-1][0].callback_data,'card:send_receipt')
        auto=kb.card_payment_copy_kb('6037991234567890',10000,invoice_id=1)
        self.assertFalse(any(b.callback_data=='card:send_receipt' for row in auto.inline_keyboard for b in row))

    async def test_sheba_validation_normalizes_digits_and_checks_checksum(self):
        from bank_details import normalize_sheba
        body='2960000000100324200001'
        check=98-int(body+'182700')%97
        valid=f'IR{check:02d}'+body
        self.assertEqual(normalize_sheba(valid.lower()),valid)
        self.assertEqual(normalize_sheba(valid[2:]),valid)
        persian=valid.translate(str.maketrans('0123456789','۰۱۲۳۴۵۶۷۸۹'))
        self.assertEqual(normalize_sheba(persian),valid)
        for bad in ['IR00123',valid[:-1]+str((int(valid[-1])+1)%10),'','not-a-sheba']:
            with self.assertRaises(ValueError):normalize_sheba(bad)

    async def test_sheba_save_invalid_input_and_remove(self):
        db=SimpleNamespace(set_setting=AsyncMock(),log_admin_action=AsyncMock())
        # to_thread executes the database functions synchronously.
        from unittest.mock import Mock,patch
        db.set_setting=Mock()
        db.log_admin_action=Mock()
        fn,_=self.handler('process_set_card_sheba',source='handlers_admin.py',db=db,full_admin_only=lambda uid:True)
        state=SimpleNamespace(clear=AsyncMock())
        message=SimpleNamespace(text='invalid',from_user=SimpleNamespace(id=1),answer=AsyncMock())
        await fn(message,state)
        db.set_setting.assert_not_called()
        state.clear.assert_not_awaited()
        message.text='حذف'
        with patch('keyboards.card_settings_kb',return_value=None):await fn(message,state)
        db.set_setting.assert_called_once_with('card_sheba','')
        state.clear.assert_awaited_once()

    async def test_admin_sheba_valid_save_and_access_control(self):
        from unittest.mock import Mock,patch
        from bank_details import normalize_sheba
        body='2960000000100324200001'
        valid=f'IR{98-int(body+"182700")%97:02d}'+body
        db=SimpleNamespace(set_setting=Mock(),log_admin_action=Mock())
        state=SimpleNamespace(clear=AsyncMock())
        message=SimpleNamespace(text=valid.lower(),from_user=SimpleNamespace(id=1),answer=AsyncMock())
        fn,_=self.handler('process_set_card_sheba',source='handlers_admin.py',db=db,full_admin_only=lambda uid:False)
        await fn(message,state)
        db.set_setting.assert_not_called()
        fn,_=self.handler('process_set_card_sheba',source='handlers_admin.py',db=db,full_admin_only=lambda uid:True)
        with patch('keyboards.card_settings_kb',return_value=None):await fn(message,state)
        db.set_setting.assert_called_once_with('card_sheba',valid)
        state.clear.assert_awaited_once()
