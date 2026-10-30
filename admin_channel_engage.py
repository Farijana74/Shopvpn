# -*- coding: utf-8 -*-
"""نظرسنجی و کوییز کانال با AI، منوی انواع پست و کلید پیشنهاد خودکار پست."""

import asyncio
import html
import json
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import admin_channel_posts
import campaign_ai
import channel_ai
import channel_suggestions
from i18n import tr
from states import AdminChannelEngage

_log = logging.getLogger("admin_channel_engage")
MENU = "adm_cengage"
BACK = "adm_cposts"
_CHANNEL_SETTING = "promo_channel_id"
KIND_LABELS = {"poll": "🗳 نظرسنجی", "quiz": "❓ کوییز"}
BUTTON_TEXT = "🛒 ورود به فروشگاه"


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(text), callback_data=data) for text, data in row] for row in rows
    ])


def _cancel_kb() -> InlineKeyboardMarkup:
    return _kb([("❌ لغو", MENU)])


def register(router: Router, db, senior_admin_only, deny_mid):

    async def _guard(call: CallbackQuery) -> bool:
        if not senior_admin_only(call.from_user.id):
            await deny_mid(call)
            return False
        return True

    async def _menu_text() -> str:
        enabled = await asyncio.to_thread(db.get_setting, channel_suggestions.ENABLED_KEY, "1") == "1"
        return (
            "🧩 <b>انواع پست کانال</b>\n\n"
            "نظرسنجی و کوییز با AI ساخته و به‌صورت نظرسنجی بومی تلگرام ارسال می‌شود؛ پست آموزشی یک کپشن آموزشی است.\n"
            "پیشنهاد خودکار: وقتی محصول جدید اضافه شود، موجودی کم شود یا کد تخفیف عمومی ساخته شود، از مدیران می‌پرسم پست بسازم یا نه.\n\n"
            "💡 پیشنهاد خودکار: " + ("✅ روشن" if enabled else "❌ خاموش")
        )

    async def _show_menu(call: CallbackQuery, state: FSMContext):
        await state.clear()
        markup = _kb(
            [(KIND_LABELS["poll"], "adm_ceng:poll"), (KIND_LABELS["quiz"], "adm_ceng:quiz")],
            [("📚 پست آموزشی", "adm_camp_edu")],
            [("💡 پیشنهاد خودکار پست: روشن/خاموش", "adm_ceng_sugg")],
            [("⬅️ بازگشت", BACK)],
        )
        text = await _menu_text()
        try:
            await call.message.edit_text(text, reply_markup=markup)
        except Exception:
            await call.message.answer(text, reply_markup=markup)
        await call.answer()

    @router.callback_query(F.data == MENU)
    async def cb_menu(call: CallbackQuery, state: FSMContext):
        if await _guard(call):
            await _show_menu(call, state)

    @router.callback_query(F.data == "adm_ceng_sugg")
    async def cb_toggle(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        current = await asyncio.to_thread(db.get_setting, channel_suggestions.ENABLED_KEY, "1")
        await asyncio.to_thread(db.set_setting, channel_suggestions.ENABLED_KEY, "0" if current == "1" else "1")
        await _show_menu(call, state)

    @router.callback_query(F.data.in_({"adm_ceng:poll", "adm_ceng:quiz"}))
    async def cb_start(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        if not await asyncio.to_thread(channel_ai.text_configured, db):
            await call.answer("مدل متن کانال تنظیم نشده؛ از «⚙️ مدل‌های AI کانال» کلید را وارد کن.", show_alert=True)
            return
        kind = call.data.split(":", 1)[1]
        await state.clear()
        await state.set_state(AdminChannelEngage.waiting_topic)
        await state.update_data(kind=kind)
        await call.message.answer(
            f"{tr(KIND_LABELS[kind])}\nموضوع را بنویس (مثلاً «امنیت در وای‌فای عمومی»)، یا دکمه‌ی زیر را بزن.",
            reply_markup=_kb([("▶️ بدون موضوع خاص", "adm_ceng_go")], [("❌ لغو", MENU)]),
        )
        await call.answer()

    async def _generate(target: Message, state: FSMContext, topic: str):
        kind = (await state.get_data()).get("kind")
        status = await target.answer("⏳ در حال ساخت با AI...")
        try:
            item = await campaign_ai.generate_engagement(db, kind, topic)
        except Exception as exc:
            _log.warning("ساخت نظرسنجی/کوییز ناموفق بود: %s", exc)
            await status.edit_text(
                "❌ ساخت انجام نشد: " + html.escape(str(exc)),
                reply_markup=_kb([("🔄 تلاش دوباره", "adm_ceng_regen")], [("⬅️ بازگشت", MENU)]),
            )
            await state.update_data(topic=topic)
            return
        try:
            await status.delete()
        except Exception:
            pass
        await state.update_data(topic=topic, item=item)
        await state.set_state(AdminChannelEngage.confirming)
        await target.answer("👀 پیش‌نمایش (همین به کانال می‌رود):")
        try:
            await admin_channel_posts.deliver_poll(target.bot, target.chat.id, item, kind, BUTTON_TEXT, {"type": "none"})
        except Exception as exc:
            await target.answer("❌ ساخت پیش‌نمایش ناموفق بود: " + html.escape(str(exc)), reply_markup=_kb([("⬅️ بازگشت", MENU)]))
            return
        await target.answer(
            "این نظرسنجی به کانال ارسال شود؟",
            reply_markup=_kb(
                [("📣 ارسال به کانال", "adm_ceng_send"), ("💾 ذخیره پیش‌نویس", "adm_ceng_draft")],
                [("🔄 نسخه‌ی دیگر", "adm_ceng_regen"), ("❌ لغو", MENU)],
            ),
        )

    @router.callback_query(F.data == "adm_ceng_go")
    async def cb_go(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await call.answer()
        await _generate(call.message, state, "")

    @router.message(AdminChannelEngage.waiting_topic, F.text)
    async def msg_topic(message: Message, state: FSMContext):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        await _generate(message, state, (message.text or "").strip()[:200])

    @router.callback_query(F.data == "adm_ceng_regen")
    async def cb_regen(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        data = await state.get_data()
        if not data.get("kind"):
            await call.answer("از اول شروع کن.", show_alert=True)
            return
        await call.answer()
        await _generate(call.message, state, data.get("topic", ""))

    async def _save(call: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        item, kind = data.get("item"), data.get("kind")
        if not item or kind not in KIND_LABELS:
            await call.answer("اطلاعات کامل نیست؛ از اول شروع کن.", show_alert=True)
            return None
        channel = await asyncio.to_thread(db.get_setting, _CHANNEL_SETTING, "")
        if not channel:
            await call.answer("کانال تنظیم نشده؛ یک‌بار از «🤖 ساخت پست جدید» کانال را ثبت کن.", show_alert=True)
            return None
        post_id = await asyncio.to_thread(
            db.create_channel_post, call.from_user.id, str(channel), item["question"], BUTTON_TEXT,
            json.dumps({"type": "none"}), None, KIND_LABELS[kind], "draft", kind, json.dumps(item, ensure_ascii=False),
        )
        await state.clear()
        return post_id

    async def _show_post(call: CallbackQuery, post_id: int, prefix: str):
        post = await asyncio.to_thread(db.get_channel_post, post_id)
        stats = (await asyncio.to_thread(db.get_channel_post_stats)).get(post_id)
        await call.message.answer(prefix + admin_channel_posts._view_text(post, stats), reply_markup=admin_channel_posts._view_kb(post))

    @router.callback_query(F.data == "adm_ceng_draft")
    async def cb_draft(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        post_id = await _save(call, state)
        if post_id is None:
            return
        await call.answer()
        await _show_post(call, post_id, "💾 پیش‌نویس ذخیره شد.\n\n")

    @router.callback_query(F.data == "adm_ceng_send")
    async def cb_send(call: CallbackQuery, state: FSMContext, bot: Bot):
        if not await _guard(call):
            return
        post_id = await _save(call, state)
        if post_id is None:
            return
        ok, error = await admin_channel_posts.publish_record(bot, db, post_id)
        if ok:
            await asyncio.to_thread(db.log_admin_action, call.from_user.id, "ai_channel_post", f"پست کانال #{post_id} ارسال شد", "channel_post", post_id)
        await call.answer("✅ ارسال شد." if ok else "❌ ارسال ناموفق بود.", show_alert=not ok)
        await _show_post(call, post_id, "")
