import asyncio
import types
import unittest
from unittest.mock import AsyncMock

from aiogram.types import MessageEntity

from broadcast_i18n import send_scheduled_broadcast
from handlers_admin import create_admin_router
import test_managed_resellers as fixtures


class BroadcastEntitiesTests(unittest.TestCase):
    setUp = fixtures.ManagedAgencyTests.setUp
    tearDown = fixtures.ManagedAgencyTests.tearDown

    def test_scheduled_text_preserves_premium_emoji_and_offsets(self):
        text = '  ⭐ پیام  '
        entity = MessageEntity(type='custom_emoji', offset=2, length=1, custom_emoji_id='123456789')
        router = create_admin_router(self.root, True)
        handler = next(h.callback for h in router.message.handlers if h.callback.__name__ == 'process_broadcast')
        data = {}
        state = types.SimpleNamespace(update_data=AsyncMock(side_effect=lambda **values: data.update(values)), set_state=AsyncMock())
        message = types.SimpleNamespace(from_user=types.SimpleNamespace(id=1), media_group_id=None, chat=types.SimpleNamespace(id=1),
                                        message_id=11, text=text, caption=None, entities=[entity], answer=AsyncMock())
        bot = AsyncMock()
        async def run():
            await handler(message, state, bot)
            self.root.create_scheduled_broadcast(1, data['broadcast_text'], '2000-01-01', data['broadcast_entities'])
            job = self.root.get_due_scheduled_broadcasts('2000-01-02')[0]
            await send_scheduled_broadcast(bot, 42, job['message_text'], message_entities=job['message_entities'])
        asyncio.run(run())
        args = bot.send_message.await_args
        self.assertEqual(args.args, (42, text))
        self.assertEqual(args.kwargs['entities'][0], entity)
        self.assertIsNone(args.kwargs['parse_mode'])
        bot.copy_message.assert_awaited_once()

    def test_old_scheduled_messages_still_send(self):
        self.root.create_scheduled_broadcast(1, 'متن قدیمی', '2000-01-01')
        job = self.root.get_due_scheduled_broadcasts('2000-01-02')[0]
        bot = AsyncMock()
        asyncio.run(send_scheduled_broadcast(bot, 42, job['message_text'], message_entities=job['message_entities']))
        bot.send_message.assert_awaited_once_with(42, 'متن قدیمی')
