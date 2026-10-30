# -*- coding: utf-8 -*-
"""ارسال خودکار ردپای اقدامات مدیران (admin_logs) به تاپیک «📝 لاگ مدیران».

چون همه‌ی مسیرها (بات، پنل وب، مینی‌اپ) از db.log_admin_action استفاده می‌کنند، یک حلقه‌ی
پس‌زمینه که رکوردهای تازه‌ی admin_logs را بخواند همه‌ی آن‌ها را یکجا پوشش می‌دهد؛ بدون دست‌زدن
به صدها نقطه‌ی فراخوانی. اولین اجرا فقط نقطه‌ی شروع را روی آخرین لاگ می‌گذارد تا تاریخچه‌ی قدیمی
در گروه ریخته نشود.

مقدارهای حساس (token/secret/password/key ...) قبل از ارسال ماسک می‌شوند.
"""

import asyncio
import html
import logging
import re
from datetime import datetime, timezone

import report_router

logger = logging.getLogger(__name__)

SETTING_LAST_ID = "audit_forwarded_last_id"
POLL_SECONDS = 30
BATCH_LIMIT = 100
LINES_PER_MESSAGE = 12
MAX_DETAIL = 220

_SENSITIVE = re.compile(
    r"(?i)((?:[A-Za-z0-9_\-]*(?:token|secret|password|passwd|pass|api[_\-]?key|private|cookie|hash)[A-Za-z0-9_\-]*)\s*[=:]\s*)(\S+)"
)

ACTION_LABELS = {
    "setting_change": "⚙️ تغییر تنظیمات",
    "user_block": "🚫 بلاک کاربر", "user_unblock": "✅ آنبلاک کاربر", "user_block_toggle": "🚫 تغییر وضعیت بلاک",
    "user_wallet_adjust": "👛 تغییر موجودی کیف پول", "user_direct_message": "✉️ پیام مستقیم به کاربر",
    "user_exclusive_discount": "🎟 تخفیف اختصاصی کاربر", "user_bulk_toggle_configs": "🔌 فعال/غیرفعال گروهی کانفیگ‌ها",
    "product_add": "➕ افزودن محصول", "product_delete": "🗑 حذف محصول", "product_toggle": "🔁 فعال/غیرفعال محصول",
    "product_source_edit": "✏️ ویرایش منبع محصول", "category_add": "➕ افزودن دسته", "category_delete": "🗑 حذف دسته",
    "category_toggle": "🔁 فعال/غیرفعال دسته", "discount_add": "🎟 افزودن کد تخفیف", "discount_delete": "🗑 حذف کد تخفیف",
    "discount_toggle": "🔁 فعال/غیرفعال کد تخفیف", "pricing_tier_delete": "🗑 حذف پله‌ی قیمت",
    "panel_add": "🖥 افزودن پنل", "panel_update": "🖥 ویرایش پنل", "panel_server_add": "🖥 افزودن سرور پنل",
    "panel_server_delete": "🗑 حذف سرور پنل", "factory_reset": "🏭 بازگشت به حالت کارخانه",
    "backup_create": "🗄 ساخت بکاپ", "backup_interval_set": "⏱ تغییر فاصله‌ی بکاپ",
    "tg_admin_add": "👮 افزودن مدیر", "tg_admin_remove": "👮 حذف مدیر", "tg_admin_role": "👮 تغییر نقش مدیر",
    "web_admin_add": "🔐 افزودن مدیر پنل وب", "web_admin_delete": "🔐 حذف مدیر پنل وب",
    "web_admin_active": "🔐 فعال/غیرفعال مدیر پنل وب", "web_admin_permissions": "🔐 تغییر دسترسی مدیر پنل وب",
    "topup_approve": "✅ تایید شارژ", "topup_reject": "❌ رد شارژ", "topup_ai_auto_reject": "🕵️ رد خودکار شارژ (AI)",
    "order_ai_auto_reject": "🕵️ رد خودکار سفارش (AI)", "order_fake_receipt": "🕵️ فیش فیک",
    "ticket_reply": "💬 پاسخ تیکت", "ticket_close": "🔒 بستن تیکت",
    "reseller_credit_adjust": "🏪 تغییر اعتبار نماینده", "reseller_credit_toggle": "🏪 فعال/غیرفعال اعتبار نماینده",
    "tier_request_approve": "🏅 تایید ارتقای سطح", "ai_model_change": "🤖 تغییر مدل AI",
    "ai_provider_add": "🤖 افزودن ارائه‌دهنده‌ی AI", "ai_provider_delete": "🤖 حذف ارائه‌دهنده‌ی AI",
    "ai_channel_post": "📢 پست کانال AI", "xui_inbound_create": "🧩 ساخت inbound",
    "report_topic_rename": "🏷 تغییر نام تاپیک", "report_topic_toggle": "🗂 روشن/خاموش تاپیک",
    "report_topic_mute": "🔕 بی‌صدا/عادی کردن تاپیک", "report_topic_profile": "🎛 اعمال پروفایل تاپیک‌ها",
    "report_topic_rebuild": "♻️ بازسازی تاپیک", "report_group_set": "📣 تنظیم گروه گزارش", "report_group_clear": "📣 حذف گروه گزارش",
}


