# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""Telegram Business: AI auto-reply on the seller's account, human handoff, in-bot admin settings."""

import asyncio
import html
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BusinessConnection, BusinessMessagesDeleted, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from aiogram import BaseMiddleware

import ai_support
import global_switch
import user_limit as ul
from i18n import tr
from spam_guard import ThrottleMiddleware
from states import AdminBusiness
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

logger = logging.getLogger(__name__)

KEY_ENABLED = "business_enabled"
KEY_MARK_READ = "business_mark_read"
KEY_NOTIFY_CHANGES = "business_notify_changes"
KEY_SALES_CARD = "business_sales_card"

MAX_TEXT = 4096
TRANSCRIPT_LINES = 10
LOG_PRUNE_INTERVAL = 3600

FALLBACK_REPLY = "یه مشکلی پیش اومد؛ پیامت رو برای پشتیبانی انسانی می‌فرستم."


def _flag(db, key: str, default: str = "0") -> bool:
    return str(db.get_setting(key, default)).strip() == "1"


def _rights(conn: BusinessConnection):
    rights = getattr(conn, "rights", None)
    if rights is not None:
        return bool(getattr(rights, "can_reply", False)), bool(getattr(rights, "can_read_messages", False))
    return bool(getattr(conn, "can_reply", True)), False


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _customer_label(row) -> str:
    name = html.escape((row["first_name"] or "").strip()) or str(row["chat_id"])
    username = (row["username"] or "").strip()
    return f"{name} (@{html.escape(username)})" if username else name


class BusinessGuardMiddleware(BaseMiddleware):
    """Drops updates the bot must never answer and injects the connection row as ``biz_conn``."""

    def __init__(self, db):
        super().__init__()
        self.db = db

    async def _load_connection(self, bot: Bot, connection_id: str):
        row = await asyncio.to_thread(self.db.get_business_connection, connection_id)
        if row is not None:
            return row
        try:
            conn = await bot.get_business_connection(connection_id)
        except Exception:
            logger.warning("get_business_connection failed for %s", connection_id, exc_info=True)
            return None
        can_reply, can_read = _rights(conn)
        user = conn.user
        await asyncio.to_thread(
            self.db.upsert_business_connection, conn.id, user.id,
            getattr(conn, "user_chat_id", None) or user.id, can_reply, can_read,
            bool(conn.is_enabled), user.first_name or "", user.username or "",
        )
        return await asyncio.to_thread(self.db.get_business_connection, connection_id)

    async def __call__(self, handler, event: Message, data: dict):
        db = self.db
        if not _flag(db, KEY_ENABLED):
            return
        connection_id = getattr(event, "business_connection_id", None)
        user = event.from_user
        if not connection_id or user is None or user.is_bot:
            return
        if str(db.get_setting(global_switch.SETTING_KEY, "1")).strip() == "0":
            return
        conn = await self._load_connection(data["bot"], connection_id)
        if conn is None or not conn["is_enabled"] or not conn["auto_reply"]:
            return
        if user.id == conn["user_id"] or db.is_admin(user.id):
            return
        db_user = await asyncio.to_thread(db.get_user, user.id)
        if db_user and db_user["is_blocked"]:
            return
        if await asyncio.to_thread(db.is_business_exception, conn["id"], user.id):
            return
        data["biz_conn"] = conn
        return await handler(event, data)


