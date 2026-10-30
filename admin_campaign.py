# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""ساخت پست و کمپین تبلیغاتی کانال با AI داخل پنل مدیریت بات (منوی «ابزار دیپ‌لینک و پست کانال»)."""

import asyncio
import html
import io
import logging
import re

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import admin_channel_posts
import campaign_ai
import channel_ai
import jarvis_guide
import post_image
from i18n import tr
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
from states import AdminCampaign, AdminChannelAI, AdminChannelPost

_log = logging.getLogger("admin_campaign")
_BACK = "adm_deeplink_tools"
_CHANNEL_SETTING = "promo_channel_id"
_CHANNEL_RE = re.compile(r"^(-100\d{5,}|@[A-Za-z][A-Za-z0-9_]{3,})$")
_EDU_GOAL = "یک پست آموزشی کوتاه و کاربردی (مثلاً روش اتصال و استفاده از کانفیگ، یا یک نکته‌ی امنیتی و حریم خصوصی) بنویس؛ لحن دوستانه، بدون ادعای تبلیغاتی جدید و بدون قیمت یا عدد ساختگی."
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(text), callback_data=data) for text, data in row] for row in rows
    ])


def _cancel_kb() -> InlineKeyboardMarkup:
    return _kb([("❌ لغو", "adm_camp_cancel")])


def _variant_text(index: int, v: dict) -> str:
    param = campaign_ai.start_param_for(v["deeplink"]) or "بدون دیپ‌لینک"
    header = f"نسخه {index}: {html.escape(v['angle'])}" if index else f"کپشن پیشنهادی: {html.escape(v['angle'])}"
    text = f"<b>━━ {header} ━━</b>\n\n{html.escape(v['caption'])}\n\n"
    text += f"🔘 دکمه: {html.escape(v['button_text'])}\n🔗 دیپ‌لینک: <code>{html.escape(param)}</code>"
    if v["note_for_admin"]:
        text += f"\n💡 {html.escape(v['note_for_admin'])}"
    return text


