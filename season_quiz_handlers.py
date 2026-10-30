# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""هندلرهای فصل رقابتی و کوییز روزانه: صفحه‌ی کاربر (جدول، نشان‌ها) و پنل مدیریت ادمین."""

import asyncio
import html
import json
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import daily_quiz
import seasons
from i18n import tr

_log = logging.getLogger("season_quiz_handlers")
_PAGE = 8
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_TOGGLES = {
    "season_enabled": "0", "season_auto": "1", "season_announce": "1",
    "quiz_enabled": "0", "quiz_ai_fallback": "1",
}
_NUMERIC = {**seasons.SETTINGS, **daily_quiz.SETTINGS}
_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


class SeasonQuizAdmin(StatesGroup):
    value = State()
    bank_question = State()
    bank_options = State()
    bank_correct = State()
    bank_explanation = State()


def _btn(text, data):
    return InlineKeyboardButton(text=tr(text), callback_data=data)


def _kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows])


async def _edit(call: CallbackQuery, text: str, markup=None):
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:
        await call.message.answer(text, reply_markup=markup)


def _to_int(text):
    cleaned = (text or "").translate(_DIGITS).replace(",", "").replace("٬", "").strip()
    return int(cleaned) if cleaned.isdigit() else None


def _on(value: bool) -> str:
    return "✅ روشن" if value else "❌ خاموش"


def _back_target(key: str) -> str:
    return "ssn_adm" if key.startswith("season_") else "dq_adm"


async def season_user_view(db, user_id: int):
    """متن و کیبورد صفحه‌ی فصل برای کاربر؛ اگر قابلیت خاموش باشد None."""
    if not seasons.enabled(db):
        return None
    season = await asyncio.to_thread(db.season_get_active)
    badges_row = [_btn("🏅 نشان‌های من", "season_badges")]
    if not season:
        return tr("🏆 فصل رقابتی\n\nفصل تازه به‌زودی شروع می‌شود؛ منتظر باش!"), _kb(badges_row)
    standings = await asyncio.to_thread(db.season_standings, season["id"], 10)
    mine = await asyncio.to_thread(db.season_user_stats, season["id"], user_id)
    lines = [
        f"🏆 فصل رقابتی زیرمجموعه‌گیری — {html.escape(season['title'])}",
        f"⏳ پایان فصل: {seasons.fmt_local(season['ends_at'])}",
        "",
        f"هر دعوت جدید {season['invite_points']} امتیاز، و اگر دعوت‌شده خرید کند {season['purchase_points']} امتیاز اضافه.",
        "",
        "🎁 جوایز:",
        *seasons.prize_lines(seasons.prizes_snapshot(db), db),
        "",
    ]
    if mine["rank"]:
        lines.append(f"📊 امتیاز تو: {mine['score']} (دعوت: {mine['invites']}، خرید: {mine['buyers']}) | رتبه‌ی تو: {mine['rank']}")
    else:
        lines.append("📊 هنوز امتیازی نداری؛ لینک دعوتت را با دوستانت به اشتراک بگذار.")
    lines += ["", "🔝 جدول برترین‌ها:"]
    if not standings:
        lines.append("هنوز کسی امتیازی نگرفته؛ اولین نفر باش!")
    for index, row in enumerate(standings, start=1):
        marker = " 👈" if row["user_id"] == user_id else ""
        lines.append(f"{_MEDALS.get(index, f'{index}.')} {seasons.mask_name(row['first_name'], row['username'], row['user_id'])} — {row['score']}{marker}")
    return tr("\n".join(lines)), _kb([_btn("🔄 به‌روزرسانی", "season_view")], badges_row)


async def show_season_for_message(db, message: Message):
    """نمایش صفحه‌ی فصل از دیپ‌لینک /start season."""
    view = await season_user_view(db, message.from_user.id)
    if view is None:
        await message.answer(tr("فصل رقابتی در حال حاضر فعال نیست."))
        return
    await message.answer(view[0], reply_markup=view[1])


