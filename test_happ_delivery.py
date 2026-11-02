import unittest
from unittest.mock import AsyncMock, patch

from happ_delivery import HappDeliveryError, get_product_delivery_links


class _Db:
    def get_panel_server(self, server_id):
        return {"id": server_id, "panel_type": "3xui", "is_active": 1}


class HappDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = AsyncMock()
        self.provider.get_happ_link.return_value = "happ://crypt5/test-encrypted-payload"
        self.provider_patch = patch("happ_delivery.get_provider", return_value=self.provider)
        self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)
        self.product = {
            "delivery_mode": "happ", "is_auto_provision": 1,
            "provision_server_id": 7, "provision_ssh_node_id": None,
        }
        self.items = [{
            "username": "client-1",
            "panel_server_id": 7,
            "subscription_url": "https://example.invalid/sub/token",
        }]

    async def test_happ_mode_returns_only_a_crypt5_link(self):
        links = await get_product_delivery_links(_Db(), self.product, self.items, 12)
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0].startswith("happ://crypt5/"))
        self.assertNotIn(self.items[0]["subscription_url"], links)

    async def test_happ_mode_never_falls_back_on_panel_failure(self):
        self.provider.get_happ_link.side_effect = RuntimeError("panel unavailable")
        with self.assertRaises(HappDeliveryError):
            await get_product_delivery_links(_Db(), self.product, self.items, 12)

    async def test_happ_mode_rejects_non_3xui_server(self):
        class OtherPanelDb:
            def get_panel_server(self, server_id):
                return {"id": server_id, "panel_type": "marzban", "is_active": 1}

        with self.assertRaises(HappDeliveryError):
            await get_product_delivery_links(OtherPanelDb(), self.product, self.items, 12)

    async def test_both_mode_keeps_subscription_and_adds_crypt5(self):
        links = await get_product_delivery_links(
            _Db(), {**self.product, "delivery_mode": "both"}, self.items, 12,
        )
        self.assertEqual(links[0], self.items[0]["subscription_url"])
        self.assertEqual(len(links), 2)
        self.assertTrue(links[1].startswith("happ://crypt5/"))

    async def test_both_mode_keeps_normal_link_on_panel_failure(self):
        self.provider.get_happ_link.side_effect = RuntimeError("panel unavailable")
        links = await get_product_delivery_links(
            _Db(), {**self.product, "delivery_mode": "both"}, self.items, 12,
        )
        self.assertEqual(links, [self.items[0]["subscription_url"]])

    async def test_normal_mode_is_unchanged(self):
        links = await get_product_delivery_links(
            _Db(), {**self.product, "delivery_mode": "normal"}, self.items, 12,
        )
        self.assertEqual(links, [self.items[0]["subscription_url"]])

    async def test_happ_rejects_normal_url_from_panel(self):
        self.provider.get_happ_link.return_value = self.items[0]["subscription_url"]
        with self.assertRaises(HappDeliveryError):
            await get_product_delivery_links(_Db(), self.product, self.items, 12)

    async def test_happ_uses_provisioned_username(self):
        await get_product_delivery_links(_Db(), self.product, self.items, 12)
        self.provider.get_happ_link.assert_awaited_once_with("client-1")

    async def test_normal_mode_does_not_call_panel(self):
        await get_product_delivery_links(_Db(), {**self.product, "delivery_mode": "normal"}, self.items)
        self.provider.get_happ_link.assert_not_awaited()

    async def test_missing_username_prevents_delivery(self):
        del self.items[0]["username"]
        with self.assertRaises(HappDeliveryError):
            await get_product_delivery_links(_Db(), self.product, self.items)
        self.provider.get_happ_link.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
