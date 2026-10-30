# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
اسکیمای مشترک «تنظیمات تکمیلی» برای پنل وب مدیر و بخش مدیریت مینی‌اپ.

تنظیماتی که بات از آن‌ها استفاده می‌کند ولی تا الان در پنل وب یا مینی‌اپ فرم
نداشتند اینجا تعریف شده‌اند. هر دو سرویس (admin_panel/server.py و
miniapp/server.py) از همین فایل می‌خوانند/می‌نویسند؛ پس برای افزودن تنظیم جدید
فقط کافی است همین‌جا یک فیلد اضافه شود.

ساختار هر فیلد:
  key      کلید در جدول settings (همان که بات می‌خواند)
  label    برچسب فارسی
  type     bool | number | text | textarea | password | select
  default  مقدار پیش‌فرض (دقیقاً همان که کد بات وقتی تنظیم نیست استفاده می‌کند)
  hint     توضیح کوتاه (اختیاری)
  min/max  برای number (اختیاری)
  options  برای select: [[value, label], ...]

هر گروه دو پرچم دارد: web / mini. اگر False باشد یعنی آن سطح از قبل فرم اختصاصی
دارد و تکراری نمی‌شود.

مقدار bool همیشه "1" / "0" ذخیره می‌شود (با تمام الگوهای `== "1"` و `!= "0"` در بات سازگار است).
"""

from typing import Any, Dict, List, Optional

SECRET_MASK = "••••••••"


def _b(key, label, default="0", hint=""):
    return {"key": key, "label": label, "type": "bool", "default": default, "hint": hint}


def _n(key, label, default="0", hint="", min=None, max=None):
    f = {"key": key, "label": label, "type": "number", "default": default, "hint": hint}
    if min is not None:
        f["min"] = min
    if max is not None:
        f["max"] = max
    return f


def _t(key, label, default="", hint=""):
    return {"key": key, "label": label, "type": "text", "default": default, "hint": hint}


def _ta(key, label, default="", hint=""):
    return {"key": key, "label": label, "type": "textarea", "default": default, "hint": hint}


def _pw(key, label, hint=""):
    return {"key": key, "label": label, "type": "password", "default": "", "hint": hint}


def _sel(key, label, options, default="", hint=""):
    return {"key": key, "label": label, "type": "select", "default": default, "hint": hint, "options": options}


EXTRA_SETTINGS_GROUPS: List[Dict[str, Any]] = [
    {
        "id": "terms", "title": "📜 قوانین و شرایط استفاده", "web": True, "mini": True,
        "fields": [
            _b("terms_enabled", "نمایش قوانین به کاربر جدید (و کاربرانی که هنوز نپذیرفته‌اند)", "0",
               "با روشن شدن، تا قوانین را نپذیرد کاربر به بقیه‌ی بات/مینی‌اپ دسترسی ندارد. متن قوانین نباید خالی باشد."),
            {**_ta("terms_text", "متن قوانین (حداکثر ۳۵۰۰ کاراکتر)", ""), "maxlen": 3500},
        ],
    },
    {
        "id": "signup_gift", "title": "🎁 هدیه‌ی ثبت‌نام (برای کاربرانی که خرید نکرده‌اند)", "web": True, "mini": True,
        "fields": [
            _b("signup_gift_enabled", "فعال بودن هدیه‌ی ثبت‌نام", "0", "مبلغ هدیه باید بزرگ‌تر از صفر باشد."),
            _n("signup_gift_amount", "مبلغ هدیه (تومان)", "0", min=0),
            _n("signup_gift_delay_days", "چند روز بعد از عضویت (بدون خرید) هدیه داده شود", "3", min=0),
        ],
    },
    {
        "id": "lottery", "title": "🎰 قرعه‌کشی و سکه", "web": True, "mini": True,
        "fields": [
            _b("score_enabled", "سیستم سکه", "1"),
            _b("lottery_enabled", "قرعه‌کشی", "1"),
            _b("lottery_agent_enabled", "قرعه‌کشی برای نماینده‌ها", "0"),
            _sel("lottery_prize_type", "نوع جایزه", [["wallet", "شارژ کیف پول (تومان)"], ["discount", "کد تخفیف (درصد)"]], "wallet"),
            _t("lottery_prizes", "جایزه‌ی رتبه‌ها (با ویرگول؛ تومان یا درصد)", "50000,30000,20000",
               "رتبه‌ی ۱ تا ۳ به ترتیب، مثلاً 50000,30000,20000"),
            _n("lottery_min_coins", "حداقل سکه برای ورود به قرعه‌کشی", "1", min=0),
            _n("lottery_discount_expiry_hours", "اعتبار کد تخفیف جایزه (ساعت)", "24", min=1),
            _t("lottery_report_chat_id", "آیدی گروه گزارش نتیجه (خالی = پیام خصوصی به ادمین‌ها)", ""),
        ],
    },
    {
        "id": "receipt_ai", "title": "🧾 بررسی هوشمند رسید (AI)", "web": True, "mini": True,
        "fields": [
            _b("receipt_ai_check_enabled", "بررسی رسید با هوش مصنوعی", "1"),
            _b("receipt_ai_auto_reject_enabled", "رد خودکار رسیدهای بسیار مشکوک", "1"),
            _b("receipt_ai_strict_mode", "حالت سخت‌گیرانه", "1"),
            _b("receipt_ai_multi_model_enabled", "بررسی چندمدلی (دقت بالاتر، هزینه‌ی بیشتر)", "1"),
            _n("receipt_ai_reject_weight_threshold", "آستانه‌ی وزنی رد خودکار", "1.5",
               "عدد کمتر = رد سریع‌تر؛ پیش‌فرض ۱.۵", min=0.1, max=10),
        ],
    },
    {
        "id": "backup", "title": "💾 بکاپ خودکار و SFTP", "web": True, "mini": True,
        "fields": [
            _n("backup_interval_hours", "فاصله‌ی بکاپ خودکار (ساعت)", "24", min=1, max=720),
            _t("backup_secondary_chat_id", "آیدی چت دوم برای ارسال نسخه‌ی بکاپ", ""),
            _b("backup_sftp_enabled", "ارسال بکاپ به سرور دوم با SFTP", "0"),
            _t("backup_sftp_host", "هاست SFTP", ""),
            _n("backup_sftp_port", "پورت SFTP", "22", min=1, max=65535),
            _t("backup_sftp_username", "یوزرنیم SFTP", ""),
            _pw("backup_sftp_password", "پسورد SFTP", "خالی = بدون تغییر. اگر از کلید استفاده می‌کنی خالی بگذار."),
            _t("backup_sftp_key_path", "مسیر کلید خصوصی روی سرور (اختیاری)", ""),
            _t("backup_sftp_remote_dir", "پوشه‌ی مقصد روی سرور دوم", "/root/vpn_backups"),
        ],
    },
    {
        "id": "smart_sub", "title": "🔗 اشتراک هوشمند (ادغام کانفیگ‌ها)", "web": True, "mini": True,
        "fields": [
            _b("smart_subscription_enabled", "فعال بودن لینک اشتراک هوشمند", "1"),
            _t("smart_subscription_base_url", "آدرس پایه‌ی لینک (خالی = آدرس API پیش‌فرض)", "",
               "مثلاً https://sub.example.com"),
        ],
    },
    {
        "id": "delivery", "title": "📦 نحوه‌ی تحویل کانفیگ", "web": True, "mini": True,
        "fields": [
            _b("deliver_sub_link_enabled", "ارسال لینک اشتراک (Subscription)", "1"),
            _b("deliver_individual_configs_enabled", "ارسال کانفیگ‌های تکی", "1"),
            _ta("post_delivery_custom_text", "متن سفارشی بعد از تحویل سرویس (خالی = ندارد)", ""),
            _ta("test_post_delivery_text", "متن سفارشی بعد از تحویل کانفیگ تست (خالی = همان متن عمومی)", ""),
            _t("custom_config_prefix", "پیشوند نام کانفیگ‌های سفارشی", ""),
        ],
    },
    {
        "id": "location", "title": "📍 تغییر لوکیشن سرویس", "web": True, "mini": True,
        "fields": [
            _n("location_change_user_limit", "سقف تغییر لوکیشن برای هر کاربر (۰ = نامحدود)", "0", min=0),
            _n("location_change_free_quota", "تعداد تغییر رایگان (۰ = بدون سهمیه‌ی رایگان)", "0", min=0),
        ],
    },
    {
        "id": "wallet", "title": "👛 کیف پول و کش‌بک", "web": True, "mini": True,
        "fields": [
            _b("wallet_show_transfer", "دکمه‌ی «انتقال موجودی به کاربر دیگر»", "1"),
            _n("topup_cashback_percent", "کش‌بک شارژ کیف پول (٪)", "0", min=0, max=100),
            _n("renewal_cashback_percent", "کش‌بک تمدید سرویس (٪)", "0", min=0, max=100),
        ],
    },
    {
        "id": "alerts", "title": "🔔 هشدار موجودی و کانال سرویس", "web": True, "mini": True,
        "fields": [
            _n("low_stock_threshold", "هشدار موجودی کم — وقتی موجودی محصول به این عدد یا کمتر رسید", "3", min=0),
            _t("service_alert_channel", "کانال اعلان وضعیت سرویس‌ها (مثلاً @channel)", ""),
            _t("support_admin_id", "آیدی ادمین پشتیبان پیش‌فرض (خالی = همه‌ی ادمین‌ها)", ""),
        ],
    },
    {
        "id": "cleanup", "title": "🧹 پاکسازی خودکار سرویس‌ها", "web": True, "mini": True,
        "fields": [
            _n("expired_delete_days", "حذف سرویس منقضی‌شده بعد از N روز (۰ = غیرفعال)", "0", min=0),
            _n("test_delete_days", "حذف سرویس تست بعد از N روز (۰ = غیرفعال)", "0", min=0),
            _n("expired_cleanup_warning_days", "اخطار قبل از حذف (روز)", "3", min=0),
            _b("expired_cleanup_dry_run", "حالت آزمایشی (فقط گزارش می‌دهد، حذف نمی‌کند)", "1",
               "قبل از خاموش کردن، گزارش آزمایشی را بررسی کن."),
            _t("inactive_config_delete_time", "ساعت اجرای حذف کانفیگ‌های غیرفعال (HH:MM، خالی = غیرفعال)", ""),
        ],
    },
    {
        "id": "referral_adv", "title": "🤝 زیرمجموعه‌گیری پیشرفته", "web": True, "mini": True,
        "fields": [
            _b("referral_button_enabled", "نمایش دکمه‌ی زیرمجموعه‌گیری برای کاربران", "1"),
            _b("referral_multilevel_enabled", "پورسانت چندسطحی (سطح ۲ و ۳)", "0"),
            _n("referral_level2_percent", "درصد پورسانت سطح ۲", "3", min=0, max=100),
            _n("referral_level3_percent", "درصد پورسانت سطح ۳", "1", min=0, max=100),
            _n("referral_min_purchase_amount", "حداقل مبلغ خرید برای محاسبه‌ی پورسانت (تومان)", "0", min=0),
            _b("referral_min_purchase_strict", "سخت‌گیرانه (زیر حداقل مبلغ، هیچ پورسانتی ندهد)", "0"),
            _n("referral_renewal_percent", "درصد پورسانت تمدید", "0", min=0, max=100),
            _n("referral_renewal_max_count", "سقف دفعات پورسانت تمدید (۰ = نامحدود)", "0", min=0),
        ],
    },
    {
        "id": "renewal_adv", "title": "🔄 قیمت‌گذاری تمدید و پیشنهاد ریزش", "web": True, "mini": True,
        "fields": [
            _n("renewal_price_per_gb", "قیمت هر گیگ در تمدید حجمی (تومان)", "0", min=0),
            _n("renewal_price_per_day", "قیمت هر روز در تمدید زمانی (تومان)", "0", min=0),
            _n("churn_offer_default_cycle_days", "چرخه‌ی پیش‌فرض خرید برای تحلیل ریزش (روز، حداقل ۷)", "30", min=7),
        ],
    },
    {
        "id": "currency", "title": "💱 ارز، کریپتو و کارت‌به‌کارت", "web": True, "mini": True,
        "fields": [
            _b("card_to_card_enabled", "پرداخت کارت‌به‌کارت", "1"),
            _n("usd_to_toman_rate", "نرخ دستی هر دلار (تومان) — ۰ = نرخ زنده", "0", min=0),
            _n("crypto_expire_min", "مهلت پرداخت کریپتو (دقیقه)", "80", min=5, max=1440),
            _t("crypto_allowed_currencies", "ارزهای مجاز کریپتو (با ویرگول، خالی = همه)", "", "مثلاً USDT_TRX,BTC,TRX"),
        ],
    },
    {
        "id": "provisioning", "title": "🛠 تحویل خودکار، تست و بازگشت وجه", "web": True, "mini": True,
        "fields": [
            _n("auto_provision_max_qty", "سقف تعداد در هر سفارش تحویل خودکار (۰ = بدون سقف)", "0", min=0),
            _n("custom_config_duration_days", "مدت کانفیگ سفارشی (روز)", "30", min=1),
            _n("test_config_panel_volume_gb", "حجم کانفیگ تست پنل (گیگ)", "1", min=0),
            _n("test_config_panel_duration_days", "مدت کانفیگ تست پنل (روز)", "1", min=0),
            _n("svc_refund_window_hours", "مهلت بازگشت وجه هنگام حذف سرویس (ساعت، ۰ = غیرفعال)", "24", min=0),
            _b("reseller_inline_commission_enabled", "کمیسیون نمایندگی در حالت اینلاین", "1"),
            _n("reseller_inline_commission_percent", "درصد کمیسیون پیش‌فرض نماینده‌های قدیمی", "10", min=0, max=100),
            _t("reseller_fixed_product_ids", "شناسه‌ی محصولات ثابت نماینده (با ویرگول)", ""),
        ],
    },
    {
        "id": "system", "title": "⚙️ سیستم و گزارش‌ها", "web": True, "mini": True,
        "fields": [
            _b("daily_report_enabled", "گزارش روزانه برای ادمین", "1"),
            _t("daily_report_time", "ساعت گزارش روزانه (HH:MM)", "23:45"),
            _b("panel_health_enabled", "پایش سلامت پنل‌ها", "1"),
            _b("spam_guard_enabled", "ضداسپم", "1"),
            _t("admin_panel_url", "آدرس عمومی پنل وب (برای لینک‌های داخل بات)", ""),
        ],
    },
    {
        "id": "translation_keys", "title": "🌐 کلید ترجمه‌ی خودکار", "web": True, "mini": True,
        "fields": [
            _pw("translation_gemini_api_key", "کلید(های) Gemini برای ترجمه", "هر خط یا هر ویرگول یک کلید؛ خالی = بدون تغییر"),
            _pw("translation_openrouter_api_key", "کلید(های) OpenRouter برای ترجمه", "خالی = بدون تغییر"),
        ],
    },
    # فقط مینی‌اپ: در پنل وب این‌ها فرم اختصاصی دارند.
    {
        "id": "churn", "title": "📉 پیشنهاد هوشمند ضدریزش", "web": False, "mini": True,
        "fields": [
            _b("churn_offer_enabled", "فعال بودن", "0"),
            _b("churn_offer_ai_enabled", "تصمیم‌گیری با AI", "1"),
            _n("churn_offer_min_score", "حداقل امتیاز ریزش (۱ تا ۱۰۰)", "60", min=1, max=100),
            _n("churn_offer_discount_percent", "درصد تخفیف پایه", "15", min=0, max=100),
            _n("churn_offer_max_discount_percent", "سقف درصد تخفیف", "25", min=0, max=100),
            _n("churn_offer_expiry_hours", "اعتبار کد (ساعت)", "72", min=1),
            _n("churn_offer_cooldown_days", "فاصله بین دو پیشنهاد به یک کاربر (روز)", "30", min=1),
            _n("churn_offer_max_per_run", "سقف پیشنهاد در هر اجرا", "20", min=1),
        ],
    },
    {
        "id": "force_join", "title": "📢 عضویت اجباری در کانال", "web": False, "mini": True,
        "fields": [
            _b("force_join_enabled", "عضویت اجباری", "0"),
            _t("force_join_channel", "آیدی کانال (مثلاً @mychannel)", ""),
        ],
    },
    {
        "id": "referral_fraud", "title": "🕵️ تشخیص تقلب در زیرمجموعه‌گیری", "web": False, "mini": True,
        "fields": [
            _b("referral_fraud_detection_enabled", "تشخیص فعال", "1"),
            _n("referral_fraud_burst_count", "تعداد زیرمجموعه‌ی تازه که یعنی دعوت انبوه", "5", min=1),
            _n("referral_fraud_burst_minutes", "بازه‌ی بررسی (دقیقه)", "60", min=1),
            _n("referral_fraud_min_invites", "حداقل دعوت برای معیار نرخ بی‌خریدی", "5", min=1),
            _n("referral_fraud_zero_purchase_ratio", "درصد زیرمجموعه‌ی بی‌خرید مشکوک", "80", min=1, max=100),
            _b("referral_fraud_auto_suspend", "توقف خودکار پاداش در صورت مشکوک بودن", "1"),
        ],
    },
]
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

# ----------------------------------------------------------------------------‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

_FIELD_INDEX: Dict[str, Dict[str, Any]] = {
    f["key"]: f for g in EXTRA_SETTINGS_GROUPS for f in g["fields"]
}


def groups_for(surface: str) -> List[Dict[str, Any]]:
    """surface: 'web' یا 'mini'"""
    return [g for g in EXTRA_SETTINGS_GROUPS if g.get(surface)]


def allowed_keys(surface: str) -> set:
    return {f["key"] for g in groups_for(surface) for f in g["fields"]}


def load_values(db, surface: str) -> Dict[str, Any]:
    """فرم‌ها را پر می‌کند. مقدار فیلد password هرگز برنمی‌گردد (فقط وضعیت set/unset)."""
    out: Dict[str, Any] = {}
    for g in groups_for(surface):
        for f in g["fields"]:
            raw = db.get_setting(f["key"], f["default"])
            raw = f["default"] if raw is None else str(raw)
            if f["type"] == "password":
                out[f["key"]] = SECRET_MASK if raw.strip() else ""
            elif f["type"] == "bool":
                out[f["key"]] = "1" if raw.strip() == "1" else "0"
            else:
                out[f["key"]] = raw
    return out


class SettingsValidationError(ValueError):
    pass


def _clean(field: Dict[str, Any], value: Any) -> Optional[str]:
    """مقدار را اعتبارسنجی/نرمال می‌کند. None یعنی «دست نزن» (برای password خالی)."""
    t = field["type"]
    if value is None:
        value = ""
    if isinstance(value, bool):
        value = "1" if value else "0"
    value = str(value)
    label = field["label"]
    if t == "password":
        value = value.strip()
        if value == "" or value == SECRET_MASK:
            return None
        return value
    if t == "bool":
        if value.strip() not in ("0", "1", "true", "false", "True", "False"):
            raise SettingsValidationError(f"مقدار «{label}» نامعتبر است.")
        return "1" if value.strip() in ("1", "true", "True") else "0"
    if t == "number":
        v = value.strip()
        if v == "":
            return str(field["default"])
        try:
            num = float(v.replace(",", ""))
        except ValueError:
            raise SettingsValidationError(f"«{label}» باید عدد باشد.")
        if num != num or num in (float("inf"), float("-inf")):
            raise SettingsValidationError(f"«{label}» نامعتبر است.")
        if "min" in field and num < field["min"]:
            raise SettingsValidationError(f"«{label}» نباید کمتر از {field['min']} باشد.")
        if "max" in field and num > field["max"]:
            raise SettingsValidationError(f"«{label}» نباید بیشتر از {field['max']} باشد.")
        return str(int(num)) if num == int(num) and "." not in str(field["default"]) else str(num)
    if t == "select":
        allowed = {o[0] for o in field.get("options", [])}
        if value not in allowed:
            raise SettingsValidationError(f"«{label}» نامعتبر است.")
        return value
    # text / textarea‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
    value = value.strip() if t == "text" else value.strip("\r\n ")
    if len(value) > int(field.get("maxlen", 8000)):
        raise SettingsValidationError(f"«{label}» بیش از حد طولانی است (حداکثر {field.get('maxlen', 8000)} کاراکتر).")
    return value


def _cross_check(values: Dict[str, str], db) -> None:
    """قواعد بین‌فیلدی که بات هم همان‌ها را اعمال می‌کند."""
    def cur(key):
        if key in values:
            return values[key]
        f = _FIELD_INDEX[key]
        v = db.get_setting(key, f["default"])
        return f["default"] if v is None else str(v)

    if "terms_enabled" in values or "terms_text" in values:
        if cur("terms_enabled") == "1" and not cur("terms_text").strip():
            raise SettingsValidationError("برای فعال‌سازی قوانین، متن قوانین نمی‌تواند خالی باشد.")
    if "signup_gift_enabled" in values or "signup_gift_amount" in values:
        if cur("signup_gift_enabled") == "1" and float(cur("signup_gift_amount") or 0) <= 0:
            raise SettingsValidationError("برای فعال‌سازی هدیه‌ی ثبت‌نام، مبلغ باید بزرگ‌تر از صفر باشد.")
    if "deliver_sub_link_enabled" in values or "deliver_individual_configs_enabled" in values:
        if cur("deliver_sub_link_enabled") == "0" and cur("deliver_individual_configs_enabled") == "0":
            raise SettingsValidationError("حداقل یکی از «لینک اشتراک» یا «کانفیگ تکی» باید فعال بماند.")
    if "backup_sftp_enabled" in values or "backup_sftp_host" in values:
        if cur("backup_sftp_enabled") == "1" and not cur("backup_sftp_host").strip():
            raise SettingsValidationError("برای فعال‌سازی SFTP، هاست الزامی است.")
    if "force_join_enabled" in values or "force_join_channel" in values:
        if cur("force_join_enabled") == "1" and not cur("force_join_channel").strip():
            raise SettingsValidationError("برای فعال‌سازی عضویت اجباری، آیدی کانال الزامی است.")
    if "lottery_prizes" in values:
        parts = [p.strip() for p in values["lottery_prizes"].split(",") if p.strip()]
        if not parts or not all(p.isdigit() and int(p) > 0 for p in parts):
            raise SettingsValidationError("جایزه‌ی رتبه‌ها باید اعداد مثبتِ جداشده با ویرگول باشد.")
        values["lottery_prizes"] = ",".join(str(int(p)) for p in parts)
    if "lottery_prize_type" in values or "lottery_prizes" in values:
        if cur("lottery_prize_type") == "discount":
            if any(int(p) > 100 for p in cur("lottery_prizes").split(",") if p.strip().isdigit()):
                raise SettingsValidationError("در حالت «کد تخفیف» جایزه‌ها درصد هستند و نباید از ۱۰۰ بیشتر باشند.")
    if "churn_offer_discount_percent" in values or "churn_offer_max_discount_percent" in values:
        if float(cur("churn_offer_discount_percent")) > float(cur("churn_offer_max_discount_percent")):
            raise SettingsValidationError("درصد تخفیف پایه نمی‌تواند از سقف تخفیف بیشتر باشد.")
    for k in ("inactive_config_delete_time", "daily_report_time"):
        if k in values and values[k]:
            import re
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", values[k]):
                raise SettingsValidationError("ساعت باید با قالب HH:MM باشد (مثلاً 23:45).")
    if "reseller_fixed_product_ids" in values and values["reseller_fixed_product_ids"]:
        if not all(p.strip().isdigit() for p in values["reseller_fixed_product_ids"].split(",") if p.strip()):
            raise SettingsValidationError("شناسه‌ی محصولات باید عدد و با ویرگول جدا شده باشند.")
    for k in ("force_join_channel", "service_alert_channel"):
        if k in values and values[k] and not (values[k].startswith("@") or values[k].lstrip("-").isdigit()
                                                or values[k].startswith("https://t.me/")):
            raise SettingsValidationError("آیدی کانال باید با @ شروع شود (یا آیدی عددی باشد).")
    for k in ("backup_secondary_chat_id", "lottery_report_chat_id", "support_admin_id"):
        if k in values and values[k] and not values[k].lstrip("-").isdigit():
            raise SettingsValidationError("این آیدی باید عددی باشد.")


def save_values(db, surface: str, payload: Dict[str, Any]) -> List[str]:
    """
    payload = {key: value}. فقط کلیدهای مجازِ همان سطح پذیرفته می‌شوند. همه‌چیز
    اول اعتبارسنجی می‌شود و بعد نوشته می‌شود (یا همه یا هیچ). لیست کلیدهای تغییرکرده را برمی‌گرداند.
    """
    allowed = allowed_keys(surface)
    cleaned: Dict[str, str] = {}
    for key, raw in (payload or {}).items():
        if key not in allowed:
            raise SettingsValidationError(f"کلید ناشناخته: {key}")
        v = _clean(_FIELD_INDEX[key], raw)
        if v is not None:
            cleaned[key] = v
    _cross_check(cleaned, db)
    changed = []
    for key, v in cleaned.items():
        old = db.get_setting(key, None)
        if old is None or str(old) != v:
            db.set_setting(key, v)
            changed.append(key)
    # همان اثر جانبی که ثبت متن قوانین از داخل بات دارد: زمان آخرین ویرایش (برای پذیرش مجدد)
    if "terms_text" in changed:
        from datetime import datetime
        db.set_setting("terms_updated_at", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
    return changed
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
