# -*- coding: utf-8 -*-
"""ذخیره و مدیریت پست‌های کانال: پیش‌نویس، ارسال، ویرایش، حذف و پین از داخل بات."""

import asyncio
import html
import inspect
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import campaign_ai
from i18n import tr
from states import AdminChannelPost

_log = logging.getLogger("admin_channel_posts")
PAGE_SIZE = 8
BACK = "adm_cposts"
STATUS_LABELS = {
    "draft": "📝 پیش‌نویس",
    "scheduled": "⏰ زمان‌بندی‌شده",
    "sent": "✅ ارسال‌شده",
    "error": "⚠️ خطا",
}
STATUS_ICONS = {"draft": "📝", "scheduled": "⏰", "sent": "✅", "error": "⚠️"}
FILTERS = ("all", "draft", "scheduled", "sent", "error")
FILTER_LABELS = {
    "all": "📋 همه", "draft": "📝 پیش‌نویس", "scheduled": "📅 تقویم (زمان‌بندی‌شده)", "sent": "✅ ارسال‌شده", "error": "⚠️ خطا",
}
TEHRAN = ZoneInfo("Asia/Tehran")
SCHEDULE_PROMPT = "⏰ زمان ارسال را به وقت تهران بفرست:\nYYYY-MM-DD HH:MM\nمثال: 2026-10-20 20:30"


def to_tehran(iso_utc: str) -> str:
    try:
        value = datetime.fromisoformat(str(iso_utc)).replace(tzinfo=timezone.utc)
    except ValueError:
        return str(iso_utc)
    return value.astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M")


def parse_tehran(text: str):
    try:
        local = datetime.strptime((text or "").strip(), "%Y-%m-%d %H:%M").replace(tzinfo=TEHRAN)
    except ValueError:
        return None
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(text), callback_data=data) for text, data in row] for row in rows
    ])


def post_deeplink(post) -> dict:
    try:
        value = json.loads(post["deeplink"] or "{}")
    except ValueError:
        value = {}
    return value if isinstance(value, dict) and value.get("type") else {"type": "none"}


def tracked_param(param: str, post_id) -> str:
    if not post_id:
        return param
    tracked = f"cp{post_id}-{param}" if param else f"cp{post_id}"
    return tracked if len(tracked) <= 64 else param


async def build_markup(bot: Bot, deeplink: dict, button_text: str, post_id: int = None) -> InlineKeyboardMarkup:
    me = await bot.get_me()
    param = tracked_param(campaign_ai.start_param_for(deeplink), post_id)
    url = f"https://t.me/{me.username}?start={param}" if param else f"https://t.me/{me.username}"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=button_text, url=url)]])


async def deliver(bot: Bot, chat_id, caption: str, button_text: str, deeplink: dict, photo_id: str = None,
                  post_id: int = None) -> Message:
    markup = await build_markup(bot, deeplink, button_text, post_id)
    text = html.escape(caption)
    if photo_id:
        return await bot.send_photo(chat_id=chat_id, photo=photo_id, caption=text, reply_markup=markup)
    return await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)


async def deliver_poll(bot: Bot, chat_id, poll: dict, kind: str, button_text: str, deeplink: dict, post_id: int = None) -> Message:
    markup = await build_markup(bot, deeplink, button_text, post_id)
    kwargs = {"chat_id": chat_id, "question": poll["question"], "options": list(poll["options"]), "is_anonymous": True, "reply_markup": markup}
    if kind == "quiz":
        params = inspect.signature(bot.send_poll).parameters
        kwargs["type"] = "quiz"
        kwargs["explanation"] = poll.get("explanation") or None
        if "correct_option_ids" in params and "correct_option_id" not in params:
            kwargs["correct_option_ids"] = [poll["correct_index"]]
        else:
            kwargs["correct_option_id"] = poll["correct_index"]
    return await bot.send_poll(**kwargs)


def create_from_data(db, admin_id: int, data: dict, status: str = "draft") -> int:
    variant = data["variant"]
    return db.create_channel_post(
        admin_id, str(data["channel_id"]), variant["caption"], variant["button_text"],
        json.dumps(variant["deeplink"], ensure_ascii=False), data.get("photo_id"), variant.get("angle", ""), status,
    )


