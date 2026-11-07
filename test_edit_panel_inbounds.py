import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch
from test_managed_resellers import ManagedAgencyTests
from handlers_admin import create_admin_router


class EditPanelInboundsTests(unittest.TestCase):
    setUp = ManagedAgencyTests.setUp
    tearDown = ManagedAgencyTests.tearDown

    def test_fetch_and_save_selected_inbounds(self):
        server = self.root.get_product(self.pid)['provision_server_id']
        router = create_admin_router(self.root, True)
        callbacks = {h.callback.__name__: h.callback for h in router.callback_query.handlers}
        state = AsyncMock()
        state.get_data.return_value = {'edit_inbounds': True, 'server_id': server, 'panel_type': '3xui', 'selected_inbound_ids': [8]}
        call = types.SimpleNamespace(data=f'adm_inbounds_edit:{server}', from_user=types.SimpleNamespace(id=1), message=types.SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        provider = types.SimpleNamespace(list_inbounds=AsyncMock(return_value=[{'id': 8, 'remark': 'new', 'protocol': 'vless', 'port': 443}]))
        async def run():
            with patch('handlers_admin.get_provider', return_value=provider):
                await callbacks['cb_edit_panel_inbounds'](call, state)
            await callbacks['cb_panel_server_inbound_confirm'](call, state)
        asyncio.run(run())
        self.assertEqual(self.root.get_panel_server(server)['xui_inbound_ids'], '[8]')
