# -*- coding: utf-8 -*-
"""تست کشف و عضویت هوشمند (منطق خالص + دیتابیس واقعی + جریان کامل با کلاینت ساختگی).

اجرا: python -m unittest test_leak_discovery -v
نیازی به telethon/aiogram نیست؛ ماژول‌های تلگرام در تست جعل می‌شوند.
"""

import asyncio
import os
import random
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timezone

import leak_discovery as ld

NOW = time.time()
CFG = "vless://8f14e45f-ceea-467a-9a57-0c9d1c2e7b3a@example.com:443?security=tls#x"


def _sample(n_cfg, n_plain, age_hours=2):
    out = []
    for i in range(n_cfg):
        out.append((f"کانفیگ جدید {CFG}", NOW - age_hours * 3600 - i * 1800))
    for i in range(n_plain):
        out.append(("سلام دوستان امروز چه خبر", NOW - age_hours * 3600 - (n_cfg + i) * 1800))
    return out


class ExtractTests(unittest.TestCase):
    def test_config_link_count(self):
        text = f"{CFG}\nhttps://panel.example.com/sub/AbCdEf123456\nhello"
        self.assertEqual(ld.count_config_links(text), 2)
        self.assertEqual(ld.count_config_links("هیچی"), 0)
        self.assertEqual(ld.count_config_links("hy2://secretpass@h.example.com:443#a"), 1)

    def test_refs_users_and_invites(self):
        text = (
            "کانال ما https://t.me/free_cfg_channel و t.me/+AbCdEfGhIjKlMnOpQr و @another_cfg_ch "
            "و t.me/joinchat/AbCdEfGhIjKlMnOpQrSt و https://t.me/proxy?server=1 و me@gmail.com "
            "و t.me/some_bot و t.me/c/12345/6 و vless://uuid@host.example.com:1"
        )
        refs = ld.extract_refs(text)
        self.assertIn(("user", "free_cfg_channel"), refs)
        self.assertIn(("user", "another_cfg_ch"), refs)
        self.assertIn(("invite", "AbCdEfGhIjKlMnOpQr"), refs)
        self.assertIn(("invite", "AbCdEfGhIjKlMnOpQrSt"), refs)
        names = [v for k, v in refs if k == "user"]
        self.assertNotIn("gmail", names)
        self.assertNotIn("some_bot", names)
        self.assertNotIn("proxy", names)
        self.assertEqual(len(names), 2)

    def test_refs_dedup_case_insensitive(self):
        refs = ld.extract_refs("t.me/Free_Cfg_Chan و t.me/free_cfg_chan و @FREE_CFG_CHAN")
        self.assertEqual(len(refs), 1)

    def test_message_text_includes_hidden_links(self):
        ent = types.SimpleNamespace(url="https://t.me/hidden_chan")
        btn = types.SimpleNamespace(url="vless://abcdefgh@h.example.com:1")
        row = types.SimpleNamespace(buttons=[btn])
        msg = types.SimpleNamespace(
            message="کلیک کن", entities=[ent], reply_markup=types.SimpleNamespace(rows=[row]),
        )
        text = ld.message_text(msg)
        self.assertIn("hidden_chan", text)
        self.assertIn("vless://", text)