async def publish_record(bot: Bot, db, post_id: int) -> tuple:
    post = await asyncio.to_thread(db.get_channel_post, post_id)
    try:
        if post["kind"] in ("poll", "quiz"):
            msg = await deliver_poll(bot, post["channel_id"], json.loads(post["poll_json"]), post["kind"], post["button_text"], post_deeplink(post), post_id)
        else:
            msg = await deliver(bot, post["channel_id"], post["caption"], post["button_text"], post_deeplink(post), post["photo_id"], post_id)
    except Exception as exc:
        await asyncio.to_thread(db.update_channel_post, post_id, status="error", error=str(exc)[:300])
        return False, str(exc)
    await asyncio.to_thread(
        db.update_channel_post, post_id, status="sent", message_id=msg.message_id, error=None,
        sent_at=datetime.utcnow().isoformat(timespec="seconds"),
    )
    return True, ""


def _money(value: int) -> str:
    return f"{int(value or 0):,}"


def _stats_text(stats: dict) -> str:
    if not stats:
        return "📊 هنوز کلیکی ثبت نشده"
    return (
        f"📊 کلیک: {stats['clicks']} ({stats['users']} نفر) | خرید: {stats['purchases']} | "
        f"درآمد: {_money(stats['revenue'])} تومان"
    )


def _view_text(post, stats: dict = None) -> str:
    param = campaign_ai.start_param_for(post_deeplink(post)) or "بدون دیپ‌لینک"
    lines = [f"<b>پست #{post['id']}</b> | {STATUS_LABELS.get(post['status'], post['status'])}"]
    lines.append(f"📢 کانال: <code>{html.escape(str(post['channel_id']))}</code>")
    if post["message_id"]:
        lines.append(f"🆔 message_id: <code>{post['message_id']}</code>" + (" | 📌 پین‌شده" if post["is_pinned"] else ""))
    lines.append(f"🕒 ساخت: {post['created_at']}")
    if post["status"] == "scheduled" and post["scheduled_at"]:
        lines.append(f"⏰ زمان ارسال (تهران): {to_tehran(post['scheduled_at'])}")
    if post["sent_at"]:
        lines.append(f"📤 ارسال: {post['sent_at']}")
    if post["error"]:
        lines.append("❌ خطا: " + html.escape(post["error"]))
    caption = post["caption"]
    lines += ["", html.escape(caption[:700]) + ("…" if len(caption) > 700 else ""), ""]
    lines.append(f"🔘 دکمه: {html.escape(post['button_text'] or '')}")
    lines.append(f"🔗 دیپ‌لینک: <code>{html.escape(param)}</code>")
    if post["photo_id"]:
        lines.append("🖼 دارای عکس")
    if post["kind"] in ("poll", "quiz"):
        try:
            poll = json.loads(post["poll_json"] or "{}")
        except ValueError:
            poll = {}
        lines.append(("🗳 نظرسنجی" if post["kind"] == "poll" else "❓ کوییز") + ": " + " | ".join(html.escape(o) for o in poll.get("options", [])))
    if post["status"] == "sent":
        lines.append(_stats_text(stats))
    return "\n".join(lines)


def _view_kb(post) -> InlineKeyboardMarkup:
    pid = post["id"]
    edit_row = [("✏️ ویرایش متن", f"adm_cpost_edit:{pid}:c"), ("🔘 ویرایش دکمه", f"adm_cpost_edit:{pid}:b")]
    edit_rows = [] if post["kind"] in ("poll", "quiz") else [edit_row]
    back = [("⬅️ لیست پست‌ها", "adm_cpost_list:all:0")]
    if post["status"] == "sent":
        pin = ("📍 برداشتن پین", f"adm_cpost_unpin:{pid}") if post["is_pinned"] else ("📌 پین در کانال", f"adm_cpost_pin:{pid}")
        return _kb(*edit_rows, [pin], [("🗑 حذف از کانال", f"adm_cpost_del:{pid}")], back)
    if post["status"] == "scheduled":
        return _kb(
            [("📣 ارسال همین حالا", f"adm_cpost_send:{pid}")],
            [("⏰ تغییر زمان", f"adm_cpost_sched:{pid}"), ("🚫 لغو زمان‌بندی", f"adm_cpost_unsched:{pid}")],
            *edit_rows, [("🗑 حذف", f"adm_cpost_rm:{pid}")], back,
        )
    return _kb(
        [("📣 ارسال به کانال", f"adm_cpost_send:{pid}"), ("⏰ زمان‌بندی", f"adm_cpost_sched:{pid}")],
        *edit_rows, [("🗑 حذف پیش‌نویس", f"adm_cpost_rm:{pid}")], back,
    )


