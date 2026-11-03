import asyncio
import types
import unittest
from unittest.mock import AsyncMock
import test_managed_resellers as agency_tests
from managed_resellers import sync_bot, reserve, notify_root_sales
import keyboards

class ResellerAdminFeatureTests(unittest.TestCase):
    def setUp(self):
        self.fixture=agency_tests.ManagedAgencyTests();self.fixture.setUp()
        self.root,self.local=self.fixture.root,self.fixture.local
    def tearDown(self):self.fixture.tearDown()

    def test_categories_keep_local_names_and_status_across_sync(self):
        original=self.root.get_product(self.fixture.pid)['category_id']
        cat_id=self.fixture.local_product()['category_id']
        self.local.edit_category(cat_id,'فروشگاه من','12345')
        self.local.toggle_category(cat_id)
        self.root.edit_category(original,'نام جدید اصلی','67890')
        sync_bot(self.root,self.fixture.aid)
        cat=self.local.get_category(cat_id)
        self.assertEqual(cat['name'],'فروشگاه من')
        self.assertEqual(cat['icon_custom_emoji_id'],'12345')
        self.assertEqual(cat['is_active'],0)
        self.assertEqual(cat['upstream_category_id'],original)
        self.assertEqual(self.fixture.local_product()['category_id'],cat_id)
        self.assertFalse(self.fixture.local_product()['is_active'])
        self.assertTrue(self.root.get_category(original)['is_active'])
        self.assertEqual(self.local.toggle_product(self.fixture.local_product()['id']),0)
        with self.assertRaises(ValueError):self.local.toggle_product(self.fixture.local_product()['id'])
        self.local.toggle_category(cat_id)
        self.assertFalse(self.fixture.local_product()['is_active'])
        self.local.toggle_product(self.fixture.local_product()['id'])
        self.assertTrue(self.fixture.local_product()['is_active'])
        with self.local._get_conn() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM categories WHERE upstream_category_id=?',(original,)).fetchone()[0],1)

    def test_managed_admin_menu_has_real_user_route_and_appearance_settings(self):
        from handlers_admin import create_admin_router
        router=create_admin_router(self.local,False)
        menu=keyboards.admin_panel_kb(self.local,False)
        callbacks=[b.callback_data for row in menu.inline_keyboard for b in row]
        self.assertIn('adm_stats_user',callbacks)
        self.assertIn('adm_cat:appearance',callbacks)
        self.assertIn('adm_categories',callbacks)
        callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='cb_admin_stats_user')
        call=types.SimpleNamespace(data='adm_stats_user',from_user=types.SimpleNamespace(id=101),message=types.SimpleNamespace(edit_text=AsyncMock(),answer=AsyncMock()),answer=AsyncMock())
        state=AsyncMock()
        asyncio.run(callback(call,state))
        state.set_state.assert_awaited_once()
        call.message.edit_text.assert_awaited_once()
        cats=keyboards.admin_categories_kb(self.local.get_categories(False),managed=True)
        cb=[b.callback_data for row in cats.inline_keyboard for b in row]
        self.assertNotIn('adm_cat_add',cb)
        self.assertFalse(any(c.startswith('adm_cat_del:') for c in cb))
        with self.assertRaises(ValueError):self.local.add_category('unauthorized')

    def test_main_bot_gets_sale_notice_once_and_failed_sends_retry(self):
        purchase=reserve(self.root,self.fixture.aid,99,self.fixture.pid,1)
        with self.root._get_conn() as conn:
            conn.execute("UPDATE managed_purchases SET status='completed' WHERE root_order_id=?",(purchase,))
        bot=types.SimpleNamespace(send_message=AsyncMock(side_effect=[RuntimeError('offline'),True]))
        asyncio.run(notify_root_sales(self.root,bot))
        with self.root._get_conn() as conn:
            self.assertEqual(conn.execute('SELECT root_notified FROM managed_purchases WHERE root_order_id=?',(purchase,)).fetchone()[0],0)
        asyncio.run(notify_root_sales(self.root,bot))
        asyncio.run(notify_root_sales(self.root,bot))
        self.assertEqual(bot.send_message.await_count,2)
        self.assertEqual(bot.send_message.call_args.args[0],1)
        text=bot.send_message.call_args.args[1]
        self.assertIn('@parent',text)
        self.assertIn('99',text)
        self.assertIn('100',text)
        self.assertEqual(self.root.get_wallet_credit(101),9900)
