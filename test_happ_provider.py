import unittest
from unittest.mock import AsyncMock, MagicMock

from panel_providers import PanelError
from panel_providers.threexui_provider import ThreeXUIProvider


class HappProviderTests(unittest.IsolatedAsyncioTestCase):
    def provider(self, client_response, happ_response):
        provider = ThreeXUIProvider({"api_url": "https://example.invalid/base"})
        session = MagicMock()
        for method, data in ((session.get, client_response), (session.post, happ_response)):
            response = MagicMock(status=200)
            response.json = AsyncMock(return_value=data)
            method.return_value.__aenter__ = AsyncMock(return_value=response)
            method.return_value.__aexit__ = AsyncMock(return_value=False)
        provider._session = MagicMock()
        provider._session.return_value.__aenter__ = AsyncMock(return_value=session)
        provider._session.return_value.__aexit__ = AsyncMock(return_value=False)
        return provider, session

    async def test_crypt5_requested_for_client_numeric_id(self):
        provider, session = self.provider(
            {"success": True, "obj": {"client": {"id": 37}}},
            {"success": True, "obj": {"encryptedLink": "happ://crypt5/encrypted"}},
        )
        self.assertEqual(await provider.get_happ_link("client/name"), "happ://crypt5/encrypted")
        self.assertTrue(session.get.call_args.args[0].endswith("/get/client%2Fname"))
        self.assertTrue(session.post.call_args.args[0].endswith("/happLink/37"))

    async def test_invalid_client_response_is_handled(self):
        for data in ([], {"obj": []}, {"obj": {"client": []}}, {"obj": {"client": {"id": 0}}}):
            with self.subTest(data=data):
                provider, session = self.provider(data, {})
                with self.assertRaises(PanelError):
                    await provider.get_happ_link("client")
                session.post.assert_not_called()

    async def test_invalid_happ_response_is_handled(self):
        for data in ([], {"obj": []}, {"success": False}, {"obj": {"encryptedLink": "https://example.invalid/sub"}}):
            with self.subTest(data=data):
                provider, _ = self.provider({"obj": {"client": {"id": 37}}}, data)
                with self.assertRaises(PanelError):
                    await provider.get_happ_link("client")


if __name__ == "__main__":
    unittest.main()
