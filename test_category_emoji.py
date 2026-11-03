import types
import unittest
from aiogram.types import MessageEntity
from button_emoji import message_button_label
from keyboards import categories_kb, admin_categories_kb

class CategoryEmojiTests(unittest.TestCase):
    def test_extracts_utf16_entity_after_non_bmp_character(self):
        message=types.SimpleNamespace(text='🚀 ایران ⭐',entities=[MessageEntity(type='custom_emoji',offset=9,length=1,custom_emoji_id='12345')])
        self.assertEqual(message_button_label(message),('🚀 ایران','12345'))

    def test_plain_text_clears_icon(self):
        self.assertEqual(message_button_label(types.SimpleNamespace(text=' ایران ',entities=[])),('ایران',''))

    def test_renders_individual_category_icon(self):
        db=types.SimpleNamespace(get_setting=lambda key,default='':default,count_active_custom_config_products=lambda:0,is_full_access_bot=lambda value:False)
        cat={'id':1,'name':'ایران','is_active':1,'icon_custom_emoji_id':'12345'}
        self.assertEqual(categories_kb(db,[cat]).inline_keyboard[0][0].icon_custom_emoji_id,'12345')
        self.assertEqual(admin_categories_kb([cat]).inline_keyboard[0][0].icon_custom_emoji_id,'12345')

    def test_category_icon_persists_and_syncs_to_reseller(self):
        from test_managed_resellers import ManagedAgencyTests
        from managed_resellers import sync_bot
        fixture=ManagedAgencyTests();fixture.setUp()
        try:
            category=fixture.root.get_product(fixture.pid)['category_id']
            fixture.root.edit_category(category,'ایران','12345')
            fixture.root.edit_category(category,'ایران جدید')
            self.assertEqual(fixture.root.get_category(category)['icon_custom_emoji_id'],'12345')
            sync_bot(fixture.root,fixture.aid)
            local_category=fixture.local.get_category(fixture.local_product()['category_id'])
            self.assertEqual(local_category['icon_custom_emoji_id'],'12345')
            fixture.root.edit_category(category,'ایران جدید','')
            self.assertFalse(fixture.root.get_category(category)['icon_custom_emoji_id'])
        finally:fixture.tearDown()

if __name__=='__main__':unittest.main()
