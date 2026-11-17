import base64
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from trial_delivery import send_trial
from test_trial_delivery import FakeDB


class TrialIndividualExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_inherited_enabled_setting_extracts_real_base64_subscription_and_renders_copy_code(self):
        configs=['vless://first?a=1&b=2#first','trojan://second#second']
        raw=base64.b64encode('\n'.join(configs).encode()).decode()
        response=MagicMock()
        response.status=200
        response.text=AsyncMock(return_value=raw)
        response.__aenter__=AsyncMock(return_value=response)
        response.__aexit__=AsyncMock(return_value=None)
        session=MagicMock()
        session.__aenter__=AsyncMock(return_value=session)
        session.__aexit__=AsyncMock(return_value=None)
        session.get.return_value=response
        bot=AsyncMock()
        db=FakeDB({'deliver_individual_configs_enabled':'1'})
        with patch('sub_info._session',return_value=session), \
             patch('config_delivery.fetch_sub_info',new=AsyncMock(return_value={'ok':False})), \
             patch('trial_delivery.dedicated_sub.dedicated_link_for_upstream',side_effect=lambda db,url:url), \
             patch('trial_delivery.build_qr_bytes',return_value=b'png'):
            await send_trial(bot,db,42,'https://example.invalid/sub')
        session.get.assert_called_once()
        caption=bot.send_photo.await_args.kwargs['caption']
        self.assertIn('<pre><code>vless://first?a=1&amp;b=2#first\n\ntrojan://second#second</code></pre>',caption)
        self.assertIn('لینک ساب',caption)
        self.assertNotIn('expandable',caption)
        rows=bot.send_photo.await_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(rows[0][0].web_app.url, 'https://example.invalid/sub')
        self.assertEqual([row[0].callback_data for row in rows[1:]],['trial:help','trial:main_menu'])
        bot.send_message.assert_not_awaited()

    async def test_explicit_test_enabled_overrides_disabled_purchase_setting(self):
        bot=AsyncMock()
        db=FakeDB({'deliver_individual_configs_enabled':'0','deliver_test_individual_configs_enabled':'1'})
        with patch('trial_delivery.collect_service_info',new=AsyncMock(return_value={'configs':['vless://trial']})) as collect, \
             patch('trial_delivery.dedicated_sub.dedicated_link_for_upstream',side_effect=lambda db,url:url), \
             patch('trial_delivery.build_qr_bytes',return_value=b'png'):
            await send_trial(bot,db,42,'https://example.invalid/sub')
        collect.assert_awaited_once()
        self.assertIn('<pre><code>vless://trial</code></pre>',bot.send_photo.await_args.kwargs['caption'])

    async def test_explicit_test_disabled_does_not_extract_even_with_purchase_enabled(self):
        bot=AsyncMock()
        db=FakeDB({'deliver_individual_configs_enabled':'1','deliver_test_individual_configs_enabled':'0'})
        with patch('trial_delivery.collect_service_info',new=AsyncMock()) as collect, \
             patch('trial_delivery.dedicated_sub.dedicated_link_for_upstream',side_effect=lambda db,url:url), \
             patch('trial_delivery.build_qr_bytes',return_value=b'png'):
            await send_trial(bot,db,42,'https://example.invalid/sub')
        collect.assert_not_awaited()
        caption=bot.send_photo.await_args.kwargs['caption']
        self.assertNotIn('کانفیگ‌های تکی',caption)
        self.assertIn('https://example.invalid/sub',caption)

    async def test_trial_extracts_links_without_waiting_for_subscription_metadata(self):
        import asyncio
        from config_delivery import collect_service_info
        metadata=AsyncMock(side_effect=asyncio.TimeoutError())
        with patch('config_delivery.fetch_individual_links',new=AsyncMock(return_value=['vless://fast'])), \
             patch('config_delivery.fetch_sub_info',new=metadata):
            result=await collect_service_info('https://example.invalid/sub',include_info=False)
        self.assertEqual(result['configs'],['vless://fast'])
        metadata.assert_not_awaited()

    async def test_slow_optional_metadata_does_not_hold_back_purchased_configs(self):
        import asyncio
        from config_delivery import collect_service_info
        async def slow_metadata(_):
            await asyncio.sleep(30)
        with patch('config_delivery.fetch_individual_links',new=AsyncMock(return_value=['vless://fast'])), \
             patch('config_delivery.fetch_sub_info',new=slow_metadata):
            result=await asyncio.wait_for(collect_service_info('https://example.invalid/sub'),timeout=5)
        self.assertEqual(result['configs'],['vless://fast'])
        self.assertIsNone(result['volume'])