def register(router: Router, db, senior_admin_only, deny_mid):

    async def _show(call: CallbackQuery, text: str, markup: InlineKeyboardMarkup):
        try:
            await call.message.edit_text(text, reply_markup=markup)
        except Exception:
            await call.message.answer(text, reply_markup=markup)

    async def _stats(post_id: int):
        return (await asyncio.to_thread(db.get_channel_post_stats)).get(post_id)

    async def _show_post(call: CallbackQuery, post_id: int):
        post = await asyncio.to_thread(db.get_channel_post, post_id)
        if not post or post["status"] == "deleted":
            await call.answer("این پست پیدا نشد.", show_alert=True)
            return
        await _show(call, _view_text(post, await _stats(post_id)), _view_kb(post))

    async def _guard(call: CallbackQuery) -> bool:
        if not senior_admin_only(call.from_user.id):
            await deny_mid(call)
            return False
        return True

    async def _log_action(admin_id: int, action: str, post_id: int, detail: str):
        await asyncio.to_thread(db.log_admin_action, admin_id, action, detail, "channel_post", post_id)

    @router.callback_query(F.data == BACK)
    async def cb_posts_menu(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.clear()
        counts = {name: await asyncio.to_thread(db.count_channel_posts, None if name == "all" else name) for name in FILTERS}
        text = (
            "🤖 <b>مرکز مدیریت محتوای کانال با AI</b>\n\n"
            f"همه: {counts['all']} | پیش‌نویس: {counts['draft']} | زمان‌بندی‌شده: {counts['scheduled']} | "
            f"ارسال‌شده: {counts['sent']} | خطا: {counts['error']}"
        )
        await _show(call, text, _kb(
            [(FILTER_LABELS["all"], "adm_cpost_list:all:0"), (FILTER_LABELS["draft"], "adm_cpost_list:draft:0")],
            [(FILTER_LABELS["scheduled"], "adm_cpost_list:scheduled:0")],
            [(FILTER_LABELS["sent"], "adm_cpost_list:sent:0"), (FILTER_LABELS["error"], "adm_cpost_list:error:0")],
            [("🤖 ساخت پست جدید", "adm_camp_start"), ("🗓 برنامه‌ی هفتگی با AI", "adm_cplan")],
            [("🧩 نظرسنجی، کوییز و آموزشی", "adm_cengage")],
            [("⚙️ تنظیمات AI و یادگیری", "adm_chai"), ("🎨 قالب برند تصاویر", "adm_pbrand")],
            [("🔗 ابزار دیپ‌لینک تبلیغاتی", "adm_deeplink_tools")],
            [("⬅️ بازگشت به بازاریابی و رشد", "adm_cat:marketing")],
        ))
        await call.answer()

    @router.callback_query(F.data == "adm_cpost_stats")
    async def cb_posts_stats(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.clear()
        stats = await asyncio.to_thread(db.get_channel_post_stats)
        if not stats:
            await _show(call, "📊 هنوز کلیکی روی پست‌ها ثبت نشده. فقط پست‌هایی که از این نسخه به بعد ارسال شوند ردیابی می‌شوند.", _kb([("⬅️ بازگشت", BACK)]))
            await call.answer()
            return
        ranked = sorted(stats.items(), key=lambda kv: (kv[1]["revenue"], kv[1]["purchases"], kv[1]["clicks"]), reverse=True)[:10]
        lines = ["📊 <b>پرفروش‌ترین پست‌ها</b>\n(خرید = سفارش تأییدشده‌ای که آخرین کلیک کاربر در ۷ روز قبلش روی همان پست بوده)\n"]
        buttons = []
        for post_id, st in ranked:
            post = await asyncio.to_thread(db.get_channel_post, post_id)
            if not post:
                continue
            label = html.escape((post["angle"] or post["caption"][:30]).replace("\n", " "))
            lines.append(f"<b>#{post_id}</b> {label}\n{_stats_text(st)}\n")
            buttons.append([(f"#{post_id} {(post['angle'] or post['caption'])[:24]}", f"adm_cpost_view:{post_id}")])
        buttons.append([("⬅️ بازگشت", BACK)])
        await _show(call, "\n".join(lines), _kb(*buttons))
        await call.answer()

    @router.callback_query(F.data.startswith("adm_cpost_list:"))
    async def cb_posts_list(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.clear()
        try:
            _, flt, page_raw = call.data.split(":")
            page = max(int(page_raw), 0)
        except ValueError:
            await call.answer()
            return
        if flt not in FILTERS:
            await call.answer()
            return
        status = None if flt == "all" else flt
        total = await asyncio.to_thread(db.count_channel_posts, status)
        rows = await asyncio.to_thread(db.list_channel_posts, status, PAGE_SIZE, page * PAGE_SIZE)
        buttons = []
        for post in rows:
            when = f" {to_tehran(post['scheduled_at'])}" if post["status"] == "scheduled" and post["scheduled_at"] else ""
            label = f"#{post['id']} {STATUS_ICONS.get(post['status'], '')}{when} {post['caption'][:20].replace(chr(10), ' ')}"
            buttons.append([(label, f"adm_cpost_view:{post['id']}")])
        nav = []
        if page > 0:
            nav.append(("◀️ قبلی", f"adm_cpost_list:{flt}:{page - 1}"))
        if (page + 1) * PAGE_SIZE < total:
            nav.append(("بعدی ▶️", f"adm_cpost_list:{flt}:{page + 1}"))
        if nav:
            buttons.append(nav)
        buttons.append([("⬅️ بازگشت", BACK)])
        title = f"🗂 {FILTER_LABELS[flt]} ({total})" if total else f"🗂 {FILTER_LABELS[flt]}: پستی وجود ندارد."
        await _show(call, title, _kb(*buttons))
        await call.answer()

    @router.callback_query(F.data.startswith("adm_cpost_view:"))
    async def cb_post_view(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.clear()
        await _show_post(call, int(call.data.split(":", 1)[1]))
        await call.answer()

    @router.callback_query(F.data.startswith("adm_cpost_edit:"))
    async def cb_post_edit(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        _, pid_raw, field = call.data.split(":")
        post = await asyncio.to_thread(db.get_channel_post, int(pid_raw))
        if not post or field not in ("c", "b"):
            await call.answer("این پست پیدا نشد.", show_alert=True)
            return
        await state.set_state(AdminChannelPost.waiting_value)
        await state.update_data(cpost_id=post["id"], cpost_field=field)
        prompt = (
            f"✏️ متن جدید کپشن را بفرست (حداکثر {campaign_ai.CAPTION_LIMIT} کاراکتر)."
            if field == "c" else "🔘 متن جدید دکمه را بفرست (حداکثر ۶۴ کاراکتر)."
        )
        await call.message.answer(prompt, reply_markup=_kb([("❌ لغو", f"adm_cpost_view:{post['id']}")]))
        await call.answer()

    @router.message(AdminChannelPost.waiting_value, F.text)
    async def msg_post_edit(message: Message, state: FSMContext, bot: Bot):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        data = await state.get_data()
        post = await asyncio.to_thread(db.get_channel_post, data.get("cpost_id") or 0)
        field = data.get("cpost_field")
        if not post or field not in ("c", "b"):
            await state.clear()
            return
        value = (message.text or "").strip()
        limit = campaign_ai.CAPTION_LIMIT if field == "c" else 64
        cancel = _kb([("❌ لغو", f"adm_cpost_view:{post['id']}")])
        if not value or len(value) > limit:
            await message.answer(f"متن باید بین ۱ تا {limit} کاراکتر باشد (الان {len(value)}). دوباره بفرست.", reply_markup=cancel)
            return
        caption = value if field == "c" else post["caption"]
        button = value if field == "b" else post["button_text"]
        if post["status"] == "sent" and post["message_id"]:
            markup = await build_markup(bot, post_deeplink(post), button, post["id"])
            try:
                if post["photo_id"]:
                    await bot.edit_message_caption(chat_id=post["channel_id"], message_id=post["message_id"], caption=html.escape(caption), reply_markup=markup)
                else:
                    await bot.edit_message_text(text=html.escape(caption), chat_id=post["channel_id"], message_id=post["message_id"], reply_markup=markup)
            except Exception as exc:
                await message.answer("❌ ویرایش در کانال ناموفق بود: " + html.escape(str(exc)), reply_markup=cancel)
                return
        await asyncio.to_thread(db.update_channel_post, post["id"], caption=caption, button_text=button)
        await _log_action(message.from_user.id, "channel_post_edit", post["id"], f"ویرایش پست کانال #{post['id']}")
        await state.clear()
        updated = await asyncio.to_thread(db.get_channel_post, post["id"])
        await message.answer("✅ ویرایش شد.\n\n" + _view_text(updated, await _stats(updated["id"])), reply_markup=_view_kb(updated))

    @router.callback_query(F.data.startswith("adm_cpost_sched:"))
    async def cb_post_sched(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        post = await asyncio.to_thread(db.get_channel_post, int(call.data.split(":", 1)[1]))
        if not post or post["status"] not in ("draft", "error", "scheduled"):
            await call.answer("این پست قابل زمان‌بندی نیست.", show_alert=True)
            return
        await state.set_state(AdminChannelPost.waiting_schedule)
        await state.update_data(cpost_id=post["id"])
        await call.message.answer(SCHEDULE_PROMPT, reply_markup=_kb([("❌ لغو", f"adm_cpost_view:{post['id']}")]))
        await call.answer()

    @router.message(AdminChannelPost.waiting_schedule, F.text)
    async def msg_post_sched(message: Message, state: FSMContext):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        post_id = (await state.get_data()).get("cpost_id") or 0
        post = await asyncio.to_thread(db.get_channel_post, post_id)
        if not post or post["status"] not in ("draft", "error", "scheduled"):
            await state.clear()
            return
        cancel = _kb([("❌ لغو", f"adm_cpost_view:{post_id}")])
        when = parse_tehran(message.text)
        if when is None:
            await message.answer("⚠️ قالب نامعتبر است. مثال: 2026-10-20 20:30", reply_markup=cancel)
            return
        if when <= datetime.utcnow():
            await message.answer("⚠️ زمان باید در آینده باشد.", reply_markup=cancel)
            return
        await asyncio.to_thread(
            db.update_channel_post, post_id, status="scheduled", scheduled_at=when.isoformat(timespec="seconds"), error=None,
        )
        await _log_action(message.from_user.id, "channel_post_schedule", post_id, f"زمان‌بندی پست کانال #{post_id} برای {to_tehran(when.isoformat())} (تهران)")
        await state.clear()
        updated = await asyncio.to_thread(db.get_channel_post, post_id)
        await message.answer("✅ زمان‌بندی شد.\n\n" + _view_text(updated, await _stats(updated["id"])), reply_markup=_view_kb(updated))

    @router.callback_query(F.data.startswith("adm_cpost_unsched:"))
    async def cb_post_unsched(call: CallbackQuery):
        if not await _guard(call):
            return
        post_id = int(call.data.split(":", 1)[1])
        post = await asyncio.to_thread(db.get_channel_post, post_id)
        if not post or post["status"] != "scheduled":
            await call.answer("این پست زمان‌بندی‌شده نیست.", show_alert=True)
            return
        await asyncio.to_thread(db.update_channel_post, post_id, status="draft", scheduled_at=None)
        await _log_action(call.from_user.id, "channel_post_schedule", post_id, f"لغو زمان‌بندی پست کانال #{post_id}")
        await call.answer("🚫 زمان‌بندی لغو شد.")
        await _show_post(call, post_id)

    @router.callback_query(F.data.startswith("adm_cpost_send:"))
    async def cb_post_send(call: CallbackQuery, bot: Bot):
        if not await _guard(call):
            return
        post_id = int(call.data.split(":", 1)[1])
        post = await asyncio.to_thread(db.get_channel_post, post_id)
        if not post or post["status"] not in ("draft", "error", "scheduled"):
            await call.answer("این پست قابل ارسال نیست.", show_alert=True)
            return
        if post["status"] == "scheduled" and not await asyncio.to_thread(db.claim_channel_post, post_id):
            await call.answer("این پست همین حالا در حال ارسال است.", show_alert=True)
            return
        ok, err = await publish_record(bot, db, post_id)
        if not ok:
            await call.answer("❌ ارسال ناموفق بود: " + err[:150], show_alert=True)
        else:
            await _log_action(call.from_user.id, "ai_channel_post", post_id, f"پست کانال #{post_id} ارسال شد")
            await call.answer("✅ ارسال شد.")
        await _show_post(call, post_id)

    async def _pin(call: CallbackQuery, bot: Bot, pin: bool):
        if not await _guard(call):
            return
        post_id = int(call.data.split(":", 1)[1])
        post = await asyncio.to_thread(db.get_channel_post, post_id)
        if not post or post["status"] != "sent" or not post["message_id"]:
            await call.answer("این پست در کانال نیست.", show_alert=True)
            return
        try:
            if pin:
                await bot.pin_chat_message(chat_id=post["channel_id"], message_id=post["message_id"], disable_notification=True)
            else:
                await bot.unpin_chat_message(chat_id=post["channel_id"], message_id=post["message_id"])
        except Exception as exc:
            await call.answer("❌ " + str(exc)[:180], show_alert=True)
            return
        await asyncio.to_thread(db.update_channel_post, post_id, is_pinned=1 if pin else 0)
        await _log_action(call.from_user.id, "channel_post_pin", post_id, ("پین" if pin else "برداشتن پین") + f" پست کانال #{post_id}")
        await call.answer("📌 پین شد." if pin else "📍 پین برداشته شد.")
        await _show_post(call, post_id)

    @router.callback_query(F.data.startswith("adm_cpost_pin:"))
    async def cb_post_pin(call: CallbackQuery, bot: Bot):
        await _pin(call, bot, True)

    @router.callback_query(F.data.startswith("adm_cpost_unpin:"))
    async def cb_post_unpin(call: CallbackQuery, bot: Bot):
        await _pin(call, bot, False)

    @router.callback_query(F.data.startswith("adm_cpost_del:"))
    async def cb_post_del(call: CallbackQuery):
        if not await _guard(call):
            return
        post_id = int(call.data.split(":", 1)[1])
        await _show(call, f"⚠️ پست #{post_id} از کانال حذف شود؟ این کار برگشت‌پذیر نیست.", _kb(
            [("🗑 بله، حذف شود", f"adm_cpost_delok:{post_id}")],
            [("❌ انصراف", f"adm_cpost_view:{post_id}")],
        ))
        await call.answer()

    @router.callback_query(F.data.startswith("adm_cpost_delok:"))
    async def cb_post_delok(call: CallbackQuery, bot: Bot):
        if not await _guard(call):
            return
        post_id = int(call.data.split(":", 1)[1])
        post = await asyncio.to_thread(db.get_channel_post, post_id)
        if not post or post["status"] != "sent" or not post["message_id"]:
            await call.answer("این پست در کانال نیست.", show_alert=True)
            return
        try:
            await bot.delete_message(chat_id=post["channel_id"], message_id=post["message_id"])
        except Exception as exc:
            await _show(call, "❌ حذف از کانال ناموفق بود: " + html.escape(str(exc)), _kb(
                [("🗑 فقط حذف رکورد از بات", f"adm_cpost_rm:{post_id}")],
                [("⬅️ بازگشت", f"adm_cpost_view:{post_id}")],
            ))
            await call.answer()
            return
        await asyncio.to_thread(db.update_channel_post, post_id, status="deleted", is_pinned=0)
        await _log_action(call.from_user.id, "channel_post_delete", post_id, f"حذف پست کانال #{post_id} از کانال")
        await call.answer("🗑 حذف شد.")
        await _show(call, f"🗑 پست #{post_id} از کانال حذف شد.", _kb([("⬅️ لیست پست‌ها", "adm_cpost_list:all:0")]))

    @router.callback_query(F.data.startswith("adm_cpost_rm:"))
    async def cb_post_rm(call: CallbackQuery):
        if not await _guard(call):
            return
        post_id = int(call.data.split(":", 1)[1])
        await asyncio.to_thread(db.delete_channel_post_record, post_id)
        await _log_action(call.from_user.id, "channel_post_delete", post_id, f"حذف رکورد پست کانال #{post_id}")
        await call.answer("🗑 رکورد حذف شد.")
        await _show(call, f"🗑 رکورد پست #{post_id} حذف شد.", _kb([("⬅️ لیست پست‌ها", "adm_cpost_list:all:0")]))
