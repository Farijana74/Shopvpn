# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""رجیستری درگاه‌های پرداخت افزوده‌شده از میرزابات (فقط داده، بدون import سنگین)."""

GATEWAY_ORDER = ["zarinpal", "aqayepardakht", "tetra98", "cubepay", "nowpayments", "tgstars"]

GATEWAYS = {
    "zarinpal": {
        "title": "زرین‌پال",
        "icon": "🟡",
        "default_text": "🟡 پرداخت آنلاین با زرین‌پال (تایید آنی)",
        "ttl_minutes": 65,
        "needs_base_url": True,
        "fields": [
            {"setting": "zarinpal_merchant_id", "label": "مرچنت کد", "secret": True, "required": True, "numeric": False},
        ],
        "help": "مرچنت کد را از پنل زرین‌پال بگیر. مبلغ به تومان ارسال می‌شود.",
    },
    "aqayepardakht": {
        "title": "آقای پرداخت",
        "icon": "🔵",
        "default_text": "🔵 پرداخت آنلاین با آقای پرداخت (تایید آنی)",
        "ttl_minutes": 65,
        "needs_base_url": True,
        "fields": [
            {"setting": "aqayepardakht_pin", "label": "پین درگاه", "secret": True, "required": True, "numeric": False},
        ],
        "help": "پین درگاه را از پنل آقای پرداخت بگیر. دامنه‌ی برگشت باید با دامنه‌ی ثبت‌شده‌ی درگاه یکی باشد.",
    },
    "tetra98": {
        "title": "تترا۹۸",
        "icon": "💸",
        "default_text": "💸 پرداخت با تترا۹۸ (تایید آنی)",
        "ttl_minutes": 65,
        "needs_base_url": True,
        "fields": [
            {"setting": "tetra98_api_key", "label": "کلید API", "secret": True, "required": True, "numeric": False},
        ],
        "help": "ApiKey را از پنل فروشنده‌ی تترا۹۸ (منوی اطلاعات API) بگیر.",
    },
    "cubepay": {
        "title": "کیوب‌پی",
        "icon": "💳",
        "default_text": "💳 کارت‌به‌کارت خودکار کیوب‌پی (تایید آنی)",
        "ttl_minutes": 35,
        "needs_base_url": True,
        "fields": [
            {"setting": "cubepay_token", "label": "توکن API", "secret": True, "required": True, "numeric": False},
            {"setting": "cubepay_fee", "label": "کارمزد اضافه به مبلغ کاربر (درصد تا ۱۰۰، بالاتر یعنی مبلغ ثابت تومان)", "secret": False, "required": False, "numeric": True},
        ],
        "help": "توکن را از ربات @cubepy_bot بگیر. اگر کارمزد تعیین کنی به مبلغ قابل پرداخت کاربر اضافه می‌شود ولی شارژ/سفارش با مبلغ اصلی ثبت می‌شود.",
    },
    "nowpayments": {
        "title": "NowPayments",
        "icon": "💰",
        "default_text": "💰 پرداخت کریپتو با NowPayments (تایید آنی)",
        "ttl_minutes": 180,
        "needs_base_url": True,
        "fields": [
            {"setting": "nowpayments_api_key", "label": "کلید API", "secret": True, "required": True, "numeric": False},
            {"setting": "nowpayments_ipn_secret", "label": "IPN Secret (پیشنهادی)", "secret": True, "required": False, "numeric": False},
        ],
        "help": "کلید API و IPN Secret را از پنل NowPayments بگیر. آدرس IPN در فاکتور به‌صورت خودکار ارسال می‌شود. مبلغ با نرخ دلار به تومان (تنظیم کریپتو) به دلار تبدیل می‌شود.",
    },
    "tgstars": {
        "title": "استارز داخلی تلگرام",
        "icon": "⭐",
        "default_text": "⭐ پرداخت با استارز تلگرام (تایید آنی)",
        "ttl_minutes": 65,
        "needs_base_url": False,
        "fields": [
            {"setting": "tgstars_rate_toman_per_star", "label": "نرخ دستی هر استارز (تومان) - ۰ یعنی خودکار", "secret": False, "required": False, "numeric": True},
            {"setting": "tgstars_usd_per_star", "label": "ارزش هر استارز به دلار (نرخ خودکار)", "secret": False, "required": False, "numeric": True, "default": "0.013"},
            {"setting": "tgstars_margin_percent", "label": "درصد حاشیه سود روی نرخ خودکار", "secret": False, "required": False, "numeric": True},
        ],
        "help": (
            "پرداخت مستقیم داخل خود تلگرام با Telegram Stars. تعداد استارز = مبلغ تقسیم بر نرخ هر استارز (رو به بالا).\n"
            "حالت خودکار (پیش‌فرض، وقتی «نرخ دستی» صفر باشد): نرخ لحظه‌ای دلار × ارزش هر استارز به دلار × (۱ + حاشیه سود٪) "
            "هنگام ساخت هر فاکتور محاسبه می‌شود. اگر یک عدد بزرگ‌تر از صفر در «نرخ دستی» بگذاری، همان ثابت استفاده می‌شود."
        ),
    },
}


def enable_setting(key: str) -> str:
    return f"{key}_payment_enabled"
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


def min_amount_setting(key: str) -> str:
    return f"min_amount_{key}"


def default_settings() -> dict:
    out = {}
    for key in GATEWAY_ORDER:
        out[enable_setting(key)] = "0"
        for field in GATEWAYS[key]["fields"]:
            out[field["setting"]] = field.get("default", "0" if field["numeric"] else "")
        out[min_amount_setting(key)] = "0"
    return out


def settings_groups() -> list:
    """گروه‌های فرم تنظیمات (پنل وب/اپ موبایل) برای همه‌ی درگاه‌های این رجیستری."""
    groups = []
    for key in GATEWAY_ORDER:
        meta = GATEWAYS[key]
        fields = [{"key": enable_setting(key), "label": f"فعال بودن درگاه {meta['title']}", "type": "bool"}]
        for field in meta["fields"]:
            kind = "password" if field["secret"] else ("number" if field["numeric"] else "text")
            fields.append({"key": field["setting"], "label": field["label"], "type": kind})
        groups.append({"title": f"{meta['icon']} {meta['title']} (تایید آنی)", "fields": fields})
    return groups
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
