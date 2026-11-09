import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from panel_providers.threexui_provider import ThreeXUIProvider


def provider_session():
    provider = ThreeXUIProvider(dict(api_url='https://example.invalid/panel',
        api_password='mock',xui_inbound_ids='[7,8]',xui_sub_base_url='https://example.invalid/sub'))
    session = MagicMock()
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={'success':True})
    session.post.return_value.__aenter__ = AsyncMock(return_value=response)
    session.post.return_value.__aexit__ = AsyncMock(return_value=False)
    provider._session = MagicMock()
    provider._session.return_value.__aenter__ = AsyncMock(return_value=session)
    provider._session.return_value.__aexit__ = AsyncMock(return_value=False)
    return provider,session


class ThreeXUIHwidTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_sends_hwid_limit_with_selected_count_to_all_inbounds(self):
        for limit in (1,3,0):
            with self.subTest(limit=limit):
                provider,session=provider_session()
                await provider.create_user('customer',10,30,user_limit=limit)
                payload=session.post.call_args.kwargs['json']
                self.assertEqual(payload['inboundIds'],[7,8])
                self.assertEqual(payload['client']['limitHwid'],limit)
                self.assertEqual(payload['client']['limitIp'],limit)

    async def test_update_applies_limit_to_each_inbound_without_resetting_credentials(self):
        for limit in (1,4,0):
            with self.subTest(limit=limit):
                provider,session=provider_session()
                clients=[dict(id=f'uuid-{n}',email='customer',subId='stable',limitIp=2,
                    limitHwid=2,expiryTime=4102444800000,totalGB=1000) for n in (7,8)]
                provider._find_client_all_inbounds=AsyncMock(return_value=list(zip(clients,(7,8))))
                await provider.update_user('customer',user_limit=limit)
                self.assertEqual(session.post.call_count,2)
                for original,call in zip(clients,session.post.call_args_list):
                    payload=call.kwargs['json']
                    self.assertEqual(payload['limitHwid'],limit)
                    self.assertEqual(payload['limitIp'],limit)
                    self.assertEqual(payload['id'],original['id'])
                    self.assertEqual(payload['subId'],'stable')
                    self.assertEqual(payload['totalGB'],1000)
                    self.assertEqual(payload['expiryTime'],original['expiryTime'])
                    self.assertEqual(original['limitHwid'],2)

    async def test_renewal_without_user_count_preserves_existing_hwid_limit(self):
        provider,session=provider_session()
        client=dict(id='uuid',email='customer',subId='stable',limitIp=5,limitHwid=9,
                    expiryTime=4102444800000,totalGB=1000)
        provider._find_client_all_inbounds=AsyncMock(return_value=[(client,7)])
        await provider.update_user('customer',add_days=30)
        payload=session.post.call_args.kwargs['json']
        self.assertEqual((payload['limitIp'],payload['limitHwid']),(5,9))

    async def test_legacy_client_without_hwid_is_not_changed_without_explicit_count(self):
        provider,session=provider_session()
        client=dict(id='uuid',email='customer',subId='stable',limitIp=0,totalGB=0)
        provider._find_client_all_inbounds=AsyncMock(return_value=[(client,7)])
        await provider.update_user('customer',add_volume_gb=5)
        self.assertNotIn('limitHwid',session.post.call_args.kwargs['json'])


class ProductHwidIntegrationTests(unittest.TestCase):
    def setUp(self):
        import test_managed_resellers as fixtures
        self.fixture=fixtures.ManagedAgencyTests()
        self.fixture.setUp()
        self.root,self.local=self.fixture.root,self.fixture.local

    def tearDown(self):
        self.fixture.tearDown()

    def test_existing_product_fixed_count_reaches_panel_hwid_limit(self):
        from direct_panel_provision import provision_direct
        self.root.set_product_user_limit(self.fixture.pid,0,0,base_users=1)
        product=self.root.get_product(self.fixture.pid)
        order=self.root.create_order(101,product['id'],100)
        provider,session=provider_session()
        with patch('direct_panel_provision.get_provider',return_value=provider):
            asyncio.run(provision_direct(self.root,product,1,101,order))
        self.assertEqual(session.post.call_args.kwargs['json']['client']['limitHwid'],1)

    def test_new_product_and_order_override_reach_panel(self):
        from direct_panel_provision import provision_direct
        source=self.root.get_product(self.fixture.pid)
        pid=self.root.add_product(source['category_id'],'new',100,is_auto_provision=True,
            provision_server_id=source['provision_server_id'],auto_provision_volume_gb=10,
            duration_days=30,base_users=1,extra_user_price=10,max_users=3)
        order=self.root.create_order(101,pid,120,user_limit=3)
        provider,session=provider_session()
        with patch('direct_panel_provision.get_provider',return_value=provider):
            asyncio.run(provision_direct(self.root,self.root.get_product(pid),1,101,order))
        self.assertEqual(session.post.call_args.kwargs['json']['client']['limitHwid'],3)

    def test_managed_reseller_purchase_applies_upstream_product_count(self):
        from direct_panel_provision import provision_direct
        from managed_resellers import sync_bot
        self.root.set_product_user_limit(self.fixture.pid,0,0,base_users=1)
        sync_bot(self.root,self.fixture.aid)
        product=self.fixture.local_product()
        with self.local._get_conn() as conn:
            conn.execute('INSERT INTO users(telegram_id) VALUES(303)')
        order=self.local.create_order(303,product['id'],100)
        provider,session=provider_session()
        with patch('direct_panel_provision.get_provider',return_value=provider):
            results=asyncio.run(provision_direct(self.local,product,1,303,order))
        self.assertEqual(session.post.call_args.kwargs['json']['client']['limitHwid'],1)
        self.assertEqual(results[0]['user_limit'],1)