def redact(text: str) -> str:
    return _SENSITIVE.sub(lambda m: m.group(1) + "***", text or "")


def _actor(db, admin_id: int) -> str:
    if not admin_id:
        return "🤖 سیستم"
    try:
        row = db.get_user(admin_id)
    except Exception:
        row = None
    if row:
        name = (row["first_name"] or "").strip()
        uname = row["username"] or ""
        label = name or (f"@{uname}" if uname else "")
        if label:
            return html.escape(label) + f" <code>{admin_id}</code>"
    return f"مدیر <code>#{admin_id}</code>"


def format_entry(db, row) -> str:
    label = ACTION_LABELS.get(row["action"]) or f"📝 {row['action']}"
    detail = redact(row["details"] or "").strip().replace("\n", " ")
    if len(detail) > MAX_DETAIL:
        detail = detail[:MAX_DETAIL].rstrip() + "…"
    line = f"<b>{html.escape(label)}</b> — {_actor(db, row['admin_id'])}"
    if detail:
        line += f"\n   ↳ {html.escape(detail)}"
    return line


async def forward_new_logs(bot, db) -> int:
    """رکوردهای جدید را ارسال می‌کند؛ تعداد ارسال‌شده را برمی‌گرداند."""
    if report_router.get_chat_id(db) is None:
        return 0
    raw = (await asyncio.to_thread(db.get_setting, SETTING_LAST_ID, "")) or ""
    if not raw.strip():
        max_id = await asyncio.to_thread(db.get_max_admin_log_id)
        await asyncio.to_thread(db.set_setting, SETTING_LAST_ID, str(max_id))
        return 0
    try:
        last_id = int(raw)
    except ValueError:
        last_id = await asyncio.to_thread(db.get_max_admin_log_id)
    rows = await asyncio.to_thread(db.get_admin_logs_after, last_id, BATCH_LIMIT)
    if not rows:
        return 0
    # اگر تاپیک «لاگ مدیران» خاموش باشد نقطه‌ی شروع جلو می‌رود ولی چیزی به «سایر» ریخته نمی‌شود.
    if not report_router.is_enabled(db, "audit"):
        await asyncio.to_thread(db.set_setting, SETTING_LAST_ID, str(rows[-1]["id"]))
        return 0
    sent = 0
    for i in range(0, len(rows), LINES_PER_MESSAGE):
        chunk = rows[i:i + LINES_PER_MESSAGE]
        text = "📝 <b>اقدامات مدیران</b>\n\n" + "\n\n".join(format_entry(db, r) for r in chunk)
        text = text[:4000]
        ok = await report_router.notify(bot, db, "audit", text)
        if not ok:
            break  # بعداً از همین نقطه دوباره تلاش می‌شود
        await asyncio.to_thread(db.set_setting, SETTING_LAST_ID, str(chunk[-1]["id"]))
        sent += len(chunk)
    return sent


async def audit_forward_loop(bot, db) -> None:
    while True:
        try:
            await forward_new_logs(bot, db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("حلقه‌ی ارسال لاگ مدیران خطا داد.")
        await asyncio.sleep(POLL_SECONDS)
