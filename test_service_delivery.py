import ast
import asyncio
import logging
from customer_messages import customer_error
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from database import Database
from happ_delivery import HappDeliveryError
from service_delivery import service_delivery_links, service_delivery_mode, rotate_service_credentials


class ServiceDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = dict(id=3, order_id=9, username='client', panel_server_id=7,
                            subscription_url='https://example.invalid/old', delivery_mode='happ')
        self.db = MagicMock()
        self.db.get_panel_server.return_value = dict(panel_type='3xui', is_active=1)
        self.provider = AsyncMock()
        self.provider.get_happ_link.return_value = 'happ://crypt5/encrypted'
        self.provider_patch = patch('service_delivery.get_provider', return_value=self.provider)
        self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)

    async def test_happ_card_links_never_include_raw_subscription(self):
        self.assertEqual(await service_delivery_links(self.db, self.service), ['happ://crypt5/encrypted'])

    async def test_panel_failure_does_not_expose_normal_link(self):
        self.provider.get_happ_link.side_effect = RuntimeError('unavailable')
        with self.assertRaises(HappDeliveryError):
            await service_delivery_links(self.db, self.service)

    async def test_non_happ_panel_response_is_rejected(self):
        self.provider.get_happ_link.return_value = self.service['subscription_url']
        with self.assertRaises(HappDeliveryError):
            await service_delivery_links(self.db, self.service)

    async def test_normal_service_never_calls_happ_api(self):
        service = {**self.service, 'delivery_mode': 'normal'}
        self.assertEqual(await service_delivery_links(self.db, service), [service['subscription_url']])
        self.provider.get_happ_link.assert_not_awaited()

    async def test_both_retains_both_formats(self):
        service = {**self.service, 'delivery_mode': 'both'}
        self.assertEqual(await service_delivery_links(self.db, service), [service['subscription_url'], 'happ://crypt5/encrypted'])

    async def test_rotation_persists_new_subscription_then_generates_new_happ(self):
        self.provider.revoke_credentials.return_value = types.SimpleNamespace(subscription_url='https://example.invalid/new')
        def save(config_id, url):
            self.service['subscription_url'] = url
        self.db.update_custom_config_subscription_url.side_effect = save
        async def encrypt(username):
            self.assertEqual(self.service['subscription_url'], 'https://example.invalid/new')
            return 'happ://crypt5/new'
        self.provider.get_happ_link.side_effect = encrypt
        await rotate_service_credentials(self.db, self.service, self.provider)
        self.assertEqual(await service_delivery_links(self.db, self.service), ['happ://crypt5/new'])
        self.db.update_custom_config_subscription_url.assert_called_once_with(3, 'https://example.invalid/new')

    async def test_failed_encryption_after_rotation_keeps_new_upstream(self):
        self.provider.revoke_credentials.return_value = types.SimpleNamespace(subscription_url='https://example.invalid/new')
        await rotate_service_credentials(self.db, self.service, self.provider)
        self.provider.get_happ_link.side_effect = RuntimeError('unavailable')
        with self.assertRaises(HappDeliveryError):
            await service_delivery_links(self.db, self.service)
        self.db.update_custom_config_subscription_url.assert_called_once_with(3, 'https://example.invalid/new')

    async def test_saved_format_wins_over_edited_product(self):
        self.db.get_product.return_value = {'delivery_mode': 'normal'}
        self.assertEqual(service_delivery_mode(self.db, self.service), 'happ')
        self.db.get_product.assert_not_called()

    async def test_legacy_service_resolves_order_product(self):
        service = {**self.service, 'delivery_mode': None}
        self.db.get_order.return_value = {'product_id': 4}
        self.db.get_product.return_value = {'delivery_mode': 'happ'}
        self.assertEqual(service_delivery_mode(self.db, service), 'happ')

    async def test_real_service_card_displays_happ_without_raw_link(self):
        # Exercise the actual nested renderer without importing Telegram routers.
        tree = ast.parse(Path('handlers_user.py').read_text())
        renderer = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == '_my_orders_item_text')
        namespace = dict(asyncio=asyncio, db=self.db, get_provider=lambda server: self.provider,
                         escape_md=lambda x: x, to_jalali_str=lambda *a, **k: 'date',
                         service_delivery_links=service_delivery_links, HappDeliveryError=HappDeliveryError,
                         fetch_sub_info=AsyncMock(return_value={}), format_sub_info_fa=lambda info: '',
                         _item_has_individual_links=lambda item: False, logging=logging,customer_error=customer_error)
        exec(compile(ast.Module(body=[renderer], type_ignores=[]), 'handlers_user.py', 'exec'), namespace)
        service = {**self.service, 'display_name': None, 'volume_gb': 10, 'duration_days': 30,
                   'created_at': 'date', 'user_limit': 1, 'start_on_first_use': 0, 'expires_at': None}
        text = await namespace['_my_orders_item_text']({'kind': 'custom', 'custom': service})
        self.assertIn('happ://crypt5/encrypted', text)
        self.assertNotIn(service['subscription_url'], text)
        self.provider.get_user.assert_not_awaited()
        namespace['service_delivery_links']=AsyncMock(side_effect=HappDeliveryError('نرخ تأمین نماینده روی پنل بالادست نامعتبر است.'))
        failed=await namespace['_my_orders_item_text']({'kind':'custom','custom':service})
        for private in ('بالادست','نرخ تأمین','نماینده',service['subscription_url']):self.assertNotIn(private,failed)


class ServiceDeliveryDatabaseTests(unittest.TestCase):
    def test_migration_and_creation_preserve_service_format(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / 'test.db'))
            db._sync_text_registry = lambda: None
            worker = types.SimpleNamespace(ensure_worker=lambda database: None)
            try:
                with patch.dict('sys.modules', {'bulk_gifts': worker}):
                    db.init_db(owner_id=123)
                    with db._get_conn() as conn:
                        category = conn.execute("INSERT INTO categories(name) VALUES ('test')").lastrowid
                        product = conn.execute("INSERT INTO products(category_id,name,price,delivery_mode) VALUES (?,'vpn',1,'happ')", (category,)).lastrowid
                        order = conn.execute("INSERT INTO orders(user_id,product_id) VALUES (123,?)", (product,)).lastrowid
                        server = conn.execute("INSERT INTO panel_servers(name,api_url,panel_type) VALUES ('test','https://example.invalid','3xui')").lastrowid
                    config_id = db.add_custom_config(123, server, 'client', 10, 30, 'https://example.invalid/old', order_id=order, source='direct_product')
                    with db._get_conn() as conn:
                        self.assertEqual(conn.execute('SELECT delivery_mode FROM custom_configs WHERE id=?', (config_id,)).fetchone()[0], 'happ')
                        conn.execute('UPDATE custom_configs SET delivery_mode=NULL WHERE id=?', (config_id,))
                    db.init_db(owner_id=123)
                    with db._get_conn() as conn:
                        self.assertEqual(conn.execute('SELECT delivery_mode FROM custom_configs WHERE id=?', (config_id,)).fetchone()[0], 'happ')
                        conn.execute("UPDATE products SET delivery_mode='normal' WHERE id=?", (product,))
                    db.init_db(owner_id=123)
                    with db._get_conn() as conn:
                        self.assertEqual(conn.execute('SELECT delivery_mode FROM custom_configs WHERE id=?', (config_id,)).fetchone()[0], 'happ')
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()
