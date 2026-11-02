import asyncio
import signal
import unittest
from unittest.mock import patch

from service_lifecycle import run_service


class ServiceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_sigterm_cancels_main_and_runs_cleanup(self):
        loop = asyncio.get_running_loop()
        handlers = {}
        cleaned = []
        async def main():
            try:
                handlers[signal.SIGTERM]()
                handlers[signal.SIGTERM]()
                await asyncio.sleep(0)
            finally:
                cleaned.append(True)
        with patch.object(loop, 'add_signal_handler', side_effect=lambda sig, fn: handlers.update({sig: fn})), patch.object(loop, 'remove_signal_handler') as remove:
            await run_service(main)
        self.assertEqual(cleaned, [True])
        self.assertEqual(remove.call_count, 2)

    async def test_external_cancellation_is_preserved(self):
        loop = asyncio.get_running_loop()
        async def main():
            raise asyncio.CancelledError()
        with patch.object(loop, 'add_signal_handler'), patch.object(loop, 'remove_signal_handler'):
            with self.assertRaises(asyncio.CancelledError):
                await run_service(main)

    async def test_normal_completion_removes_handlers(self):
        loop = asyncio.get_running_loop()
        async def main():
            return
        with patch.object(loop, 'add_signal_handler'), patch.object(loop, 'remove_signal_handler') as remove:
            await run_service(main)
        self.assertEqual(remove.call_count, 2)