class ScoreTests(unittest.TestCase):
    def test_config_channel_scores_high(self):
        r = ld.score_channel(_sample(30, 10), "Free V2ray Configs", "free_v2ray", 50000, NOW)
        self.assertGreaterEqual(r["score"], 70)
        self.assertEqual(r["config_posts"], 30)

    def test_chat_channel_without_configs_scores_low(self):
        r = ld.score_channel(_sample(0, 40), "گپ دوستانه", "chat_group", 80000, NOW)
        self.assertLessEqual(r["score"], 20)

    def test_dead_channel_is_capped(self):
        r = ld.score_channel(_sample(30, 10, age_hours=24 * 200), "v2ray configs", "v2ray_cfg", 50000, NOW)
        self.assertLessEqual(r["score"], 25)

    def test_tiny_sample_is_not_trusted(self):
        small = ld.score_channel(_sample(2, 0), "x channel", "", 0, NOW)
        big = ld.score_channel(_sample(30, 10), "x channel", "", 0, NOW)
        self.assertLess(small["score"], big["score"])

    def test_no_preview_uses_title_and_members(self):
        good = ld.score_channel([], "کانفیگ رایگان v2ray", "", 100000, NOW)
        bad = ld.score_channel([], "آشپزی", "", 100000, NOW)
        self.assertGreaterEqual(good["score"], 35)
        self.assertLess(bad["score"], 35)
        self.assertFalse(good["preview"])

    def test_title_relevance_ignores_short_substrings(self):
        self.assertEqual(ld.title_relevance("subway lovers"), 0.0)
        self.assertGreater(ld.title_relevance("Free VLESS channel"), 0.0)


class QueryTests(unittest.TestCase):
    def _rows(self):
        return [
            {"query": "fresh", "runs": 0, "good_found": 0, "last_run_at": 0},
            {"query": "winner", "runs": 10, "good_found": 9, "last_run_at": 0},
            {"query": "loser", "runs": 10, "good_found": 0, "last_run_at": 0},
            {"query": "recent", "runs": 0, "good_found": 0, "last_run_at": NOW - 60},
        ]

    def test_unrun_first_and_recent_excluded(self):
        picked = ld.pick_queries(self._rows(), NOW, 3, random.Random(1))
        self.assertEqual(picked[0], "fresh")
        self.assertNotIn("recent", picked)
        self.assertEqual(picked[1], "winner")

    def test_learn_queries(self):
        titles = ["Reality Fast Nodes", "reality_nodes pro", "Free VPN"]
        learned = ld.learn_queries(titles, ["vpn"], limit=3)
        self.assertEqual(set(learned[:2]), {"reality", "nodes"})
        self.assertNotIn("vpn", learned)
        self.assertNotIn("free", learned)

    def test_join_budget(self):
        self.assertEqual(ld.join_budget(0, 5, 0, 100), 5)
        self.assertEqual(ld.join_budget(5, 5, 0, 100), 0)
        self.assertEqual(ld.join_budget(1, 5, 99, 100), 1)
        self.assertEqual(ld.join_budget(0, 5, 120, 100), 0)


# ---------------------------------------------------------------- fake telegram
class Channel:
    def __init__(self, id, username, title, members=0, broadcast=True):
        self.id, self.username, self.title = id, username, title
        self.participants_count, self.broadcast, self.megagroup = members, broadcast, not broadcast
        self.scam = self.fake = False


class FakeMsg:
    def __init__(self, id, text, ts):
        self.id, self.message, self.entities, self.reply_markup = id, text, [], None
        self.date = datetime.fromtimestamp(ts, timezone.utc)


def _install_fake_telethon():
    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        sys.modules[name] = mod
        return mod

    def req(name):
        return type(name, (), {"__init__": lambda self, *a, **k: self.__dict__.update(a=a, k=k)})

    module("telethon", __path__=[])
    module("telethon.tl", __path__=[])
    module("telethon.tl.types", PeerChannel=lambda cid: cid)
    module("telethon.tl.functions", __path__=[])
    module("telethon.tl.functions.contacts", SearchRequest=req("SearchRequest"))
    module("telethon.tl.functions.channels", JoinChannelRequest=req("JoinChannelRequest"),
           GetFullChannelRequest=req("GetFullChannelRequest"), LeaveChannelRequest=req("LeaveChannelRequest"))
    module("telethon.tl.functions.messages", ImportChatInviteRequest=req("ImportChatInviteRequest"),
           CheckChatInviteRequest=req("CheckChatInviteRequest"))


