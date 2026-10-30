# -*- coding: utf-8 -*-
"""تست همگام‌سازی مصرف SSH با پنل VPN در سرویس‌های ترکیبی (استخر سهمیه‌ی مشترک).

اجرا: python -m unittest test_quota_panel_sync -v
بدون شبکه: پنل VPN و نود SSH با کلاس‌های جعلی جایگزین می‌شوند.
"""

import asyncio
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("BOT_TOKEN", "123:test")
os.environ.setdefault("OWNER_ID", "1")

import config  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="qsync_test_")
config.DB_PATH = os.path.join(_TMP, "bot.db")

from database import Database  # noqa: E402
from quota_reconciler import QuotaReconciler  # noqa: E402
try:  # renewal_log به aiogram نیاز دارد؛ این تست فقط منطق تمدید استخر را می‌سنجد
    import renewal_engine  # noqa: E402
except ImportError:
    import sys
    import types
    sys.modules.setdefault("renewal_log", types.ModuleType("renewal_log"))
    import renewal_engine  # noqa: E402

GB = 1024 ** 3


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakeVPN:
    supports_data_limit_sync = True

    def __init__(self, used=0, limit=0, status="active"):
        self.used, self.limit, self.status = used, limit, status
        self.limit_calls, self.enabled_calls = [], []

    async def get_user_usage(self, username):
        return {"used_bytes": self.used, "data_limit_bytes": self.limit, "status": self.status}

    async def set_data_limit_bytes(self, username, limit_bytes, reenable=False):
        self.limit_calls.append((limit_bytes, reenable))
        self.limit = limit_bytes

    async def set_enabled(self, username, enabled):
        self.enabled_calls.append(enabled)


class FakeVPNNoSync(FakeVPN):
    supports_data_limit_sync = False


class FakeSSH:
    def __init__(self, used=0, status="active"):
        self.used, self.status = used, status
        self.updated, self.enabled_calls = [], []

    async def get_usage(self, account_id):
        return {"accounting_ready": True, "used_bytes": self.used, "status": self.status,
                "source": "nftables_port_counters", "reported_at": datetime.now(timezone.utc).isoformat()}

    async def update_account(self, account_id, limit_bytes=None, expires_at=None):
        self.updated.append((limit_bytes, expires_at))
        return True

    async def set_account_enabled(self, account_id, enabled, reason=None):
        self.enabled_calls.append(enabled)
        if enabled:
            self.status = "active"
        return True


def make_world(limit_gb=10, expires_in_days=30, vpn=None, ssh=None):
    db = Database(os.path.join(_TMP, f"t{time.time_ns()}.db"))
    db.init_db(owner_id=1)
    panel_id = db.add_panel_server("P1", "pasarguard", "https://panel.example.com", "u", "p")
    cc_id = db.add_custom_config(100, panel_id, "combo1", limit_gb, 30, "https://sub.example.com/x",
                                 source="direct_product")
    expires = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
    pool_id = db.create_quota_pool(100, limit_gb, expires, label="test")
    db.attach_custom_config_to_quota_pool(pool_id, cc_id)
    node_id = db.create_ssh_node("N1", "linux", "ssh.example.com")
    db.register_ssh_account(node_id, 100, "acc-1", "combo_ssh", "ssh.example.com", 22, pool_id, expires, "enc")
    vpn = vpn or FakeVPN()
    ssh = ssh or FakeSSH()
    rec = QuotaReconciler(db, vpn_provider_factory=lambda server: vpn,
                          ssh_provider_factory=lambda node, secret_resolver=None: ssh,
                          secret_resolver=lambda ref: "x")
    return db, rec, vpn, ssh, pool_id, cc_id


