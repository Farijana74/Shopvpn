# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""هندلرهای صندوق شانس و پیشرفت: صفحه‌ی کاربر (خرید، باز کردن، پیشرفت) و پنل مدیریت ادمین."""

import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import engagement as eg
from i18n import tr
from states import BuyFlow

_log = logging.getLogger("engagement_handlers")
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫", "01234567890123456789.")
_TOGGLES = {"lootbox_enabled": "0", "streak_enabled": "0"}
_KIND_LABELS = {"wallet": "💰 شارژ کیف پول", "discount": "🎟 کد تخفیف", "product": "🆓 سرویس رایگان"}
_BACK_WALLET = "coins_back"
_BACK_ACCOUNT = "acct:hub"
_MAX_BOX_QTY = 10


class EngageAdmin(StatesGroup):
    value = State()
    prize_value = State()
    prize_chance = State()
    grant_user = State()
    grant_count = State()
    ms_threshold = State()


def _btn(text, data):
    return InlineKeyboardButton(text=tr(text), callback_data=data)


def _kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows])


async def _edit(call: CallbackQuery, text: str, markup=None):
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:
        await call.message.answer(text, reply_markup=markup)


def _num(text):
    cleaned = (text or "").translate(_DIGITS).replace(",", "").replace("٬", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _int(text):
    value = _num(text)
    return int(value) if value is not None and value == int(value) and value >= 0 else None


def _on(value: bool) -> str:
    return "✅ روشن" if value else "❌ خاموش"


async def lootbox_user_view(db, user_id: int):
    if not eg.lootbox_enabled(db):
        return None
    balance = await asyncio.to_thread(db.get_wallet_credit, user_id)
    inventory = await asyncio.to_thread(db.box_list_user, user_id)
    types = [t for t in await asyncio.to_thread(db.box_type_list, True) if t["price"] > 0]
    lines = ["🎁 صندوق شانس", "", f"👛 موجودی کیف پول: {eg.money(balance)} تومان", ""]
    rows = []
    if inventory:
        lines.append("صندوق‌های بسته‌ی تو (هر وقت خواستی باز کن):")
        for item in inventory:
            lines.append(f"{item['emoji'] or '📦'} {html.escape(item['type_title'] or '')} × {item['n']}")
            rows.append([_btn(f"🔓 باز کردن {item['emoji'] or '📦'} {item['type_title']} ({item['n']})", f"lb_open:{item['first_id']}")])
        lines.append("")
    else:
        lines += ["صندوق بسته‌ای نداری.", ""]
    if types:
        lines.append("🛒 خرید صندوق:")
        for box_type in types:
            lines.append(f"{box_type['emoji']} {html.escape(box_type['title'])} — {eg.money(box_type['price'])} تومان")
            rows.append([_btn(f"🛒 {box_type['emoji']} {box_type['title']} ({eg.money(box_type['price'])} تومان)", f"lb_buy:{box_type['id']}")])
    rows.append([_btn("📜 تاریخچه‌ی بازشدن‌ها", "lb_hist")])
    rows.append([_btn("⬅️ بازگشت", _BACK_ACCOUNT)])
    return tr("\n".join(lines)), _kb(*rows)


async def notify_lootbox_delivered(bot, db, user_id: int, type_id: int, box_ids) -> None:
    """بعد از پرداخت موفق (هر روشی) صندوق‌های خریداری‌شده را به کاربر اطلاع می‌دهد و دکمه‌ی باز کردن می‌دهد."""
    box_type = await asyncio.to_thread(db.box_type_get, type_id)
    label = f"{box_type['emoji'] or '📦'} {box_type['title']}" if box_type else "📦 صندوق شانس"
    count = len(box_ids)
    text = f"✅ پرداخت انجام شد!\n{label}" + (f" × {count}" if count > 1 else "") + " به صندوق‌های تو اضافه شد.\nهر وقت خواستی بازش کن 👇"
    await bot.send_message(
        user_id, tr(text),
        reply_markup=_kb([_btn("🔓 باز کردن", f"lb_open:{box_ids[0]}")], [_btn("🎁 صندوق شانس", "lb_menu")]),
    )


async def _pm_summary(db, allowed) -> str:
    if allowed is None:
        return "همه‌ی روش‌های پرداخت"
    catalog = {item["key"]: item["label"] for item in await asyncio.to_thread(db.get_payment_methods_catalog)}
    return "، ".join(catalog.get(key, key) for key in allowed) or "---"


async def lootbox_confirm_view(db, user_id: int, type_id: int, quantity: int):
    """صفحه‌ی تایید خرید صندوق (مثل صفحه‌ی تایید خرید محصول). خروجی: (text, markup) یا رشته‌ی خطا."""
    if not eg.lootbox_enabled(db):
        return "صندوق شانس در حال حاضر فعال نیست."
    box_type = await asyncio.to_thread(db.box_type_get, type_id)
    if not box_type or not box_type["is_active"] or box_type["price"] <= 0:
        return "این صندوق در دسترس نیست."
    max_qty = _MAX_BOX_QTY
    limit = eg.get_int(db, "lootbox_daily_limit")
    if limit:
        max_qty = min(max_qty, max(limit - await asyncio.to_thread(db.box_bought_today, user_id), 0))
        if max_qty <= 0:
            return "به سقف خرید روزانه‌ی صندوق رسیده‌ای؛ فردا دوباره امتحان کن."
    quantity = max(1, min(int(quantity), max_qty))
    balance = await asyncio.to_thread(db.get_wallet_credit, user_id)
    allowed = await asyncio.to_thread(db.box_type_payment_methods, type_id)
    odds = await asyncio.to_thread(eg.odds_lines, db, box_type["id"])
    total = int(box_type["price"]) * quantity
    lines = [
        f"{box_type['emoji']} {html.escape(box_type['title'])}",
        f"💵 قیمت هر صندوق: {eg.money(box_type['price'])} تومان",
        f"🔢 تعداد: {quantity}",
        f"💰 مبلغ کل: {eg.money(total)} تومان",
        f"👛 موجودی کیف پول: {eg.money(balance)} تومان",
    ]
    if allowed is not None:
        lines.append(f"💳 روش‌های پرداخت: {await _pm_summary(db, allowed)}")
    lines += ["", "🎲 شانس جوایز:", "\n".join(odds) if odds else "هنوز جایزه‌ای تعریف نشده."]
    qty_row = []
    if quantity > 1:
        qty_row.append(_btn("➖", f"lb_q:{type_id}:{quantity - 1}"))
    qty_row.append(_btn(f"🔢 تعداد: {quantity}", "noop"))
    if quantity < max_qty:
        qty_row.append(_btn("➕", f"lb_q:{type_id}:{quantity + 1}"))
    markup = _kb(qty_row, [_btn("✅ ادامه و انتخاب پرداخت", f"lb_pay:{type_id}:{quantity}")], [_btn("⬅️ بازگشت", "lb_menu")])
    return tr("\n".join(lines)), markup


async def show_lootbox_for_message(db, message: Message):
    view = await lootbox_user_view(db, message.from_user.id)
    if view is None:
        await message.answer(tr("صندوق شانس در حال حاضر فعال نیست."))
        return
    await message.answer(view[0], reply_markup=view[1])


async def progress_user_view(db, user_id: int):
    if not eg.streak_enabled(db):
        return None
    await asyncio.to_thread(db.streak_touch, user_id, *eg._tehran_days())
    text = await asyncio.to_thread(eg.progress_text, db, user_id)
    return tr(text), _kb([_btn("🏅 نشان‌های من", "prog_badges")], [_btn("⬅️ بازگشت", _BACK_WALLET)])


async def show_progress_for_message(db, message: Message):
    view = await progress_user_view(db, message.from_user.id)
    if view is None:
        await message.answer(tr("بخش پیشرفت در حال حاضر فعال نیست."))
        return
    await message.answer(view[0], reply_markup=view[1])


def create_engagement_router(db, is_main_bot: bool = True) -> Router:
    router = Router()

    def is_admin(user_id: int) -> bool:
        return db.is_full_admin(user_id)

    # ------------------------------------------------------------------
    # کاربر
    # ------------------------------------------------------------------

    @router.callback_query(F.data == "lb_menu")
    async def cb_lb_menu(call: CallbackQuery, state: FSMContext):
        await state.clear()
        view = await lootbox_user_view(db, call.from_user.id)
        if view is None:
            await call.answer(tr("صندوق شانس در حال حاضر فعال نیست."), show_alert=True)
            return
        await _edit(call, view[0], view[1])
        await call.answer()

    @router.callback_query(F.data == "lb_hist")
    async def cb_lb_hist(call: CallbackQuery):
        rows = await asyncio.to_thread(db.box_history, call.from_user.id)
        lines = ["📜 آخرین صندوق‌های بازشده", ""]
        if not rows:
            lines.append("هنوز صندوقی باز نکرده‌ای.")
        for row in rows:
            lines.append(f"{row['emoji'] or '📦'} {html.escape(row['type_title'] or '')}: {html.escape(row['prize_title'] or 'پوچ')}")
        await _edit(call, tr("\n".join(lines)), _kb([_btn("⬅️ بازگشت", "lb_menu")]))
        await call.answer()

    @router.callback_query(F.data.startswith("lb_buy:") | F.data.startswith("lb_q:"))
    async def cb_lb_buy(call: CallbackQuery, state: FSMContext):
        parts = call.data.split(":")
        type_id = int(parts[1])
        quantity = int(parts[2]) if len(parts) > 2 else 1
        view = await lootbox_confirm_view(db, call.from_user.id, type_id, quantity)
        if isinstance(view, str):
            await call.answer(tr(view), show_alert=True)
            return
        await _edit(call, view[0], view[1])
        await call.answer()

    async def _start_purchase(call: CallbackQuery, state: FSMContext, bot: Bot, type_id: int, quantity: int):
        """خرید صندوق دقیقاً مثل خرید یک محصول: سفارش ساخته می‌شود و کاربر با هر روش پرداخت
        مجاز (کیف پول/کارت/کریپتو/درگاه‌ها) پرداخت می‌کند؛ بعد از تایید پرداخت صندوق تحویل می‌شود."""
        if not eg.lootbox_enabled(db):
            await call.answer(tr("صندوق شانس در حال حاضر فعال نیست."), show_alert=True)
            return
        box_type = await asyncio.to_thread(db.box_type_get, type_id)
        if not box_type or not box_type["is_active"] or box_type["price"] <= 0:
            await call.answer(tr("این صندوق در دسترس نیست."), show_alert=True)
            return
        if not await asyncio.to_thread(db.box_prize_list, type_id):
            await call.answer(tr("این صندوق هنوز جایزه ندارد."), show_alert=True)
            return
        user_id = call.from_user.id
        quantity = max(1, min(int(quantity), _MAX_BOX_QTY))
        limit = eg.get_int(db, "lootbox_daily_limit")
        if limit and (await asyncio.to_thread(db.box_bought_today, user_id)) + quantity > limit:
            await call.answer(tr("به سقف خرید روزانه‌ی صندوق رسیده‌ای؛ فردا دوباره امتحان کن."), show_alert=True)
            return

        total = int(box_type["price"]) * quantity
        allowed = await asyncio.to_thread(db.box_type_payment_methods, type_id)
        wallet_allowed = allowed is None or "wallet" in allowed
        plan = await asyncio.to_thread(db.plan_wallet_spend, user_id, total, wallet_allowed)
        if plan["blocked"]:
            await call.message.answer(tr(plan["message"]))
            await call.answer()
            return
        wallet_used = plan["wallet_used"]
        if wallet_used > 0:
            wallet_used = await asyncio.to_thread(db.deduct_wallet_credit, user_id, wallet_used)

        order_id = await asyncio.to_thread(db.create_lootbox_order, user_id, type_id, quantity, total, wallet_used)
        order = await asyncio.to_thread(db.get_order, order_id)
        label = f"{box_type['emoji']} {box_type['title']}" + (f" × {quantity}" if quantity > 1 else "")
        try:
            if order["final_price"] <= 0:
                # کل مبلغ از کیف پول پوشش داده شده: همان لحظه تحویل بده
                await state.clear()
                box_ids = await asyncio.to_thread(db.approve_lootbox_order, order_id)
                if not box_ids:
                    await asyncio.to_thread(db.reject_order, order_id)
                    await _edit(call, tr("⛔️ خرید انجام نشد. مبلغ کسرشده از کیف پول شما بازگردانده شد."))
                    await call.answer()
                    return
                await _edit(call, tr("✅ مبلغ سفارش از کیف پول پوشش داده شد."))
                await notify_lootbox_delivered(bot, db, user_id, type_id, box_ids)
                await call.answer()
                return

            remaining = order["final_price"]
            if not await asyncio.to_thread(db.has_any_payable_method, remaining, allowed):
                await asyncio.to_thread(db.reject_order, order_id)
                await state.clear()
                msg = "⛔️ در حال حاضر هیچ روش پرداخت فعالی برای این مبلغ در دسترس نیست."
                if allowed == ["wallet"]:
                    msg = "⛔️ این صندوق فقط با کیف پول قابل خرید است و موجودی کیف پول شما کافی نیست."
                if wallet_used > 0:
                    msg += "\nمبلغ کسرشده از کیف پول شما بازگردانده شد."
                await _edit(call, tr(msg))
                await call.answer()
                return

            import abangateway_payment
            import blupal_payment
            import crypto_payment
            import custom_gateway_payment
            import extra_gateway_payment
            import keyboards as kb
            import noapay_payment

            await state.set_state(BuyFlow.waiting_receipt)
            await state.update_data(order_id=order_id)
            text = f"🎁 {label}\n💰 قیمت: {eg.money(total)} تومان\n"
            if wallet_used:
                text += f"👛 استفاده از کیف پول: {eg.money(wallet_used)} تومان\n"
            text += f"💰 مبلغ نهایی قابل پرداخت: {eg.money(order['final_price'])} تومان\n\nلطفاً روش پرداخت را انتخاب کنید:"
            markup = kb.payment_choice_kb(
                crypto_payment.crypto_payment_available(db),
                abangateway_payment.abangateway_payment_available(db),
                custom_gateway_payment.list_enabled_gateways(db),
                (await asyncio.to_thread(db.get_setting, "card_to_card_enabled", "1")) == "1",
                card_auto_enabled=(await asyncio.to_thread(db.get_setting, "card_to_card_auto_enabled", "0")) == "1",
                amount=remaining,
                db=db,
                allowed_methods=allowed,
                noapay_enabled=noapay_payment.noapay_payment_available(db),
                blupal_enabled=blupal_payment.blupal_payment_available(db),
                extra_gateways=extra_gateway_payment.available_keys(db, is_main_bot),
                user_id=user_id,
            )
            await _edit(call, tr(text), markup)
            await call.answer()
        except Exception:
            _log.exception("خطای غیرمنتظره در خرید صندوق شانس (سفارش #%s) برای کاربر %s", order_id, user_id)
            await asyncio.to_thread(db.reject_order, order_id)
            await state.clear()
            await _edit(call, tr(
                "⛔️ خطای غیرمنتظره‌ای در پردازش خرید رخ داد.\n"
                "اگر مبلغی از کیف پول شما کسر شده بود، به‌طور کامل بازگردانده شد. لطفاً دوباره تلاش کنید."
            ))
            try:
                await call.answer()
            except Exception:
                pass

    @router.callback_query(F.data.startswith("lb_pay:"))
    async def cb_lb_pay(call: CallbackQuery, state: FSMContext, bot: Bot):
        _p, type_id, quantity = call.data.split(":")
        await _start_purchase(call, state, bot, int(type_id), int(quantity))

    @router.callback_query(F.data.startswith("lb_buyok:"))
    async def cb_lb_buy_ok(call: CallbackQuery, state: FSMContext, bot: Bot):
        # دکمه‌ی قدیمیِ پیام‌های قبلی: به همان مسیر جدید (سفارش + انتخاب روش پرداخت) می‌رود.
        await _start_purchase(call, state, bot, int(call.data.split(":")[1]), 1)

    @router.callback_query(F.data.startswith("lb_open:"))
    async def cb_lb_open(call: CallbackQuery):
        if not eg.lootbox_enabled(db):
            await call.answer(tr("صندوق شانس در حال حاضر فعال نیست."), show_alert=True)
            return
        valid_days = eg.get_int(db, "lootbox_code_valid_days")
        result = await asyncio.to_thread(db.box_open, int(call.data.split(":")[1]), call.from_user.id, valid_days)
        if not result["ok"]:
            await call.answer(tr("این صندوق پیدا نشد یا قبلاً باز شده است."), show_alert=True)
            return
        await call.answer()
        frame_message = await eg.animate(call.message)
        text = tr(eg.result_text(result, valid_days))
        markup = _kb([_btn("📦 صندوق‌های من", "lb_menu")])
        try:
            await frame_message.edit_text(text, reply_markup=markup)
        except Exception:
            await call.message.answer(text, reply_markup=markup)

    @router.callback_query(F.data == "prog_view")
    async def cb_prog_view(call: CallbackQuery, state: FSMContext):
        await state.clear()
        view = await progress_user_view(db, call.from_user.id)
        if view is None:
            await call.answer(tr("بخش پیشرفت در حال حاضر فعال نیست."), show_alert=True)
            return
        await _edit(call, view[0], view[1])
        await call.answer()

    @router.callback_query(F.data == "prog_badges")
    async def cb_prog_badges(call: CallbackQuery):
        badges = await asyncio.to_thread(db.badge_list, call.from_user.id)
        lines = ["🏅 نشان‌های تو", ""]
        lines += [f"{b['emoji'] or '🎖'} {html.escape(b['title'])}" for b in badges] or ["هنوز نشانی نگرفته‌ای."]
        await _edit(call, tr("\n".join(lines)), _kb([_btn("⬅️ بازگشت", "prog_view")]))
        await call.answer()

    if not is_main_bot:
        return router

    # ------------------------------------------------------------------
    # ادمین: نماها
    # ------------------------------------------------------------------

    async def _guard(call: CallbackQuery) -> bool:
        if not is_admin(call.from_user.id):
            await call.answer()
            return False
        return True

    async def _main_view():
        return (
            "🎁 صندوق‌ها و پیشرفت\n\nصندوق شانس با جایزه‌ی تصادفی، و استریک و نقاط عطف پیشرفت کاربران.",
            _kb([_btn("📦 صندوق شانس", "lb_adm")], [_btn("📈 پیشرفت و استریک", "st_adm")], [_btn("⬅️ بازگشت", "adm_cat:marketing")]),
        )

    async def _lb_view():
        stats = await asyncio.to_thread(db.box_stats)
        types = await asyncio.to_thread(db.box_type_list)
        lines = [
            "📦 مدیریت صندوق شانس", "",
            f"وضعیت: {_on(eg.lootbox_enabled(db))}",
            f"سقف خرید روزانه: {eg.get_int(db, 'lootbox_daily_limit')} | اعتبار کد جایزه: {eg.get_int(db, 'lootbox_code_valid_days')} روز",
            f"بسته: {stats['sealed']} | بازشده: {stats['opened']} | درآمد فروش: {eg.money(stats['revenue'])} تومان | جایزه‌ی نقدی پرداختی: {eg.money(stats['wallet_paid'])} تومان",
            "", "انواع صندوق:" if types else "هنوز نوع صندوقی تعریف نشده است.",
        ]
        rows = [
            [_btn(("⏸ خاموش کردن" if eg.lootbox_enabled(db) else "▶️ روشن کردن"), "eng_tg:lootbox_enabled")],
            [_btn("✏️ سقف خرید روزانه", "eng_set:lootbox_daily_limit"), _btn("⏱ اعتبار کد (روز)", "eng_set:lootbox_code_valid_days")],
        ]
        for box_type in types:
            state_icon = "🟢" if box_type["is_active"] else "🔴"
            rows.append([_btn(f"{state_icon} {box_type['emoji']} {box_type['title']} | {eg.money(box_type['price'])}", f"lb_t:{box_type['id']}")])
        rows += [[_btn("➕ افزودن نوع صندوق", "lb_add")], [_btn("⬅️ بازگشت", "eng_adm")]]
        return "\n".join(lines), _kb(*rows)

    async def _lb_type_view(type_id: int):
        box_type = await asyncio.to_thread(db.box_type_get, type_id)
        if not box_type:
            return await _lb_view()
        prizes = await asyncio.to_thread(db.box_prize_list, type_id)
        total = sum(float(p["chance"]) for p in prizes)
        expected = sum(float(p["chance"]) / 100 * p["value"] for p in prizes if p["kind"] == "wallet")
        lines = [
            f"{box_type['emoji']} {html.escape(box_type['title'])} (#{box_type['id']})",
            f"وضعیت: {_on(bool(box_type['is_active']))} | قیمت: {eg.money(box_type['price'])} تومان (۰ = فقط اهدایی)",
            f"💳 روش‌های پرداخت: {await _pm_summary(db, await asyncio.to_thread(db.box_type_payment_methods, type_id))}",
            "", "جوایز:" if prizes else "هنوز جایزه‌ای تعریف نشده؛ بدون جایزه این صندوق قابل خرید نیست.",
        ]
        lines += [f"• {eg.prize_summary(p)} — {p['chance']:g}٪" for p in prizes]
        lines.append(f"\nمجموع شانس: {total:g}٪ | پوچ: {round(100 - total, 4):g}٪")
        if expected:
            lines.append(f"میانگین جایزه‌ی نقدی هر باز کردن: {eg.money(round(expected))} تومان")
        rows = [
            [_btn(("🔴 غیرفعال کردن" if box_type["is_active"] else "🟢 فعال کردن"), f"lb_tgt:{type_id}"), _btn("✏️ قیمت", f"lb_price:{type_id}")],
            [_btn("💳 روش‌های پرداخت", f"lb_pm:{type_id}")],
            [_btn("➕ افزودن جایزه", f"lb_pa:{type_id}"), _btn("🎁 اهدا به کاربر", f"lb_g:{type_id}")],
        ]
        rows += [[_btn(f"🗑 {eg.prize_summary(p)[:30]} ({p['chance']:g}٪)", f"lb_pd:{p['id']}:{type_id}")] for p in prizes]
        rows.append([_btn("⬅️ بازگشت", "lb_adm")])
        return "\n".join(lines), _kb(*rows)

    async def _st_view():
        milestones = await asyncio.to_thread(db.milestone_list)
        lines = [
            "📈 مدیریت پیشرفت و استریک", "",
            f"وضعیت: {_on(eg.streak_enabled(db))}",
            f"اعتبار کد تخفیف نقاط عطف: {eg.get_int(db, 'streak_code_valid_days')} روز", "",
            "هر نقطه‌ی عطف نشان می‌دهد؛ جایزه‌ی اضافه (کیف پول، کد تخفیف یا صندوق) را از داخل آن تنظیم کن.",
        ]
        rows = [
            [_btn(("⏸ خاموش کردن" if eg.streak_enabled(db) else "▶️ روشن کردن"), "eng_tg:streak_enabled")],
            [_btn("⏱ اعتبار کد (روز)", "eng_set:streak_code_valid_days")],
        ]
        for milestone in milestones:
            icon = "🟢" if milestone["is_active"] else "🔴"
            reward = eg.reward_text(milestone["wallet"], milestone["discount"], "صندوق" if milestone["box_type_id"] else None)
            rows.append([_btn(f"{icon} {milestone['emoji']} {milestone['title']}" + (f" | {reward}" if reward else ""), f"st_m:{milestone['id']}")])
        rows.append([_btn(f"➕ {emoji} {label}", f"st_add:{track}") for track, (emoji, label, _t) in eg.TRACKS.items()][:3])
        rows.append([_btn("⬅️ بازگشت", "eng_adm")])
        return "\n".join(lines), _kb(*rows)

    async def _ms_view(milestone_id: int):
        milestone = await asyncio.to_thread(db.milestone_get, milestone_id)
        if not milestone:
            return await _st_view()
        box_name = "ندارد"
        if milestone["box_type_id"]:
            box_type = await asyncio.to_thread(db.box_type_get, milestone["box_type_id"])
            box_name = html.escape(box_type["title"]) if box_type else f"#{milestone['box_type_id']} (نامعتبر)"
        track = eg.TRACKS[milestone["track"]]
        lines = [
            f"{milestone['emoji']} {html.escape(milestone['title'])} (#{milestone['id']})",
            f"شرط: {milestone['threshold']} {track[1]} | وضعیت: {_on(bool(milestone['is_active']))}", "",
            f"💰 جایزه‌ی کیف پول: {eg.money(milestone['wallet'])} تومان",
            f"🎟 کد تخفیف: {milestone['discount']}٪", f"📦 صندوق جایزه: {box_name}",
        ]
        return "\n".join(lines), _kb(
            [_btn("💰 کیف پول", f"st_f:{milestone_id}:wallet"), _btn("🎟 تخفیف", f"st_f:{milestone_id}:discount")],
            [_btn("📦 شناسه‌ی نوع صندوق", f"st_f:{milestone_id}:box_type_id")],
            [_btn(("🔴 غیرفعال" if milestone["is_active"] else "🟢 فعال"), f"st_tgm:{milestone_id}"), _btn("🗑 حذف", f"st_del:{milestone_id}")],
            [_btn("⬅️ بازگشت", "st_adm")],
        )

    async def _view_for(back: str):
        if back == "lb_adm":
            return await _lb_view()
        if back.startswith("lb_t:"):
            return await _lb_type_view(int(back.split(":")[1]))
        if back.startswith("st_m:"):
            return await _ms_view(int(back.split(":")[1]))
        return await _st_view()

    async def _show(target, back: str):
        text, markup = await _view_for(back)
        if isinstance(target, CallbackQuery):
            await _edit(target, tr(text), markup)
        else:
            await target.answer(tr(text), reply_markup=markup)

    # ------------------------------------------------------------------
    # ادمین: ناوبری و کلیدها
    # ------------------------------------------------------------------

    @router.callback_query(F.data == "eng_adm")
    async def cb_eng_main(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.clear()
        text, markup = await _main_view()
        await _edit(call, tr(text), markup)
        await call.answer()

    @router.callback_query(F.data.in_({"lb_adm", "st_adm"}) | F.data.startswith("lb_t:") | F.data.startswith("st_m:"))
    async def cb_nav(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.clear()
        await _show(call, call.data)
        await call.answer()

    @router.callback_query(F.data.startswith("eng_tg:"))
    async def cb_toggle(call: CallbackQuery):
        if not await _guard(call):
            return
        key = call.data.split(":", 1)[1]
        if key not in _TOGGLES:
            await call.answer()
            return
        current = await asyncio.to_thread(db.get_setting, key, _TOGGLES[key])
        await asyncio.to_thread(db.set_setting, key, "0" if current == "1" else "1")
        await _show(call, "lb_adm" if key == "lootbox_enabled" else "st_adm")
        await call.answer()

    async def _ask_value(call: CallbackQuery, state: FSMContext, prompt: str, back: str, **data):
        await state.set_state(EngageAdmin.value)
        await state.update_data(back=back, **data)
        await call.message.answer(tr(prompt), reply_markup=_kb([_btn("❌ انصراف", back)]))
        await call.answer()

    @router.callback_query(F.data.startswith("eng_set:"))
    async def cb_set(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        key = call.data.split(":", 1)[1]
        if key not in eg.SETTINGS:
            await call.answer()
            return
        label, default, low, high = eg.SETTINGS[key]
        current = await asyncio.to_thread(db.get_setting, key, str(default))
        back = "lb_adm" if key.startswith("lootbox_") else "st_adm"
        await _ask_value(call, state, f"{label}\nمقدار فعلی: {current}\nعدد جدید را بفرستید ({low:,} تا {high:,}):", back, mode="setting", key=key)

    @router.callback_query(F.data.startswith("lb_price:"))
    async def cb_price(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        type_id = int(call.data.split(":")[1])
        await _ask_value(call, state, "قیمت جدید صندوق (تومان) را بفرستید؛ ۰ یعنی فقط اهدایی:", f"lb_t:{type_id}", mode="box_price", id=type_id)

    async def _pm_view(type_id: int):
        box_type = await asyncio.to_thread(db.box_type_get, type_id)
        if not box_type:
            return None
        allowed = await asyncio.to_thread(db.box_type_payment_methods, type_id)
        catalog = await asyncio.to_thread(db.get_payment_methods_catalog)
        all_allowed = allowed is None
        rows = [[_btn(f"{'✅' if all_allowed else '⬜️'} همه‌ی روش‌ها فعال باشند", f"lb_pma:{type_id}")]]
        for item in catalog:
            checked = all_allowed or item["key"] in (allowed or [])
            suffix = "" if item["enabled"] else " (غیرفعال)"
            rows.append([InlineKeyboardButton(
                text=f"{'✅' if checked else '⬜️'} {item['label']}{suffix}",
                callback_data=f"lb_pmt:{type_id}:{item['key']}",
            )])
        rows.append([_btn("⬅️ بازگشت", f"lb_t:{type_id}")])
        text = (
            f"💳 روش‌های پرداخت مجاز برای «{box_type['title']}»:\n\n"
            "با لمس هر گزینه، فعال/غیرفعال می‌شود. اگر «همه‌ی روش‌ها» تیک بخورد، این صندوق از هر روش پرداخت فعالی "
            "قابل خرید است (با اضافه‌شدن هر درگاه جدید هم خودکار برایش فعال می‌شود)."
        )
        return text, _kb(*rows)

    @router.callback_query(F.data.startswith("lb_pm:") | F.data.startswith("lb_pma:") | F.data.startswith("lb_pmt:"))
    async def cb_lb_paymethods(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.clear()
        parts = call.data.split(":", 2)
        action = parts[0]
        try:
            type_id = int(parts[1])
        except (ValueError, IndexError):
            await call.answer(tr("درخواست نامعتبر است."), show_alert=True)
            return
        if action == "lb_pma":
            await asyncio.to_thread(db.box_type_set_payment_methods, type_id, None)
        elif action == "lb_pmt":
            method_key = parts[2] if len(parts) > 2 else ""
            catalog_keys = [item["key"] for item in await asyncio.to_thread(db.get_payment_methods_catalog)]
            allowed = await asyncio.to_thread(db.box_type_payment_methods, type_id)
            current = set(catalog_keys) if allowed is None else set(allowed)
            if method_key in current:
                current.discard(method_key)
            else:
                current.add(method_key)
            if not current:
                await call.answer(tr("⚠️ حداقل یک روش پرداخت باید برای این صندوق فعال بماند."), show_alert=True)
                return
            await asyncio.to_thread(db.box_type_set_payment_methods, type_id, None if current == set(catalog_keys) else sorted(current))
        view = await _pm_view(type_id)
        if view is None:
            await call.answer(tr("⚠️ این صندوق دیگر وجود ندارد."), show_alert=True)
            return
        await _edit(call, tr(view[0]), view[1])
        await call.answer()

    @router.callback_query(F.data.startswith("st_f:"))
    async def cb_ms_field(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        _p, milestone_id, field = call.data.split(":")
        prompts = {
            "wallet": "مبلغ جایزه‌ی کیف پول (تومان، ۰ = ندارد):",
            "discount": "درصد کد تخفیف جایزه (۰ تا ۱۰۰، ۰ = ندارد):",
            "box_type_id": "شناسه‌ی نوع صندوق جایزه (۰ = ندارد):",
        }
        if field not in prompts:
            await call.answer()
            return
        await _ask_value(call, state, prompts[field], f"st_m:{milestone_id}", mode="ms_field", id=int(milestone_id), field=field)

    async def _create_box_type(message: Message, state: FSMContext):
        text = " ".join(message.text.split())
        parts = text.split(" ", 1)
        emoji, title = "📦", text
        if len(parts) == 2 and not parts[0].isalnum() and len(parts[0]) <= 4:
            emoji, title = parts
        if not title or len(title) > 40:
            await message.answer(tr("⚠️ نام صندوق باید بین 1 و 40 کاراکتر باشد."))
            return
        type_id = await asyncio.to_thread(db.box_type_add, title, emoji, 0)
        await state.clear()
        await message.answer(tr("✅ نوع صندوق ساخته شد. قیمت و جوایزش را تنظیم کنید (قیمت ۰ یعنی فقط اهدایی)."))
        await _show(message, f"lb_t:{type_id}")

    @router.message(EngageAdmin.value, F.text)
    async def msg_value(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        data = await state.get_data()
        if data.get("mode") == "box_new":
            await _create_box_type(message, state)
            return
        value, mode, back = _int(message.text), data.get("mode"), data.get("back") or "eng_adm"
        if mode == "setting":
            _label, _d, low, high = eg.SETTINGS[data["key"]]
        elif mode == "ms_field":
            low, high = 0, (100 if data["field"] == "discount" else 1_000_000_000)
        else:
            low, high = 0, 1_000_000_000
        if value is None or not low <= value <= high:
            await message.answer(tr(f"⚠️ یک عدد صحیح بین {low:,} و {high:,} بفرستید."))
            return
        if mode == "setting":
            await asyncio.to_thread(db.set_setting, data["key"], str(value))
        elif mode == "box_price":
            await asyncio.to_thread(db.box_type_update, data["id"], price=value)
        elif mode == "ms_field":
            if data["field"] == "box_type_id" and value and not await asyncio.to_thread(db.box_type_get, value):
                await message.answer(tr("⚠️ نوع صندوقی با این شناسه وجود ندارد."))
                return
            await asyncio.to_thread(db.milestone_update, data["id"], **{data["field"]: value})
        await state.clear()
        await message.answer(tr("✅ ذخیره شد."))
        await _show(message, back)

    # ------------------------------------------------------------------
    # ادمین: انواع صندوق و جوایز
    # ------------------------------------------------------------------

    @router.callback_query(F.data == "lb_add")
    async def cb_lb_add(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.set_state(EngageAdmin.value)
        await state.update_data(mode="box_new", back="lb_adm")
        await call.message.answer(
            tr("نام صندوق را بفرستید؛ می‌توانید اول نام یک ایموجی بگذارید (مثلاً: 🎁 صندوق طلایی)."),
            reply_markup=_kb([_btn("❌ انصراف", "lb_adm")]),
        )
        await call.answer()

    @router.callback_query(F.data.startswith("lb_tgt:"))
    async def cb_lb_toggle_type(call: CallbackQuery):
        if not await _guard(call):
            return
        type_id = int(call.data.split(":")[1])
        box_type = await asyncio.to_thread(db.box_type_get, type_id)
        if box_type:
            await asyncio.to_thread(db.box_type_update, type_id, is_active=0 if box_type["is_active"] else 1)
        await _show(call, f"lb_t:{type_id}")
        await call.answer()

    @router.callback_query(F.data.startswith("lb_pa:"))
    async def cb_prize_kind(call: CallbackQuery):
        if not await _guard(call):
            return
        type_id = int(call.data.split(":")[1])
        await _edit(
            call, tr("نوع جایزه را انتخاب کنید:"),
            _kb(*[[_btn(label, f"lb_pk:{type_id}:{kind}")] for kind, label in _KIND_LABELS.items()], [_btn("⬅️ بازگشت", f"lb_t:{type_id}")]),
        )
        await call.answer()

    @router.callback_query(F.data.startswith("lb_pk:"))
    async def cb_prize_start(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        _p, type_id, kind = call.data.split(":")
        prompts = {"wallet": "مبلغ جایزه (تومان) را بفرستید:", "discount": "درصد کد تخفیف (۱ تا ۱۰۰) را بفرستید:",
                   "product": "شناسه‌ی محصول جایزه را بفرستید (کد ۱۰۰٪ فقط برای همان محصول ساخته می‌شود):"}
        if kind not in prompts:
            await call.answer()
            return
        await state.set_state(EngageAdmin.prize_value)
        await state.update_data(type_id=int(type_id), kind=kind)
        await call.message.answer(tr(prompts[kind]), reply_markup=_kb([_btn("❌ انصراف", f"lb_t:{type_id}")]))
        await call.answer()

    @router.message(EngageAdmin.prize_value, F.text)
    async def msg_prize_value(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        data = await state.get_data()
        value = _int(message.text)
        limit = 100 if data["kind"] == "discount" else 1_000_000_000
        if value is None or not 1 <= value <= limit:
            await message.answer(tr(f"⚠️ یک عدد صحیح بین 1 و {limit:,} بفرستید."))
            return
        if data["kind"] == "product":
            product = await asyncio.to_thread(db.get_product, value)
            if not product:
                await message.answer(tr("⚠️ محصولی با این شناسه وجود ندارد."))
                return
            await state.update_data(value=100, product_id=value, title=f"سرویس رایگان {product['name']}")
        elif data["kind"] == "wallet":
            await state.update_data(value=value, product_id=0, title=f"{eg.money(value)} تومان شارژ کیف پول")
        else:
            await state.update_data(value=value, product_id=0, title=f"کد تخفیف {value}٪")
        await state.set_state(EngageAdmin.prize_chance)
        total = await asyncio.to_thread(db.box_chance_total, data["type_id"])
        await message.answer(tr(f"شانس این جایزه را به درصد بفرستید (مثلاً 2.5). شانس‌های فعلی: {total:g}٪ از ۱۰۰٪؛ باقی‌مانده پوچ است."))

    @router.message(EngageAdmin.prize_chance, F.text)
    async def msg_prize_chance(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        data = await state.get_data()
        chance = _num(message.text)
        total = await asyncio.to_thread(db.box_chance_total, data["type_id"])
        if chance is None or chance <= 0 or total + chance > 100.0001:
            await message.answer(tr(f"⚠️ عددی بزرگ‌تر از ۰ بفرستید که مجموع شانس‌ها از ۱۰۰٪ بیشتر نشود (حداکثر مجاز: {round(100 - total, 4):g})."))
            return
        await asyncio.to_thread(
            db.box_prize_add, data["type_id"], data["kind"], data["title"], data["value"], data.get("product_id", 0), chance,
        )
        await state.clear()
        await message.answer(tr("✅ جایزه اضافه شد."))
        await _show(message, f"lb_t:{data['type_id']}")

    @router.callback_query(F.data.startswith("lb_pd:"))
    async def cb_prize_delete(call: CallbackQuery):
        if not await _guard(call):
            return
        _p, prize_id, type_id = call.data.split(":")
        await asyncio.to_thread(db.box_prize_delete, int(prize_id))
        await _show(call, f"lb_t:{type_id}")
        await call.answer(tr("حذف شد."))

    @router.callback_query(F.data.startswith("lb_g:"))
    async def cb_grant_start(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        type_id = int(call.data.split(":")[1])
        await state.set_state(EngageAdmin.grant_user)
        await state.update_data(type_id=type_id)
        await call.message.answer(tr("آیدی عددی تلگرام کاربر گیرنده را بفرستید:"), reply_markup=_kb([_btn("❌ انصراف", f"lb_t:{type_id}")]))
        await call.answer()

    @router.message(EngageAdmin.grant_user, F.text)
    async def msg_grant_user(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        user_id = _int(message.text)
        if not user_id or not await asyncio.to_thread(db.get_user, user_id):
            await message.answer(tr("⚠️ کاربری با این آیدی پیدا نشد."))
            return
        await state.update_data(user_id=user_id)
        await state.set_state(EngageAdmin.grant_count)
        await message.answer(tr("چند صندوق اهدا شود؟ (۱ تا ۲۰)"))

    @router.message(EngageAdmin.grant_count, F.text)
    async def msg_grant_count(message: Message, state: FSMContext, bot: Bot):
        if not is_admin(message.from_user.id):
            return
        count = _int(message.text)
        if count is None or not 1 <= count <= 20:
            await message.answer(tr("⚠️ عددی بین 1 و 20 بفرستید."))
            return
        data = await state.get_data()
        granted = await asyncio.to_thread(db.box_grant, data["user_id"], data["type_id"], "admin", count)
        await state.clear()
        if not granted:
            await message.answer(tr("⚠️ اهدا انجام نشد."))
        else:
            await message.answer(tr(f"✅ {granted} صندوق اهدا شد."))
            try:
                await bot.send_message(data["user_id"], f"🎁 {granted} صندوق شانس به تو هدیه داده شد! از کیف پول ← صندوق‌های شانس بازش کن.")
            except Exception:
                _log.info("grant notice to %s failed", data["user_id"])
        await _show(message, f"lb_t:{data['type_id']}")

    # ------------------------------------------------------------------
    # ادمین: نام‌گذاری صندوق جدید و نقاط عطف
    # ------------------------------------------------------------------

    @router.callback_query(F.data.startswith("st_tgm:"))
    async def cb_ms_toggle(call: CallbackQuery):
        if not await _guard(call):
            return
        milestone_id = int(call.data.split(":")[1])
        milestone = await asyncio.to_thread(db.milestone_get, milestone_id)
        if milestone:
            await asyncio.to_thread(db.milestone_update, milestone_id, is_active=0 if milestone["is_active"] else 1)
        await _show(call, f"st_m:{milestone_id}")
        await call.answer()

    @router.callback_query(F.data.startswith("st_del:"))
    async def cb_ms_delete(call: CallbackQuery):
        if not await _guard(call):
            return
        await asyncio.to_thread(db.milestone_delete, int(call.data.split(":")[1]))
        await _show(call, "st_adm")
        await call.answer(tr("حذف شد."))

    @router.callback_query(F.data.startswith("st_add:"))
    async def cb_ms_add(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        track = call.data.split(":")[1]
        if track not in eg.TRACKS:
            await call.answer()
            return
        await state.set_state(EngageAdmin.ms_threshold)
        await state.update_data(track=track)
        await call.message.answer(
            tr(f"عدد هدف را بفرستید ({eg.TRACKS[track][1]}، مثلاً ۲۰):"), reply_markup=_kb([_btn("❌ انصراف", "st_adm")]),
        )
        await call.answer()

    @router.message(EngageAdmin.ms_threshold, F.text)
    async def msg_ms_threshold(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        threshold = _int(message.text)
        if threshold is None or not 1 <= threshold <= 100000:
            await message.answer(tr("⚠️ عددی بین 1 و 100000 بفرستید."))
            return
        track = (await state.get_data())["track"]
        emoji, _label, title_format = eg.TRACKS[track]
        milestone_id = await asyncio.to_thread(db.milestone_add, track, threshold, emoji, title_format.format(n=threshold))
        await state.clear()
        if not milestone_id:
            await message.answer(tr("⚠️ برای این عدد قبلاً نقطه‌ی عطف ساخته شده است."))
            await _show(message, "st_adm")
            return
        await message.answer(tr("✅ نقطه‌ی عطف ساخته شد؛ جایزه‌اش را تنظیم کنید."))
        await _show(message, f"st_m:{milestone_id}")

    return router
