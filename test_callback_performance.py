import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from blocked_user import BlockedUserMiddleware

class CallbackPerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_user_does_not_write_database(self):
        row={'username':'u','first_name':'Name','is_blocked':0}
        db=MagicMock();db.is_admin.return_value=True
        handler=AsyncMock()
        data={'event_from_user':types.SimpleNamespace(id=1,username='u',first_name='Name'),'_shopvpn_db_user':row}
        await BlockedUserMiddleware(db)(handler,object(),data)
        db.add_or_update_user.assert_not_called()
        db.get_user.assert_not_called()
        handler.assert_awaited_once()

    async def test_changed_profile_is_saved_and_current_block_is_enforced(self):
        db=MagicMock();db.is_admin.return_value=False
        db.get_user.return_value={'username':'new','first_name':'Name','is_blocked':1}
        handler=AsyncMock()
        data={'event_from_user':types.SimpleNamespace(id=1,username='new',first_name='Name'),'_shopvpn_db_user':{'username':'old','first_name':'Name','is_blocked':0}}
        await BlockedUserMiddleware(db)(handler,object(),data)
        db.add_or_update_user.assert_called_once_with(1,'new','Name')
        handler.assert_not_awaited()

    async def test_blocked_user_with_unchanged_profile_is_not_allowed(self):
        db=MagicMock();db.is_admin.return_value=False
        handler=AsyncMock()
        data={'event_from_user':types.SimpleNamespace(id=1,username='u',first_name='Name'),'_shopvpn_db_user':{'username':'u','first_name':'Name','is_blocked':1}}
        await BlockedUserMiddleware(db)(handler,object(),data)
        handler.assert_not_awaited()