class PanelSyncTests(unittest.TestCase):
    def test_ssh_usage_is_applied_to_panel_limit(self):
        """مصرف ۱ گیگ روی SSH باید در سقف پنل VPN دیده شود (۱۰ − ۱ = ۹ گیگ)."""
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, ssh=FakeSSH(used=1 * GB))
        res = run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual(res["state"], "active")
        self.assertEqual(vpn.limit_calls, [(9 * GB, False)])
        self.assertTrue(all(r["ok"] for r in res["panel_sync"]))

    def test_vpn_and_ssh_usage_combined(self):
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, vpn=FakeVPN(used=2 * GB), ssh=FakeSSH(used=1 * GB))
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        # سقف پنل = مصرف خودِ پنل (۲) + باقیمانده‌ی مشترک (۷)
        self.assertEqual(vpn.limit_calls, [(9 * GB, False)])

    def test_no_repeated_panel_calls_when_unchanged(self):
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, ssh=FakeSSH(used=1 * GB))
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual(len(vpn.limit_calls), 1)

    def test_vpn_usage_growth_does_not_trigger_new_writes(self):
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, ssh=FakeSSH(used=1 * GB))
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        vpn.used = 3 * GB  # مصرف VPN بالا رفت؛ سقف پنل = ۳ + ۶ = ۹ همان قبلی است
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual(len(vpn.limit_calls), 1)

    def test_ssh_growth_updates_panel_again(self):
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, ssh=FakeSSH(used=1 * GB))
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        ssh.used = 4 * GB
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual([c[0] for c in vpn.limit_calls], [9 * GB, 6 * GB])

    def test_never_writes_zero_limit_for_limited_pool(self):
        """سقف 0 در پنل‌ها یعنی نامحدود؛ وقتی تمام مصرف از SSH بوده و حجم تمام شده نباید 0 نوشته شود."""
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, ssh=FakeSSH(used=10 * GB))
        res = run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual(res["state"], "exceeded")
        self.assertTrue(all(c[0] >= 1 for c in vpn.limit_calls))
        self.assertEqual(vpn.enabled_calls, [False])

    def test_unlimited_pool_is_not_touched(self):
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=0, ssh=FakeSSH(used=5 * GB))
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual(vpn.limit_calls, [])

    def test_unsupported_panel_is_skipped_without_breaking_enforcement(self):
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, vpn=FakeVPNNoSync(), ssh=FakeSSH(used=1 * GB))
        res = run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual(res["state"], "active")
        self.assertEqual(vpn.limit_calls, [])

    def test_panel_error_does_not_break_pool(self):
        class Boom(FakeVPN):
            async def set_data_limit_bytes(self, username, limit_bytes, reenable=False):
                raise RuntimeError("panel down")
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, vpn=Boom(), ssh=FakeSSH(used=1 * GB))
        res = run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual(res["state"], "active")
        self.assertFalse(res["panel_sync"][0]["ok"])

    def test_reenable_when_panel_had_stopped_user_at_old_limit(self):
        """پنل (مثل 3x-ui) کاربر را در سقف قبلی غیرفعال کرده بود؛ بعد از تمدید باید دوباره فعال شود."""
        vpn = FakeVPN(used=3 * GB, limit=3 * GB, status="disabled")
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=20, vpn=vpn, ssh=FakeSSH(used=1 * GB))
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual(vpn.limit_calls, [(19 * GB, True)])

    def test_vpn_only_pool_is_not_synced(self):
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, ssh=FakeSSH(used=1 * GB))
        db.delete_ssh_account_record(db.list_ssh_accounts()[0]["id"])
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual(vpn.limit_calls, [])

    def test_expired_remote_ssh_is_healed_when_pool_is_still_valid(self):
        """بعد از تمدید استخر، نود SSH هنوز با تاریخ قدیمی حساب را expired کرده؛ باید فعال و هم‌گام شود."""
        ssh = FakeSSH(used=1 * GB, status="expired")
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, ssh=ssh)
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual(ssh.enabled_calls, [True])
        self.assertEqual(len(ssh.updated), 1)
        self.assertEqual(ssh.updated[0][1], db.get_quota_pool(pool_id)["expires_at"])


class UserFacingUsageTests(unittest.TestCase):
    def test_user_sees_pool_usage_and_original_total_not_panel_remaining(self):
        """بات/مینی‌اپ/فروشگاه: مصرف = VPN+SSH و سقف = حجم اصلی بسته (نه سقف کم‌شده‌ی پنل)."""
        import sub_info
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, vpn=FakeVPN(used=2 * GB), ssh=FakeSSH(used=1 * GB))
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual(vpn.limit, 9 * GB)  # پنل: سقف = ۲ + ۷ (فقط برای قطع لحظه‌ای)
        config.DB_PATH = db.db_path
        sub_info._pool_db = None
        info = sub_info._pool_override_sync("https://sub.example.com/x")
        self.assertEqual(info["upload"] + info["download"], 3 * GB)
        self.assertEqual(info["total"], 10 * GB)
        self.assertIsNone(sub_info._pool_override_sync("https://other.example.com/none"))