class FakeClient:
    def __init__(self):
        self.channels = {}
        self.posts = {}
        self.search_results = []
        self.joined, self.left = [], []
        self.full_about = "t.me/hidden_gem_channel"

    def is_connected(self):
        return True

    async def get_entity(self, username):
        if username not in self.channels:
            raise ValueError("No user has that username")
        return self.channels[username]

    async def get_messages(self, entity, limit=1, **kw):
        return self.posts.get(getattr(entity, "username", entity), [])[:limit]

    async def __call__(self, request):
        name = type(request).__name__
        if name == "SearchRequest":
            return types.SimpleNamespace(chats=self.search_results)
        if name == "GetFullChannelRequest":
            return types.SimpleNamespace(full_chat=types.SimpleNamespace(about=self.full_about, participants_count=777))
        if name == "JoinChannelRequest":
            self.joined.append(request.a[0].username)
            return types.SimpleNamespace(chats=[request.a[0]])
        if name == "LeaveChannelRequest":
            self.left.append(request.a[0])
            return types.SimpleNamespace()
        raise AssertionError(name)

    async def get_input_entity(self, peer):
        return peer


class DiscoveryFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_fake_telethon()
        from db import Database

        cls._tmp = tempfile.TemporaryDirectory()
        cls.db = Database(os.path.join(cls._tmp.name, "t.db"))
        cls.db.init_db(owner_id=1)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls._tmp.cleanup()

    def setUp(self):
        for key in (ld.KEY_ENABLED, ld.KEY_JOIN_MODE):
            self.db.set_setting(key, ld.DEFAULTS[key])
        self.db.set_setting(ld.KEY_ENABLED, "1")
        with self.db._get_conn() as conn:
            for table in ("leak_candidates", "leak_queries", "leak_channels", "leak_seen_posts"):
                conn.execute(f"DELETE FROM {table}")
        ld.DSTATE["join_paused_until"] = 0.0
        self.client = FakeClient()
        self.client.channels["cfg_channel"] = Channel(11, "cfg_channel", "Free V2ray Configs", 90000)
        self.client.channels["chatty"] = Channel(12, "chatty", "گپ دوستانه", 90000)
        self.client.posts["cfg_channel"] = [
            FakeMsg(100 - i, f"config {CFG}", NOW - i * 3600) for i in range(30)
        ]
        self.client.posts["chatty"] = [FakeMsg(50 - i, "سلام", NOW - i * 3600) for i in range(30)]
        self.handled = []

        async def call(factory):
            return await factory()

        async def handle_post(db, bot, text, cid, title, username, pid):
            self.handled.append((cid, pid))
            return 0

        self.disc = ld.Discovery(self.client, self.db, None, call, handle_post, {"watched_at": 1.0, "last_flood": 0})

    def run_async(self, coro):
        return asyncio.run(coro)

    def _candidate(self, username):
        return self.db.list_leak_candidates(
            ["new", "qualified", "pending", "approved", "joined", "watching", "skipped", "failed", "left"], 50
        )

    def test_search_adds_candidates_and_learns_yield(self):
        self.client.search_results = [self.client.channels["cfg_channel"], self.client.channels["chatty"], object()]
        found = self.run_async(self.disc.run_searches())
        self.assertGreaterEqual(found, 2)
        keys = {c["key"] for c in self._candidate("")}
        self.assertIn("u:cfg_channel", keys)
        self.assertIn("u:chatty", keys)
        # دوباره همان نتیجه: تکراری است، کاندیدای تازه نمی‌شود
        self.assertEqual(self.run_async(self.disc.run_searches()), 0)

    def test_evaluate_scores_scans_sample_and_qualifies(self):
        self.db.upsert_leak_candidate("u:cfg_channel", username="cfg_channel", source="search", source_query="q:v2ray")
        self.db.upsert_leak_candidate("u:chatty", username="chatty", source="search")
        self.db.ensure_leak_queries(["v2ray"])
        done = self.run_async(self.disc.evaluate_pending())
        self.assertEqual(done, 2)
        by_key = {c["key"]: c for c in self._candidate("")}
        self.assertEqual(by_key["u:cfg_channel"]["state"], "qualified")
        self.assertGreater(by_key["u:cfg_channel"]["score"], 60)
        self.assertEqual(by_key["u:chatty"]["state"], "skipped")
        # پست‌های نمونه برای نشت اسکن شده‌اند و دوباره اسکن نمی‌شوند
        self.assertEqual(len([h for h in self.handled if h[0] == 11]), 30)
        # کلیدواژه‌ی خوب پاداش گرفت
        row = [r for r in self.db.list_leak_queries() if r["query"] == "v2ray"][0]
        self.assertEqual(row["good_found"], 1)
        # توضیح کانال هم دنبال شد (لینک hidden_gem_channel کاندیدا شد)
        self.assertIn("u:hidden_gem_channel", {c["key"] for c in self._candidate("")})

    def test_unknown_username_is_failed_not_crash(self):
        self.db.upsert_leak_candidate("u:ghost_channel", username="ghost_channel", source="crawl")
        self.run_async(self.disc.evaluate_pending())
        self.assertEqual(self._candidate("")[0]["state"], "failed")

    def test_join_auto_respects_daily_cap_and_registers_channel(self):
        self.db.set_setting(ld.KEY_DAILY_JOINS, "1")
        ld.JOIN_DELAY = (0.0, 0.0)
        for name in ("cfg_channel",):
            self.db.upsert_leak_candidate(f"u:{name}", username=name, source="search")
        self.run_async(self.disc.evaluate_pending())
        self.assertEqual(self.run_async(self.disc.join_ready()), 1)
        self.assertEqual(self.client.joined, ["cfg_channel"])
        ch = self.db.get_leak_channel(11)
        self.assertEqual((ch["joined"], ch["auto_joined"], ch["source"]), (1, 1, "discovery"))
        self.assertEqual(self.db.count_leak_joins_since(24), 1)
        # سقف روزانه تمام شد
        self.db.upsert_leak_candidate("u:chatty", username="chatty")
        self.db.update_leak_candidate(self.db.list_leak_candidates(["new"], 5)[0]["id"], state="qualified")
        self.assertEqual(self.run_async(self.disc.join_ready()), 0)
        self.db.set_setting(ld.KEY_DAILY_JOINS, "5")

    def test_approve_mode_waits_then_joins_after_approval(self):
        self.db.set_setting(ld.KEY_JOIN_MODE, "approve")
        notified = []

        async def fake_notify(text, markup=None):
            notified.append(text)

        self.disc.notify = fake_notify
        ld.Discovery._approval_markup = staticmethod(lambda cid: None)
        ld.JOIN_DELAY = (0.0, 0.0)
        self.db.upsert_leak_candidate("u:cfg_channel", username="cfg_channel", source="search")
        self.run_async(self.disc.evaluate_pending())
        cand = self._candidate("")[0]
        self.assertEqual(cand["state"], "pending")
        self.assertEqual(len(notified), 1)
        self.assertEqual(self.run_async(self.disc.join_ready()), 0)
        self.db.update_leak_candidate(cand["id"], state="approved")
        self.assertEqual(self.run_async(self.disc.join_ready()), 1)
        self.db.set_setting(ld.KEY_JOIN_MODE, "auto")

    def test_watch_mode_never_joins(self):
        self.db.set_setting(ld.KEY_JOIN_MODE, "watch")
        self.db.upsert_leak_candidate("u:cfg_channel", username="cfg_channel", source="search")
        self.run_async(self.disc.evaluate_pending())
        self.assertEqual(self._candidate("")[0]["state"], "watching")
        ch = self.db.get_leak_channel(11)
        self.assertEqual((ch["joined"], ch["auto_joined"]), (0, 0))
        self.assertEqual(self.run_async(self.disc.join_ready()), 0)
        self.assertEqual(self.client.joined, [])
        self.db.set_setting(ld.KEY_JOIN_MODE, "auto")

    def test_flood_pauses_joins(self):
        self.db.upsert_leak_candidate("u:cfg_channel", username="cfg_channel", source="search")
        self.run_async(self.disc.evaluate_pending())

        async def flooded(factory):
            return None

        async def silent(text, markup=None):
            return None

        self.disc.notify = silent
        self.disc.call = flooded
        self.disc.state["last_flood"] = 7200
        self.assertEqual(self.run_async(self.disc.join_ready()), 0)
        self.assertGreater(ld.DSTATE["join_paused_until"], time.time() + 7000)

    def test_prune_only_leaves_scanner_joined_useless_channels(self):
        old = "2000-01-01 00:00:00"
        self.db.add_leak_channel(21, "mine", "mine_ch", 0, "discovery", True, True, 40, 10)      # عضوشده توسط اسکنر
        self.db.add_leak_channel(22, "personal", "personal_ch", 0, "discovery", True, False, 40, 10)  # از قبل عضو بوده
        self.db.add_leak_channel(23, "good", "good_ch", 0, "discovery", True, True, 40, 10)
        with self.db._get_conn() as conn:
            conn.execute("UPDATE leak_channels SET joined_at=?", (old,))
        self.db.bump_leak_channel_stats(23, config_posts=20)
        self.assertEqual(self.run_async(self.disc.prune()), 1)
        self.assertEqual(self.client.left, [21])
        self.assertIsNone(self.db.get_leak_channel(21))
        self.assertIsNotNone(self.db.get_leak_channel(22))
        self.assertIsNotNone(self.db.get_leak_channel(23))

    def test_known_channel_is_not_candidate_again(self):
        self.db.add_leak_channel(31, "known", "known_ch", 0)
        self.assertFalse(self.db.upsert_leak_candidate("u:known_ch", username="known_ch"))
        self.assertFalse(self.db.upsert_leak_candidate("c:31", channel_id=31))

    def test_admin_keywords_add_toggle_delete(self):
        self.db.ensure_leak_queries(["seedword"], "seed")
        self.assertTrue(self.db.add_leak_query("  کانفیگ   ویژه  "))
        self.assertFalse(self.db.add_leak_query("کانفیگ ویژه"))      # تکراری
        rows = {r["query"]: r for r in self.db.list_leak_queries()}
        self.assertEqual(rows["کانفیگ ویژه"]["source"], "admin")
        # غیرفعال‌شده‌ها انتخاب نمی‌شوند، حتی پیش‌فرض‌ها
        self.db.set_leak_query_active(rows["seedword"]["id"], False)
        picked = ld.pick_queries(self.db.list_leak_queries(), time.time(), 10)
        self.assertNotIn("seedword", picked)
        self.assertIn("کانفیگ ویژه", picked)
        # ensure_leak_queries دوباره seed را فعال نمی‌کند
        self.db.ensure_leak_queries(["seedword"], "seed")
        self.assertEqual(self.db.get_leak_query(rows["seedword"]["id"])["active"], 0)
        # seed حذف نمی‌شود، دستی حذف می‌شود
        self.assertFalse(self.db.delete_leak_query(rows["seedword"]["id"]))
        self.assertTrue(self.db.delete_leak_query(rows["کانفیگ ویژه"]["id"]))
        # افزودن دوباره‌ی یک کلیدواژه‌ی غیرفعال آن را فعال می‌کند
        self.assertFalse(self.db.add_leak_query("seedword"))
        self.assertEqual(self.db.get_leak_query(rows["seedword"]["id"])["active"], 1)

    def test_collect_refs_respects_switches(self):
        self.db.set_setting(ld.KEY_CRAWL, "0")
        self.assertEqual(ld.collect_refs(self.db, "t.me/abcde_channel"), 0)
        self.db.set_setting(ld.KEY_CRAWL, "1")
        self.assertEqual(ld.collect_refs(self.db, "t.me/abcde_channel"), 1)
        self.assertEqual(ld.collect_refs(self.db, "t.me/abcde_channel"), 0)


if __name__ == "__main__":
    unittest.main()