def create_season_quiz_router(db, is_main_bot: bool = True) -> Router:
    router = Router()

    def is_admin(user_id: int) -> bool:
        return db.is_full_admin(user_id)

    # ------------------------------------------------------------------
    # کاربر
    # ------------------------------------------------------------------

    @router.callback_query(F.data == "season_view")
    async def cb_season_view(call: CallbackQuery, state: FSMContext):
        await state.clear()
        view = await season_user_view(db, call.from_user.id)
        if view is None:
            await call.answer(tr("فصل رقابتی در حال حاضر فعال نیست."), show_alert=True)
            return
        await _edit(call, view[0], view[1])
        await call.answer()

    @router.callback_query(F.data == "season_badges")
    async def cb_season_badges(call: CallbackQuery):
        badges = await asyncio.to_thread(db.badge_list, call.from_user.id)
        if badges:
            lines = ["🏅 نشان‌های تو", ""] + [f"{b['emoji'] or '🎖'} {html.escape(b['title'])}" for b in badges]
        else:
            lines = ["🏅 نشان‌های تو", "", "هنوز نشانی نگرفته‌ای. با رسیدن به ده نفر برتر هر فصل نشان ویژه می‌گیری."]
        await _edit(call, tr("\n".join(lines)), _kb([_btn("⬅️ بازگشت", "season_view")]))
        await call.answer()

    if not is_main_bot:
        return router

    # ------------------------------------------------------------------
    # ادمین: منوی اصلی
    # ------------------------------------------------------------------

    async def _guard(call: CallbackQuery) -> bool:
        if not is_admin(call.from_user.id):
            await call.answer()
            return False
        return True

    async def _main_panel(call: CallbackQuery):
        await _edit(
            call, tr("🏆 فصل رقابتی و کوییز روزانه\n\nرقابت ماهانه‌ی زیرمجموعه‌گیری و کوییز روزانه‌ی کانال با جایزه."),
            _kb([_btn("🏆 فصل رقابتی", "ssn_adm")], [_btn("❓ کوییز روزانه", "dq_adm")], [_btn("⬅️ بازگشت", "adm_cat:marketing")]),
        )

    @router.callback_query(F.data == "ssq_adm")
    async def cb_main(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.clear()
        await _main_panel(call)
        await call.answer()

    @router.message(Command("seasonadmin"))
    async def cmd_admin(message: Message):
        if is_admin(message.from_user.id):
            await message.answer(tr("🏆 فصل رقابتی و کوییز روزانه"), reply_markup=_kb([_btn("باز کردن پنل", "ssq_adm")]))

    # ------------------------------------------------------------------
    # ادمین: پنل فصل
    # ------------------------------------------------------------------

    async def _season_panel_view():
        season = await asyncio.to_thread(db.season_get_active)
        on = seasons.enabled(db)
        lines = [
            "🏆 مدیریت فصل رقابتی", "",
            f"وضعیت: {_on(on)}",
            f"شروع خودکار فصل ماهانه: {_on(seasons.auto_renew(db))}",
            f"اعلان نتیجه در کانال: {_on(seasons.announce_enabled(db))}",
            "",
        ]
        if season:
            count = len(await asyncio.to_thread(db.season_standings, season["id"], 5000))
            lines += [
                f"فصل جاری: «{html.escape(season['title'])}» (#{season['id']})",
                f"پایان: {seasons.fmt_local(season['ends_at'])} | شرکت‌کننده‌ها: {count}",
            ]
        else:
            lines.append("فصل جاری: ندارد")
        lines += [
            f"امتیاز: دعوت {seasons.get_int(db, 'season_invite_points')} | خرید {seasons.get_int(db, 'season_purchase_points')}",
            "", "جوایز فعلی (برای فصل‌های بعدی و فصل جاری هنگام بسته شدن):",
            *seasons.prize_lines(seasons.prizes_snapshot(db), db),
            "", "امتیاز هر دعوت و خرید از لحظه‌ی شروع هر فصل ثابت می‌ماند؛ جوایز هنگام بستن فصل طبق همین تنظیمات ثبت می‌شوند.",
        ]
        rows = [
            [_btn(("⏸ خاموش کردن" if on else "▶️ روشن کردن"), "ssq_tg:season_enabled")],
            [_btn("🔁 شروع خودکار ماهانه", "ssq_tg:season_auto"), _btn("📣 اعلان در کانال", "ssq_tg:season_announce")],
            [_btn("✏️ امتیاز دعوت", "ssq_set:season_invite_points"), _btn("✏️ امتیاز خرید", "ssq_set:season_purchase_points")],
        ]
        for rank in (1, 2, 3):
            rows.append([
                _btn(f"💰 رتبه {rank}", f"ssq_set:season_prize_wallet_{rank}"),
                _btn(f"🎟 رتبه {rank}", f"ssq_set:season_prize_discount_{rank}"),
                _btn(f"🆓 رتبه {rank}", f"ssq_set:season_prize_product_{rank}"),
            ])
        rows.append([_btn("⏱ اعتبار کد جایزه (روز)", "ssq_set:season_prize_valid_days")])
        if season:
            rows.append([_btn("⏹ پایان فصل و اهدای جوایز", "ssn_end")])
        else:
            rows.append([_btn("▶️ شروع فصل جدید", "ssn_start")])
        rows.append([_btn("📜 فصل‌های قبل", "ssn_hist")])
        rows.append([_btn("⬅️ بازگشت", "ssq_adm")])
        return "\n".join(lines), _kb(*rows)

    async def _show_season_panel(call: CallbackQuery):
        text, markup = await _season_panel_view()
        await _edit(call, tr(text), markup)

    @router.callback_query(F.data == "ssn_adm")
    async def cb_season_panel(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.clear()
        await _show_season_panel(call)
        await call.answer()

    @router.callback_query(F.data == "ssn_start")
    async def cb_season_start(call: CallbackQuery):
        if not await _guard(call):
            return
        if not seasons.enabled(db):
            await call.answer(tr("ابتدا فصل رقابتی را روشن کنید."), show_alert=True)
            return
        started = await asyncio.to_thread(seasons.start_season, db)
        await call.answer(tr("فصل جدید شروع شد.") if started else tr("یک فصل فعال از قبل وجود دارد."), show_alert=not started)
        await _show_season_panel(call)

    @router.callback_query(F.data == "ssn_end")
    async def cb_season_end_ask(call: CallbackQuery):
        if not await _guard(call):
            return
        await _edit(
            call,
            tr("⏹ فصل جاری همین حالا بسته می‌شود؛ نتیجه ثبت می‌شود، سه نفر اول جایزه‌های تنظیم‌شده را می‌گیرند و ده نفر برتر نشان دریافت می‌کنند. ادامه می‌دهید؟"),
            _kb([_btn("✅ بله، پایان فصل", "ssn_endok")], [_btn("⬅️ خیر", "ssn_adm")]),
        )
        await call.answer()

    @router.callback_query(F.data == "ssn_endok")
    async def cb_season_end(call: CallbackQuery, bot: Bot):
        if not await _guard(call):
            return
        active = await asyncio.to_thread(db.season_get_active)
        if not active:
            await call.answer(tr("فصل فعالی وجود ندارد."), show_alert=True)
            await _show_season_panel(call)
            return
        await call.answer(tr("در حال پایان فصل..."))
        await seasons.close_and_award(db, bot, active["id"])
        if seasons.enabled(db) and seasons.auto_renew(db):
            await asyncio.to_thread(seasons.start_season, db)
        await _show_season_panel(call)

    @router.callback_query(F.data == "ssn_hist")
    async def cb_season_history(call: CallbackQuery):
        if not await _guard(call):
            return
        closed = await asyncio.to_thread(db.season_list_closed, 5)
        lines = ["📜 فصل‌های قبل", ""]
        if not closed:
            lines.append("هنوز فصلی بسته نشده است.")
        for season in closed:
            lines.append(f"«{html.escape(season['title'])}» (#{season['id']})")
            results = await asyncio.to_thread(db.season_results, season["id"])
            for row in results[:3]:
                name = html.escape(row["first_name"] or row["username"] or str(row["user_id"]))
                lines.append(f"  {_MEDALS[row['rank']]} {name} ({row['user_id']}) — {row['score']} امتیاز")
            if not results:
                lines.append("  بدون برنده")
        await _edit(call, tr("\n".join(lines)), _kb([_btn("⬅️ بازگشت", "ssn_adm")]))
        await call.answer()

    # ------------------------------------------------------------------
    # ادمین: کلیدها و تنظیم عددی
    # ------------------------------------------------------------------

    async def _show_panel_for(call_or_message, key: str):
        text, markup = await (_season_panel_view() if key.startswith("season_") else _quiz_panel_view())
        if isinstance(call_or_message, CallbackQuery):
            await _edit(call_or_message, tr(text), markup)
        else:
            await call_or_message.answer(tr(text), reply_markup=markup)

    @router.callback_query(F.data.startswith("ssq_tg:"))
    async def cb_toggle(call: CallbackQuery):
        if not await _guard(call):
            return
        key = call.data.split(":", 1)[1]
        if key not in _TOGGLES:
            await call.answer()
            return
        current = await asyncio.to_thread(db.get_setting, key, _TOGGLES[key])
        new_value = "0" if current == "1" else "1"
        await asyncio.to_thread(db.set_setting, key, new_value)
        if key == "season_enabled" and new_value == "1" and seasons.auto_renew(db):
            if not await asyncio.to_thread(db.season_get_active):
                await asyncio.to_thread(seasons.start_season, db)
        await _show_panel_for(call, key)
        await call.answer()

    @router.callback_query(F.data.startswith("ssq_set:"))
    async def cb_set(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        key = call.data.split(":", 1)[1]
        if key not in _NUMERIC:
            await call.answer()
            return
        label, default, low, high = _NUMERIC[key]
        current = await asyncio.to_thread(db.get_setting, key, str(default))
        await state.set_state(SeasonQuizAdmin.value)
        await state.update_data(key=key)
        await call.message.answer(
            tr(f"{label}\nمقدار فعلی: {current}\nعدد جدید را بفرستید ({low:,} تا {high:,}):"),
            reply_markup=_kb([_btn("❌ انصراف", _back_target(key))]),
        )
        await call.answer()

    @router.message(SeasonQuizAdmin.value, F.text)
    async def msg_set_value(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        key = (await state.get_data()).get("key")
        if key not in _NUMERIC:
            await state.clear()
            return
        _label, _default, low, high = _NUMERIC[key]
        value = _to_int(message.text)
        if value is None or not low <= value <= high:
            await message.answer(tr(f"⚠️ یک عدد صحیح بین {low:,} و {high:,} بفرستید."))
            return
        if key.startswith("season_prize_product_") and value > 0 and not await asyncio.to_thread(db.get_product, value):
            await message.answer(tr("⚠️ محصولی با این شناسه وجود ندارد."))
            return
        await asyncio.to_thread(db.set_setting, key, str(value))
        await state.clear()
        await message.answer(tr("✅ ذخیره شد."))
        await _show_panel_for(message, key)

    # ------------------------------------------------------------------
    # ادمین: پنل کوییز
    # ------------------------------------------------------------------

    async def _quiz_panel_view():
        channel = db.get_setting("promo_channel_id", "")
        bank = await asyncio.to_thread(db.quiz_bank_count)
        last = await asyncio.to_thread(db.quiz_last)
        on = daily_quiz.enabled(db)
        lines = [
            "❓ مدیریت کوییز روزانه", "",
            f"وضعیت: {_on(on)}",
            "کانال: " + ("✅ تنظیم شده" if channel else "⚠️ تنظیم نشده (از «مدیریت محتوای کانال با AI» کانال را انتخاب کنید)"),
            f"ساخت خودکار سوال با AI وقتی بانک خالی است: {_on(daily_quiz.ai_fallback(db))}",
            f"سوال‌های بانک: {bank}",
            f"انتشار هر روز ساعت {daily_quiz.get_int(db, 'quiz_hour')} (وقت تهران) | باز بودن {daily_quiz.get_int(db, 'quiz_close_hours')} ساعت",
            f"جایزه: {daily_quiz.get_int(db, 'quiz_winner_limit')} نفر اول درست‌جواب، کد تخفیف {daily_quiz.get_int(db, 'quiz_discount_percent')}٪ "
            f"با اعتبار {daily_quiz.get_int(db, 'quiz_code_valid_hours')} ساعت",
            "",
        ]
        if last:
            stats = await asyncio.to_thread(db.quiz_stats, last["id"])
            state_label = "باز" if last["status"] == "open" else "بسته"
            lines.append(
                f"آخرین کوییز: #{last['id']} ({state_label}) | شرکت‌کننده: {stats['total']} | درست: {stats['correct']} | برنده: {stats['winners']}"
            )
        else:
            lines.append("هنوز کوییزی منتشر نشده است.")
        rows = [
            [_btn(("⏸ خاموش کردن" if on else "▶️ روشن کردن"), "ssq_tg:quiz_enabled")],
            [_btn("🤖 ساخت با AI وقتی بانک خالی است", "ssq_tg:quiz_ai_fallback")],
            [_btn("🕗 ساعت انتشار", "ssq_set:quiz_hour"), _btn("⏳ مدت باز بودن", "ssq_set:quiz_close_hours")],
            [_btn("👥 تعداد برندگان", "ssq_set:quiz_winner_limit"), _btn("🎟 درصد تخفیف", "ssq_set:quiz_discount_percent")],
            [_btn("⏱ اعتبار کد (ساعت)", "ssq_set:quiz_code_valid_hours")],
            [_btn("📚 بانک سوال", "dq_bank:0")],
            [_btn("📣 ارسال کوییز همین حالا", "dq_now")],
        ]
        if last and last["status"] == "open":
            rows.append([_btn("🔒 بستن کوییز باز و اعلام نتیجه", "dq_close")])
        rows.append([_btn("⬅️ بازگشت", "ssq_adm")])
        return "\n".join(lines), _kb(*rows)

    async def _show_quiz_panel(call: CallbackQuery):
        text, markup = await _quiz_panel_view()
        await _edit(call, tr(text), markup)

    @router.callback_query(F.data == "dq_adm")
    async def cb_quiz_panel(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.clear()
        await _show_quiz_panel(call)
        await call.answer()

    @router.callback_query(F.data == "dq_now")
    async def cb_quiz_now(call: CallbackQuery, bot: Bot):
        if not await _guard(call):
            return
        await call.answer(tr("در حال ارسال..."))
        ok, info = await daily_quiz.post_quiz(db, bot, force=True)
        await call.message.answer(tr(f"✅ کوییز #{info} در کانال منتشر شد." if ok else f"❌ {info}"))
        await _show_quiz_panel(call)

    @router.callback_query(F.data == "dq_close")
    async def cb_quiz_close(call: CallbackQuery, bot: Bot):
        if not await _guard(call):
            return
        last = await asyncio.to_thread(db.quiz_last)
        quiz = await asyncio.to_thread(db.quiz_close_now, last["id"]) if last else None
        if not quiz:
            await call.answer(tr("کوییز بازی وجود ندارد."), show_alert=True)
        else:
            await call.answer(tr("کوییز بسته شد."))
            await daily_quiz.finalize_message(db, bot, quiz)
        await _show_quiz_panel(call)

    # ------------------------------------------------------------------
    # ادمین: بانک سوال
    # ------------------------------------------------------------------

    @router.callback_query(F.data.startswith("dq_bank:"))
    async def cb_bank(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.clear()
        page = max(0, int(call.data.split(":")[1]))
        total = await asyncio.to_thread(db.quiz_bank_count)
        rows_db = await asyncio.to_thread(db.quiz_bank_list, _PAGE, page * _PAGE)
        rows = [[_btn(f"{'🤖' if r['source'] == 'ai' else '✍️'} {r['question'][:40]}", f"dq_item:{r['id']}")] for r in rows_db]
        nav = []
        if page > 0:
            nav.append(_btn("◀️ قبلی", f"dq_bank:{page - 1}"))
        if (page + 1) * _PAGE < total:
            nav.append(_btn("بعدی ▶️", f"dq_bank:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([_btn("➕ افزودن سوال", "dq_add"), _btn("🤖 ساخت با AI", "dq_ai")])
        rows.append([_btn("⬅️ بازگشت", "dq_adm")])
        await _edit(call, tr(f"📚 بانک سوال کوییز ({total} سوال)\nهر روز کم‌استفاده‌ترین سوال منتشر می‌شود."), _kb(*rows))
        await call.answer()

    def _item_text(row) -> str:
        options = json.loads(row["options_json"])
        lines = [f"❓ {html.escape(row['question'])}", ""]
        for index, option in enumerate(options):
            lines.append(f"{'✅' if index == row['correct_index'] else '▫️'} {index + 1}. {html.escape(option)}")
        if row["explanation"]:
            lines += ["", f"💡 {html.escape(row['explanation'])}"]
        lines += ["", f"تعداد دفعات استفاده: {row['used_count']}"]
        return "\n".join(lines)

    @router.callback_query(F.data.startswith("dq_item:"))
    async def cb_item(call: CallbackQuery):
        if not await _guard(call):
            return
        row = await asyncio.to_thread(db.quiz_bank_get, int(call.data.split(":")[1]))
        if not row or not row["is_active"]:
            await call.answer(tr("این سوال پیدا نشد."), show_alert=True)
            return
        await _edit(
            call, tr(_item_text(row)),
            _kb([_btn("🗑 حذف", f"dq_del:{row['id']}")], [_btn("⬅️ بازگشت", "dq_bank:0")]),
        )
        await call.answer()

    @router.callback_query(F.data.startswith("dq_del:"))
    async def cb_delete(call: CallbackQuery):
        if not await _guard(call):
            return
        await asyncio.to_thread(db.quiz_bank_delete, int(call.data.split(":")[1]))
        await call.answer(tr("حذف شد."))
        await _edit(call, tr("🗑 سوال حذف شد."), _kb([_btn("⬅️ بانک سوال", "dq_bank:0")]))

    @router.callback_query(F.data == "dq_ai")
    async def cb_ai(call: CallbackQuery):
        if not await _guard(call):
            return
        import campaign_ai
        import channel_ai
        if not await asyncio.to_thread(channel_ai.text_configured, db):
            await call.answer(tr("مدل متن کانال تنظیم نشده؛ از «⚙️ مدل‌های AI کانال» کلید را وارد کنید."), show_alert=True)
            return
        await call.answer(tr("در حال ساخت با AI..."))
        try:
            generated = await campaign_ai.generate_engagement(db, "quiz", "")
            item = daily_quiz.validate_item(
                generated["question"], generated["options"], generated["correct_index"], generated["explanation"],
            )
            if not item:
                raise ValueError("سوال ساخته‌شده معتبر نبود.")
        except Exception as exc:
            await call.message.answer(tr("❌ ساخت انجام نشد: ") + html.escape(str(exc)), reply_markup=_kb([_btn("⬅️ بانک سوال", "dq_bank:0")]))
            return
        bank_id = await asyncio.to_thread(
            db.quiz_bank_add, item["question"], item["options"], item["correct_index"], item["explanation"], "ai",
        )
        row = await asyncio.to_thread(db.quiz_bank_get, bank_id)
        await call.message.answer(
            tr("✅ به بانک اضافه شد:\n\n" + _item_text(row)),
            reply_markup=_kb([_btn("🗑 حذف", f"dq_del:{bank_id}")], [_btn("🤖 یکی دیگر", "dq_ai")], [_btn("⬅️ بانک سوال", "dq_bank:0")]),
        )

    @router.callback_query(F.data == "dq_add")
    async def cb_add(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.set_state(SeasonQuizAdmin.bank_question)
        await call.message.answer(
            tr("متن سوال را بفرستید (حداکثر ۲۵۰ کاراکتر):"), reply_markup=_kb([_btn("❌ انصراف", "dq_bank:0")]),
        )
        await call.answer()

    @router.message(SeasonQuizAdmin.bank_question, F.text)
    async def msg_question(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        text = " ".join(message.text.split())
        if not text or len(text) > 250:
            await message.answer(tr("⚠️ متن سوال باید بین ۱ تا ۲۵۰ کاراکتر باشد."))
            return
        await state.update_data(question=text)
        await state.set_state(SeasonQuizAdmin.bank_options)
        await message.answer(tr("گزینه‌ها را بفرستید؛ هر گزینه در یک خط (۲ تا ۶ گزینه، هرکدام حداکثر ۶۰ کاراکتر):"))

    @router.message(SeasonQuizAdmin.bank_options, F.text)
    async def msg_options(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        options = [" ".join(line.split()) for line in message.text.splitlines() if line.strip()]
        if not 2 <= len(options) <= 6 or any(len(o) > 60 for o in options) or len(set(options)) != len(options):
            await message.answer(tr("⚠️ بین ۲ تا ۶ گزینه‌ی متفاوت و هرکدام حداکثر ۶۰ کاراکتر بفرستید."))
            return
        await state.update_data(options=options)
        await state.set_state(SeasonQuizAdmin.bank_correct)
        await message.answer(tr(f"شماره‌ی گزینه‌ی درست را بفرستید (۱ تا {len(options)}):"))

    @router.message(SeasonQuizAdmin.bank_correct, F.text)
    async def msg_correct(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        options = (await state.get_data()).get("options") or []
        number = _to_int(message.text)
        if number is None or not 1 <= number <= len(options):
            await message.answer(tr(f"⚠️ عددی بین ۱ و {len(options)} بفرستید."))
            return
        await state.update_data(correct_index=number - 1)
        await state.set_state(SeasonQuizAdmin.bank_explanation)
        await message.answer(tr("توضیح کوتاه پاسخ درست را بفرستید (حداکثر ۲۰۰ کاراکتر) یا «-» برای بدون توضیح:"))

    @router.message(SeasonQuizAdmin.bank_explanation, F.text)
    async def msg_explanation(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        explanation = "" if message.text.strip() == "-" else message.text
        data = await state.get_data()
        item = daily_quiz.validate_item(data.get("question"), data.get("options") or [], data.get("correct_index"), explanation)
        if not item:
            await message.answer(tr("⚠️ توضیح نباید بیشتر از ۲۰۰ کاراکتر باشد."))
            return
        bank_id = await asyncio.to_thread(
            db.quiz_bank_add, item["question"], item["options"], item["correct_index"], item["explanation"], "manual",
        )
        await state.clear()
        await message.answer(tr(f"✅ سوال #{bank_id} به بانک اضافه شد."), reply_markup=_kb([_btn("📚 بانک سوال", "dq_bank:0")]))

    return router
