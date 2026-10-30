# -*- coding: utf-8 -*-
"""هندلرهای کارت هدیه: ساخت و دریافت توسط کاربر، و مدیریت کامل توسط ادمین."""

import asyncio
import io
import json
import logging
from urllib.parse import quote

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import gift_card_render as gr
import gift_cards as gc
import report_router
from i18n import tr
from jalali import to_jalali_str

_log = logging.getLogger("gift_card_handlers")
_PAGE = 8
_MAX_NAME = 40
_MAX_MESSAGE = 300
_MAX_FAILED = 8
_username_cache = {}


class GiftSend(StatesGroup):
    amount = State()
    recipient = State()
    message = State()


class GiftRedeem(StatesGroup):
    code = State()


class GiftAdmin(StatesGroup):
    theme_title = State()
    theme_image = State()
    setting_value = State()


def _btn(text, data=None, url=None):
    if url:
        return InlineKeyboardButton(text=tr(text), url=url)
    return InlineKeyboardButton(text=tr(text), callback_data=data)


def _kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows])


async def _bot_username(bot: Bot) -> str:
    if bot.token not in _username_cache:
        _username_cache[bot.token] = (await bot.get_me()).username
    return _username_cache[bot.token]


async def _edit(call: CallbackQuery, text: str, markup=None):
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:
        await call.message.answer(text, reply_markup=markup)


def _share_markup(link: str, card_id: int):
    text = tr("🎁 یک کارت هدیه برات فرستادم! روی لینک بزن و دریافتش کن:")
    url = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(text)}"
    return _kb([_btn("📤 اشتراک‌گذاری کارت", url=url)], [_btn("🗂 کارت‌های من", "gift_my:0")])


def _kind_title(card) -> str:
    if card["kind"] == "wallet":
        return f"{gc.money(card['amount'])} تومان"
    snap = json.loads(card["config_snapshot"] or "{}")
    return f"کانفیگ {snap.get('name') or ''}".strip()


def _card_line(card) -> str:
    return f"#{card['id']} {gc.STATUS_LABELS.get(card['status'], card['status'])} | {_kind_title(card)} | {card['recipient_name'] or '-'}"


def _fmt_time(value) -> str:
    return to_jalali_str(value, with_time=True) if value else "-"


async def redeem_gift_for_message(db, message: Message, code: str):
    """دریافت کارت با کد (از لینک /start یا ورود دستی)؛ نتیجه را به کاربر و فرستنده اطلاع می‌دهد."""
    user = message.from_user
    if not await asyncio.to_thread(db.get_user, user.id):
        await asyncio.to_thread(db.add_or_update_user, user.id, user.username or "", user.first_name or "")
    if await asyncio.to_thread(db.gift_failed_attempts, user.id) >= _MAX_FAILED:
        await message.answer(tr("⛔️ تعداد تلاش‌های ناموفق زیاد بود. یک ساعت دیگر دوباره امتحان کنید."))
        return
    result = await asyncio.to_thread(db.gift_redeem, code, user.id)
    if not result["ok"]:
        reasons = {
            "not_found": "❌ کد کارت هدیه نامعتبر است.",
            "expired": "⌛️ این کارت هدیه منقضی شده است.",
            "redeemed": "ℹ️ این کارت قبلاً دریافت شده است.",
            "revoked": "🚫 این کارت هدیه باطل شده است.",
            "cancelled": "🚫 این کارت هدیه توسط فرستنده لغو شده است.",
            "disabled": "⏸ این کارت هدیه موقتاً غیرفعال است. بعداً دوباره تلاش کنید.",
            "self": "ℹ️ کارتی که خودتان ساخته‌اید را نمی‌توانید دریافت کنید؛ از «کارت‌های من» می‌توانید لغوش کنید.",
            "receiver": "⛔️ امکان دریافت برای حساب شما وجود ندارد.",
            "escrow_lost": "⚠️ مشکلی در تحویل کارت پیش آمد؛ با پشتیبانی تماس بگیرید.",
        }
        await message.answer(tr(reasons.get(result["error"], "❌ دریافت کارت ناموفق بود.")))
        return
    card = result["card"]
    await report_router.notify(message.bot, db, "discount", report_router.format_event(
        "گیفت‌کارت دریافت شد", [
            ("دریافت‌کننده", report_router.user_link(user.id, user.first_name or "", user.username or "")),
            ("نوع", "کیف پول" if card["kind"] == "wallet" else "کانفیگ"),
            ("مبلغ", f"{gc.money(card['amount'])} تومان" if card["kind"] == "wallet" else ""),
            ("فرستنده", report_router.code(card["sender_id"])),
        ], icon="🎁"))
    if card["kind"] == "wallet":
        balance = await asyncio.to_thread(db.get_wallet_credit, user.id)
        await message.answer(tr(
            f"🎉 کارت هدیه دریافت شد!\n{gc.money(card['amount'])} تومان به کیف پول شما اضافه شد.\n"
            f"موجودی فعلی: {gc.money(balance)} تومان"
        ))
    else:
        await _deliver_config(db, message, card)
    try:
        who = f"@{user.username}" if user.username else (user.first_name or str(user.id))
        await message.bot.send_message(card["sender_id"], tr(f"🎉 کارت هدیه‌ی شما (به مقصد «{card['recipient_name'] or '-'}») توسط {who} دریافت شد."))
    except Exception:
        _log.info("sender notify failed for card %s", card["id"])


async def _deliver_config(db, message: Message, card):
    snap = json.loads(card["config_snapshot"] or "{}")
    if card["kind"] == "stock":
        link = await asyncio.to_thread(db.gift_stock_link, card["stock_config_id"])
    else:
        rows = await asyncio.to_thread(db.get_user_custom_configs, message.from_user.id)
        link = next((r["subscription_url"] for r in rows if r["id"] == card["custom_config_id"]), "") or ""
    text = f"🎉 کارت هدیه دریافت شد!\n📦 کانفیگ: {snap.get('name') or '-'}\n"
    if "volume_gb" in snap:
        text += f"💾 حجم: {str(snap['volume_gb']) + ' گیگابایت' if snap['volume_gb'] else 'نامحدود'}\n"
    if "duration_days" in snap:
        text += f"⏳ مدت: {str(snap['duration_days']) + ' روز' if snap['duration_days'] else 'نامحدود'}\n"
    if link:
        text += f"\n🔗 لینک اشتراک:\n{link}\n"
    text += "\nهمین کانفیگ از بخش «سرویس‌ها و سفارش‌های من» هم قابل مشاهده است."
    await message.answer(tr(text))