def register(router: Router, db, senior_admin_only, deny_mid):

    async def _send_post(bot: Bot, chat_id, data: dict):
        variant = data["variant"]
        return await admin_channel_posts.deliver(
            bot, chat_id, variant["caption"], variant["button_text"], variant["deeplink"], data.get("photo_id"),
        )

    async def _generate_and_show(target: Message, state: FSMContext, goal: str):
        status = await target.answer("⏳ در حال نوشتن کپشن کامل با AI بر اساس موضوع شما و داده‌ی واقعی فروشگاه...")
        try:
            variants, _facts = await campaign_ai.generate_campaigns(db, goal, 1)
        except Exception as exc:
            _log.warning("تولید کمپین ناموفق بود: %s", exc)
            await status.edit_text(
                "❌ تولید با AI انجام نشد: " + html.escape(str(exc)),
                reply_markup=_kb([("🔄 تلاش دوباره", "adm_camp_regen")], [("⬅️ بازگشت", _BACK)]),
            )
            await state.update_data(goal=goal)
            await state.set_state(AdminCampaign.choosing)
            return
        await state.update_data(goal=goal, variants=variants)
        await state.set_state(AdminCampaign.choosing)
        try:
            await status.delete()
        except Exception:
            pass
        await target.answer(
            _variant_text(0, variants[0]),
            reply_markup=_kb(
                [("✅ استفاده از این کپشن", "adm_camp_pick:0")],
                [("🔄 بازنویسی", "adm_camp_regen"), ("✏️ تغییر موضوع", "adm_camp_goal")],
                [("❌ لغو", "adm_camp_cancel")],
            ),
        )

    async def _ask_goal(call_or_msg, state: FSMContext):
        if channel_ai.text_configured(db):
            await state.set_state(AdminCampaign.waiting_goal)
            text = ("🤖 موضوع پست را بنویس؛ هرچه کامل‌تر (نکات اصلی، پیشنهاد، مخاطب، قیمت یا شرایط، لحن) کپشن دقیق‌تر و جامع‌تر می‌شود.\n"
                    "مثال: «معرفی کانفیگ تست رایگان ۲۴ ساعته؛ بدون نیاز به ثبت‌نام، مناسب اینستاگرام و تلگرام، تأکید بر ساده بودن اتصال».\n"
                    "AI همه‌ی نکاتی را که بنویسی می‌آورد و جز آن‌ها فقط از محصولات، قیمت‌ها و کدهای واقعی فروشگاه استفاده می‌کند.\n\n"
                    "یا کپشن را خودت بنویس.")
            markup = _kb(
                [("▶️ بدون هدف خاص", "adm_camp_nogoal")],
                [("✍️ کپشن دستی (بدون AI)", "adm_camp_manual")],
                [("❌ لغو", "adm_camp_cancel")],
            )
        else:
            await state.clear()
            text = ("مدل متن کانال تنظیم نشده؛ برای ساخت کپشن با AI از «⚙️ مدل‌های AI کانال» کلید Jarvis را وارد کن.\n"
                    "فعلاً می‌توانی کپشن را دستی بنویسی.")
            markup = _kb(
                [("✍️ کپشن دستی (بدون AI)", "adm_camp_manual")],
                [("⚙️ مدل‌های AI کانال", "adm_chai")],
                [("❌ لغو", "adm_camp_cancel")],
            )
        if isinstance(call_or_msg, CallbackQuery):
            await call_or_msg.message.answer(text, reply_markup=markup)
            await call_or_msg.answer()
        else:
            await call_or_msg.answer(text, reply_markup=markup)

    @router.callback_query(F.data == "adm_camp_start")
    async def cb_start(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await state.clear()
        await _ask_goal(call, state)

    @router.callback_query(F.data == "adm_camp_goal")
    async def cb_goal(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await _ask_goal(call, state)

    @router.callback_query(F.data == "adm_camp_nogoal")
    async def cb_nogoal(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await call.answer()
        await _generate_and_show(call.message, state, "")

    @router.message(AdminCampaign.waiting_goal, F.text)
    async def msg_goal(message: Message, state: FSMContext):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        goal = (message.text or "").strip()
        if len(goal) > campaign_ai.SINGLE_GOAL_LIMIT:
            await message.answer(f"موضوع حداکثر {campaign_ai.SINGLE_GOAL_LIMIT} کاراکتر باشد (الان {len(goal)}). کوتاه‌ترش کن و دوباره بفرست.", reply_markup=_cancel_kb())
            return
        await _generate_and_show(message, state, goal)

    @router.callback_query(F.data == "adm_camp_manual")
    async def cb_manual(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await state.clear()
        await state.set_state(AdminCampaign.waiting_manual_caption)
        await call.message.answer(
            f"✍️ متن کپشن پست را بفرست (حداکثر {campaign_ai.CAPTION_LIMIT} کاراکتر).",
            reply_markup=_cancel_kb(),
        )
        await call.answer()

    @router.message(AdminCampaign.waiting_manual_caption, F.text)
    async def msg_manual_caption(message: Message, state: FSMContext):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        caption = (message.text or "").strip()
        if not caption or len(caption) > campaign_ai.CAPTION_LIMIT:
            await message.answer(
                f"کپشن باید بین ۱ تا {campaign_ai.CAPTION_LIMIT} کاراکتر باشد (الان {len(caption)}). دوباره بفرست.",
                reply_markup=_cancel_kb(),
            )
            return
        await state.update_data(manual_caption=caption)
        await state.set_state(AdminCampaign.waiting_manual_button)
        await message.answer(
            "🔘 متن دکمه‌ی زیر پست را بفرست (مثلاً «🎁 خرید با ۳۰٪ تخفیف»، حداکثر ۶۴ کاراکتر).",
            reply_markup=_cancel_kb(),
        )

    @router.message(AdminCampaign.waiting_manual_button, F.text)
    async def msg_manual_button(message: Message, state: FSMContext):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        button = (message.text or "").strip()
        if not button or len(button) > 64:
            await message.answer("متن دکمه باید بین ۱ تا ۶۴ کاراکتر باشد. دوباره بفرست.", reply_markup=_cancel_kb())
            return
        await state.update_data(manual_button=button)
        await state.set_state(AdminCampaign.waiting_manual_link)
        await message.answer(
            "🔗 دکمه به چه چیزی وصل شود؟",
            reply_markup=_kb(
                [("🚫 فقط باز شدن بات", "adm_camp_dl:none"), ("🛒 منوی خرید", "adm_camp_dl:buy")],
                [("🧪 کانفیگ تست", "adm_camp_dl:test"), ("🎡 گردونه شانس", "adm_camp_dl:wheel")],
                [("🎟 کد تخفیف", "adm_camp_dl:disc"), ("🏷 پارامتر دلخواه", "adm_camp_dl:camp")],
                [("❌ لغو", "adm_camp_cancel")],
            ),
        )

    async def _finish_manual(target: Message, state: FSMContext, deeplink: dict):
        data = await state.get_data()
        variant = {
            "angle": "کپشن دستی",
            "caption": data["manual_caption"],
            "button_text": data["manual_button"],
            "deeplink": deeplink,
            "note_for_admin": "",
            "image_prompt": "",
            "manual": True,
        }
        await state.update_data(variant=variant, photo_id=None, raw_bg=None)
        await _ask_photo(target, state)

    @router.callback_query(F.data.startswith("adm_camp_dl:"))
    async def cb_manual_link(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        data = await state.get_data()
        if not data.get("manual_caption") or not data.get("manual_button"):
            await call.answer("اطلاعات پست کامل نیست؛ از اول شروع کن.", show_alert=True)
            return
        kind = call.data.split(":", 1)[1]
        if kind == "none":
            deeplink = {"type": "none"}
        elif kind == "buy":
            deeplink = {"type": "campaign", "param": "buy"}
        elif kind == "test":
            if (await asyncio.to_thread(db.get_setting, "test_enabled", "1")) != "1":
                await call.answer("کانفیگ تست در بات غیرفعال است.", show_alert=True)
                return
            deeplink = {"type": "test"}
        elif kind == "wheel":
            if not (await asyncio.to_thread(db.get_wheel_settings))["enabled"]:
                await call.answer("گردونه شانس در بات غیرفعال است.", show_alert=True)
                return
            deeplink = {"type": "wheel"}
        elif kind in ("disc", "camp"):
            await state.update_data(manual_link_kind=kind)
            await state.set_state(AdminCampaign.waiting_manual_value)
            prompt = (
                "🎟 کد تخفیف را بفرست (باید از قبل ساخته شده، فعال و عمومی باشد)."
                if kind == "disc" else
                "🏷 پارامتر را بفرست (فقط حروف انگلیسی، عدد و زیرخط، حداکثر ۴۰ کاراکتر؛ برای باز کردن یک محصول مثلاً prod_12)."
            )
            await call.message.answer(prompt, reply_markup=_cancel_kb())
            await call.answer()
            return
        else:
            await call.answer()
            return
        await call.answer()
        await _finish_manual(call.message, state, deeplink)

    @router.message(AdminCampaign.waiting_manual_value, F.text)
    async def msg_manual_value(message: Message, state: FSMContext):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        data = await state.get_data()
        raw = (message.text or "").strip()
        if data.get("manual_link_kind") == "disc":
            codes = await asyncio.to_thread(campaign_ai.active_discount_codes, db)
            code = next((c for c in codes if c.lower() == raw.lower()), None)
            if not code:
                await message.answer("کد تخفیفِ عمومی و فعالی با این نام پیدا نشد. دوباره بفرست.", reply_markup=_cancel_kb())
                return
            deeplink = {"type": "discount", "code": code}
        else:
            if not campaign_ai.is_valid_param(raw):
                await message.answer("پارامتر نامعتبر است (فقط حروف انگلیسی، عدد و زیرخط، حداکثر ۴۰). دوباره بفرست.", reply_markup=_cancel_kb())
                return
            deeplink = {"type": "campaign", "param": raw}
        await _finish_manual(message, state, deeplink)

    @router.callback_query(F.data == "adm_camp_edu")
    async def cb_edu(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        if not await asyncio.to_thread(channel_ai.text_configured, db):
            await call.answer("مدل متن کانال تنظیم نشده؛ از «⚙️ مدل‌های AI کانال» کلید را وارد کن.", show_alert=True)
            return
        await state.clear()
        await call.answer()
        await _generate_and_show(call.message, state, _EDU_GOAL)

    def _suggest_goal(kind: str, ref: int) -> str:
        if kind == "d":
            code = next((dict(r)["code"] for r in db.list_discount_codes() if r["id"] == ref), None)
            return f"معرفی و اطلاع‌رسانی کد تخفیف {code}" if code else ""
        product = db.get_product(ref)
        if not product:
            return ""
        if kind == "p":
            return f"معرفی محصول جدید «{product['name']}»"
        return f"آخرین موجودی محصول «{product['name']}»؛ تأکید بر محدود بودن موجودی و فوریت خرید"

    @router.callback_query(F.data.startswith("adm_sugg:"))
    async def cb_suggest(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        try:
            _, kind, ref = call.data.split(":")
            goal = await asyncio.to_thread(_suggest_goal, kind, int(ref)) if kind in ("p", "d", "l") else ""
        except ValueError:
            goal = ""
        if not goal:
            await call.answer("این مورد دیگر موجود نیست.", show_alert=True)
            return
        await state.clear()
        await call.answer()
        await _generate_and_show(call.message, state, goal)

    @router.callback_query(F.data == "adm_sugg_skip")
    async def cb_suggest_skip(call: CallbackQuery):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.answer()

    @router.callback_query(F.data == "adm_camp_regen")
    async def cb_regen(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        data = await state.get_data()
        await call.answer()
        await _generate_and_show(call.message, state, data.get("goal", ""))

    @router.callback_query(F.data.startswith("adm_camp_pick:"))
    async def cb_pick(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        data = await state.get_data()
        variants = data.get("variants") or []
        try:
            variant = variants[int(call.data.split(":", 1)[1])]
        except (ValueError, IndexError):
            await call.answer("این نسخه دیگر معتبر نیست؛ دوباره تولید کن.", show_alert=True)
            return
        await state.update_data(variant=variant, photo_id=None, raw_bg=None)
        await _ask_photo(call.message, state)
        await call.answer()

    async def _ask_photo(target: Message, state: FSMContext):
        await state.set_state(AdminCampaign.waiting_photo)
        rows = [[("🎨 ساخت بنر فارسی با AI", "adm_camp_genimg")]] if channel_ai.image_configured(db) else []
        await target.answer(
            "🖼 عکس پست را بفرست، یا اگر پست بدون عکس می‌خواهی دکمه‌ی زیر را بزن.",
            reply_markup=_kb(*rows, [("📝 بدون عکس", "adm_camp_nophoto")], [("❌ لغو", "adm_camp_cancel")]),
        )

    async def _after_photo(target: Message, state: FSMContext, bot: Bot):
        channel = await asyncio.to_thread(db.get_setting, _CHANNEL_SETTING, "")
        if channel:
            await state.update_data(channel_id=channel)
            await _show_preview(target, state, bot)
        else:
            await _ask_channel(target, state)

    @router.message(AdminCampaign.waiting_photo, F.photo)
    async def msg_photo(message: Message, state: FSMContext, bot: Bot):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        await state.update_data(photo_id=message.photo[-1].file_id, raw_bg=None)
        await _after_photo(message, state, bot)

    @router.callback_query(F.data == "adm_camp_nophoto")
    async def cb_nophoto(call: CallbackQuery, state: FSMContext, bot: Bot):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await state.update_data(photo_id=None, raw_bg=None)
        await call.answer()
        await _after_photo(call.message, state, bot)

    @router.callback_query(F.data == "adm_camp_genimg")
    async def cb_genimg(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        variant = (await state.get_data()).get("variant")
        if not variant:
            await call.answer("ابتدا یک نسخه انتخاب کن.", show_alert=True)
            return
        await call.answer()
        await _ask_colors(call.message, state)

    async def _ask_colors(target: Message, state: FSMContext):
        """قبل از ساخت بنر، رنگ‌های برند از مدیر پرسیده می‌شود."""
        saved = await asyncio.to_thread(channel_ai.saved_colors, db)
        await state.set_state(AdminCampaign.waiting_banner_colors)
        rows = [[(f"🎨 همان رنگ‌های قبلی ({saved[:30]})", "adm_camp_colors_prev")]] if saved else []
        await target.answer(
            "🎨 قبل از ساخت بنر، رنگ‌های برند را بنویس.\n"
            "اسم رنگ یا کد هگز بفرست، مثلاً: <code>آبی و طلایی</code> یا <code>#0B5FFF, #FFC400</code>",
            reply_markup=_kb(*rows, [("🤖 انتخاب رنگ با AI", "adm_camp_colors_auto")], [("❌ لغو", "adm_camp_cancel")]),
        )

    @router.message(AdminCampaign.waiting_banner_colors, F.text)
    async def msg_banner_colors(message: Message, state: FSMContext, bot: Bot):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        colors = (message.text or "").strip()
        if not colors or len(colors) > 150:
            await message.answer("رنگ‌ها باید بین ۱ تا ۱۵۰ کاراکتر باشد. دوباره بفرست.", reply_markup=_cancel_kb())
            return
        await asyncio.to_thread(channel_ai.save_colors, db, colors)
        await _run_banner(message, state, bot, colors)

    @router.callback_query(F.data.in_({"adm_camp_colors_prev", "adm_camp_colors_auto"}))
    async def cb_banner_colors_pick(call: CallbackQuery, state: FSMContext, bot: Bot):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        colors = await asyncio.to_thread(channel_ai.saved_colors, db) if call.data == "adm_camp_colors_prev" else ""
        await call.answer()
        await _run_banner(call.message, state, bot, colors)

    async def _run_banner(target: Message, state: FSMContext, bot: Bot, colors: str):
        data = await state.get_data()
        variant = data.get("variant")
        if not variant:
            await state.clear()
            await target.answer("اطلاعات پست کامل نیست؛ از اول شروع کن.", reply_markup=_kb([("⬅️ بازگشت", _BACK)]))
            return
        status = await target.answer("🎨 در حال ساخت بنر فارسی با AI (ممکن است چند دقیقه طول بکشد)...")
        base_prompt = await asyncio.to_thread(channel_ai.saved_banner_base_prompt, db)
        try:
            raw, source, used_mode = await channel_ai.make_banner(db, variant.get("overlay") or {}, variant.get("image_prompt") or "", colors, base_prompt)
        except Exception as exc:
            _log.warning("ساخت بنر ناموفق بود: %s", exc)
            await status.edit_text(
                "❌ ساخت بنر انجام نشد: " + html.escape(str(exc)),
                reply_markup=_kb([("🔄 تلاش دوباره", "adm_camp_genimg")], [("📝 بدون عکس", "adm_camp_nophoto")], [("❌ لغو", "adm_camp_cancel")]),
            )
            await state.set_state(AdminCampaign.waiting_photo)
            return
        try:
            await status.delete()
        except Exception:
            pass
        img_size = data.get("img_size") or await asyncio.to_thread(post_image.default_size, db)
        token = await asyncio.to_thread(post_image.save_raw, raw)
        await state.update_data(raw_bg=token, img_size=img_size, logo_mode=used_mode)
        await _compose_and_send(target, state, "🎨 بنر ساخته‌شده: " + source)
        await _after_photo(target, state, bot)

    async def _compose_and_send(target: Message, state: FSMContext, label: str) -> bool:
        data = await state.get_data()
        raw = await asyncio.to_thread(post_image.load_raw, data.get("raw_bg"))
        if raw is None:
            return False
        size = data.get("img_size") or "post"
        out = await asyncio.to_thread(channel_ai.compose_banner, raw, size, data.get("logo_mode") or "exact")
        sent = await target.answer_photo(BufferedInputFile(out, filename="banner.jpg"), caption=tr(label))
        await state.update_data(photo_id=sent.photo[-1].file_id)
        return True

    @router.callback_query(F.data == "adm_camp_size")
    async def cb_image_tweak(call: CallbackQuery, state: FSMContext, bot: Bot):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        data = await state.get_data()
        if not data.get("raw_bg") or not data.get("variant"):
            await call.answer("تصویر AI برای تغییر وجود ندارد؛ دوباره بساز.", show_alert=True)
            return
        await state.update_data(img_size=post_image.next_size(data.get("img_size") or "post"))
        await call.answer()
        if not await _compose_and_send(call.message, state, "🎨 بنر بازسازی شد"):
            await call.message.answer("❌ تصویر پایه پاک شده؛ «بنر جدید با AI» را بزن.")
            return
        await _show_preview(call.message, state, bot)

    async def _ask_channel(target: Message, state: FSMContext):
        await state.set_state(AdminCampaign.waiting_channel)
        await target.answer(
            "📢 آیدی کانال را بفرست (مثل <code>-1001234567890</code> یا <code>@channel_username</code>) "
            "یا یک پست از همان کانال را فوروارد کن.\nبات باید در کانال ادمین با دسترسی ارسال پیام باشد.",
            reply_markup=_cancel_kb(),
        )

    @router.callback_query(F.data == "adm_camp_channel")
    async def cb_channel(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await call.answer()
        await _ask_channel(call.message, state)

    @router.message(AdminCampaign.waiting_channel)
    async def msg_channel(message: Message, state: FSMContext, bot: Bot):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        origin = getattr(message, "forward_origin", None)
        channel = None
        if origin is not None and getattr(origin, "chat", None) is not None:
            channel = str(origin.chat.id)
        else:
            raw = (message.text or "").strip().translate(_DIGITS)
            if _CHANNEL_RE.match(raw):
                channel = raw
        if not channel:
            await message.answer("آیدی نامعتبر بود. آیدی عددی (شروع با -100) یا @username بفرست یا یک پست کانال را فوروارد کن.", reply_markup=_cancel_kb())
            return
        try:
            chat = await bot.get_chat(channel)
            if chat.type != "channel":
                raise ValueError("این چت کانال نیست.")
        except Exception as exc:
            await message.answer("❌ کانال پیدا نشد یا بات به آن دسترسی ندارد: " + html.escape(str(exc)), reply_markup=_cancel_kb())
            return
        await asyncio.to_thread(db.set_setting, _CHANNEL_SETTING, str(chat.id))
        await state.update_data(channel_id=str(chat.id))
        await _show_preview(message, state, bot)

    async def _show_preview(target: Message, state: FSMContext, bot: Bot):
        data = await state.get_data()
        await state.set_state(AdminCampaign.confirming)
        await target.answer("👀 پیش‌نمایش پست (دقیقاً همین به کانال می‌رود):")
        try:
            await _send_post(bot, target.chat.id, data)
        except Exception as exc:
            await target.answer("❌ ساخت پیش‌نمایش ناموفق بود: " + html.escape(str(exc)), reply_markup=_kb([("⬅️ بازگشت", _BACK)]))
            await state.clear()
            return
        image_rows = [[("🎨 بنر جدید با AI", "adm_camp_genimg")]] if channel_ai.image_configured(db) else []
        if data.get("raw_bg"):
            image_rows.insert(0, [(post_image.SIZE_LABELS[data.get("img_size") or "post"], "adm_camp_size")])
        await target.answer(
            "این پست به کانال ارسال شود؟",
            reply_markup=_kb(
                [("📣 ارسال به کانال", "adm_camp_send"), ("💾 ذخیره پیش‌نویس", "adm_camp_draft"), ("⏰ زمان‌بندی", "adm_camp_sched")],
                *image_rows,
                [("📢 تغییر کانال", "adm_camp_channel")] + ([] if data["variant"].get("manual") else [("🔄 نسخه‌ی دیگر", "adm_camp_regen")]),
                [("❌ لغو", "adm_camp_cancel")],
            ),
        )

    @router.callback_query(F.data == "adm_camp_send")
    async def cb_send(call: CallbackQuery, state: FSMContext, bot: Bot):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        data = await state.get_data()
        if not data.get("variant") or not data.get("channel_id"):
            await call.answer("اطلاعات پست کامل نیست؛ از اول شروع کن.", show_alert=True)
            return
        await state.clear()
        post_id = await asyncio.to_thread(admin_channel_posts.create_from_data, db, call.from_user.id, data)
        ok, error = await admin_channel_posts.publish_record(bot, db, post_id)
        if not ok:
            await call.message.answer(
                "❌ ارسال به کانال ناموفق بود: " + html.escape(error) + "\n(احتمالاً بات ادمین کانال نیست یا دسترسی ارسال پیام ندارد)\n"
                f"پست به‌صورت #{post_id} با وضعیت خطا ذخیره شد.",
                reply_markup=_kb([("🔄 تلاش دوباره", f"adm_cpost_send:{post_id}")], [("⬅️ بازگشت", _BACK)]),
            )
            await call.answer()
            return
        param = campaign_ai.start_param_for(data["variant"]["deeplink"]) or "-"
        await asyncio.to_thread(
            db.log_admin_action, call.from_user.id, "ai_channel_post", f"پست کانال با AI ارسال شد | دیپ‌لینک: {param}",
            "channel_post", post_id,
        )
        if not data["variant"].get("manual"):
            await asyncio.to_thread(channel_ai.record_approved, db, data["variant"], data.get("goal", ""))
        await call.message.answer(
            f"✅ پست در کانال ارسال شد (#{post_id}).",
            reply_markup=_kb([("🗂 مدیریت این پست", f"adm_cpost_view:{post_id}")], [("⬅️ بازگشت", _BACK)]),
        )
        await call.answer()

    @router.callback_query(F.data == "adm_camp_draft")
    async def cb_draft(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        data = await state.get_data()
        if not data.get("variant") or not data.get("channel_id"):
            await call.answer("اطلاعات پست کامل نیست؛ از اول شروع کن.", show_alert=True)
            return
        await state.clear()
        post_id = await asyncio.to_thread(admin_channel_posts.create_from_data, db, call.from_user.id, data)
        await call.message.answer(
            f"💾 پیش‌نویس ذخیره شد (#{post_id}).",
            reply_markup=_kb([("🗂 مدیریت این پست", f"adm_cpost_view:{post_id}")], [("⬅️ بازگشت", _BACK)]),
        )
        await call.answer()

    @router.callback_query(F.data == "adm_camp_sched")
    async def cb_sched(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        data = await state.get_data()
        if not data.get("variant") or not data.get("channel_id"):
            await call.answer("اطلاعات پست کامل نیست؛ از اول شروع کن.", show_alert=True)
            return
        post_id = await asyncio.to_thread(admin_channel_posts.create_from_data, db, call.from_user.id, data)
        await state.clear()
        await state.set_state(AdminChannelPost.waiting_schedule)
        await state.update_data(cpost_id=post_id)
        await call.message.answer(
            admin_channel_posts.SCHEDULE_PROMPT,
            reply_markup=_kb([("❌ لغو (ذخیره به‌عنوان پیش‌نویس)", f"adm_cpost_view:{post_id}")]),
        )
        await call.answer()

    def _chai_text() -> str:
        has_key = bool(channel_ai.jarvis_keys(db))
        return (
            "⚙️ <b>مدل‌های AI پست کانال (فقط Jarvis)</b>\n"
            "این بخش فقط از Jarvis استفاده می‌کند و جدا از بقیه‌ی ایجنت‌های بات است.\n\n"
            f"🔑 کلید Jarvis: {'✅ ثبت شده' if has_key else '❌ ثبت نشده'}\n\n"
            "📝 <b>مدل متن (کپشن با ایموجی)</b>\n"
            f"<code>{html.escape(channel_ai.jarvis_text_model(db))}</code>\n\n"
            "🎨 <b>مدل تصویر (بنر فارسی)</b>\n"
            f"<code>{html.escape(channel_ai.jarvis_image_model(db))}</code>\n\n"
            f"🖼 لوگوی بنر: {'✅ دارد' if channel_ai.has_logo() else '❌ ندارد'}\n"
            f"🖼 بنر مرجع سبک: {'✅ دارد' if channel_ai.has_style_ref() else '❌ ندارد'}\n"
            f"🎯 حالت لوگو: {'لوگوی دقیق (چسباندن اصلی در نشان درخشان)' if channel_ai.logo_mode(db) == 'exact' else 'تصویر مرجع به مدل AI'}\n"
            f"🧩 پرامپت پایه‌ی سفارشی: {'✅ تنظیم شده' if (db.get_setting(channel_ai.BANNER_BASE_PROMPT_KEY, '') or '').strip() else 'پیش‌فرض'}\n"
            f"🧠 پست‌های تأییدشده‌ی یادگرفته‌شده: {len(channel_ai.learning_examples(db, channel_ai.LEARNING_LIMIT))}"
        )

    def _chai_kb() -> InlineKeyboardMarkup:
        return _kb(
            [("🔑 کلید Jarvis", "adm_chai_key")],
            [("📝 مدل متن", "adm_chai_tmodel"), ("🎨 مدل تصویر", "adm_chai_imodel")],
            [("🖼 لوگوی بنر", "adm_chai_logo"), ("🧠 یادگیری AI", "adm_chai_learn")],
            [("🧩 پرامپت پایه بنر", "adm_chai_banner_prompt"), ("🎯 حالت لوگو", "adm_chai_logomode")],
            [("🖼 بنر مرجع سبک", "adm_chai_styleref"), ("📖 راهنمای Jarvis", "adm_chai_guide")],
            [("🧪 تست متن", "adm_chai_ttest"), ("🧪 تست تصویر (از اعتبار کم می‌شود)", "adm_chai_itest")],
            [("⬅️ بازگشت", _BACK)],
        )

    def _chai_back_kb() -> InlineKeyboardMarkup:
        return _kb([("⬅️ بازگشت", "adm_chai")])

    async def _show_chai(target: Message, edit: bool = True):
        if edit:
            try:
                await target.edit_text(_chai_text(), reply_markup=_chai_kb())
                return
            except Exception:
                pass
        await target.answer(_chai_text(), reply_markup=_chai_kb())

    async def _chai_log(admin_id: int, detail: str):
        await asyncio.to_thread(db.log_admin_action, admin_id, "channel_ai_change", detail)

    @router.callback_query(F.data == "adm_chai")
    async def cb_chai(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await state.clear()
        await _show_chai(call.message)
        await call.answer()

    @router.callback_query(F.data == "adm_chai_banner_prompt")
    async def cb_chai_banner_prompt(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        current = (await asyncio.to_thread(db.get_setting, channel_ai.BANNER_BASE_PROMPT_KEY, "") or "").strip()
        preview = current if current else channel_ai.DEFAULT_BANNER_BASE_PROMPT
        await state.set_state(AdminChannelAI.waiting_banner_base_prompt)
        await call.message.answer(
            "🧩 <b>پرامپت پایه‌ی بنر</b>\n\n"
            "دستورالعمل ثابت برند را بفرست. این متن در کنار اطلاعات داینامیک هر پست (موضوع، محصول، قیمت و تخفیف) به مدل تصویرساز داده می‌شود.\n"
            "حداکثر ۳۰۰۰ کاراکتر. برای برگشت به پرامپت پیش‌فرض فقط - بفرست.\n\n"
            "<b>پرامپت فعلی:</b>\n" + html.escape(preview[:1800]),
            reply_markup=_chai_back_kb(),
        )
        await call.answer()

    @router.message(AdminChannelAI.waiting_banner_base_prompt, F.text)
    async def msg_chai_banner_prompt(message: Message, state: FSMContext):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        value = (message.text or "").strip()
        if not value:
            await message.answer("متن پرامپت خالی است. متن بفرست یا برای پیش‌فرض - بفرست.", reply_markup=_chai_back_kb())
            return
        if len(value) > 3000:
            await message.answer("پرامپت حداکثر ۳۰۰۰ کاراکتر باشد.", reply_markup=_chai_back_kb())
            return
        await asyncio.to_thread(channel_ai.save_banner_base_prompt, db, value)
        await _chai_log(message.from_user.id, "پرامپت پایه بنر به‌روزرسانی شد" if value != "-" else "پرامپت پایه بنر به پیش‌فرض برگشت")
        await state.clear()
        await _show_chai(message, edit=False)

    @router.callback_query(F.data == "adm_chai_logomode")
    async def cb_chai_logomode(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        new = await asyncio.to_thread(channel_ai.toggle_logo_mode, db)
        await _chai_log(call.from_user.id, f"حالت لوگوی بنر: {new}")
        await _show_chai(call.message)
        await call.answer("🎯 لوگوی دقیق (پیشنهادی)" if new == "exact" else "🎯 تصویر مرجع به مدل AI (اگر درگاه پشتیبانی نکند خودکار به حالت دقیق برمی‌گردد)", show_alert=True)

    @router.callback_query(F.data == "adm_chai_guide")
    async def cb_chai_guide(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await state.clear()
        await call.answer()
        last = len(jarvis_guide.PARTS) - 1
        for index, part in enumerate(jarvis_guide.PARTS):
            await call.message.answer(part, reply_markup=_chai_back_kb() if index == last else None, disable_web_page_preview=True)

    @router.callback_query(F.data == "adm_chai_styleref")
    async def cb_chai_styleref(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await state.set_state(AdminChannelAI.waiting_style_ref)
        await call.message.answer(
            "🖼 <b>بنر مرجع سبک</b>\n\n"
            "یک بنر نهایی که سبک دلخواهت است بفرست (ترجیحاً به‌صورت فایل برای کیفیت بالا). "
            "این تصویر همراه هر درخواست به مدل داده می‌شود تا چیدمان، نور، رنگ و تایپوگرافی را شبیه همان بسازد و فقط متن‌ها عوض شوند.\n"
            "اگر درگاه تصویر مرجع را نپذیرد، خودکار بدون مرجع ساخته می‌شود.\n"
            "برای حذف: <code>-</code>",
            reply_markup=_chai_back_kb(),
        )
        await call.answer()

    @router.message(AdminChannelAI.waiting_style_ref, F.photo | F.document | F.text)
    async def msg_chai_styleref(message: Message, state: FSMContext, bot: Bot):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        if message.text is not None:
            if message.text.strip() != "-":
                await message.answer("تصویر بنر را بفرست، یا برای حذف <code>-</code> بفرست.", reply_markup=_chai_back_kb())
                return
            removed = await asyncio.to_thread(channel_ai.remove_style_ref)
            await _chai_log(message.from_user.id, "بنر مرجع سبک حذف شد" if removed else "حذف بنر مرجع: چیزی ثبت نشده بود")
            await state.clear()
            await _show_chai(message, edit=False)
            return
        if message.photo:
            file_id, size = message.photo[-1].file_id, message.photo[-1].file_size or 0
        elif message.document and (message.document.mime_type or "").startswith("image/"):
            file_id, size = message.document.file_id, message.document.file_size or 0
        else:
            await message.answer("⚠️ فقط فایل تصویری (PNG یا JPG) قابل قبول است.", reply_markup=_chai_back_kb())
            return
        if size > 10 * 1024 * 1024:
            await message.answer("⚠️ حجم تصویر بیش از ۱۰ مگابایت است.", reply_markup=_chai_back_kb())
            return
        buf = io.BytesIO()
        try:
            await bot.download(file_id, destination=buf)
            await asyncio.to_thread(channel_ai.save_style_ref, buf.getvalue())
        except Exception as exc:
            _log.warning("ذخیره‌ی بنر مرجع ناموفق بود: %s", exc)
            await message.answer("❌ این فایل به‌عنوان تصویر خوانده نشد: " + html.escape(str(exc)[:150]), reply_markup=_chai_back_kb())
            return
        await _chai_log(message.from_user.id, "بنر مرجع سبک ذخیره شد")
        await state.clear()
        await _show_chai(message, edit=False)

    @router.callback_query(F.data == "adm_chai_logo")
    async def cb_chai_logo(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await state.set_state(AdminChannelAI.waiting_logo)
        await call.message.answer(
            "🖼 لوگوی بنرهای این بخش را بفرست. برای حفظ شفافیت، آن را «به‌صورت فایل» (PNG) بفرست.\n"
            "برای حذف لوگو: <code>-</code>",
            reply_markup=_chai_back_kb(),
        )
        await call.answer()

    @router.message(AdminChannelAI.waiting_logo, F.photo | F.document | F.text)
    async def msg_chai_logo(message: Message, state: FSMContext, bot: Bot):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        if message.text is not None:
            if message.text.strip() != "-":
                await message.answer("عکس یا فایل لوگو را بفرست، یا برای حذف <code>-</code> بفرست.", reply_markup=_chai_back_kb())
                return
            removed = await asyncio.to_thread(channel_ai.remove_logo)
            await _chai_log(message.from_user.id, "لوگوی بنر کانال حذف شد" if removed else "حذف لوگو: لوگویی ثبت نشده بود")
            await state.clear()
            await _show_chai(message, edit=False)
            return
        if message.photo:
            file_id, size = message.photo[-1].file_id, message.photo[-1].file_size or 0
        elif message.document and (message.document.mime_type or "").startswith("image/"):
            file_id, size = message.document.file_id, message.document.file_size or 0
        else:
            await message.answer("⚠️ فقط فایل تصویری (PNG یا JPG) قابل قبول است.", reply_markup=_chai_back_kb())
            return
        if size > 5 * 1024 * 1024:
            await message.answer("⚠️ حجم لوگو بیش از ۵ مگابایت است.", reply_markup=_chai_back_kb())
            return
        buf = io.BytesIO()
        try:
            await bot.download(file_id, destination=buf)
            await asyncio.to_thread(channel_ai.save_logo, buf.getvalue())
        except Exception as exc:
            _log.warning("ذخیره‌ی لوگوی بنر ناموفق بود: %s", exc)
            await message.answer("❌ این فایل به‌عنوان تصویر خوانده نشد: " + html.escape(str(exc)[:150]), reply_markup=_chai_back_kb())
            return
        await _chai_log(message.from_user.id, "لوگوی بنر کانال ذخیره شد")
        await state.clear()
        await _show_chai(message, edit=False)

    def _learn_text() -> str:
        items = channel_ai.learning_examples(db, channel_ai.LEARNING_LIMIT)
        lines = [
            "🧠 <b>یادگیری AI پست کانال</b>",
            "هوش مصنوعی فقط از پست‌هایی یاد می‌گیرد که تو بعد از بررسی تأیید و به کانال فرستادی. "
            "پیش‌نویس‌ها، نسخه‌های ردشده و پست‌های دستی یاد گرفته نمی‌شوند.",
            "",
            f"تعداد پست‌های یادگرفته‌شده: <b>{len(items)}</b> از {channel_ai.LEARNING_LIMIT}",
        ]
        if items:
            lines.append("\n<b>آخرین پست‌های تأییدشده:</b>")
            for item in reversed(items[-5:]):
                preview = (item.get("caption") or "").replace("\n", " ")[:70]
                lines.append(f"• {html.escape(item.get('angle') or '—')}: {html.escape(preview)}…")
        else:
            lines.append("هنوز پستی تأیید نشده است.")
        return "\n".join(lines)

    def _learn_kb(confirm: bool = False) -> InlineKeyboardMarkup:
        if confirm:
            return _kb([("✅ بله، پاک کن", "adm_chai_learn_clear_ok")], [("⬅️ انصراف", "adm_chai_learn")])
        return _kb([("🗑 پاک‌سازی یادگیری", "adm_chai_learn_clear")], [("⬅️ بازگشت", "adm_chai")])

    async def _show_learn(call: CallbackQuery, confirm: bool = False):
        try:
            await call.message.edit_text(_learn_text(), reply_markup=_learn_kb(confirm))
        except Exception:
            await call.message.answer(_learn_text(), reply_markup=_learn_kb(confirm))

    @router.callback_query(F.data == "adm_chai_learn")
    async def cb_chai_learn(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await state.clear()
        await _show_learn(call)
        await call.answer()

    @router.callback_query(F.data == "adm_chai_learn_clear")
    async def cb_chai_learn_clear(call: CallbackQuery):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await _show_learn(call, confirm=True)
        await call.answer()

    @router.callback_query(F.data == "adm_chai_learn_clear_ok")
    async def cb_chai_learn_clear_ok(call: CallbackQuery):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await asyncio.to_thread(channel_ai.clear_learning, db)
        await _chai_log(call.from_user.id, "یادگیری AI پست کانال پاک شد")
        await _show_learn(call)
        await call.answer("یادگیری پاک شد.")

    @router.callback_query(F.data == "adm_chai_key")
    async def cb_chai_key(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await state.set_state(AdminChannelAI.waiting_key)
        await call.message.answer(
            "🔑 کلید(های) Jarvis را بفرست؛ هر خط یک کلید (با pmai_ شروع می‌شود). بعد از ذخیره، پیام تو پاک می‌شود.\n"
            "برای حذف کلید: <code>-</code>",
            reply_markup=_chai_back_kb(),
        )
        await call.answer()

    @router.message(AdminChannelAI.waiting_key, F.text)
    async def msg_chai_key(message: Message, state: FSMContext):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        raw = (message.text or "").strip()
        try:
            await message.delete()
        except Exception:
            pass
        keys = [] if raw == "-" else list(dict.fromkeys(k.strip() for k in raw.replace(",", "\n").splitlines() if k.strip()))
        if raw != "-" and not keys:
            await message.answer("کلیدی پیدا نشد؛ دوباره بفرست.", reply_markup=_chai_back_kb())
            return
        base = channel_ai.jarvis_base(db)
        rejected = [str(i) for i, key in enumerate(keys, 1) if not (await channel_ai.check_jarvis_key(base, key))[0]]
        if rejected:
            await message.answer(
                "❌ Jarvis این کلیدها را رد کرد (شماره‌ی خط): " + ", ".join(rejected) + "\nکلید را بررسی و دوباره بفرست.",
                reply_markup=_chai_back_kb(),
            )
            return
        await asyncio.to_thread(db.set_setting, "jarvis_api_key", "\n".join(keys))
        await _chai_log(message.from_user.id, "کلید Jarvis ذخیره شد" if keys else "کلید Jarvis حذف شد")
        await state.clear()
        await _show_chai(message, edit=False)

    async def _ask_model(call: CallbackQuery, state: FSMContext, kind: str):
        text_ids, image_ids = [], []
        try:
            text_ids, image_ids = await channel_ai.list_jarvis_models(db)
        except Exception as exc:
            _log.warning("دریافت فهرست مدل‌های Jarvis ناموفق بود: %s", exc)
        ids = text_ids if kind == "text" else image_ids
        current = channel_ai.jarvis_text_model(db) if kind == "text" else channel_ai.jarvis_image_model(db)
        title = "📝 مدل متن" if kind == "text" else "🎨 مدل تصویر"
        listing = ("\n\nمدل‌های موجود در Jarvis:\n" + "\n".join(f"<code>{html.escape(i)}</code>" for i in ids[:15])) if ids else ""
        await state.set_state(AdminChannelAI.waiting_text_model if kind == "text" else AdminChannelAI.waiting_image_model)
        await call.message.answer(
            f"{title}\nمدل فعلی: <code>{html.escape(current)}</code>{listing}\n\n"
            "شناسه‌ی دقیق مدل را بفرست؛ برای برگشت به پیش‌فرض: <code>-</code>",
            reply_markup=_chai_back_kb(),
        )
        await call.answer()

    @router.callback_query(F.data == "adm_chai_tmodel")
    async def cb_chai_tmodel(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await _ask_model(call, state, "text")

    @router.callback_query(F.data == "adm_chai_imodel")
    async def cb_chai_imodel(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await _ask_model(call, state, "image")

    async def _save_model(message: Message, state: FSMContext, kind: str):
        if not senior_admin_only(message.from_user.id):
            await state.clear()
            return
        value = "" if (message.text or "").strip() == "-" else (message.text or "").strip()
        if value and any(ch.isspace() for ch in value):
            await message.answer("شناسه‌ی مدل نباید فاصله داشته باشد.", reply_markup=_chai_back_kb())
            return
        if kind == "text" and value and not channel_ai.is_text_model(value):
            await message.answer("این شناسه مدل متنی نیست (شامل image/embed/tts و مانند آن است).", reply_markup=_chai_back_kb())
            return
        key = "jarvis_text_model" if kind == "text" else "jarvis_image_model"
        await asyncio.to_thread(db.set_setting, key, value)
        await _chai_log(message.from_user.id, f"{key}: {value or 'پیش‌فرض'}")
        await state.clear()
        await _show_chai(message, edit=False)

    @router.message(AdminChannelAI.waiting_text_model, F.text)
    async def msg_chai_text_model(message: Message, state: FSMContext):
        await _save_model(message, state, "text")

    @router.message(AdminChannelAI.waiting_image_model, F.text)
    async def msg_chai_image_model(message: Message, state: FSMContext):
        await _save_model(message, state, "image")

    @router.callback_query(F.data == "adm_chai_ttest")
    async def cb_chai_ttest(call: CallbackQuery):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await call.answer("⏳ در حال تست...")
        try:
            _data, source = await channel_ai.ask_json(db, 'فقط این JSON را برگردان: {"ok": true}', "ping")
        except Exception as exc:
            await call.message.answer("❌ تست متن ناموفق بود: " + html.escape(str(exc)), reply_markup=_chai_back_kb())
            return
        await call.message.answer(f"✅ مدل متن کانال کار می‌کند: <code>{html.escape(source)}</code>", reply_markup=_chai_back_kb())

    @router.callback_query(F.data == "adm_chai_itest")
    async def cb_chai_itest(call: CallbackQuery):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await call.answer("⏳ در حال ساخت تصویر آزمایشی...")
        try:
            raw, source = await channel_ai.generate_image(db, "")
        except Exception as exc:
            await call.message.answer("❌ تست تصویر ناموفق بود: " + html.escape(str(exc)), reply_markup=_chai_back_kb())
            return
        await call.message.answer_photo(BufferedInputFile(raw, filename="test.jpg"), caption="✅ مدل تصویر کانال کار می‌کند: " + source, reply_markup=_chai_back_kb())

    @router.callback_query(F.data == "adm_camp_cancel")
    async def cb_cancel(call: CallbackQuery, state: FSMContext):
        if not senior_admin_only(call.from_user.id):
            return await deny_mid(call)
        await state.clear()
        await call.message.answer("لغو شد.", reply_markup=_kb([("⬅️ بازگشت", _BACK)]))
        await call.answer()
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
