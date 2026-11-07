import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.types import MessageEntity

from menu_restore import menu_text, restore_main_menu, save_menu_text


class FakeDB:
    def __init__(self):
        self.settings = {}

    def set_text(self, key, value):
        self.settings[key] = value

    def get_text(self, key, default):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def get_setting(self, key, default):
        return self.settings.get(key, default)


class MenuEmojiTests(unittest.TestCase):
    def test_emoji_ids_survive_utf16_offsets_and_text_is_escaped(self):
        db = FakeDB()
        message = types.SimpleNamespace(text=' 😀 ⭐ & منو ', entities=[
            MessageEntity(type='custom_emoji', offset=4, length=1, custom_emoji_id='123456789'),
        ])
        save_menu_text(db, message, message.text.strip())
        rendered, options = menu_text(db)
        self.assertEqual(rendered, '😀 <tg-emoji emoji-id="123456789">⭐</tg-emoji> &amp; منو')
        self.assertEqual(options, {'parse_mode': 'HTML'})
        bot = types.SimpleNamespace(send_message=AsyncMock())
        async def run():
            with patch('menu_restore.main_menu_markup', return_value='keyboard'):
                await restore_main_menu(bot, db, 42)
        asyncio.run(run())
        bot.send_message.assert_awaited_once_with(42, rendered, reply_markup='keyboard', parse_mode='HTML')

    def test_plain_replacement_or_web_edit_does_not_reuse_old_emoji(self):
        db = FakeDB()
        message = types.SimpleNamespace(text='⭐', entities=[MessageEntity(type='custom_emoji', offset=0, length=1, custom_emoji_id='123')])
        save_menu_text(db, message, '⭐')
        db.set_text('handlers_user.auto_f368dac9', 'منوی جدید')
        self.assertEqual(menu_text(db), ('منوی جدید', {}))
        save_menu_text(db, types.SimpleNamespace(text='⭐', entities=[]), '⭐')
        self.assertEqual(menu_text(db), ('⭐', {}))
