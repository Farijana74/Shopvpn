import types
import unittest
from aiogram.types import MessageEntity
import keyboards
import product_emoji
from managed_resellers import sync_bot
import test_managed_resellers as fixtures


class ProductEmojiTests(unittest.TestCase):
    setUp = fixtures.ManagedAgencyTests.setUp
    tearDown = fixtures.ManagedAgencyTests.tearDown
    local_product = fixtures.ManagedAgencyTests.local_product

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
