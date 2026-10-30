# -*- coding: utf-8 -*-
"""برنامه‌ی هفتگی پست کانال: AI چند پست با زاویه‌ی متفاوت پیشنهاد می‌دهد و ادمین یک‌جا تأیید می‌کند."""

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import admin_channel_posts
import campaign_ai
import channel_ai
from i18n import tr
from states import AdminChannelPlan

_log = logging.getLogger("admin_channel_plan")
_CHANNEL_SETTING = "promo_channel_id"
_HOUR_SETTING = "channel_plan_hour"
_GOAL = "برنامه‌ی هفتگی کانال: پست‌هایی با زاویه‌های کاملاً متفاوت برای هفته‌ی آینده؛ هر پست یک زاویه‌ی جدا."
_COUNTS = (3, 5, 7)


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(text), callback_data=data) for text, data in row] for row in rows
    ])


def _plan_hour(raw: str) -> tuple:
    try:
        hour, minute = (int(x) for x in (raw or "").split(":"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except ValueError:
        pass
    return 20, 0


def plan_times(count: int, hour: int, minute: int, now_utc: datetime = None) -> list:
    """زمان‌های UTC برای count پست پخش‌شده روی ۷ روز آینده (به وقت تهران)."""
    now_utc = now_utc or datetime.utcnow()
    today = now_utc.replace(tzinfo=timezone.utc).astimezone(admin_channel_posts.TEHRAN).date()
    times = []
    for k in range(count):
        offset = 1 if count == 1 else int(1 + k * 6 / (count - 1))
        local = datetime(today.year, today.month, today.day, hour, minute, tzinfo=admin_channel_posts.TEHRAN) + timedelta(days=offset)
        times.append(local.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"))
    return times


def register(router: Router, db, senior_admin_only, deny_mid):

    async def _guard(call: CallbackQuery) -> bool:
        if not senior_admin_only(call.from_user.id):
            await deny_mid(call)
            return False
        return True

    @router.callback_query(F.data == "adm_cplan")
    async def cb_plan_start(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.clear()
        channel = await asyncio.to_thread(db.get_setting, _CHANNEL_SETTING, "")
        if not channel_ai.text_configured(db):
            await call.message.answer(
                "مدل متن کانال تنظیم نشده؛ ابتدا از «⚙️ مدل‌های AI کانال» کلید Jarvis را وارد کن.",
                reply_markup=_kb([("⚙️ مدل‌های AI کانال", "adm_chai")], [("⬅️ بازگشت", admin_channel_posts.BACK)]),
            )
        elif not channel:
            await call.message.answer(
                "آیدی کانال هنوز ثبت نشده؛ یک‌بار از «🤖 ساخت پست کانال با AI» کانال را تنظیم کن.",
                reply_markup=_kb([("🤖 ساخت پست کانال با AI", "adm_camp_start")], [("⬅️ بازگشت", admin_channel_posts.BACK)]),
            )
        else:
            await call.message.answer(
                "🗓 <b>برنامه‌ی هفتگی</b>\nچند پست برای ۷ روز آینده پیشنهاد بدهم؟ AI فقط از محصولات، قیمت‌ها و کدهای تخفیف واقعی استفاده می‌کند.",
                reply_markup=_kb([(f"{n} پست", f"adm_cplan_gen:{n}") for n in _COUNTS], [("⬅️ بازگشت", admin_channel_posts.BACK)]),
            )
        await call.answer()

    @router.callback_query(F.data.startswith("adm_cplan_gen:"))
    async def cb_plan_generate(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        try:
            count = int(call.data.split(":", 1)[1])
        except ValueError:
            await call.answer()
            return
        if count not in _COUNTS:
            await call.answer()
            return
        await call.answer()
        status = await call.message.answer("⏳ در حال ساخت برنامه‌ی هفتگی با AI...")
        try:
            variants, _facts = await campaign_ai.generate_campaigns(db, _GOAL, count)
        except Exception as exc:
            _log.warning("تولید برنامه‌ی هفتگی ناموفق بود: %s", exc)
            await status.edit_text(
                "❌ تولید با AI انجام نشد: " + html.escape(str(exc)),
                reply_markup=_kb([("🔄 تلاش دوباره", f"adm_cplan_gen:{count}")], [("⬅️ بازگشت", admin_channel_posts.BACK)]),
            )
            return
        hour, minute = _plan_hour(await asyncio.to_thread(db.get_setting, _HOUR_SETTING, "20:00"))
        times = plan_times(len(variants), hour, minute)
        items = [{"variant": v, "when": t} for v, t in zip(variants, times)]
        await state.set_state(AdminChannelPlan.confirming)
        await state.update_data(plan_items=items, plan_count=count)
        lines = ["🗓 <b>برنامه‌ی پیشنهادی (وقت تهران)</b>\n"]
        for i, item in enumerate(items, 1):
            v = item["variant"]
            caption = v["caption"]
            lines.append(
                f"<b>{i}) {admin_channel_posts.to_tehran(item['when'])} | {html.escape(v['angle'])}</b>\n"
                f"{html.escape(caption[:180])}{'…' if len(caption) > 180 else ''}\n"
            )
        lines.append("با تأیید، همه به‌صورت زمان‌بندی‌شده ثبت می‌شوند و بعداً از «📅 تقویم» قابل ویرایش، تغییر زمان یا حذف‌اند.")
        await status.edit_text(
            "\n".join(lines),
            reply_markup=_kb(
                [("✅ تأیید و زمان‌بندی همه", "adm_cplan_ok")],
                [("🔄 پیشنهاد دوباره", f"adm_cplan_gen:{count}")],
                [("❌ لغو", admin_channel_posts.BACK)],
            ),
        )

    @router.callback_query(F.data == "adm_cplan_ok")
    async def cb_plan_confirm(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        data = await state.get_data()
        items = data.get("plan_items") or []
        channel = await asyncio.to_thread(db.get_setting, _CHANNEL_SETTING, "")
        if not items or not channel:
            await call.answer("برنامه دیگر معتبر نیست؛ دوباره بساز.", show_alert=True)
            return
        await state.clear()
        for item in items:
            post_id = await asyncio.to_thread(
                admin_channel_posts.create_from_data, db, call.from_user.id, {"variant": item["variant"], "channel_id": channel},
            )
            await asyncio.to_thread(db.update_channel_post, post_id, status="scheduled", scheduled_at=item["when"])
            await asyncio.to_thread(
                db.log_admin_action, call.from_user.id, "channel_post_schedule",
                f"پست برنامه‌ی هفتگی #{post_id} برای {admin_channel_posts.to_tehran(item['when'])} (تهران)", "channel_post", post_id,
            )
        await call.message.answer(
            f"✅ {len(items)} پست زمان‌بندی شد.",
            reply_markup=_kb([("📅 تقویم محتوا", "adm_cpost_list:scheduled:0")], [("⬅️ بازگشت", admin_channel_posts.BACK)]),
        )
        await call.answer()
