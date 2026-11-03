"""Restore a usable menu after a completed interaction."""
import asyncio
import os


def main_menu_markup(db, user_id):
    import keyboards
    from config import DB_PATH
    main = os.path.abspath(db.db_path) == os.path.abspath(DB_PATH)
    markup = keyboards.inline_menu_for_user(db, user_id, main)
    return markup or keyboards.menu_for_user(db, user_id, main)


async def restore_main_menu(bot, db, user_id):
    markup = await asyncio.to_thread(main_menu_markup, db, user_id)
    await bot.send_message(user_id, '📋 منو:', reply_markup=markup)