def _admin_kb_rows(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def create_business_router(db, language_mw=None) -> Router:
    router = Router()
    chat_locks: dict = {}
    state = {"last_prune": 0.0}

    guard = BusinessGuardMiddleware(db)
    router.business_message.outer_middleware(guard)
    router.business_message.outer_middleware(ThrottleMiddleware(db))
    router.edited_business_message.outer_middleware(guard)
    if language_mw is not None:
        router.business_message.outer_middleware(language_mw)

    def _lock_for(key):
        lock = chat_locks.get(key)
        if lock is None:
            if len(chat_locks) > 2000:
                for k in [k for k, v in chat_locks.items() if not v.locked()]:
                    chat_locks.pop(k, None)
            lock = chat_locks[key] = asyncio.Lock()
        return lock

    async def _send(bot: Bot, connection_id: str, chat_id: int, text: str, reply_markup=None):
        await bot.send_message(
            chat_id=chat_id, text=_clip(text), business_connection_id=connection_id, parse_mode=None,
            reply_markup=reply_markup,
        )

    async def _send_product_card(bot: Bot, connection_id: str, chat_id: int, product_id) -> bool:
        """کارت محصول داخل چت بیزنس. فقط دکمه‌ی URL دارد (نه callback)؛ خرید و پرداخت
        داخل خودِ بات و با دیپ‌لینک prod_<id> ادامه پیدا می‌کند."""
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            return False
        product = await asyncio.to_thread(db.get_product, product_id)
        if not product or not product["is_active"]:
            return False
        auto = bool(product["is_auto_provision"])
        stock = 0 if auto else await asyncio.to_thread(db.count_available_configs, product_id)
        if not auto and stock <= 0:
            await _send(bot, connection_id, chat_id, tr("⛔️ موجودی این محصول در حال حاضر تمام شده."))
            return True
        lines = [
            f"📦 {product['name']}",
            f"💰 {tr('قیمت')}: {ul.price_for_users(product, 0):,} {tr('تومان')}",
        ]
        specs = []
        if auto and product["auto_provision_volume_gb"]:
            specs.append(f"📦 {tr('حجم')}: {product['auto_provision_volume_gb']} {tr('گیگ')}")
        if product["duration_days"]:
            specs.append(f"⏳ {tr('مدت')}: {product['duration_days']} {tr('روز')}")
        if specs:
            lines.append(" | ".join(specs))
        if product["description"]:
            lines.append(f"📝 {product['description']}")
        me = await bot.me()
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=tr("🛒 ادامه‌ی خرید در ربات"), url=f"https://t.me/{me.username}?start=prod_{product_id}",
        )]])
        await _send(bot, connection_id, chat_id, "\n".join(lines), markup)
        return True

    async def _notify_seller(bot: Bot, conn, text: str, markup=None):
        targets = []
        for target in (conn["user_chat_id"], conn["user_id"]):
            if target and target not in targets:
                targets.append(target)
        for target in targets:
            try:
                await bot.send_message(target, _clip(text), reply_markup=markup)
                return True
            except Exception:
                logger.warning("business notify to %s failed", target, exc_info=True)
        try:
            admin_ids = await asyncio.to_thread(db.list_admins)
        except Exception:
            return False
        delivered = False
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, _clip(text), reply_markup=markup)
                delivered = True
            except Exception:
                logger.warning("business notify to admin %s failed", admin_id, exc_info=True)
        return delivered

    async def _handoff(bot: Bot, conn, chat_id: int, user, history, user_text: str, reply_text: str):
        await asyncio.to_thread(db.set_business_chat_mode, conn["id"], chat_id, "human")
        lines = [
            tr("🤝 تحویل چت بیزنس به انسان"),
            f"👤 {html.escape(user.first_name or '')} (@{html.escape(user.username or '---')})",
            f"🆔 <code>{user.id}</code>",
            "",
            tr("دستیار هوشمند در این چت ساکت شد. بعد از پاسخ‌دادن، دکمه‌ی زیر را بزنید تا دوباره خودکار شود."),
            "",
            tr("--- آخرین پیام‌ها ---"),
        ]
        tail = list(history)[-TRANSCRIPT_LINES:]
        for row in tail:
            who = tr("👤 مشتری") if row["role"] == "user" else tr("🤖 دستیار")
            lines.append(f"{who}: {html.escape(row['message'])}")
        lines.append(f"{tr('👤 مشتری')}: {html.escape(user_text)}")
        if reply_text:
            lines.append(f"{tr('🤖 دستیار')}: {html.escape(reply_text)}")
        markup = _admin_kb_rows([
            [InlineKeyboardButton(text=tr("▶️ بازگشت به حالت خودکار"), callback_data=f"bizr:{conn['id']}:{chat_id}")],
            [InlineKeyboardButton(text=tr("💬 باز کردن چت"), url=f"tg://user?id={chat_id}")],
        ])
        await _notify_seller(bot, conn, "\n".join(lines), markup)
        await asyncio.to_thread(db.clear_business_ai_conversation, conn["id"], chat_id)

    async def _maybe_prune():
        now = time.monotonic()
        if now - state["last_prune"] < LOG_PRUNE_INTERVAL:
            return
        state["last_prune"] = now
        try:
            await asyncio.to_thread(db.prune_business_message_log)
        except Exception:
            logger.warning("business log prune failed", exc_info=True)

    @router.business_connection()
    async def on_business_connection(conn: BusinessConnection, bot: Bot):
        can_reply, can_read = _rights(conn)
        user = conn.user
        await asyncio.to_thread(
            db.upsert_business_connection, conn.id, user.id,
            getattr(conn, "user_chat_id", None) or user.id, can_reply, can_read,
            bool(conn.is_enabled), user.first_name or "", user.username or "",
        )
        status = tr("🟢 فعال شد") if conn.is_enabled else tr("🔴 قطع شد")
        text = (
            f"💼 {tr('اتصال تلگرام بیزنس')} {status}\n"
            f"👤 {html.escape(user.first_name or '')} (@{html.escape(user.username or '---')})\n"
            f"🆔 <code>{user.id}</code>\n"
            f"{tr('پاسخ‌دادن')}: {'✅' if can_reply else '❌'} | {tr('خواندن پیام‌ها')}: {'✅' if can_read else '❌'}"
        )
        if not _flag(db, KEY_ENABLED):
            text += "\n\n" + tr("⚠️ قابلیت بیزنس هنوز از پنل مدیریت روشن نشده است.")
        try:
            admin_ids = await asyncio.to_thread(db.list_admins)
        except Exception:
            admin_ids = []
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except Exception:
                logger.warning("business connection notice to %s failed", admin_id, exc_info=True)

    @router.business_message()
    async def on_business_message(message: Message, bot: Bot, biz_conn):
        user = message.from_user
        chat_id = message.chat.id
        conn_row = biz_conn["id"]
        connection_id = biz_conn["connection_id"]
        chat = await asyncio.to_thread(
            db.touch_business_chat, conn_row, chat_id, user.first_name or "", user.username or ""
        )
        text = (message.text or "").strip()
        if text:
            await asyncio.to_thread(db.log_business_message, conn_row, chat_id, message.message_id, user.id, text)
            await _maybe_prune()
        if chat["mode"] == "human" or not text or not biz_conn["can_reply"]:
            return
        if not ai_support.is_configured(db):
            return

        async with _lock_for((conn_row, chat_id)):
            chat = await asyncio.to_thread(db.get_business_chat, conn_row, chat_id)
            if chat is None or chat["mode"] == "human":
                return
            try:
                if biz_conn["first_message"] and not chat["greeted"]:
                    await asyncio.to_thread(db.mark_business_chat_greeted, conn_row, chat_id)
                    await _send(bot, connection_id, chat_id, biz_conn["first_message"])
                try:
                    await bot.send_chat_action(
                        chat_id=chat_id, action="typing", business_connection_id=connection_id
                    )
                except Exception:
                    pass
                history = await asyncio.to_thread(db.get_business_ai_conversation, conn_row, chat_id)
                try:
                    mode = ai_support.BUSINESS_SALES if _flag(db, KEY_SALES_CARD) else True
                    result = await ai_support.get_reply(db, user.id, history, text, business_mode=mode)
                except Exception:
                    logger.exception("business AI reply failed for user %s", user.id)
                    result = {"reply": FALLBACK_REPLY, "escalate": True}
                reply = (result.get("reply") or "").strip()
                if reply:
                    await _send(bot, connection_id, chat_id, reply)
                    await asyncio.to_thread(db.add_business_ai_message, conn_row, chat_id, "user", text)
                    await asyncio.to_thread(db.add_business_ai_message, conn_row, chat_id, "model", reply)
                if _flag(db, KEY_MARK_READ) and biz_conn["can_read"]:
                    try:
                        await bot.read_business_message(
                            business_connection_id=connection_id, chat_id=chat_id, message_id=message.message_id
                        )
                    except Exception:
                        logger.warning("read_business_message failed", exc_info=True)
                if result.get("escalate"):
                    await _handoff(bot, biz_conn, chat_id, user, history, text, reply)
                    return
                ui_action = result.get("ui_action") or {}
                if ui_action.get("type") == "show_product" and _flag(db, KEY_SALES_CARD):
                    await _send_product_card(bot, connection_id, chat_id, ui_action.get("product_id"))
                elif ui_action.get("type") == "deliver_test_config":
                    me = await bot.me()
                    await _send(
                        bot, connection_id, chat_id,
                        tr("🧪 برای دریافت کانفیگ تست وارد ربات بشید و دکمه‌ی «کانفیگ تست» را بزنید:")
                        + f"\nhttps://t.me/{me.username}",
                    )
            except Exception:
                logger.exception("business message handling failed (chat %s)", chat_id)

    @router.edited_business_message()
    async def on_edited_business_message(message: Message, bot: Bot, biz_conn):
        text = (message.text or "").strip()
        if not text:
            return
        chat_id = message.chat.id
        old = await asyncio.to_thread(
            db.update_business_message_text, biz_conn["id"], chat_id, message.message_id, text
        )
        chat = await asyncio.to_thread(db.get_business_chat, biz_conn["id"], chat_id)
        if chat is None or chat["mode"] != "human" or not _flag(db, KEY_NOTIFY_CHANGES, "1"):
            return
        lines = [f"✏️ {tr('مشتری پیامش را ویرایش کرد')}: {_customer_label(chat)}"]
        if old:
            lines.append(f"{tr('قبل')}: {html.escape(old)}")
        lines.append(f"{tr('بعد')}: {html.escape(text)}")
        await _notify_seller(bot, biz_conn, "\n".join(lines))

    @router.deleted_business_messages()
    async def on_deleted_business_messages(event: BusinessMessagesDeleted, bot: Bot):
        if not _flag(db, KEY_ENABLED) or not _flag(db, KEY_NOTIFY_CHANGES, "1"):
            return
        conn = await asyncio.to_thread(db.get_business_connection, event.business_connection_id)
        if conn is None or not conn["is_enabled"]:
            return
        chat_id = event.chat.id
        rows = await asyncio.to_thread(db.pop_business_messages, conn["id"], chat_id, event.message_ids)
        chat = await asyncio.to_thread(db.get_business_chat, conn["id"], chat_id)
        if not rows or chat is None or chat["mode"] != "human":
            return
        lines = [f"🗑 {tr('مشتری پیام حذف کرد')}: {_customer_label(chat)}"]
        lines.extend(html.escape(r["text"] or "") for r in rows)
        await _notify_seller(bot, conn, "\n".join(lines))

    async def _resume(call: CallbackQuery) -> bool:
        try:
            _, conn_row, chat_id = call.data.split(":")
            conn_row, chat_id = int(conn_row), int(chat_id)
        except ValueError:
            await call.answer()
            return False
        conn = await asyncio.to_thread(db.get_business_connection_by_id, conn_row)
        if conn is None:
            await call.answer(tr("اتصال پیدا نشد."), show_alert=True)
            return False
        if call.from_user.id != conn["user_id"] and not db.is_full_admin(call.from_user.id):
            await call.answer(tr("⛔️ دسترسی ندارید."), show_alert=True)
            return False
        await asyncio.to_thread(db.set_business_chat_mode, conn_row, chat_id, "auto")
        await asyncio.to_thread(db.clear_business_ai_conversation, conn_row, chat_id)
        await call.answer(tr("✅ چت به حالت خودکار برگشت."))
        return True

    @router.callback_query(F.data.startswith("bizr:"))
    async def cb_resume(call: CallbackQuery):
        if await _resume(call) and call.message is not None:
            try:
                await call.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # In-bot admin panel‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
    # ------------------------------------------------------------------‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

    async def _deny(call: CallbackQuery):
        await call.answer(tr("⛔️ این بخش فقط برای مدیران کامل در دسترس است."), show_alert=True)

    async def _show(call: CallbackQuery, text: str, markup):
        if call.message is None:
            return
        try:
            await call.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
        except Exception:
            pass
        try:
            await call.message.delete()
        except Exception:
            pass
        try:
            await call.message.answer(text, reply_markup=markup)
        except Exception:
            pass

    def _onoff(value: bool) -> str:
        return "🟢" if value else "🔴"

    async def _show_main(call: CallbackQuery):
        conns = await asyncio.to_thread(db.list_business_connections)
        humans = await asyncio.to_thread(db.count_business_human_chats)
        active = sum(1 for c in conns if c["is_enabled"])
        enabled = _flag(db, KEY_ENABLED)
        mark_read = _flag(db, KEY_MARK_READ)
        notify = _flag(db, KEY_NOTIFY_CHANGES, "1")
        sales = _flag(db, KEY_SALES_CARD)
        text = (
            f"{tr('💼 تلگرام بیزنس')}\n\n"
            f"{tr('وضعیت کل قابلیت')}: {_onoff(enabled)}\n"
            f"{tr('اتصال‌های فعال')}: {active}\n"
            f"{tr('چت‌های تحویل‌شده به انسان')}: {humans}\n\n"
            + tr("برای اتصال: فروشنده در تلگرام به Settings → Telegram Business → Chatbots برود و یوزرنیم همین ربات را وارد کند.")
            + "\n"
            + tr("برای تست، با حساب غیرادمین به حساب فروشنده پیام بدهید؛ پیام ادمین‌ها و خود فروشنده نادیده گرفته می‌شود.")
        )
        rows = [
            [InlineKeyboardButton(text=f"{_onoff(enabled)} {tr('قابلیت بیزنس')}", callback_data="adm_biz_master")],
            [InlineKeyboardButton(text=f"{_onoff(mark_read)} {tr('علامت‌گذاری خوانده‌شده')}", callback_data="adm_biz_markread")],
            [InlineKeyboardButton(text=f"{_onoff(notify)} {tr('اعلان ویرایش/حذف پیام مشتری')}", callback_data="adm_biz_notify")],
            [InlineKeyboardButton(text=f"{_onoff(sales)} {tr('کارت خرید داخل چت بیزنس')}", callback_data="adm_biz_sales")],
            [InlineKeyboardButton(text=tr("🔗 اتصال‌ها"), callback_data="adm_biz_conns")],
            [InlineKeyboardButton(text=f"{tr('🤝 چت‌های تحویل‌شده به انسان')} ({humans})", callback_data="adm_biz_humans")],
        ]
        rows.append([InlineKeyboardButton(text=tr("⬅️ بازگشت"), callback_data="adm_cat:access")])
        await _show(call, text, _admin_kb_rows(rows))

    @router.callback_query(F.data == "adm_business_settings")
    async def cb_main(call: CallbackQuery, state: FSMContext):
        if not db.is_full_admin(call.from_user.id):
            return await _deny(call)
        await state.clear()
        await _show_main(call)
        await call.answer()

    def _toggle_handler(callback_data: str, key: str, default: str, log_action: str):
        @router.callback_query(F.data == callback_data)
        async def _handler(call: CallbackQuery):
            if not db.is_full_admin(call.from_user.id):
                return await _deny(call)
            new_value = "0" if _flag(db, key, default) else "1"
            await asyncio.to_thread(db.set_setting, key, new_value)
            await asyncio.to_thread(db.log_admin_action, call.from_user.id, log_action, f"{key}={new_value}")
            await _show_main(call)
            await call.answer()
        return _handler

    _toggle_handler("adm_biz_master", KEY_ENABLED, "0", "business_toggle")
    _toggle_handler("adm_biz_markread", KEY_MARK_READ, "0", "business_markread_toggle")
    _toggle_handler("adm_biz_notify", KEY_NOTIFY_CHANGES, "1", "business_notify_toggle")
    _toggle_handler("adm_biz_sales", KEY_SALES_CARD, "0", "business_sales_toggle")

    @router.callback_query(F.data == "adm_biz_conns")
    async def cb_conns(call: CallbackQuery):
        if not db.is_full_admin(call.from_user.id):
            return await _deny(call)
        conns = await asyncio.to_thread(db.list_business_connections)
        rows = []
        for c in conns[:40]:
            name = (c["user_name"] or "").strip() or str(c["user_id"])
            mark = _onoff(bool(c["is_enabled"]) and bool(c["auto_reply"]))
            rows.append([InlineKeyboardButton(text=f"{mark} {name}", callback_data=f"adm_biz_conn:{c['id']}")])
        rows.append([InlineKeyboardButton(text=tr("⬅️ بازگشت"), callback_data="adm_business_settings")])
        text = tr("🔗 اتصال‌های بیزنس") if conns else tr("هنوز هیچ اتصالی ثبت نشده است.")
        await _show(call, text, _admin_kb_rows(rows))
        await call.answer()

    async def _show_conn(call: CallbackQuery, row_id: int):
        c = await asyncio.to_thread(db.get_business_connection_by_id, row_id)
        if c is None:
            await call.answer(tr("اتصال پیدا نشد."), show_alert=True)
            return
        exceptions = await asyncio.to_thread(db.list_business_exceptions, row_id)
        first = (c["first_message"] or "").strip()
        text = (
            f"💼 {html.escape(c['user_name'] or '')} (@{html.escape(c['username'] or '---')})\n"
            f"🆔 <code>{c['user_id']}</code>\n\n"
            f"{tr('اتصال در تلگرام')}: {_onoff(bool(c['is_enabled']))}\n"
            f"{tr('پاسخ خودکار')}: {_onoff(bool(c['auto_reply']))}\n"
            f"{tr('دسترسی پاسخ‌دادن')}: {'✅' if c['can_reply'] else '❌'} | "
            f"{tr('خواندن پیام‌ها')}: {'✅' if c['can_read'] else '❌'}\n"
            f"{tr('استثناها')}: {len(exceptions)}\n\n"
            f"{tr('پیام اول')}: {html.escape(first) if first else tr('ندارد')}"
        )
        rows = [
            [InlineKeyboardButton(text=f"{_onoff(bool(c['auto_reply']))} {tr('پاسخ خودکار')}", callback_data=f"adm_biz_tg:{row_id}")],
            [InlineKeyboardButton(text=tr("✏️ متن پیام اول"), callback_data=f"adm_biz_fm:{row_id}")],
            [InlineKeyboardButton(text=f"{tr('🚫 لیست استثناها')} ({len(exceptions)})", callback_data=f"adm_biz_ex:{row_id}")],
            [InlineKeyboardButton(text=tr("⬅️ بازگشت"), callback_data="adm_biz_conns")],
        ]
        await _show(call, text, _admin_kb_rows(rows))

    def _row_id(data: str):
        try:
            return int(data.split(":")[1])
        except (IndexError, ValueError):
            return None

    @router.callback_query(F.data.startswith("adm_biz_conn:"))
    async def cb_conn(call: CallbackQuery, state: FSMContext):
        if not db.is_full_admin(call.from_user.id):
            return await _deny(call)
        row_id = _row_id(call.data)
        if row_id is None:
            return await call.answer()
        await state.clear()
        await _show_conn(call, row_id)
        await call.answer()

    @router.callback_query(F.data.startswith("adm_biz_tg:"))
    async def cb_conn_toggle(call: CallbackQuery):
        if not db.is_full_admin(call.from_user.id):
            return await _deny(call)
        row_id = _row_id(call.data)
        c = await asyncio.to_thread(db.get_business_connection_by_id, row_id) if row_id is not None else None
        if c is None:
            return await call.answer(tr("اتصال پیدا نشد."), show_alert=True)
        await asyncio.to_thread(db.set_business_connection_auto_reply, row_id, not c["auto_reply"])
        await asyncio.to_thread(
            db.log_admin_action, call.from_user.id, "business_conn_toggle", f"conn={row_id} auto_reply={int(not c['auto_reply'])}"
        )
        await _show_conn(call, row_id)
        await call.answer()

    @router.callback_query(F.data.startswith("adm_biz_fm:"))
    async def cb_first_message(call: CallbackQuery, state: FSMContext):
        if not db.is_full_admin(call.from_user.id):
            return await _deny(call)
        row_id = _row_id(call.data)
        if row_id is None:
            return await call.answer()
        await state.set_state(AdminBusiness.waiting_first_message)
        await state.update_data(biz_row_id=row_id)
        markup = _admin_kb_rows([[InlineKeyboardButton(text=tr("⬅️ بازگشت"), callback_data=f"adm_biz_conn:{row_id}")]])
        await _show(call, tr("متن پیام اول را بفرستید. این پیام فقط یک‌بار در شروع هر چت جدید ارسال می‌شود. برای حذف، «-» بفرستید."), markup)
        await call.answer()

    @router.message(StateFilter(AdminBusiness.waiting_first_message))
    async def on_first_message_text(message: Message, state: FSMContext):
        if not db.is_full_admin(message.from_user.id):
            return
        text = (message.text or "").strip()
        if not text:
            await message.answer(tr("لطفاً متن بفرستید."))
            return
        row_id = (await state.get_data()).get("biz_row_id")
        await state.clear()
        if row_id is None:
            return
        await asyncio.to_thread(db.set_business_connection_first_message, row_id, "" if text == "-" else text)
        await asyncio.to_thread(db.log_admin_action, message.from_user.id, "business_first_message", f"conn={row_id}")
        markup = _admin_kb_rows([[InlineKeyboardButton(text=tr("⬅️ بازگشت"), callback_data=f"adm_biz_conn:{row_id}")]])
        await message.answer(tr("✅ ذخیره شد."), reply_markup=markup)

    async def _show_exceptions(call: CallbackQuery, row_id: int):
        exceptions = await asyncio.to_thread(db.list_business_exceptions, row_id)
        rows = []
        for e in exceptions[:40]:
            rows.append([
                InlineKeyboardButton(text=str(e["user_id"]), callback_data="noop"),
                InlineKeyboardButton(text="🗑", callback_data=f"adm_biz_exdel:{row_id}:{e['user_id']}"),
            ])
        rows.append([InlineKeyboardButton(text=tr("➕ افزودن آیدی عددی"), callback_data=f"adm_biz_exadd:{row_id}")])
        rows.append([InlineKeyboardButton(text=tr("⬅️ بازگشت"), callback_data=f"adm_biz_conn:{row_id}")])
        text = tr("🚫 مشتری‌هایی که ربات در چتشان پاسخ نمی‌دهد:") if exceptions else tr("لیست استثناها خالی است.")
        await _show(call, text, _admin_kb_rows(rows))

    @router.callback_query(F.data.startswith("adm_biz_ex:"))
    async def cb_exceptions(call: CallbackQuery, state: FSMContext):
        if not db.is_full_admin(call.from_user.id):
            return await _deny(call)
        row_id = _row_id(call.data)
        if row_id is None:
            return await call.answer()
        await state.clear()
        await _show_exceptions(call, row_id)
        await call.answer()

    @router.callback_query(F.data.startswith("adm_biz_exadd:"))
    async def cb_exception_add(call: CallbackQuery, state: FSMContext):
        if not db.is_full_admin(call.from_user.id):
            return await _deny(call)
        row_id = _row_id(call.data)
        if row_id is None:
            return await call.answer()
        await state.set_state(AdminBusiness.waiting_exception_id)
        await state.update_data(biz_row_id=row_id)
        markup = _admin_kb_rows([[InlineKeyboardButton(text=tr("⬅️ بازگشت"), callback_data=f"adm_biz_ex:{row_id}")]])
        await _show(call, tr("آیدی عددی تلگرام مشتری را بفرستید:"), markup)
        await call.answer()

    @router.message(StateFilter(AdminBusiness.waiting_exception_id))
    async def on_exception_id(message: Message, state: FSMContext):
        if not db.is_full_admin(message.from_user.id):
            return
        raw = (message.text or "").strip()
        if not raw.isdigit():
            await message.answer(tr("آیدی عددی معتبر بفرستید."))
            return
        row_id = (await state.get_data()).get("biz_row_id")
        await state.clear()
        if row_id is None:
            return
        await asyncio.to_thread(db.add_business_exception, row_id, int(raw))
        await asyncio.to_thread(db.log_admin_action, message.from_user.id, "business_exception_add", f"conn={row_id} user={raw}")
        markup = _admin_kb_rows([[InlineKeyboardButton(text=tr("⬅️ بازگشت"), callback_data=f"adm_biz_ex:{row_id}")]])
        await message.answer(tr("✅ اضافه شد."), reply_markup=markup)

    @router.callback_query(F.data.startswith("adm_biz_exdel:"))
    async def cb_exception_del(call: CallbackQuery):
        if not db.is_full_admin(call.from_user.id):
            return await _deny(call)
        try:
            _, row_id, user_id = call.data.split(":")
            row_id, user_id = int(row_id), int(user_id)
        except ValueError:
            return await call.answer()
        await asyncio.to_thread(db.remove_business_exception, row_id, user_id)
        await asyncio.to_thread(db.log_admin_action, call.from_user.id, "business_exception_del", f"conn={row_id} user={user_id}")
        await _show_exceptions(call, row_id)
        await call.answer()

    async def _show_humans(call: CallbackQuery):
        chats = await asyncio.to_thread(db.list_business_human_chats)
        rows = []
        for ch in chats:
            label = (ch["first_name"] or "").strip() or str(ch["chat_id"])
            rows.append([InlineKeyboardButton(
                text=f"▶️ {label}", callback_data=f"adm_biz_res:{ch['conn_row']}:{ch['chat_id']}"
            )])
        rows.append([InlineKeyboardButton(text=tr("⬅️ بازگشت"), callback_data="adm_business_settings")])
        text = tr("برای برگرداندن چت به حالت خودکار، روی نام مشتری بزنید:") if chats else tr("چتی در حالت انسانی نیست.")
        await _show(call, text, _admin_kb_rows(rows))

    @router.callback_query(F.data == "adm_biz_humans")
    async def cb_humans(call: CallbackQuery):
        if not db.is_full_admin(call.from_user.id):
            return await _deny(call)
        await _show_humans(call)
        await call.answer()

    @router.callback_query(F.data.startswith("adm_biz_res:"))
    async def cb_humans_resume(call: CallbackQuery):
        if not db.is_full_admin(call.from_user.id):
            return await _deny(call)
        if await _resume(call):
            await _show_humans(call)

    return router
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
