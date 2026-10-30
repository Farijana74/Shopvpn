# -*- coding: utf-8 -*-
"""تست پارسر و تطبیق تشخیص نشت کانفیگ.

اجرا: python -m unittest test_leak_scanner -v
"""

import base64
import json
import os
import tempfile
import unittest

import leak_index
import leak_scanner as ls
from panel_providers.base import subscription_token

UUID = "8f14e45f-ceea-467a-9a57-0c9d1c2e7b3a"
PASSWORD = "S3cr3t-pass"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


class SubscriptionTokenTests(unittest.TestCase):
    def test_last_segment_is_token(self):
        self.assertEqual(subscription_token("https://panel.example.com/sub/abcDEF123/"), "abcDEF123")
        self.assertEqual(subscription_token("https://panel.example.com/sub/abcDEF123?x=1"), "abcDEF123")
        self.assertEqual(subscription_token(""), "")


class NormalizeAndHashTests(unittest.TestCase):
    def test_uuid_is_case_insensitive(self):
        upper = UUID.upper()
        self.assertEqual(leak_index.normalize_secret(upper), UUID)
        self.assertEqual(leak_index.fingerprint_hash(upper), leak_index.fingerprint_hash(UUID))

    def test_password_keeps_case(self):
        self.assertNotEqual(
            leak_index.fingerprint_hash("Abcdef12"), leak_index.fingerprint_hash("abcdef12"),
        )

    def test_hash_is_not_plaintext(self):
        digest = leak_index.fingerprint_hash(UUID)
        self.assertEqual(len(digest), 64)
        self.assertNotIn(UUID, digest)

    def test_short_values_are_ignored(self):
        self.assertEqual(len(leak_index.hash_entries(["abc", "", UUID])), 1)


class ExtractSecretsTests(unittest.TestCase):
    def test_vless_uuid(self):
        text = f"سلام، این کانفیگ: vless://{UUID}@example.com:443?security=tls&type=ws#my-config"
        self.assertIn(("uuid", UUID), ls.extract_secrets(text))

    def test_trojan_password_is_url_decoded(self):
        link = "trojan://S3cr3t%2Dpass@example.com:443?sni=example.com#t"
        self.assertIn(("password", PASSWORD), ls.extract_secrets(link))

    def test_vmess_base64_json_id(self):
        payload = json.dumps({"v": "2", "ps": "x", "add": "example.com", "id": UUID, "port": "443"})
        link = "vmess://" + _b64(payload)
        self.assertIn(("uuid", UUID), ls.extract_secrets(link))

    def test_vmess_urlsafe_base64_without_padding(self):
        payload = json.dumps({"id": UUID, "ps": "??>>"})
        encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        self.assertIn(("uuid", UUID), ls.extract_secrets("vmess://" + encoded))

    def test_shadowsocks_sip002(self):
        userinfo = _b64(f"aes-256-gcm:{PASSWORD}")
        link = f"ss://{userinfo}@example.com:8388#ss"
        self.assertIn(("password", PASSWORD), ls.extract_secrets(link))

    def test_shadowsocks_legacy_full_base64(self):
        link = "ss://" + _b64(f"chacha20-ietf-poly1305:{PASSWORD}@example.com:8388")
        self.assertIn(("password", PASSWORD), ls.extract_secrets(link))

    def test_subscription_url_token(self):
        text = "لینک اشتراک: https://panel.example.com/sub/TokenValue123/"
        self.assertIn(("token", "TokenValue123"), ls.extract_secrets(text))

    def test_broken_links_do_not_raise(self):
        text = "vmess://!!!not-base64 ss:// vless:// trojan://nopassword"
        self.assertEqual(ls.extract_secrets(text), [])

    def test_plain_text_has_no_secrets(self):
        self.assertEqual(ls.extract_secrets("سلام، کانفیگ من کار نمی‌کند"), [])


class PostLinkTests(unittest.TestCase):
    def test_public_channel_link(self):
        self.assertEqual(ls.post_link("mychannel", 123, 45), "https://t.me/mychannel/45")

    def test_private_channel_link(self):
        self.assertEqual(ls.post_link("", 1234567, 9), "https://t.me/c/1234567/9")


class _FakeDB:
    """فقط همان متدهایی که match_secrets/get_setting لازم دارند."""

    def __init__(self, indexed: dict, settings: dict = None):
        self.indexed = indexed  # {hash: row-dict}
        self.settings = settings or {}

    def lookup_credential_hashes(self, hashes):
        return [self.indexed[h] for h in hashes if h in self.indexed]

    def get_setting(self, key, default=""):
        return self.settings.get(key, default)


