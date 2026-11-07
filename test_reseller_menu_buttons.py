import types
import unittest
from unittest.mock import AsyncMock

from aiogram.types import MessageEntity
import keyboards
from handlers_admin import create_admin_router
import test_managed_resellers as fixtures


class ResellerMenuButtonTests(unittest.TestCase):
    setUp = fixtures.ManagedAgencyTests.setUp
    tearDown = fixtures.ManagedAgencyTests.tearDown

    def test_open_button_editor_escapes_current_name(self):
        import asyncio
        from states import AdminEditButton
        router = create_admin_router(self.root, True)
        callback = next(h.callback for h in router.callback_query.handlers if h.callback.__name__ == 'cb_admin_btn_edit')
        self.root.set_setting('btn_reseller_tiers', 'همکاری <موبایل> & فروش')
        state = AsyncMock()
        call = types.SimpleNamespace(data='adm_btn_edit:btn_reseller_tiers', from_user=types.SimpleNamespace(id=1), message=types.SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        async def run():
            await callback(call, state)
            self.assertIn('همکاری &lt;موبایل&gt; &amp; فروش', call.message.edit_text.await_args.args[0])
            state.set_state.assert_awaited_with(AdminEditButton.waiting_text)
        asyncio.run(run())

    def test_editing_each_label_and_color_updates_both_menus(self):
        import asyncio
        router = create_admin_router(self.root, True)
        edit = next(h.callback for h in router.message.handlers if h.callback.__name__ == 'process_edit_button')
        state = AsyncMock()
        async def run():
            for key, label in [('btn_reseller_tiers', 'همکاری موبایل'), ('btn_reseller_panel', 'پنل همکار')]:
                state.get_data.return_value = {'setting_key': key}
                message = types.SimpleNamespace(text='⭐ ' + label, entities=[MessageEntity(type='custom_emoji', offset=0, length=1, custom_emoji_id='123456789')], from_user=types.SimpleNamespace(id=1), answer=AsyncMock())
                await edit(message, state)
                self.root.set_setting(key + '_style', 'success')
        asyncio.run(run())
        items = keyboards._menu_items(self.root, False, True, True, True, False)
        selected = {key: (text, style) for key, text, style in items}
        self.assertEqual(selected['btn_reseller_tiers'], ('همکاری موبایل', 'success'))
        self.assertEqual(selected['btn_reseller_panel'], ('پنل همکار', 'success'))
        with unittest.mock.patch.object(keyboards, '_menu_items', return_value=items):
            for markup in (keyboards.main_menu_kb(self.root, False, True), keyboards.main_menu_inline_kb(self.root, False, True)):
                rows = getattr(markup, 'inline_keyboard', None) or markup.keyboard
                buttons = {button.text: button for row in rows for button in row}
                for label in ('همکاری موبایل', 'پنل همکار'):
                    self.assertEqual(buttons[label].style, 'success')
                    self.assertEqual(buttons[label].icon_custom_emoji_id, '123456789')
        labels = [b.text for row in keyboards.admin_edit_buttons_kb(self.root).inline_keyboard for b in row]
        self.assertTrue(any('همکاری موبایل' in text for text in labels))
        self.assertTrue(any('پنل همکار' in text for text in labels))
