import asyncio
import types
import unittest
from unittest.mock import AsyncMock
from aiogram import Router
import test_managed_resellers as fixtures
from managed_resellers import sync_bot
from managed_reseller_ui import create_agency_router,AgencyFlow
import keyboards

class ProductDisplayTests(unittest.TestCase):
    setUp=fixtures.ManagedAgencyTests.setUp
    tearDown=fixtures.ManagedAgencyTests.tearDown
    local_product=fixtures.ManagedAgencyTests.local_product

    def test_rename_is_local_and_survives_sync(self):
        product=self.local_product()
        self.local.edit_product(product['id'],name='سرویس اختصاصی')
        self.root.edit_product(self.pid,name='نام جدید اصلی')
        sync_bot(self.root,self.aid)
        self.assertEqual(self.local.get_product(product['id'])['name'],'سرویس اختصاصی')
        self.assertEqual(self.root.get_product(self.pid)['name'],'نام جدید اصلی')

    def test_disabled_product_hidden_but_admin_can_reenable(self):
        p=self.local_product()
        self.local.toggle_product(p['id']);sync_bot(self.root,self.aid)
        all_products=self.local.get_products(p['category_id'],active_only=False)
        self.assertEqual(len(all_products),1)
        self.assertEqual(self.local.get_products(p['category_id'],active_only=True),[])
        markup=keyboards.products_kb(self.local,all_products,p['category_id'])
        self.assertFalse(any(b.callback_data==f"prod:{p['id']}" for row in markup.inline_keyboard for b in row))
        self.local.toggle_product(p['id']);sync_bot(self.root,self.aid)
        active=self.local.get_products(p['category_id'],active_only=True)
        labels=[b.text for row in keyboards.products_kb(self.local,active,p['category_id']).inline_keyboard for b in row if b.callback_data==f"prod:{p['id']}"]
        self.assertEqual(len(labels),1)
        self.assertFalse(labels[0].startswith('✅'))

    def test_reseller_editor_offers_name_change(self):
        product=self.local_product();router=create_agency_router(self.local,False)
        callback=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='agency_callback')
        name_handler=next(h.callback for h in router.message.handlers if h.callback.__name__=='name_message')
        state=AsyncMock();state.get_data.return_value={'agency_product':product['id']}
        call=types.SimpleNamespace(data=f"agency:edit:{product['id']}",from_user=types.SimpleNamespace(id=101),message=types.SimpleNamespace(edit_text=AsyncMock(),answer=AsyncMock()),answer=AsyncMock())
        message=types.SimpleNamespace(text='نام فروشگاه',from_user=types.SimpleNamespace(id=101),answer=AsyncMock())
        async def run():
            await callback(call,state)
            rows=call.message.edit_text.call_args.kwargs['reply_markup'].inline_keyboard
            self.assertTrue(any(b.callback_data==f"agency:name:{product['id']}" for row in rows for b in row))
            call.data=f"agency:name:{product['id']}";await callback(call,state)
            state.set_state.assert_awaited_with(AgencyFlow.name)
            await name_handler(message,state)
            self.assertEqual(self.local.get_product(product['id'])['name'],'نام فروشگاه')
        asyncio.run(run())