async def _send_card(bot: Bot, chat_id: int, card, code: str, image: bytes | None):
    link = gc.deep_link(await _bot_username(bot), code)
    caption = tr(f"🎁 کارت هدیه آماده است!\nکد: {code}\nلینک دریافت: {link}")
    markup = _share_markup(link, card["id"])
    if image:
        await bot.send_photo(chat_id, BufferedInputFile(image, filename="gift_card.png"), caption=caption, reply_markup=markup)
    else:
        await bot.send_message(chat_id, caption, reply_markup=markup)


async def deliver_gift_order(bot: Bot, db, order_id: int) -> bool:
    """اگر سفارش، «خرید به‌عنوان هدیه» باشد کارت را به خریدار می‌دهد و True برمی‌گرداند (تحویل عادیِ لینک انجام نشود)."""
    order = await asyncio.to_thread(db.gift_get_order, order_id)
    if not order or not order["card_id"]:
        return False
    if not await asyncio.to_thread(db.gift_mark_order_delivered, order_id):
        return True
    card = await asyncio.to_thread(db.gift_get_card, order["card_id"])
    code = card["code_plain"]
    image = None
    try:
        path = await gc.build_card_image(db, card, code, await _bot_username(bot))
        image = gc.card_image_bytes({"image_path": path})
    except Exception:
        _log.exception("gift order card render failed for card %s", card["id"])
    await bot.send_message(order["user_id"], tr("✅ خرید انجام شد و کانفیگ داخل کارت هدیه قرار گرفت. کارت را برای گیرنده بفرستید 👇"))
    await _send_card(bot, order["user_id"], card, code, image)
    return True


