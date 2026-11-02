import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot_manager import BotManager


class BotRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_bot_is_restarted_if_absent(self):
        manager = BotManager()
        manager.start_bot = AsyncMock()
        db = MagicMock(db_path="/tmp/test-recovery.db")
        db.list_reseller_bots.return_value = []
        db.list_pending_db_purges.return_value = []
        with patch("bot_manager.asyncio.sleep", new=AsyncMock(side_effect=[None, asyncio.CancelledError()])):
            with self.assertRaises(asyncio.CancelledError):
                await manager.reconcile_resellers_loop(db, "test-token")
        self.assertEqual(manager.start_bot.await_count, 1)
        self.assertEqual(manager.start_bot.call_args.kwargs, {"is_main_bot": True})

    async def test_finished_bot_is_removed_while_another_runs(self):
        manager = BotManager()
        completed = asyncio.create_task(asyncio.sleep(0))
        running = asyncio.create_task(asyncio.sleep(3600))
        await completed
        manager.instances = {"stopped": {"task": completed}, "running": {"task": running}}
        removed = asyncio.Event()
        async def stop(token):
            del manager.instances[token]
            removed.set()
        manager.stop_bot = AsyncMock(side_effect=stop)
        waiter = asyncio.create_task(manager.wait_all())
        try:
            await asyncio.wait_for(removed.wait(), timeout=2)
            manager.stop_bot.assert_awaited_once_with("stopped")
        finally:
            waiter.cancel()
            running.cancel()
            await asyncio.gather(waiter, running, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
