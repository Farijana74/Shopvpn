import base64
import unittest
from urllib.parse import urlsplit
from unittest.mock import AsyncMock, patch

from config_delivery import build_delivery_message, deliver_config_to_user, strip_html, _visible_len
from delivery_copy import copy_targets, delivery_copy_markup
from trial_delivery import send_trial


class DeliveryCopyTests(unittest.IsolatedAsyncioTestCase):
    def service(self, configs=None):
        return dict(is_sub=True, configs=configs or ['vless://first?a=1&b=2','vless://second'],
                    sub_url='https://example.invalid/sub', name='سرویس')

    def layout(self, configs=None):
        return build_delivery_message(self.service(configs),1,1,True,True)

    def test_layout_has_two_sections_and_one_complete_copy_entity(self):
        caption, extras=self.layout()
        body='\n\n'.join([caption]+extras)
        self.assertIn('کانفیگ‌های تکی',body)
        self.assertIn('لینک ساب',body)
        self.assertNotIn('expandable',body)
        configs,links=copy_targets(body)
        self.assertEqual(configs,'vless://first?a=1&b=2\n\nvless://second')
        self.assertEqual(links,['https://example.invalid/sub'])
        rows=delivery_copy_markup(body).inline_keyboard
        self.assertEqual(rows[0][0].text,'📋 کپی لینک')
        self.assertEqual(rows[0][0].copy_text.text,links[0])
        self.assertEqual(rows[1][0].copy_text.text,configs)

    def test_long_copy_uses_private_fragment_without_truncation(self):
        configs=['vless://'+'a'*170,'vless://'+'b'*170+'#فارسی<&']
        caption,extras=self.layout(configs)
        with patch('delivery_copy.MINIAPP_URL','https://app.invalid/miniapp/index.html?b=12'):
            button=delivery_copy_markup('\n\n'.join([caption]+extras)).inline_keyboard[1][0]
        self.assertIsNone(button.copy_text)
        parsed=urlsplit(button.web_app.url)
        self.assertEqual(parsed.path,'/miniapp/copy-links.html')
        self.assertEqual(parsed.query,'')
        decoded=base64.urlsafe_b64decode(parsed.fragment+'='*((-len(parsed.fragment))%4)).decode()
        self.assertEqual(decoded,'\n\n'.join(configs))
        self.assertNotIn('https://example.invalid/sub',decoded)

    def test_missing_miniapp_never_sends_invalid_or_partial_copy(self):
        caption,extras=self.layout(['vless://'+'a'*400])
        with patch('delivery_copy.MINIAPP_URL',''):
            button=delivery_copy_markup('\n\n'.join([caption]+extras)).inline_keyboard[1][0]
        self.assertIsNone(button.copy_text)
        self.assertIsNone(button.web_app)
        self.assertEqual(button.callback_data,'delivery_copy_help:configs')

    async def purchase(self, configs, fail_photo=False):
        caption,extras=self.layout(configs)
        bot=AsyncMock()
        if fail_photo:bot.send_photo.side_effect=RuntimeError('photo failed')
        with patch('config_delivery.prepare_delivery',new=AsyncMock(return_value=(caption,extras))), \
             patch('config_delivery._build_qr_photo',return_value=b'png'), \
             patch('config_delivery.get_post_delivery_text',return_value='راهنما'):
            await deliver_config_to_user(bot,1,'سرویس','https://example.invalid/sub')
        return bot

    async def test_short_purchase_adds_copy_to_same_qr(self):
        bot=await self.purchase(['vless://one','vless://two'])
        bot.send_photo.assert_awaited_once()
        copy=bot.send_photo.await_args.kwargs['reply_markup'].inline_keyboard[1][0]
        self.assertEqual(copy.copy_text.text,'vless://one\n\nvless://two')
        self.assertIn('https://example.invalid/sub',bot.send_photo.await_args.kwargs['caption'])

    async def test_medium_purchase_keeps_all_links_in_one_message(self):
        configs=['vless://'+str(i)+'a'*160 for i in range(10)]
        bot=await self.purchase(configs)
        bot.send_photo.assert_not_awaited()
        self.assertEqual(bot.send_message.await_count,2) # one delivery, one existing guide
        body=bot.send_message.await_args_list[0].args[1]
        self.assertLessEqual(_visible_len(body),4096)
        self.assertTrue(all(c in body for c in configs))
        self.assertIn('https://example.invalid/sub',body)

    async def test_huge_purchase_is_one_complete_file_not_broken_messages(self):
        configs=['vless://'+str(i)+'a'*200 for i in range(30)]
        bot=await self.purchase(configs)
        bot.send_photo.assert_not_awaited()
        bot.send_document.assert_awaited_once()
        document=bot.send_document.await_args.args[1]
        content=document.data.decode()
        self.assertTrue(all(c in content for c in configs))
        self.assertIn('https://example.invalid/sub',content)
        bot.send_message.assert_awaited_once() # existing guide only

    async def test_failed_photo_keeps_copy_buttons_in_text_fallback(self):
        bot=await self.purchase(['vless://one'],fail_photo=True)
        row=bot.send_message.await_args_list[0].kwargs['reply_markup'].inline_keyboard
        self.assertEqual(row[0][0].copy_text.text,'https://example.invalid/sub')
        self.assertEqual(row[1][0].copy_text.text,'vless://one')

    async def test_trial_keeps_copy_code_block_without_copy_buttons(self):
        from test_trial_delivery import FakeDB
        bot=AsyncMock()
        db=FakeDB({'deliver_test_individual_configs_enabled':'1'})
        configs=['vless://one','vless://two']
        with patch('trial_delivery.collect_service_info',new=AsyncMock(return_value={'configs':configs})), \
             patch('trial_delivery.dedicated_sub.dedicated_link_for_upstream',side_effect=lambda db,url:url), \
             patch('trial_delivery.build_qr_bytes',return_value=b'png'):
            await send_trial(bot,db,1,'https://example.invalid/sub')
        bot.send_message.assert_not_awaited()
        args=bot.send_photo.await_args.kwargs
        rows=args['reply_markup'].inline_keyboard
        self.assertEqual([row[0].callback_data for row in rows],['trial:help','trial:main_menu'])
        self.assertTrue(all(b.copy_text is None and b.web_app is None for row in rows for b in row))
        self.assertIn('<pre><code>vless://one\n\nvless://two</code></pre>',args['caption'])
        self.assertEqual(rows[-1][0].callback_data,'trial:main_menu')
        self.assertIn('لینک ساب',args['caption'])

    async def test_web_panel_uses_same_single_message_and_buttons(self):
        from admin_panel.config_delivery_web import deliver_config_to_user_web
        configs=['vless://'+str(i)+'a'*160 for i in range(10)]
        caption,extras=self.layout(configs)
        with patch('admin_panel.config_delivery_web.prepare_delivery',new=AsyncMock(return_value=(caption,extras))), \
             patch('admin_panel.config_delivery_web.tg_send_photo',new=AsyncMock()) as photo, \
             patch('admin_panel.config_delivery_web.tg_send',new=AsyncMock(return_value=True)) as send:
            await deliver_config_to_user_web(1,'سرویس','https://example.invalid/sub',bot_token='test')
        photo.assert_not_awaited()
        self.assertEqual(send.await_count,2)
        call=send.await_args_list[0]
        self.assertIn('https://example.invalid/sub',call.args[2])
        self.assertTrue(all(c in call.args[2] for c in configs))
        self.assertEqual(call.kwargs['reply_markup']['inline_keyboard'][0][0]['copy_text']['text'],'https://example.invalid/sub')

    async def test_web_short_delivery_attaches_native_copy_to_photo(self):
        from admin_panel.config_delivery_web import deliver_config_to_user_web
        caption,extras=self.layout()
        with patch('admin_panel.config_delivery_web.prepare_delivery',new=AsyncMock(return_value=(caption,extras))), \
             patch('admin_panel.config_delivery_web.build_qr_bytes',return_value=b'png'), \
             patch('admin_panel.config_delivery_web.tg_send_photo',new=AsyncMock(return_value=True)) as photo, \
             patch('admin_panel.config_delivery_web.tg_send',new=AsyncMock(return_value=True)):
            await deliver_config_to_user_web(1,'سرویس','https://example.invalid/sub',bot_token='test')
        buttons=photo.await_args.kwargs['reply_markup']['inline_keyboard']
        self.assertEqual(buttons[0][0]['copy_text']['text'],'https://example.invalid/sub')
        self.assertEqual(buttons[1][0]['copy_text']['text'],'vless://first?a=1&b=2\n\nvless://second')

    async def test_web_huge_delivery_attaches_all_links_in_one_file(self):
        from admin_panel.config_delivery_web import deliver_config_to_user_web
        configs=['vless://'+str(i)+'a'*200 for i in range(30)]
        caption,extras=self.layout(configs)
        with patch('admin_panel.config_delivery_web.prepare_delivery',new=AsyncMock(return_value=(caption,extras))), \
             patch('admin_panel.config_delivery_web.tg_send_document',new=AsyncMock(return_value=True)) as document, \
             patch('admin_panel.config_delivery_web.tg_send',new=AsyncMock(return_value=True)) as send:
            await deliver_config_to_user_web(1,'سرویس','https://example.invalid/sub',bot_token='test')
        document.assert_awaited_once()
        content=document.await_args.args[2].decode()
        self.assertTrue(all(c in content for c in configs))
        self.assertIn('https://example.invalid/sub',content)
        send.assert_awaited_once() # guide only

    async def test_long_trial_keeps_all_configs_and_sub_in_one_message(self):
        from test_trial_delivery import FakeDB
        configs=['vless://'+str(i)+'a'*160 for i in range(10)]
        bot=AsyncMock()
        with patch('trial_delivery.collect_service_info',new=AsyncMock(return_value={'configs':configs})), \
             patch('trial_delivery.dedicated_sub.dedicated_link_for_upstream',side_effect=lambda db,url:url):
            await send_trial(bot,FakeDB({'deliver_test_individual_configs_enabled':'1'}),1,'https://example.invalid/sub')
        bot.send_photo.assert_not_awaited()
        bot.send_message.assert_awaited_once()
        body=bot.send_message.await_args.args[1]
        self.assertTrue(all(c in body for c in configs))
        self.assertIn('https://example.invalid/sub',body)
        self.assertEqual(copy_targets(body)[0],'\n\n'.join(configs+['https://example.invalid/sub']))

    async def test_raw_api_preserves_copy_markup_in_photo_and_document(self):
        import json
        from unittest.mock import MagicMock
        from admin_panel.telegram_notify import send_photo,send_document_bytes as send_document
        session=MagicMock()
        session.__aenter__=AsyncMock(return_value=session)
        session.__aexit__=AsyncMock(return_value=None)
        response=MagicMock()
        response.__aenter__=AsyncMock(return_value=type('Response',(),{'status':200})())
        response.__aexit__=AsyncMock(return_value=None)
        session.post.return_value=response
        markup={'inline_keyboard':[[{'text':'کپی لینک','copy_text':{'text':'https://example.invalid/sub'}}]]}
        with patch('admin_panel.telegram_notify.aiohttp.ClientSession',return_value=session):
            self.assertTrue(await send_photo('test',1,b'png',reply_markup=markup))
            photo_fields=session.post.call_args.kwargs['data']._fields
            self.assertEqual(json.loads(next(f[2] for f in photo_fields if f[0]['name']=='reply_markup')),markup)
            self.assertTrue(await send_document('test',1,b'vless://test','configs.txt',reply_markup=markup))
            fields=session.post.call_args.kwargs['data']._fields
            self.assertEqual(json.loads(next(f[2] for f in fields if f[0]['name']=='reply_markup')),markup)
            self.assertEqual(next(f[2] for f in fields if f[0]['name']=='document'),b'vless://test')

    async def test_shared_trial_delivery_has_no_copy_buttons_while_purchase_keeps_them(self):
        caption,extras=self.layout(['vless://one','vless://two'])
        bot=AsyncMock()
        with patch('config_delivery.prepare_delivery',new=AsyncMock(return_value=(caption,extras))), \
             patch('config_delivery._build_qr_photo',return_value=b'png'):
            await deliver_config_to_user(bot,1,'تست','https://example.invalid/sub',is_test=True)
        self.assertIsNone(bot.send_photo.await_args.kwargs['reply_markup'])
        self.assertIn('<pre><code>',bot.send_photo.await_args.kwargs['caption'])
        purchased=await self.purchase(['vless://one','vless://two'])
        self.assertIsNotNone(purchased.send_photo.await_args.kwargs['reply_markup'])

    async def test_web_trial_omits_copy_buttons(self):
        from admin_panel.config_delivery_web import deliver_config_to_user_web
        caption,extras=self.layout()
        with patch('admin_panel.config_delivery_web.prepare_delivery',new=AsyncMock(return_value=(caption,extras))), \
             patch('admin_panel.config_delivery_web.build_qr_bytes',return_value=b'png'), \
             patch('admin_panel.config_delivery_web.tg_send_photo',new=AsyncMock(return_value=True)) as photo, \
             patch('admin_panel.config_delivery_web.tg_send',new=AsyncMock(return_value=True)):
            await deliver_config_to_user_web(1,'تست','https://example.invalid/sub',bot_token='test',is_test=True)
        self.assertIsNone(photo.await_args.kwargs['reply_markup'])
