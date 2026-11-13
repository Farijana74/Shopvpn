import asyncio
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.methods import SendMessage, EditMessageText
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from inline_navigation import InlineNavigationMiddleware, send_navigation
from menu_restore import back_to_main_menu_markup
from tutorial import send_device_steps


def callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    def event(self, data='tut_pick:1'):
        return types.SimpleNamespace(data=data,from_user=types.SimpleNamespace(id=42),
            message=types.SimpleNamespace(chat=types.SimpleNamespace(id=42),message_id=5))

    async def test_user_navigation_without_markup_gets_only_return_button(self):
        send=AsyncMock(return_value=True)
        async def handler(event,data):
            await send_navigation(SendMessage(chat_id=42,text='Tutorial'),send)
        with patch('menu_restore.main_menu_markup') as full:
            await InlineNavigationMiddleware(object())(handler,self.event(),{})
        full.assert_not_called()
        message=send.await_args.args[0]
        self.assertIsInstance(message,EditMessageText)
        self.assertEqual(callbacks(message.reply_markup),['trial:main_menu'])

    async def test_explicit_form_buttons_and_background_sends_are_preserved(self):
        send=AsyncMock(return_value=True)
        markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Pay',url='https://example.invalid')]])
        async def handler(event,data):
            await send_navigation(SendMessage(chat_id=42,text='Payment',reply_markup=markup),send)
            async def background():
                await send_navigation(SendMessage(chat_id=42,text='Notice'),send)
            await asyncio.create_task(background())
        await InlineNavigationMiddleware(object())(handler,self.event('pay_zarinpal'),{})
        self.assertEqual(send.await_args_list[0].args[0].reply_markup,markup)
        self.assertIsNone(send.await_args_list[1].args[0].reply_markup)

    async def test_admin_navigation_keeps_its_existing_fallback(self):
        send=AsyncMock(return_value=True)
        markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Admin',callback_data='adm_back_panel')]])
        async def handler(event,data):
            await send_navigation(SendMessage(chat_id=42,text='Admin'),send)
        with patch('menu_restore.main_menu_markup',return_value=markup):
            await InlineNavigationMiddleware(object())(handler,self.event('adm_categories'),{})
        self.assertEqual(send.await_args.args[0].reply_markup,markup)


class TutorialNavigationTests(unittest.IsolatedAsyncioTestCase):
    def db(self, steps):
        return types.SimpleNamespace(get_tutorial_steps=lambda device_id:steps,
            get_tutorial_device=lambda device_id:dict(name='Training',emoji='📚'))

    async def test_text_photo_and_video_tutorials_have_return_on_last_step(self):
        for kind in ('text','photo','video'):
            with self.subTest(kind=kind):
                first=dict(id=1,text='Step 1',photo_file_id=None,video_file_id=None)
                last=dict(id=2,text='Step 2',photo_file_id='photo' if kind=='photo' else None,
                          video_file_id='video' if kind=='video' else None)
                bot=AsyncMock()
                self.assertTrue(await send_device_steps(bot,42,self.db([first,last]),1))
                method=bot.send_message if kind=='text' else getattr(bot,'send_'+kind)
                self.assertEqual(callbacks(method.await_args.kwargs['reply_markup']),['trial:main_menu'])
                self.assertIsNone(bot.send_message.await_args_list[0].kwargs['reply_markup'])

    async def test_tutorial_picker_keeps_topics_and_adds_return(self):
        from keyboards import tutorial_devices_user_kb
        markup=tutorial_devices_user_kb([dict(id=1,name='Android',emoji='📱'),dict(id=2,name='iOS',emoji='📱')])
        self.assertEqual(callbacks(markup),['tut_pick:1','tut_pick:2','trial:main_menu'])

    async def test_no_steps_returns_false_without_sending_extra_menu(self):
        bot=AsyncMock()
        self.assertFalse(await send_device_steps(bot,42,self.db([]),1))
        bot.send_message.assert_not_awaited()


class GiftNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_gift_purchase_keeps_share_controls_and_one_return_without_menu(self):
        from config_delivery import deliver_config_to_user
        db=types.SimpleNamespace(gift_get_order=lambda oid:dict(card_id=9,user_id=42),
            gift_mark_order_delivered=lambda oid:True,gift_get_card=lambda cid:dict(id=9,code_plain='GIFT-TEST'))
        bot=AsyncMock()
        with patch('gift_card_handlers._bot_username',new=AsyncMock(return_value='shopbot')), \
             patch('gift_card_handlers.gc.build_card_image',new=AsyncMock(return_value='image')), \
             patch('gift_card_handlers.gc.card_image_bytes',return_value=b'png'), \
             patch('menu_restore.restore_main_menu',new=AsyncMock()) as restore:
            await deliver_config_to_user(bot,42,'product','vless://config',order_id=1,db=db)
        restore.assert_not_awaited()
        bot.send_message.assert_awaited_once()
        markup=bot.send_photo.await_args.kwargs['reply_markup']
        self.assertIn('gift_my:0',callbacks(markup))
        self.assertEqual(callbacks(markup).count('trial:main_menu'),1)
        self.assertTrue(any(b.url for row in markup.inline_keyboard for b in row))

    async def test_web_gift_delivery_has_only_return_and_does_not_restore_menu(self):
        from admin_panel.config_delivery_web import deliver_config_to_user_web
        db=types.SimpleNamespace(gift_get_order=lambda oid:dict(card_id=9),gift_mark_order_delivered=lambda oid:True)
        with patch('admin_panel.config_delivery_web.tg_send',new=AsyncMock(return_value=True)) as send, \
             patch('admin_panel.config_delivery_web._restore_menu_web',new=AsyncMock()) as restore:
            await deliver_config_to_user_web(42,'product','vless://config',order_id=1,db=db,bot_token='mock')
        restore.assert_not_awaited()
        send.assert_awaited_once()
        self.assertEqual(send.await_args.kwargs['reply_markup']['inline_keyboard'][0][0]['callback_data'],'trial:main_menu')


class PurchaseAdminNavigationTests(unittest.TestCase):
    def setUp(self):
        from test_managed_resellers import ManagedAgencyTests
        self.fixture=ManagedAgencyTests();self.fixture.setUp()
        self.root=self.fixture.root
    def tearDown(self):
        self.fixture.tearDown()

    def check_approval(self, kind):
        from handlers_admin import create_admin_router
        root=self.root
        if kind == 'bank':
            root.edit_product(self.fixture.pid,is_auto_provision=False)
            root.add_configs(self.fixture.pid,['vless://bank'])
        if kind == 'custom':
            server_id=root.get_product(self.fixture.pid)['provision_server_id']
            oid=root.create_custom_config_order(101,10,'custom-client',server_id,100)
        else:
            oid=root.create_order(101,self.fixture.pid,100)
        router=create_admin_router(root,True)
        cb=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='cb_order_approve')
        call=types.SimpleNamespace(data=f'order_approve:{oid}',from_user=types.SimpleNamespace(id=1),
            message=types.SimpleNamespace(chat=types.SimpleNamespace(id=1),caption='Receipt',
                edit_caption=AsyncMock(),answer=AsyncMock(),edit_text=AsyncMock()),answer=AsyncMock())
        bot=AsyncMock()
        async def delivery(bot,user_id,*args,**kwargs):
            await bot.send_message(user_id,'Delivery guide',reply_markup=back_to_main_menu_markup())
        with patch('handlers_admin.provision_direct',new=AsyncMock(return_value=[{'subscription_url':'https://example.invalid/sub'}])), \
             patch('handlers_admin.get_provider',return_value=types.SimpleNamespace(create_user=AsyncMock(return_value=types.SimpleNamespace(username='custom-client',subscription_url='https://example.invalid/sub')))), \
             patch('handlers_admin.check_and_notify_low_stock',new=AsyncMock()), \
             patch('handlers_admin.get_product_delivery_links',new=AsyncMock(return_value=['https://example.invalid/sub'])), \
             patch('handlers_admin.deliver_config_to_user',new=AsyncMock(side_effect=delivery)) as deliver, \
             patch('handlers_admin.kb.inline_menu_for_user',return_value=InlineKeyboardMarkup(inline_keyboard=[[
                 InlineKeyboardButton(text='Buy',callback_data='mm:btn_buy')]])):
            asyncio.run(cb(call,bot))
        self.assertEqual(root.get_order(oid)['status'],'approved')
        deliver.assert_awaited_once()
        messages=[c for c in bot.send_message.await_args_list if c.args and c.args[0]==101]
        self.assertEqual(len(messages),2)  # confirmation, then the delivery's guide
        self.assertEqual(callbacks(messages[-1].kwargs['reply_markup']),['trial:main_menu'])

    def test_admin_approved_product_delivers_once_without_followup_main_menu(self):
        self.check_approval('auto')

    def test_bank_product_delivery_does_not_send_followup_menu(self):
        self.check_approval('bank')

    def test_custom_config_delivery_does_not_send_followup_menu(self):
        self.check_approval('custom')

    def test_return_button_opens_full_menu_only_when_clicked_and_clears_flow(self):
        from handlers_user import create_user_router
        router=create_user_router(self.root,True)
        cb=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='cb_trial_main_menu')
        bot=AsyncMock()
        call=types.SimpleNamespace(bot=bot,from_user=types.SimpleNamespace(id=101),answer=AsyncMock())
        full=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Buy',callback_data='mm:btn_buy')]])
        state=AsyncMock()
        with patch('menu_restore.main_menu_markup',return_value=full):
            asyncio.run(cb(call,state))
        state.clear.assert_awaited_once()
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.kwargs['reply_markup'],full)
