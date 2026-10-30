# -*- coding: utf-8 -*-
"""قالب برند تصاویر پست کانال: رنگ‌ها، لوگو، فونت فارسی، اندازه‌ی پیش‌فرض و پیش‌نمایش."""

import asyncio
import html
import io
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import post_image
from i18n import tr
from states import AdminPostBrand

_log = logging.getLogger("admin_post_brand")
BACK = "adm_cposts"
MENU = "adm_pbrand"
COLOR_LABELS = {"primary": "رنگ اصلی", "accent": "رنگ تاکیدی (قیمت و تخفیف)", "text": "رنگ متن"}
MAX_LOGO_BYTES = 5 * 1024 * 1024
MAX_FONT_BYTES = 10 * 1024 * 1024


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(text), callback_data=data) for text, data in row] for row in rows
    ])


def _cancel_kb() -> InlineKeyboardMarkup:
    return _kb([("❌ لغو", MENU)])


def register(router: Router, db, senior_admin_only, deny_mid):

    async def _menu_text() -> str:
        brand = await asyncio.to_thread(post_image.load_brand, db)
        size = await asyncio.to_thread(post_image.default_size, db)
        overlay_on = await asyncio.to_thread(post_image.overlay_default, db)
        lines = [
            "🎨 <b>قالب برند تصاویر پست</b>",
            "",
            "روی تصویر AI، عنوان و قیمت (از دیتابیس) و لوگو با Pillow نوشته می‌شود.",
            "",
            f"{tr(COLOR_LABELS['primary'])}: <code>{html.escape(brand['primary'])}</code>",
            f"{tr(COLOR_LABELS['accent'])}: <code>{html.escape(brand['accent'])}</code>",
            f"{tr(COLOR_LABELS['text'])}: <code>{html.escape(brand['text'])}</code>",
            "لوگو: " + ("✅ دارد" if post_image.has_logo() else "❌ ندارد"),
            "فونت: " + html.escape(post_image.font_label()),
            tr(post_image.SIZE_LABELS[size]) + " (پیش‌فرض)",
            "متن روی تصویر (پیش‌فرض): " + ("✅ روشن" if overlay_on else "❌ خاموش"),
        ]
        if not post_image.engine_status():
            lines += ["", "⚠️ موتور نوشتن فارسی روی تصویر نصب نیست؛ روی سرور دستور زیر را اجرا و بات را ریستارت کن:", "<code>pip install -r requirements.txt</code>"]
        return "\n".join(lines)

    async def _show_menu(target, state: FSMContext = None):
        if state is not None:
            await state.clear()
        text = await _menu_text()
        markup = _kb(
            [(f"🎨 {COLOR_LABELS['primary']}", "adm_pbrand_color:primary"), (f"🎨 {COLOR_LABELS['text']}", "adm_pbrand_color:text")],
            [(f"🎨 {COLOR_LABELS['accent']}", "adm_pbrand_color:accent")],
            [("🖼 آپلود لوگو", "adm_pbrand_logo"), ("🗑 حذف لوگو", "adm_pbrand_logo_rm")],
            [("🔠 آپلود فونت", "adm_pbrand_font"), ("🗑 فونت پیش‌فرض", "adm_pbrand_font_rm")],
            [("📐 اندازه‌ی پیش‌فرض", "adm_pbrand_size"), ("🔤 متن روی تصویر", "adm_pbrand_ovr")],
            [("👁 پیش‌نمایش قالب", "adm_pbrand_preview")],
            [("⬅️ بازگشت", BACK)],
        )
        if isinstance(target, CallbackQuery):
            try:
                await target.message.edit_text(text, reply_markup=markup)
            except Exception:
                await target.message.answer(text, reply_markup=markup)
            await target.answer()
        else:
            await target.answer(text, reply_markup=markup)

    async def _guard(call: CallbackQuery) -> bool:
        if not senior_admin_only(call.from_user.id):
            await deny_mid(call)
            return False
        return True

    @router.callback_query(F.data == MENU)
    async def cb_menu(call: CallbackQuery, state: FSMContext):
        if await _guard(call):
            await _show_menu(call, state)

    @router.callback_query(F.data.startswith("adm_pbrand_color:"))
    async def cb_color(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        key = call.data.split(":", 1)[1]
        if key not in COLOR_LABELS:
            await call.answer()
            return
        await state.set_state(AdminPostBrand.waiting_color)
        await state.update_data(color_key=key)
        await call.message.answer(
            f"🎨 {tr(COLOR_LABELS[key])} را به شکل کد هگز بفرست.\nمثال: <code>#0B5FFF</code>", reply_markup=_cancel_kb(),
        )
        await call.answer()

    @router.message(AdminPostBrand.waiting_color, F.text)
    async def msg_color(message: Message, state: FSMContext):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        key = (await state.get_data()).get("color_key")
        color = post_image.parse_color(message.text)
        if key not in COLOR_LABELS or not color:
            await message.answer("⚠️ کد رنگ نامعتبر است. مثال: #0B5FFF", reply_markup=_cancel_kb())
            return
        await asyncio.to_thread(db.set_setting, f"post_brand_{key}", color)
        await _show_menu(message, state)

    @router.callback_query(F.data == "adm_pbrand_logo")
    async def cb_logo(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.set_state(AdminPostBrand.waiting_logo)
        await call.message.answer(
            "🖼 لوگو را بفرست. برای حفظ شفافیت، آن را به‌صورت «فایل» (PNG) بفرست، نه عکس فشرده‌شده.",
            reply_markup=_cancel_kb(),
        )
        await call.answer()

    @router.message(AdminPostBrand.waiting_logo, F.photo | F.document)
    async def msg_logo(message: Message, state: FSMContext, bot: Bot):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        if message.photo:
            file_id, size = message.photo[-1].file_id, message.photo[-1].file_size or 0
        else:
            doc = message.document
            if not (doc.mime_type or "").startswith("image/"):
                await message.answer("⚠️ فقط فایل تصویری (PNG یا JPG) قابل قبول است.", reply_markup=_cancel_kb())
                return
            file_id, size = doc.file_id, doc.file_size or 0
        if size > MAX_LOGO_BYTES:
            await message.answer("⚠️ حجم لوگو بیش از ۵ مگابایت است.", reply_markup=_cancel_kb())
            return
        buf = io.BytesIO()
        try:
            await bot.download(file_id, destination=buf)
            await asyncio.to_thread(post_image.save_logo, buf.getvalue())
        except Exception as exc:
            _log.warning("ذخیره‌ی لوگو ناموفق بود: %s", exc)
            await message.answer("❌ این فایل به‌عنوان تصویر خوانده نشد: " + html.escape(str(exc)[:150]), reply_markup=_cancel_kb())
            return
        await _show_menu(message, state)

    @router.callback_query(F.data == "adm_pbrand_logo_rm")
    async def cb_logo_rm(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        removed = await asyncio.to_thread(post_image.remove_logo)
        if not removed:
            await call.answer("لوگویی ثبت نشده.", show_alert=True)
            return
        await _show_menu(call, state)

    @router.callback_query(F.data == "adm_pbrand_font")
    async def cb_font(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        await state.set_state(AdminPostBrand.waiting_font)
        await call.message.answer(
            "🔠 فایل فونت فارسی (.ttf یا .otf، مثلاً وزیرمتن) را به‌صورت «فایل» بفرست.", reply_markup=_cancel_kb(),
        )
        await call.answer()

    @router.message(AdminPostBrand.waiting_font, F.document)
    async def msg_font(message: Message, state: FSMContext, bot: Bot):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        doc = message.document
        if not (doc.file_name or "").lower().endswith((".ttf", ".otf")):
            await message.answer("⚠️ فقط فایل .ttf یا .otf قابل قبول است.", reply_markup=_cancel_kb())
            return
        if (doc.file_size or 0) > MAX_FONT_BYTES:
            await message.answer("⚠️ حجم فونت بیش از ۱۰ مگابایت است.", reply_markup=_cancel_kb())
            return
        buf = io.BytesIO()
        try:
            await bot.download(doc.file_id, destination=buf)
            await asyncio.to_thread(post_image.save_font, buf.getvalue())
        except Exception as exc:
            _log.warning("ذخیره‌ی فونت ناموفق بود: %s", exc)
            await message.answer("❌ فونت معتبر نیست: " + html.escape(str(exc)[:150]), reply_markup=_cancel_kb())
            return
        await _show_menu(message, state)

    @router.callback_query(F.data == "adm_pbrand_font_rm")
    async def cb_font_rm(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        if not await asyncio.to_thread(post_image.remove_font):
            await call.answer("فونت سفارشی ثبت نشده.", show_alert=True)
            return
        await _show_menu(call, state)

    @router.callback_query(F.data == "adm_pbrand_size")
    async def cb_size(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        current = await asyncio.to_thread(post_image.default_size, db)
        await asyncio.to_thread(db.set_setting, "post_image_size", post_image.next_size(current))
        await _show_menu(call, state)

    @router.callback_query(F.data == "adm_pbrand_ovr")
    async def cb_overlay(call: CallbackQuery, state: FSMContext):
        if not await _guard(call):
            return
        current = await asyncio.to_thread(post_image.overlay_default, db)
        await asyncio.to_thread(db.set_setting, "post_overlay_enabled", "0" if current else "1")
        await _show_menu(call, state)

    @router.callback_query(F.data == "adm_pbrand_preview")
    async def cb_preview(call: CallbackQuery):
        if not await _guard(call):
            return
        size = await asyncio.to_thread(post_image.default_size, db)
        brand = await asyncio.to_thread(post_image.load_brand, db)
        try:
            raw = await asyncio.to_thread(
                lambda: post_image.render(post_image.sample_background(size), post_image.SAMPLE_OVERLAY, size, brand),
            )
        except post_image.PostImageError as exc:
            await call.answer("❌ " + str(exc)[:180], show_alert=True)
            return
        await call.message.answer_photo(BufferedInputFile(raw, filename="preview.jpg"), caption=tr("👁 پیش‌نمایش قالب برند"))
        await call.answer()
