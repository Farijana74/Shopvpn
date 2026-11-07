"""Restore a usable menu after a completed interaction."""
import asyncio
import os
import json

MENU_TEXT_KEY = 'handlers_user.auto_f368dac9'
MENU_FORMAT_KEY = 'menu_text_custom_emoji'


def save_menu_text(db, message, text):
    """Keep Telegram's custom emoji entities alongside the plain editable text."""
    from aiogram.utils.text_decorations import html_decoration
    entities = [e for e in (message.entities or []) if e.type == 'custom_emoji' and e.custom_emoji_id]
    formatted = html_decoration.unparse(message.text, entities).strip() if entities else ''
    db.set_text(MENU_TEXT_KEY, text)
    db.set_setting(MENU_FORMAT_KEY, json.dumps({'text': text, 'html': formatted}, ensure_ascii=False) if formatted else '')


def menu_text(db):
    text = db.get_text(MENU_TEXT_KEY, '📋 منو:')
    try:
        data = json.loads(db.get_setting(MENU_FORMAT_KEY, '') or '{}')
        if data.get('text') == text and data.get('html'):
            return data['html'], {'parse_mode': 'HTML'}
    except (ValueError, TypeError, AttributeError):
        pass
    return text, {}


def main_menu_markup(db, user_id):
    import keyboards
    from config import DB_PATH
    main = os.path.abspath(db.db_path) == os.path.abspath(DB_PATH)
    markup = keyboards.inline_menu_for_user(db, user_id, main)
    return markup or keyboards.menu_for_user(db, user_id, main)


async def restore_main_menu(bot, db, user_id):
    markup = await asyncio.to_thread(main_menu_markup, db, user_id)
    text, options = menu_text(db)
    await bot.send_message(user_id, text, reply_markup=markup, **options)