def create_gift_router(db, is_main_bot: bool = True) -> Router:
    router = Router()

    def is_admin(user_id: int) -> bool:
        return db.is_full_admin(user_id)

    async def _sample_card(theme_id: int, bot: Bot):
        theme = await asyncio.to_thread(db.gift_get_theme, theme_id)
        content = {
            "recipient": "مهسا", "recipient_label": gc.recipient_label("مهسا"), "title": gc.gca.fallback("", theme["title"])["title"],
            "message": gc.gca.fallback("مهسا", theme["title"])["message"], "signature": gc.gca.DEFAULT_SIGNATURE,
            "qr_link": gc.deep_link(await _bot_username(bot), "GIFTSAMPLE"),
            "info_lines": [(f"{gc.money(250000)} تومان", 1.7), ("اعتبار کیف پول", 0.7)],
            "code_lines": [("GIFT-ABCD-EFGH-JKMN", 1.0), ("اعتبار تا ۱۴۰۵/۰۱/۰۱", 0.75)],
        }
        layout = await gc.ensure_layout(db, theme)
        return await asyncio.to_thread(gr.render_card, gc.theme_background(theme), layout, content)

    @router.callback_query(F.data == "gift_menu")
    async def cb_gift_menu(call: CallbackQuery, state: FSMContext):
        await state.clear()
        if not gc.enabled(db):
            await call.answer(tr("کارت هدیه در حال حاضر غیرفعال است."), show_alert=True)
            return
        rows = [
            [_btn("➕ ساخت کارت هدیه", "gift_new")],
            [_btn("📥 دریافت کارت هدیه", "gift_redeem")],
            [_btn("🗂 کارت‌های من", "gift_my:0")],
        ]
        text = "🎁 کارت هدیه\nمبلغ یا کانفیگ خودتان را در یک کارت گرافیکی زیبا برای دیگران بفرستید."
        if await asyncio.to_thread(db.gift_get_intent, call.from_user.id):
            text += "\n\n⏳ یک خرید هدیه در انتظار شماست: خرید بعدیِ کانفیگ شخصی شما به‌صورت کارت هدیه تحویل می‌شود."
            rows.insert(0, [_btn("🛑 لغو هدیه‌ی در انتظار خرید", "gift_intent_cancel")])
        rows.append([_btn("⬅️ بازگشت", "coins_back")])
        await _edit(call, tr(text), _kb(*rows))
        await call.answer()

    @router.callback_query(F.data == "gift_cancel")
    async def cb_gift_cancel(call: CallbackQuery, state: FSMContext):
        await state.clear()
        await cb_gift_menu(call, state)

    @router.callback_query(F.data == "gift_new")
    async def cb_gift_new(call: CallbackQuery, state: FSMContext):
        await state.clear()
        if not gc.enabled(db):
            await call.answer(tr("کارت هدیه در حال حاضر غیرفعال است."), show_alert=True)
            return
        rows = []
        if gc.enabled(db, "gift_allow_wallet"):
            rows.append([_btn("💰 مبلغ از کیف پول", "gift_t:wallet")])
        if gc.enabled(db, "gift_allow_config"):
            rows.append([_btn("📦 انتقال یکی از کانفیگ‌هایم", "gift_t:config")])
        if gc.enabled(db, "gift_allow_buy"):
            rows.append([_btn("🛒 خرید کانفیگ جدید به‌عنوان هدیه", "gift_t:buy")])
        if not rows:
            await call.answer(tr("هیچ نوع کارتی فعال نیست."), show_alert=True)
            return
        rows.append([_btn("⬅️ بازگشت", "gift_menu")])
        await _edit(call, tr("چه چیزی هدیه می‌دهید؟"), _kb(*rows))
        await call.answer()

    @router.callback_query(F.data == "gift_t:wallet")
    async def cb_type_wallet(call: CallbackQuery, state: FSMContext):
        if not gc.enabled(db, "gift_allow_wallet"):
            await call.answer(tr("این نوع کارت غیرفعال است."), show_alert=True)
            return
        lo, hi = gc.int_setting(db, "gift_min_amount"), gc.int_setting(db, "gift_max_amount")
        fee = gc.int_setting(db, "gift_fee_percent")
        balance = await asyncio.to_thread(db.get_wallet_credit, call.from_user.id)
        await state.set_state(GiftSend.amount)
        await state.update_data(kind="wallet")
        text = f"💰 مبلغ هدیه را به تومان بفرستید.\nحداقل: {gc.money(lo)} | حداکثر: {gc.money(hi)}\nموجودی شما: {gc.money(balance)} تومان"
        if fee:
            text += f"\nکارمزد: {gc.money(fee)}٪ (علاوه بر مبلغ هدیه از کیف پول کسر می‌شود)"
        await _edit(call, tr(text), _kb([_btn("❌ انصراف", "gift_cancel")]))
        await call.answer()

    @router.message(GiftSend.amount)
    async def msg_amount(message: Message, state: FSMContext):
        raw = (message.text or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٬،", "0123456789,,")).replace(",", "")
        lo, hi = gc.int_setting(db, "gift_min_amount"), gc.int_setting(db, "gift_max_amount")
        if not raw.isdigit() or int(raw) < lo or (hi and int(raw) > hi):
            await message.answer(tr(f"⚠️ یک عدد بین {gc.money(lo)} و {gc.money(hi)} تومان بفرستید."), reply_markup=_kb([_btn("❌ انصراف", "gift_cancel")]))
            return
        amount = int(raw)
        total = amount + amount * gc.int_setting(db, "gift_fee_percent") // 100
        balance = await asyncio.to_thread(db.get_wallet_credit, message.from_user.id)
        if balance < total:
            await message.answer(tr(f"⛔️ موجودی کافی نیست. مبلغ لازم: {gc.money(total)} تومان | موجودی: {gc.money(balance)} تومان"), reply_markup=_kb([_btn("❌ انصراف", "gift_cancel")]))
            return
        await state.update_data(amount=amount)
        await _ask_theme(message, state)

    @router.callback_query(F.data == "gift_t:buy")
    async def cb_type_buy(call: CallbackQuery, state: FSMContext):
        if not gc.enabled(db, "gift_allow_buy"):
            await call.answer(tr("این نوع کارت غیرفعال است."), show_alert=True)
            return
        await state.update_data(kind="buy")
        await _ask_theme(call.message, state, edit=True)
        await call.answer()

    @router.callback_query(F.data == "gift_intent_cancel")
    async def cb_intent_cancel(call: CallbackQuery, state: FSMContext):
        await asyncio.to_thread(db.gift_clear_intent, call.from_user.id)
        await call.answer(tr("لغو شد."))
        await cb_gift_menu(call, state)

    @router.callback_query(F.data == "gift_t:config")
    async def cb_type_config(call: CallbackQuery, state: FSMContext):
        if not gc.enabled(db, "gift_allow_config"):
            await call.answer(tr("این نوع کارت غیرفعال است."), show_alert=True)
            return
        rows = await asyncio.to_thread(db.get_user_custom_configs, call.from_user.id)
        usable = [r for r in rows if (r["status"] or "active") == "active"
                  and (r["source"] if "source" in r.keys() else None) != "test"]
        if not usable:
            await call.answer(tr("کانفیگ فعالِ قابل انتقال ندارید."), show_alert=True)
            return
        await state.update_data(kind="config")
        buttons = []
        for r in usable[:20]:
            name = (r["display_name"] if "display_name" in r.keys() else None) or r["username"]
            buttons.append([InlineKeyboardButton(text=f"{name} | {r['volume_gb']}GB", callback_data=f"gift_cfg:{r['id']}")])
        buttons.append([_btn("⬅️ بازگشت", "gift_new")])
        await _edit(call, tr("کدام کانفیگ را هدیه می‌دهید؟ بعد از ساخت کارت، کانفیگ تا زمان دریافت یا لغو نزد کارت نگه داشته می‌شود."), InlineKeyboardMarkup(inline_keyboard=buttons))
        await call.answer()

    @router.callback_query(F.data.startswith("gift_cfg:"))
    async def cb_pick_config(call: CallbackQuery, state: FSMContext):
        await state.update_data(kind="config", config_id=int(call.data.split(":")[1]))
        await _ask_theme(call.message, state, edit=True)
        await call.answer()

    async def _ask_theme(message: Message, state: FSMContext, edit: bool = False):
        themes = await asyncio.to_thread(db.gift_list_themes, True)
        if not themes:
            await message.answer(tr("هنوز موضوعی برای کارت هدیه تعریف نشده است."))
            await state.clear()
            return
        rows = [[InlineKeyboardButton(text=f"{t['emoji']} {t['title']}", callback_data=f"gift_th:{t['id']}")] for t in themes]
        rows.append([_btn("❌ انصراف", "gift_cancel")])
        text = tr("🎨 موضوع کارت را انتخاب کنید:")
        markup = InlineKeyboardMarkup(inline_keyboard=rows)
        if edit:
            try:
                await message.edit_text(text, reply_markup=markup)
                return
            except Exception:
                pass
        await message.answer(text, reply_markup=markup)

    @router.callback_query(F.data.startswith("gift_th:"))
    async def cb_pick_theme(call: CallbackQuery, state: FSMContext):
        theme = await asyncio.to_thread(db.gift_get_theme, int(call.data.split(":")[1]))
        if not theme or not theme["is_active"]:
            await call.answer(tr("این موضوع در دسترس نیست."), show_alert=True)
            return
        await state.update_data(theme_id=theme["id"])
        await state.set_state(GiftSend.recipient)
        await _edit(call, tr(f"👤 نام گیرنده را بنویسید (حداکثر {_MAX_NAME} حرف):"), _kb([_btn("❌ انصراف", "gift_cancel")]))
        await call.answer()

    @router.message(GiftSend.recipient)
    async def msg_recipient(message: Message, state: FSMContext):
        name = (message.text or "").strip()
        if not name or len(name) > _MAX_NAME:
            await message.answer(tr(f"⚠️ نام باید بین ۱ تا {_MAX_NAME} حرف باشد."))
            return
        await state.update_data(recipient=name)
        await state.set_state(GiftSend.message)
        await message.answer(tr(f"✍️ متن پیام کارت را بنویسید (حداکثر {_MAX_MESSAGE} حرف):"), reply_markup=_kb(
            [_btn("✨ متن را هوش مصنوعی بنویسد", "gift_nomsg")], [_btn("❌ انصراف", "gift_cancel")]))

    async def _confirm(message: Message, state: FSMContext, text_message: str):
        await state.update_data(text=text_message)
        data = await state.get_data()
        days = gc.int_setting(db, "gift_expire_days") or 30
        if data["kind"] == "wallet":
            fee = data["amount"] * gc.int_setting(db, "gift_fee_percent") // 100
            what = f"💰 مبلغ: {gc.money(data['amount'])} تومان"
            if fee:
                what += f"\nکارمزد: {gc.money(fee)} تومان\nمجموع کسر از کیف پول: {gc.money(data['amount'] + fee)} تومان"
        elif data["kind"] == "buy":
            what = "🛒 خرید یک کانفیگ جدید از فروشگاه (قیمت را در مرحله‌ی خرید می‌بینید)"
        else:
            what = "📦 کانفیگ انتخاب‌شده"
        summary = f"{what}\n👤 گیرنده: {data['recipient']}\n✍️ پیام: {text_message or 'متن تبریک متناسب با موضوع کارت را هوش مصنوعی می‌نویسد'}\n⏳ اعتبار: {str(days).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))} روز\n\nاگر تا آن زمان دریافت نشود، به شما برمی‌گردد."
        await message.answer(tr(f"🧾 پیش از ساخت کارت بررسی کنید:\n\n{summary}"), reply_markup=_kb(
            [_btn("✅ ساخت کارت", "gift_ok")], [_btn("❌ انصراف", "gift_cancel")]))

    @router.message(GiftSend.message)
    async def msg_text(message: Message, state: FSMContext):
        text = (message.text or "").strip()
        if len(text) > _MAX_MESSAGE:
            await message.answer(tr(f"⚠️ متن بیش از {_MAX_MESSAGE} حرف است؛ کوتاه‌ترش کنید."))
            return
        await _confirm(message, state, text)

    @router.callback_query(F.data == "gift_nomsg")
    async def cb_nomsg(call: CallbackQuery, state: FSMContext):
        if await state.get_state() != GiftSend.message.state:
            await call.answer()
            return
        await call.answer()
        await _confirm(call.message, state, "")

    @router.callback_query(F.data == "gift_ok")
    async def cb_create(call: CallbackQuery, state: FSMContext, bot: Bot):
        data = await state.get_data()
        await state.clear()
        if not data.get("kind") or not data.get("theme_id") or not data.get("recipient"):
            await call.answer(tr("این درخواست منقضی شده است."), show_alert=True)
            return
        if not gc.enabled(db, f"gift_allow_{data['kind']}"):
            await call.answer(tr("این نوع کارت غیرفعال است."), show_alert=True)
            return
        uid = call.from_user.id
        if data["kind"] == "buy":
            await asyncio.to_thread(
                db.gift_set_intent, uid, data["theme_id"], data["recipient"], data.get("text", ""),
                gc.int_setting(db, "gift_expire_days") or 30)
            rows = [[_btn("🛒 رفتن به فروشگاه", "gift_open_store")]]
            if db.is_full_access_bot(is_main_bot) and (
                db.get_setting("custom_config_enabled", "0") == "1" or db.count_active_custom_config_products() > 0
            ):
                rows.append([_btn("🛠 ساخت کانفیگ شخصی", "custom_config_start")])
            rows.append([_btn("🛑 لغو", "gift_intent_cancel")])
            await _edit(call, tr("✅ مشخصات کارت ذخیره شد.\nحالا از فروشگاه یک کانفیگ (تعداد ۱) بخرید؛ همان خرید (تا ۶۰ دقیقه‌ی آینده) به‌جای تحویل به شما، داخل کارت هدیه قرار می‌گیرد و کارت برایتان ارسال می‌شود.\nمحصولاتی که چند عدد خریداری شوند به کارت تبدیل نمی‌شوند."), _kb(*rows))
            await call.answer()
            return
        common = dict(theme_id=data["theme_id"], recipient_name=data["recipient"], message=data.get("text", ""),
                      expires_days=gc.int_setting(db, "gift_expire_days") or 30)
        max_open = gc.int_setting(db, "gift_max_open")
        if data["kind"] == "wallet":
            result = await asyncio.to_thread(
                db.gift_create_wallet_card, uid, data["amount"], fee_percent=gc.int_setting(db, "gift_fee_percent"),
                min_amount=gc.int_setting(db, "gift_min_amount"), max_amount=gc.int_setting(db, "gift_max_amount"),
                max_open=max_open, **common)
        else:
            result = await asyncio.to_thread(db.gift_create_config_card, uid, data["config_id"], max_open=max_open, **common)
        if not result["ok"]:
            errors = {"balance": "⛔️ موجودی کیف پول کافی نیست.", "limit": "⛔️ به سقف تعداد کارت‌های باز رسیده‌اید؛ یکی را لغو کنید یا منتظر دریافتش بمانید.",
                      "amount_range": "⚠️ مبلغ خارج از بازه‌ی مجاز است.", "config": "⚠️ این کانفیگ دیگر قابل انتقال نیست."}
            await _edit(call, tr(errors.get(result["error"], "❌ ساخت کارت ناموفق بود.")), _kb([_btn("⬅️ بازگشت", "gift_menu")]))
            await call.answer()
            return
        card = await asyncio.to_thread(db.gift_get_card, result["id"])
        image = None
        try:
            path = await gc.build_card_image(db, card, result["code"], await _bot_username(bot))
            image = gc.card_image_bytes({"image_path": path})
        except Exception:
            _log.exception("card image render failed for card %s", result["id"])
        try:
            await call.message.delete()
        except Exception:
            pass
        await _send_card(bot, uid, card, result["code"], image)
        await call.answer()

    @router.callback_query(F.data == "gift_redeem")
    async def cb_redeem_ask(call: CallbackQuery, state: FSMContext):
        await state.set_state(GiftRedeem.code)
        await _edit(call, tr("🔑 کد کارت هدیه را بفرستید (مثل GIFT-ABCD-EFGH-JKMN):"), _kb([_btn("❌ انصراف", "gift_cancel")]))
        await call.answer()

    @router.message(GiftRedeem.code)
    async def msg_redeem(message: Message, state: FSMContext):
        await state.clear()
        await redeem_gift_for_message(db, message, (message.text or "").strip())

    @router.callback_query(F.data.startswith("gift_my:"))
    async def cb_my_cards(call: CallbackQuery):
        page = int(call.data.split(":")[1])
        rows, total = await asyncio.to_thread(db.gift_list_cards, None, call.from_user.id, _PAGE, page * _PAGE)
        if not total:
            await _edit(call, tr("هنوز کارت هدیه‌ای نساخته‌اید."), _kb([_btn("⬅️ بازگشت", "gift_menu")]))
            await call.answer()
            return
        buttons = [[InlineKeyboardButton(text=_card_line(c)[:60], callback_data=f"gift_view:{c['id']}")] for c in rows]
        nav = []
        if page > 0:
            nav.append(_btn("◀️ قبلی", f"gift_my:{page - 1}"))
        if (page + 1) * _PAGE < total:
            nav.append(_btn("بعدی ▶️", f"gift_my:{page + 1}"))
        if nav:
            buttons.append(nav)
        buttons.append([_btn("⬅️ بازگشت", "gift_menu")])
        await _edit(call, tr(f"🗂 کارت‌های من ({total})"), InlineKeyboardMarkup(inline_keyboard=buttons))
        await call.answer()

    @router.callback_query(F.data.startswith("gift_view:"))
    async def cb_view_card(call: CallbackQuery, bot: Bot):
        card = await asyncio.to_thread(db.gift_get_card, int(call.data.split(":")[1]))
        if not card or card["sender_id"] != call.from_user.id:
            await call.answer(tr("کارت پیدا نشد."), show_alert=True)
            return
        open_card = card["status"] in ("active", "disabled")
        text = tr(f"{_card_line(card)}\nساخته‌شده: {_fmt_time(card['created_at'])}\nاعتبار تا: {_fmt_time(card['expires_at'])}")
        rows = []
        if open_card and card["code_plain"]:
            link = gc.deep_link(await _bot_username(bot), card["code_plain"])
            share = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(tr('🎁 یک کارت هدیه برات فرستادم!'))}"
            rows.append([_btn("📤 اشتراک‌گذاری", url=share)])
        if open_card:
            rows.append([_btn("↩️ لغو کارت و بازگشت", f"gift_cx:{card['id']}")])
        rows.append([_btn("⬅️ بازگشت", "gift_my:0")])
        image = gc.card_image_bytes(card)
        if not image and open_card and card["code_plain"]:
            try:
                await gc.build_card_image(db, card, card["code_plain"], await _bot_username(bot))
                image = gc.card_image_bytes(await asyncio.to_thread(db.gift_get_card, card["id"]))
            except Exception:
                _log.exception("lazy card render failed for card %s", card["id"])
        await call.answer()
        if image:
            await call.message.answer_photo(BufferedInputFile(image, filename="gift_card.png"), caption=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        else:
            await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("gift_cx:"))
    async def cb_cancel_ask(call: CallbackQuery):
        card_id = int(call.data.split(":")[1])
        markup = _kb([_btn("✅ بله، لغو شود", f"gift_cxok:{card_id}")], [_btn("⬅️ خیر", "gift_my:0")])
        await call.message.answer(tr("مطمئنید؟ کارت باطل می‌شود و مبلغ/کانفیگ به شما برمی‌گردد."), reply_markup=markup)
        await call.answer()

    @router.callback_query(F.data.startswith("gift_cxok:"))
    async def cb_cancel_do(call: CallbackQuery):
        result = await asyncio.to_thread(
            db.gift_release, int(call.data.split(":")[1]), "cancelled", call.from_user.id, "cancelled by sender", call.from_user.id)
        if result["ok"]:
            await _edit(call, tr("✅ کارت لغو شد و به شما برگشت."), _kb([_btn("🗂 کارت‌های من", "gift_my:0")]))
        else:
            await _edit(call, tr("این کارت دیگر قابل لغو نیست."), _kb([_btn("🗂 کارت‌های من", "gift_my:0")]))
        await call.answer()

    def _admin_menu_text(stats: dict) -> str:
        s = stats["by_status"]
        parts = [f"{gc.STATUS_LABELS[k]}: {gc.money(s.get(k, 0))}" for k in gc.STATUS_LABELS]
        return f"🎴 مدیریت کارت‌های هدیه\n\n" + "\n".join(parts) + f"\n\n💰 مبلغ نگهداری‌شده در کارت‌های باز: {gc.money(stats['held_amount'])} تومان"

    @router.callback_query(F.data == "gc_adm")
    async def cb_adm(call: CallbackQuery, state: FSMContext):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        await state.clear()
        stats = await asyncio.to_thread(db.gift_stats)
        await _edit(call, tr(_admin_menu_text(stats)), _kb(
            [_btn("🎨 موضوع‌ها و عکس پایه", "gc_adm_th")], [_btn("🗂 کارت‌ها", "gc_adm_cl:all:0")],
            [_btn("⚙️ تنظیمات", "gc_adm_set")], [_btn("📜 لاگ رویدادها", "gc_adm_log")],
            [_btn("⬅️ بازگشت", "adm_cat:marketing")]))
        await call.answer()

    @router.message(Command("giftadmin"))
    async def cmd_adm(message: Message):
        if not is_admin(message.from_user.id):
            return
        await message.answer(tr("🎴 مدیریت کارت‌های هدیه"), reply_markup=_kb([_btn("باز کردن پنل", "gc_adm")]))

    @router.callback_query(F.data == "gc_adm_th")
    async def cb_adm_themes(call: CallbackQuery, state: FSMContext):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        await state.clear()
        themes = await asyncio.to_thread(db.gift_list_themes, False)
        rows = [[InlineKeyboardButton(
            text=f"{t['emoji']} {t['title']} {'✅' if t['is_active'] else '⛔️'} {'🖼' if t['base_image_path'] else '⚪️'}",
            callback_data=f"gc_adm_th:{t['id']}")] for t in themes]
        rows.append([_btn("➕ افزودن موضوع", "gc_adm_thnew")])
        rows.append([_btn("⬅️ بازگشت", "gc_adm")])
        await _edit(call, tr("🎨 موضوع‌ها\n🖼 = عکس پایه آپلود شده | ⚪️ = بدون عکس (گرادیان پیش‌فرض)"), InlineKeyboardMarkup(inline_keyboard=rows))
        await call.answer()

    @router.callback_query(F.data == "gc_adm_thnew")
    async def cb_adm_theme_new(call: CallbackQuery, state: FSMContext):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        await state.set_state(GiftAdmin.theme_title)
        await _edit(call, tr("عنوان موضوع جدید را بفرستید. می‌توانید یک ایموجی اول عنوان بگذارید (مثلاً: 🎃 هالووین)."), _kb([_btn("❌ انصراف", "gc_adm_th")]))
        await call.answer()

    @router.message(GiftAdmin.theme_title)
    async def msg_adm_theme_title(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        raw = (message.text or "").strip()
        if not raw:
            return
        emoji, title = "🎁", raw
        first, _, rest = raw.partition(" ")
        if rest and not first.isalnum() and len(first) <= 4:
            emoji, title = first, rest.strip()
        theme_id = await asyncio.to_thread(db.gift_add_theme, title, emoji)
        await state.clear()
        await message.answer(tr("✅ موضوع ساخته شد. حالا عکس پایه‌اش را آپلود کنید."), reply_markup=_kb([_btn("🖼 آپلود عکس پایه", f"gc_adm_thimg:{theme_id}")], [_btn("⬅️ موضوع‌ها", "gc_adm_th")]))

    async def _theme_detail(call: CallbackQuery, theme_id: int):
        theme = await asyncio.to_thread(db.gift_get_theme, theme_id)
        if not theme:
            await call.answer(tr("موضوع پیدا نشد."), show_alert=True)
            return
        layout = gc.theme_layout(theme)
        source = {"ai": "تشخیص AI", "default": "پیش‌فرض"}.get(layout.get("source"), "پیش‌فرض")
        text = (f"{theme['emoji']} {theme['title']}\nوضعیت: {'فعال' if theme['is_active'] else 'غیرفعال'}\n"
                f"عکس پایه: {'دارد' if theme['base_image_path'] else 'ندارد'}\nچیدمان متن: {source}")
        toggle = "⛔️ غیرفعال کردن" if theme["is_active"] else "✅ فعال کردن"
        await _edit(call, tr(text), _kb(
            [_btn("🖼 آپلود/تعویض عکس پایه", f"gc_adm_thimg:{theme_id}")],
            [_btn("🤖 تشخیص مجدد چیدمان توسط AI", f"gc_adm_thre:{theme_id}")],
            [_btn("👁 پیش‌نمایش کارت", f"gc_adm_thpv:{theme_id}")],
            [_btn(toggle, f"gc_adm_thtog:{theme_id}"), _btn("🗑 حذف", f"gc_adm_thdel:{theme_id}")],
            [_btn("⬅️ موضوع‌ها", "gc_adm_th")]))

    @router.callback_query(F.data.regexp(r"^gc_adm_th:\d+$"))
    async def cb_adm_theme(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        await _theme_detail(call, int(call.data.split(":")[1]))
        await call.answer()

    @router.callback_query(F.data.startswith("gc_adm_thtog:"))
    async def cb_adm_theme_toggle(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        theme_id = int(call.data.split(":")[1])
        theme = await asyncio.to_thread(db.gift_get_theme, theme_id)
        if theme:
            await asyncio.to_thread(db.gift_update_theme, theme_id, is_active=0 if theme["is_active"] else 1)
        await _theme_detail(call, theme_id)
        await call.answer()

    @router.callback_query(F.data.startswith("gc_adm_thdel:"))
    async def cb_adm_theme_delete(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        theme_id = int(call.data.split(":")[1])
        theme = await asyncio.to_thread(db.gift_get_theme, theme_id)
        outcome = await asyncio.to_thread(db.gift_delete_theme, theme_id)
        if outcome == "deleted" and theme:
            gc.delete_theme_image(theme)
        notes = {"deleted": "🗑 موضوع حذف شد.", "disabled": "این موضوع کارت‌هایی دارد؛ به‌جای حذف، غیرفعال شد.", "missing": "موضوع پیدا نشد."}
        await call.answer(tr(notes[outcome]), show_alert=True)
        themes = await asyncio.to_thread(db.gift_list_themes, False)
        rows = [[InlineKeyboardButton(text=f"{t['emoji']} {t['title']}", callback_data=f"gc_adm_th:{t['id']}")] for t in themes]
        rows.append([_btn("⬅️ بازگشت", "gc_adm")])
        await _edit(call, tr("🎨 موضوع‌ها"), InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("gc_adm_thimg:"))
    async def cb_adm_theme_image_ask(call: CallbackQuery, state: FSMContext):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        await state.set_state(GiftAdmin.theme_image)
        await state.update_data(theme_id=int(call.data.split(":")[1]))
        await _edit(call, tr("🖼 عکس پایه‌ی کارت را بفرستید. برای کیفیت بهتر آن را به‌صورت «فایل» (Document) بفرستید. نسبت عمودی مثل ۴:۵ یا ۲:۳ پیشنهاد می‌شود. AI خودش جای متن‌ها را پیدا می‌کند."), _kb([_btn("❌ انصراف", "gc_adm_th")]))
        await call.answer()

    @router.message(GiftAdmin.theme_image, F.photo | F.document)
    async def msg_adm_theme_image(message: Message, state: FSMContext, bot: Bot):
        if not is_admin(message.from_user.id):
            return
        data = await state.get_data()
        theme_id = data.get("theme_id")
        file_id = message.photo[-1].file_id if message.photo else message.document.file_id
        if message.document and not (message.document.mime_type or "").startswith("image/"):
            await message.answer(tr("⚠️ فقط فایل تصویر قبول است."))
            return
        wait = await message.answer(tr("⏳ در حال ذخیره و تحلیل چیدمان توسط AI..."))
        try:
            buf = io.BytesIO()
            await bot.download(file_id, destination=buf)
            layout = await gc.save_theme_image(db, theme_id, buf.getvalue())
        except Exception:
            _log.exception("theme image save failed")
            await wait.edit_text(tr("❌ ذخیره‌ی عکس ناموفق بود؛ مطمئن شوید فایل یک تصویر معتبر است."))
            return
        await state.clear()
        sample = await _sample_card(theme_id, bot)
        source = "تشخیص AI" if layout.get("source") == "ai" else "پیش‌فرض (AI در دسترس نبود یا پاسخ معتبر نداد)"
        await wait.delete()
        await message.answer_photo(BufferedInputFile(sample, filename="preview.png"), caption=tr(f"✅ عکس پایه ذخیره شد.\nچیدمان متن: {source}"), reply_markup=_kb(
            [_btn("🤖 تشخیص مجدد چیدمان", f"gc_adm_thre:{theme_id}")], [_btn("⬅️ موضوع", f"gc_adm_th:{theme_id}")]))

    @router.message(GiftAdmin.theme_image)
    async def msg_adm_theme_image_invalid(message: Message):
        await message.answer(tr("لطفاً یک تصویر بفرستید."))

    @router.callback_query(F.data.startswith("gc_adm_thre:"))
    async def cb_adm_theme_redetect(call: CallbackQuery, bot: Bot):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        theme_id = int(call.data.split(":")[1])
        await call.answer(tr("⏳ در حال تحلیل..."))
        try:
            layout = await gc.redetect_theme_layout(db, theme_id)
        except Exception:
            await call.message.answer(tr("⚠️ ابتدا عکس پایه آپلود کنید."))
            return
        sample = await _sample_card(theme_id, bot)
        source = "تشخیص AI" if layout.get("source") == "ai" else "پیش‌فرض"
        await call.message.answer_photo(BufferedInputFile(sample, filename="preview.png"), caption=tr(f"چیدمان متن: {source}"), reply_markup=_kb([_btn("⬅️ موضوع", f"gc_adm_th:{theme_id}")]))

    @router.callback_query(F.data.startswith("gc_adm_thpv:"))
    async def cb_adm_theme_preview(call: CallbackQuery, bot: Bot):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        theme_id = int(call.data.split(":")[1])
        sample = await _sample_card(theme_id, bot)
        await call.answer()
        await call.message.answer_photo(BufferedInputFile(sample, filename="preview.png"), reply_markup=_kb([_btn("⬅️ موضوع", f"gc_adm_th:{theme_id}")]))

    @router.callback_query(F.data.startswith("gc_adm_cl:"))
    async def cb_adm_cards(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        _, status, page_s = call.data.split(":")
        page = int(page_s)
        rows, total = await asyncio.to_thread(db.gift_list_cards, None if status == "all" else status, None, _PAGE, page * _PAGE)
        buttons = [[InlineKeyboardButton(text=_card_line(c)[:60], callback_data=f"gc_adm_cv:{c['id']}")] for c in rows]
        filters = [("all", "همه"), ("active", "فعال"), ("disabled", "غیرفعال"), ("redeemed", "دریافت‌شده")]
        buttons.append([InlineKeyboardButton(text=("• " if status == k else "") + tr(label), callback_data=f"gc_adm_cl:{k}:0") for k, label in filters])
        buttons.append([InlineKeyboardButton(text=("• " if status == k else "") + tr(label), callback_data=f"gc_adm_cl:{k}:0") for k, label in (("expired", "منقضی"), ("revoked", "باطل"), ("cancelled", "لغو"))])
        nav = []
        if page > 0:
            nav.append(_btn("◀️", f"gc_adm_cl:{status}:{page - 1}"))
        if (page + 1) * _PAGE < total:
            nav.append(_btn("▶️", f"gc_adm_cl:{status}:{page + 1}"))
        if nav:
            buttons.append(nav)
        buttons.append([_btn("⬅️ بازگشت", "gc_adm")])
        await _edit(call, tr(f"🗂 کارت‌ها ({total})"), InlineKeyboardMarkup(inline_keyboard=buttons))
        await call.answer()

    async def _card_detail(call: CallbackQuery, card_id: int):
        card = await asyncio.to_thread(db.gift_get_card, card_id)
        if not card:
            await call.answer(tr("کارت پیدا نشد."), show_alert=True)
            return
        lines = [
            _card_line(card), f"فرستنده: {card['sender_id']}", f"کد (۴ رقم آخر): {card['code_hint']}",
            f"ساخت: {_fmt_time(card['created_at'])}", f"انقضا: {_fmt_time(card['expires_at'])}",
        ]
        if card["kind"] == "wallet":
            lines.append(f"مبلغ: {gc.money(card['amount'])} | کارمزد: {gc.money(card['fee'] or 0)}")
        if card["message"]:
            lines.append(f"پیام: {card['message']}")
        if card["redeemed_by"]:
            lines.append(f"دریافت‌کننده: {card['redeemed_by']} ({_fmt_time(card['redeemed_at'])})")
        if card["closed_at"]:
            lines.append(f"بسته‌شده: {_fmt_time(card['closed_at'])} توسط {card['closed_by'] or 'سیستم'}")
        rows = []
        if card["status"] == "active":
            rows.append([_btn("⏸ غیرفعال کردن", f"gc_adm_en:{card_id}:0")])
        elif card["status"] == "disabled":
            rows.append([_btn("▶️ فعال کردن", f"gc_adm_en:{card_id}:1")])
        if card["status"] in ("active", "disabled"):
            rows.append([_btn("🚫 ابطال و بازگشت به فرستنده", f"gc_adm_rv:{card_id}")])
        rows.append([_btn("🖼 تصویر کارت", f"gc_adm_img:{card_id}"), _btn("📜 لاگ", f"gc_adm_clog:{card_id}")])
        rows.append([_btn("⬅️ کارت‌ها", "gc_adm_cl:all:0")])
        await _edit(call, tr("\n".join(lines)), InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("gc_adm_cv:"))
    async def cb_adm_card(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        await _card_detail(call, int(call.data.split(":")[1]))
        await call.answer()

    @router.callback_query(F.data.startswith("gc_adm_en:"))
    async def cb_adm_enable(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        _, card_id, flag = call.data.split(":")
        await asyncio.to_thread(db.gift_set_enabled, int(card_id), flag == "1", call.from_user.id)
        await _card_detail(call, int(card_id))
        await call.answer()

    @router.callback_query(F.data.startswith("gc_adm_rv:"))
    async def cb_adm_revoke_ask(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        card_id = int(call.data.split(":")[1])
        await _edit(call, tr("🚫 کارت باطل شود؟ مبلغ (و کارمزد) یا کانفیگ به فرستنده برمی‌گردد و این کار برگشت‌پذیر نیست."), _kb(
            [_btn("✅ بله، باطل شود", f"gc_adm_rvok:{card_id}")], [_btn("⬅️ خیر", f"gc_adm_cv:{card_id}")]))
        await call.answer()

    @router.callback_query(F.data.startswith("gc_adm_rvok:"))
    async def cb_adm_revoke_do(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        card_id = int(call.data.split(":")[1])
        result = await asyncio.to_thread(db.gift_release, card_id, "revoked", call.from_user.id, "revoked by admin")
        if not result["ok"]:
            await call.answer(tr("ابطال ممکن نشد (وضعیت کارت تغییر کرده است)."), show_alert=True)
        else:
            card = result["card"]
            what = f"{gc.money(card['amount'] + (card['fee'] or 0))} تومان" if card["kind"] == "wallet" else "کانفیگ"
            try:
                await call.bot.send_message(card["sender_id"], tr(f"🚫 کارت هدیه‌ی شما (به مقصد «{card['recipient_name'] or '-'}») توسط مدیریت باطل شد و {what} به شما برگشت."))
            except Exception:
                _log.info("revoke notify failed for card %s", card_id)
            await call.answer(tr("✅ باطل شد و به فرستنده برگشت."), show_alert=True)
        await _card_detail(call, card_id)

    @router.callback_query(F.data.startswith("gc_adm_img:"))
    async def cb_adm_card_image(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        card = await asyncio.to_thread(db.gift_get_card, int(call.data.split(":")[1]))
        image = gc.card_image_bytes(card) if card else None
        if not image:
            await call.answer(tr("تصویری برای این کارت ذخیره نشده."), show_alert=True)
            return
        await call.answer()
        await call.message.answer_photo(BufferedInputFile(image, filename="gift_card.png"))

    def _log_text(rows) -> str:
        if not rows:
            return "رویدادی ثبت نشده."
        return "\n".join(f"{_fmt_time(r['created_at'])} | کارت {r['card_id'] or '-'} | {r['event']} | {r['actor_id'] or 'سیستم'}" for r in rows)

    @router.callback_query(F.data.startswith("gc_adm_clog:"))
    async def cb_adm_card_log(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        card_id = int(call.data.split(":")[1])
        rows = await asyncio.to_thread(db.gift_list_log, card_id, 25)
        await _edit(call, tr("📜 لاگ کارت\n\n" + _log_text(rows)), _kb([_btn("⬅️ کارت", f"gc_adm_cv:{card_id}")]))
        await call.answer()

    @router.callback_query(F.data == "gc_adm_log")
    async def cb_adm_log(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        rows = await asyncio.to_thread(db.gift_list_log, None, 25)
        await _edit(call, tr("📜 آخرین رویدادها\n\n" + _log_text(rows)), _kb([_btn("⬅️ بازگشت", "gc_adm")]))
        await call.answer()

    async def _settings_screen(call: CallbackQuery):
        rows = []
        for key, label in gc.SETTING_LABELS.items():
            value = gc.setting(db, key)
            if key in gc.BOOL_SETTINGS:
                rows.append([InlineKeyboardButton(text=f"{'✅' if value == '1' else '⛔️'} {tr(label)}", callback_data=f"gc_adm_tg:{key}")])
            else:
                rows.append([InlineKeyboardButton(text=f"✏️ {tr(label)}: {value}", callback_data=f"gc_adm_ed:{key}")])
        rows.append([_btn("⬅️ بازگشت", "gc_adm")])
        await _edit(call, tr("⚙️ تنظیمات کارت هدیه"), InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data == "gc_adm_set")
    async def cb_adm_settings(call: CallbackQuery, state: FSMContext):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        await state.clear()
        await _settings_screen(call)
        await call.answer()

    @router.callback_query(F.data.startswith("gc_adm_tg:"))
    async def cb_adm_toggle(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        key = call.data.split(":")[1]
        if key in gc.BOOL_SETTINGS:
            await asyncio.to_thread(db.set_setting, key, "0" if gc.setting(db, key) == "1" else "1")
        await _settings_screen(call)
        await call.answer()

    @router.callback_query(F.data.startswith("gc_adm_ed:"))
    async def cb_adm_edit(call: CallbackQuery, state: FSMContext):
        if not is_admin(call.from_user.id):
            await call.answer()
            return
        key = call.data.split(":")[1]
        if key not in gc.SETTING_LABELS or key in gc.BOOL_SETTINGS:
            await call.answer()
            return
        await state.set_state(GiftAdmin.setting_value)
        await state.update_data(key=key)
        await _edit(call, tr(f"مقدار جدید «{gc.SETTING_LABELS[key]}» را به‌صورت عدد بفرستید:"), _kb([_btn("❌ انصراف", "gc_adm_set")]))
        await call.answer()

    @router.message(GiftAdmin.setting_value)
    async def msg_adm_value(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        raw = (message.text or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٬،", "0123456789,,")).replace(",", "")
        data = await state.get_data()
        key = data.get("key")
        limit = 100 if key == "gift_fee_percent" else 10**9
        if not raw.isdigit() or int(raw) > limit or (key == "gift_expire_days" and int(raw) < 1):
            await message.answer(tr("⚠️ یک عدد صحیح معتبر بفرستید."))
            return
        await asyncio.to_thread(db.set_setting, key, raw)
        await state.clear()
        await message.answer(tr("✅ ذخیره شد."), reply_markup=_kb([_btn("⚙️ تنظیمات", "gc_adm_set")]))

    return router