class MatchSecretsTests(unittest.TestCase):
    def test_match_returns_one_row_per_config(self):
        h_uuid = leak_index.fingerprint_hash(UUID)
        h_pass = leak_index.fingerprint_hash(PASSWORD)
        db = _FakeDB({
            h_uuid: {"hash": h_uuid, "kind": "secret", "config_id": 7, "user_id": 42,
                     "panel_server_id": 1, "username": "u7"},
            h_pass: {"hash": h_pass, "kind": "secret", "config_id": 7, "user_id": 42,
                     "panel_server_id": 1, "username": "u7"},
        })
        text = f"vless://{UUID}@a.com:443 trojan://{PASSWORD}@b.com:443"
        matches = ls.match_secrets(db, ls.extract_secrets(text))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["config_id"], 7)
        self.assertEqual(matches[0]["user_id"], 42)

    def test_no_match_for_unknown_secret(self):
        db = _FakeDB({})
        self.assertEqual(ls.match_secrets(db, ls.extract_secrets(f"vless://{UUID}@a.com:443")), [])

    def test_uppercase_uuid_in_post_still_matches(self):
        h = leak_index.fingerprint_hash(UUID)
        db = _FakeDB({h: {"hash": h, "kind": "secret", "config_id": 3, "user_id": 5,
                          "panel_server_id": 1, "username": "u3"}})
        matches = ls.match_secrets(db, ls.extract_secrets(f"vless://{UUID.upper()}@a.com:443"))
        self.assertEqual([m["config_id"] for m in matches], [3])


class CollectEntriesTests(unittest.TestCase):
    def test_provider_values_and_db_token_are_hashed(self):
        import asyncio

        class _Provider:
            async def get_fingerprints(self, username):
                return [UUID, PASSWORD]

        entries = asyncio.run(leak_index.collect_entries(
            _Provider(), "u1", "https://panel.example.com/sub/SubTokenXYZ"))
        self.assertEqual(entries[leak_index.fingerprint_hash(UUID)], "secret")
        self.assertEqual(entries[leak_index.fingerprint_hash(PASSWORD)], "secret")
        self.assertEqual(entries[leak_index.fingerprint_hash("SubTokenXYZ")], "token")


class DatabaseStoreTests(unittest.TestCase):
    """روی یک دیتابیس SQLite واقعی: ایندکس، کانال‌ها و یکتایی رویدادها."""

    @classmethod
    def setUpClass(cls):
        from db import Database

        cls._tmp = tempfile.TemporaryDirectory()
        cls.db = Database(os.path.join(cls._tmp.name, "test.db"))
        cls.db.init_db(owner_id=1)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls._tmp.cleanup()

    def test_index_replace_and_lookup(self):
        h = leak_index.fingerprint_hash(UUID)
        self.db.replace_credential_index(101, 1, "user101", 555, {h: "secret"})
        rows = self.db.lookup_credential_hashes([h])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], 555)
        self.db.replace_credential_index(101, 1, "user101", 555, {})
        self.assertEqual(self.db.lookup_credential_hashes([h]), [])

    def test_purge_removes_inactive_configs(self):
        h = leak_index.fingerprint_hash("PurgeMe-123456")
        self.db.replace_credential_index(202, 1, "gone", 556, {h: "secret"})
        self.db.purge_credential_index_except([])
        self.assertEqual(self.db.lookup_credential_hashes([h]), [])

    def test_post_seen_only_once(self):
        self.assertTrue(self.db.mark_leak_post_seen(-100, 1))
        self.assertFalse(self.db.mark_leak_post_seen(-100, 1))

    def test_channel_last_post_never_goes_back(self):
        self.db.add_leak_channel(-1001, "کانال", "chan", 10)
        self.db.set_leak_channel_last_post(-1001, 50)
        self.db.set_leak_channel_last_post(-1001, 20)
        self.assertEqual(self.db.get_leak_channel(-1001)["last_post_id"], 50)

    def test_event_is_unique_per_config_and_post(self):
        first = self.db.create_leak_event(42, 303, -1001, "کانال", "https://t.me/chan/1", "uuid")
        second = self.db.create_leak_event(42, 303, -1001, "کانال", "https://t.me/chan/1", "uuid")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_warning_counts_per_user(self):
        e1 = self.db.create_leak_event(77, 401, 0, "", "https://t.me/a/1", "token")
        e2 = self.db.create_leak_event(77, 402, 0, "", "https://t.me/a/2", "token")
        self.assertTrue(self.db.mark_leak_event_warned(e1))
        self.assertFalse(self.db.mark_leak_event_warned(e1))
        self.assertTrue(self.db.mark_leak_event_warned(e2))
        self.assertEqual(self.db.count_user_leak_warnings(77), 2)
        self.db.set_leak_event_status(e1, "rotated")
        self.assertEqual(self.db.count_user_leak_warnings(77), 2)


if __name__ == "__main__":
    unittest.main()
