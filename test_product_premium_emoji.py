import types
import unittest
import asyncio
from unittest.mock import AsyncMock
from aiogram.types import MessageEntity
import keyboards
import product_emoji
from managed_resellers import sync_bot
import test_managed_resellers as fixtures


class ProductEmojiTests(unittest.TestCase):
    setUp = fixtures.ManagedAgencyTests.setUp
    tearDown = fixtures.ManagedAgencyTests.tearDown
    local_product = fixtures.ManagedAgencyTests.local_product

    def test_new_product_retains_custom_emoji_from_creation_flow(self):
        from handlers_admin import create_admin_router
        router = create_admin_router(self.root, True)
        messages = {h.callback.__name__: h.callback for h in router.message.handlers}
        callbacks = {h.callback.__name__: h.callback for h in router.callback_query.handlers}
        data = {'category_id': self.root.get_product(self.pid)['category_id'], 'price': 100,
                'description': '', 'duration_days': 30}
        state = types.SimpleNamespace(update_data=AsyncMock(side_effect=lambda **values: data.update(values)),
                                      set_state=AsyncMock(), get_data=AsyncMock(side_effect=lambda: data), clear=AsyncMock())
        message = types.SimpleNamespace(text='⭐ محصول تازه', entities=[MessageEntity(type='custom_emoji', offset=0, length=1, custom_emoji_id='123456789')], answer=AsyncMock())
        call = types.SimpleNamespace(from_user=types.SimpleNamespace(id=1), message=types.SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        async def run():
            await messages['process_product_name'](message, state)
            await callbacks['cb_newprod_pm_done'](call, state)
        asyncio.run(run())
        product = self.root.get_products(data['category_id'], active_only=False)[-1]
        button = keyboards.products_kb(self.root, [product], data['category_id']).inline_keyboard[0][0]
        self.assertEqual(product['name'], message.text)
        self.assertEqual(button.icon_custom_emoji_id, '123456789')

    def test_new_product_is_appended_after_reordered_products(self):
        category = self.root.get_product(self.pid)['category_id']
        second = self.root.add_product(category, 'دوم', 100)
        self.root.reorder_products(category, [second, self.pid])
        new = self.root.add_product(category, 'جدید', 100)
        self.assertEqual([p['id'] for p in self.root.get_products(category, False)], [second, self.pid, new])

    def test_reorder_ignores_hidden_volume_templates(self):
        category = self.root.get_product(self.pid)['category_id']
        second = self.root.add_product(category, 'دوم', 100)
        hidden = self.root.add_product(category, 'داخلی', 100)
        self.root.set_setting(f'volume_template:{hidden}', '1')
        self.root.reorder_products(category, [second, self.pid])
        self.assertEqual([p['id'] for p in self.root.get_products(category, False)], [second, self.pid])

    def test_icon_is_used_and_sync_preserves_reseller_choice(self):
        msg = types.SimpleNamespace(text='⭐ سرویس', entities=[MessageEntity(type='custom_emoji', offset=0, length=1, custom_emoji_id='123456789')])
        self.root.edit_product(self.pid, name=msg.text)
        product_emoji.save(self.root, self.pid, msg, msg.text)
        product = self.root.get_product(self.pid)
        button = keyboards.products_kb(self.root, [product], product['category_id']).inline_keyboard[0][0]
        self.assertEqual(button.icon_custom_emoji_id, '123456789')
        self.assertNotIn('⭐', button.text)
        sync_bot(self.root, self.aid)
        local = self.local_product()
        self.assertEqual(product_emoji.get(self.local, local)['icon'], '123456789')
        local_msg = types.SimpleNamespace(text='🚀 فروش', entities=[MessageEntity(type='custom_emoji', offset=0, length=2, custom_emoji_id='987654321')])
        self.local.edit_product(local['id'], name=local_msg.text)
        product_emoji.save(self.local, local['id'], local_msg, local_msg.text)
        sync_bot(self.root, self.aid)
        self.assertEqual(product_emoji.get(self.local, self.local_product())['icon'], '987654321')
        product_emoji.save(self.local, local['id'], types.SimpleNamespace(text='🚀 فروش', entities=[]), '🚀 فروش')
        self.assertEqual(product_emoji.get(self.local, self.local_product()), {})
