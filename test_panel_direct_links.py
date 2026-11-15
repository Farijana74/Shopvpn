import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from config_delivery import panel_individual_links, prepare_delivery
from trial_delivery import send_trial

class PanelDirectLinksTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=MagicMock()
        self.db.get_setting.side_effect=lambda k,default='': {'deliver_test_individual_configs_enabled':'1'}.get(k,default)
        self.db.ds_find_config_by_sub_url.return_value={'user_id':42,'panel_server_id':7,'username':'client'}
        self.db.get_panel_server.return_value={'panel_type':'3xui','is_active':1}
        self.links=['vless://uuid@vpn.example:443?type=tcp&security=reality#one','vless://uuid@vpn.example:443?type=xhttp&security=reality#two']
        self.provider=SimpleNamespace(get_client_links=AsyncMock(return_value=self.links))

    async def test_trial_uses_panel_links_without_fetching_subscription(self):
        bot=AsyncMock()
        with patch('panel_providers.get_provider',return_value=self.provider), \
             patch('trial_delivery.collect_service_info',new=AsyncMock()) as sub, \
             patch('trial_delivery.dedicated_sub.dedicated_link_for_upstream',side_effect=lambda db,url:url), \
             patch('trial_delivery.build_qr_bytes',return_value=b'png'):
            await send_trial(bot,self.db,42,'https://example.invalid/sub')
        sub.assert_not_awaited()
        caption=bot.send_photo.await_args.kwargs['caption']
        self.assertTrue(all(link.replace('&','&amp;') in caption for link in self.links))
        self.assertIn('<pre><code>',caption)
        self.provider.get_client_links.assert_awaited_once_with('client')

    async def test_purchase_uses_panel_links_without_subscription_requests(self):
        self.db.get_setting.side_effect=lambda k,default='':default
        self.db.get_alternate_sub_urls.return_value=[]
        with patch('panel_providers.get_provider',return_value=self.provider), \
             patch('config_delivery.fetch_individual_links',new=AsyncMock()) as sub, \
             patch('config_delivery.fetch_sub_info',new=AsyncMock()) as info, \
             patch('config_delivery.dedicated_sub.dedicated_link_for_upstream',side_effect=lambda db,url:url):
            caption,extras=await prepare_delivery('https://example.invalid/sub','plan',1,1,db=self.db,user_tg_id=42)
        body=caption+''.join(extras)
        self.assertTrue(all(link.replace('&','&amp;') in body for link in self.links))
        sub.assert_not_awaited();info.assert_not_awaited()

    async def test_rejects_wrong_owner_and_local_endpoint(self):
        with patch('panel_providers.get_provider',return_value=self.provider):
            self.assertEqual(await panel_individual_links(self.db,99,'https://example.invalid/sub'),[])
            self.provider.get_client_links.assert_not_awaited()
            self.provider.get_client_links.return_value=self.links+['vless://uuid@127.0.0.1:443?security=reality']
            self.assertEqual(await panel_individual_links(self.db,42,'https://example.invalid/sub'),[])

    async def test_unsupported_endpoint_falls_back_without_exposing_errors(self):
        self.provider.get_client_links.side_effect=RuntimeError('secret')
        with patch('panel_providers.get_provider',return_value=self.provider):
            self.assertEqual(await panel_individual_links(self.db,42,'https://example.invalid/sub'),[])

    async def test_provider_calls_authenticated_client_links_endpoint(self):
        from panel_providers.threexui_provider import ThreeXUIProvider
        response=MagicMock(status=200)
        response.json=AsyncMock(return_value={'success':True,'obj':self.links})
        response.__aenter__=AsyncMock(return_value=response)
        response.__aexit__=AsyncMock(return_value=None)
        session=MagicMock()
        session.__aenter__=AsyncMock(return_value=session)
        session.__aexit__=AsyncMock(return_value=None)
        session.get.return_value=response
        provider=ThreeXUIProvider({'api_url':'https://panel.invalid','api_password':'token'})
        with patch.object(provider,'_session',return_value=session):
            self.assertEqual(await provider.get_client_links('user+test'),self.links)
        self.assertEqual(session.get.call_args.args[0],'https://panel.invalid/panel/api/clients/links/user%2Btest')