class RenewalTests(unittest.TestCase):
    def test_renew_adds_to_pool_limit_and_extends_expiry(self):
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, expires_in_days=5, ssh=FakeSSH(used=4 * GB))
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        pool = db.get_quota_pool(pool_id)
        new_gb = renewal_engine._renew_quota_pool(db, pool, "full", 20, 30)
        self.assertAlmostEqual(new_gb, 30)
        after = db.get_quota_pool(pool_id)
        self.assertEqual(after["limit_bytes"], 30 * GB)
        old_exp = datetime.fromisoformat(pool["expires_at"])
        new_exp = datetime.fromisoformat(after["expires_at"])
        self.assertAlmostEqual((new_exp - old_exp).total_seconds(), 30 * 86400, delta=5)

    def test_fresh_package_starts_from_current_usage(self):
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10, ssh=FakeSSH(used=4 * GB))
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        new_gb = renewal_engine._renew_quota_pool(db, db.get_quota_pool(pool_id), "full", 10, 30, fresh=True)
        self.assertAlmostEqual(new_gb, 14)

    def test_unlimited_plan_makes_pool_unlimited(self):
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10)
        new_gb = renewal_engine._renew_quota_pool(db, db.get_quota_pool(pool_id), "full", 0, 30)
        self.assertEqual(new_gb, 0)
        self.assertEqual(db.get_quota_pool(pool_id)["limit_bytes"], 0)

    def test_time_only_renewal_keeps_volume(self):
        db, rec, vpn, ssh, pool_id, _ = make_world(limit_gb=10)
        new_gb = renewal_engine._renew_quota_pool(db, db.get_quota_pool(pool_id), "time", 0, 30)
        self.assertAlmostEqual(new_gb, 10)

    def test_renew_pooled_service_end_to_end(self):
        ssh = FakeSSH(used=4 * GB, status="expired")
        db, rec, vpn, _, pool_id, _ = make_world(limit_gb=10, expires_in_days=-1, ssh=ssh)
        # استخر منقضی شده و اعضا قطع شده‌اند
        run(rec.reconcile_pool(db.get_quota_pool(pool_id)))
        self.assertEqual(db.get_quota_pool(pool_id)["status"], "expired")

        class PanelUpdate:
            calls = []

            async def update_user(self, username, **kw):
                PanelUpdate.calls.append((username, kw))

        import quota_reconciler
        orig = quota_reconciler.QuotaReconciler
        quota_reconciler.QuotaReconciler = lambda db_: QuotaReconciler(
            db_, vpn_provider_factory=lambda s: vpn, ssh_provider_factory=lambda n, secret_resolver=None: ssh,
            secret_resolver=lambda r: "x")
        try:
            new_gb = run(renewal_engine.renew_pooled_service(
                db, PanelUpdate(), "combo1", db.get_quota_pool(pool_id), "full", 20, 30))
        finally:
            quota_reconciler.QuotaReconciler = orig
        self.assertAlmostEqual(new_gb, 30)
        self.assertEqual(PanelUpdate.calls[0][1]["add_volume_gb"], 0)  # حجم روی پنل مستقیم دست نمی‌خورد
        self.assertFalse(PanelUpdate.calls[0][1]["reset_usage"])
        pool = db.get_quota_pool(pool_id)
        self.assertEqual(pool["status"], "active")
        self.assertEqual(pool["limit_bytes"], 30 * GB)
        self.assertTrue(ssh.updated, "انقضای جدید باید روی نود SSH هم نوشته شود")
        self.assertIn(True, vpn.enabled_calls)  # عضو VPN دوباره فعال شد
        self.assertEqual(vpn.limit_calls[-1][0], 26 * GB)  # ۳۰ − ۴ گیگ مصرف SSH


if __name__ == "__main__":
    unittest.main()
