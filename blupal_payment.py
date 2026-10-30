# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
منطق مشترک ساخت/بررسی فاکتور پرداخت کارت‌به‌کارت خودکار بلوپال که هم از سرور
مینی‌اپ (miniapp/server.py، برای دریافت وب‌هوک) و هم مستقیم از داخل بات
(handlers_user.py، برای ساخت فاکتور و بررسی دستی وضعیت) قابل استفاده است.

نکته درباره‌ی مبلغ: بقیه‌ی پروژه مبالغ را به «تومان» نگه می‌دارد؛ API بلوپال
مبلغ را به «ریال» می‌خواهد (۱ تومان = ۱۰ ریال). تبدیل این‌جا انجام می‌شود.

نکته درباره‌ی وب‌هوک: مثل الگوی دفاعی abangateway_payment، این ماژول به بدنه‌ی
وب‌هوک اعتماد نمی‌کند؛ فقط از آن برای پیدا کردن invoice_id استفاده می‌کند و سپس
با فراخوانی مستقیم API (با کلید API خودمان که بدنه‌ی وب‌هوک نمی‌تواند جعل کند)
وضعیت واقعی فاکتور را استعلام می‌کند. تابع try_verify_and_finalize منبع حقیقت
است و هم از مسیر وب‌هوک و هم از مسیر «بررسی دستی وضعیت» در بات صدا زده می‌شود.

نکته درباره‌ی وب‌هوک بلوپال (تفاوت با آبان گیت وی): بلوپال callback_url را
به‌ازای هر فاکتور نمی‌گیرد؛ آدرس وب‌هوک یک‌بار در داشبورد بلوپال (تنظیمات همان
API Key) ثبت می‌شود. برای پشتیبانی از چند فروشگاه (تننت) با یک کلید مشترک،
باید آدرس وب‌هوک را به شکل ".../api/webhooks/blupal?b=<tenant_id>" در داشبورد
بلوپال وارد کرد؛ در صورت استفاده از کلید API جدا برای هر فروشگاه، هرکدام آدرس
وب‌هوک تننت خودشان را ثبت می‌کنند. تابع webhook_url_hint فقط برای نمایش این
آدرس پیشنهادی به ادمین است (چیزی را خودکار ثبت نمی‌کند).
"""

import logging

from config import BLUPAL_API_KEY, API_BASE_URL
import blupal_client
from config_delivery import deliver_config_to_user
from panel_providers import get_provider
from reseller_auto_provision import provision_auto_config, ProvisionError
from direct_panel_provision import provision_direct, ProvisionError as DirectProvisionError
from stock_alerts import check_and_notify_low_stock
from renewal_engine import execute_renewal, RenewalError

logger = logging.getLogger("blupal_payment")

# نگاشت وضعیت‌های حروف‌بزرگ API بلوپال به همان الگوی داخلی lowercase که بقیه‌ی
# درگاه‌های این پروژه (abangateway/crypto/noapay) استفاده می‌کنند.‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
_REMOTE_STATUS_MAP = {
    "PENDING": "pending",
    "PAID": "paid",
    "EXPIRED": "expired",
    "CANCELED": "cancelled",
}
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


class BluPalPaymentError(Exception):
    """خطای قابل‌نمایش به کاربر/ادمین در فلوی پرداخت بلوپال."""
    pass


def resolve_api_key(db) -> str:
    """کلید API را برمی‌گرداند: اولویت با کلیدی است که ادمین از داخل بات برای همین
    فروشگاه (تننت) تنظیم کرده؛ در غیر این صورت کلید سراسری .env."""
    return db.get_setting("blupal_api_key", "") or BLUPAL_API_KEY


def resolve_api_key_source(db) -> str:
    """برای دیباگ/نمایش در پنل ادمین: کلید از کجا آمده؟"""
    if db.get_setting("blupal_api_key", ""):
        return "db"
    if BLUPAL_API_KEY:
        return "env"
    return "none"


def blupal_payment_available(db) -> bool:
    return (
        db.get_setting("blupal_payment_enabled", "0") == "1"
        and bool(resolve_api_key(db))
    )


from payment_base import toman_to_rial, rial_to_toman  # noqa: F401


def webhook_url_hint(tenant_id: str) -> str:
    """آدرسی که ادمین باید در داشبورد بلوپال (تنظیمات API Key → Webhook URL)
    وارد کند، فقط برای نمایش؛ چیزی را خودکار در بلوپال ثبت نمی‌کند."""
    if not API_BASE_URL:
        return ""
    return f"{API_BASE_URL}/api/webhooks/blupal?b={tenant_id or ''}"


async def create_invoice_for(db, tenant_id: str, tg_id: int, kind: str, ref_id: int,
                              amount_toman: int, order_name: str) -> dict:
    """یک فاکتور بلوپال برای سفارش (kind='order') یا شارژ کیف پول (kind='wallet_topup')
    می‌سازد و آن را در جدول blupal_invoices ثبت می‌کند.
    خروجی: {"payment_url": ..., "invoice_id": ...}
    در صورت خطا BluPalPaymentError صادر می‌شود."""
    api_key = resolve_api_key(db)
    if not api_key:
        raise BluPalPaymentError(
            "درگاه بلوپال هنوز تنظیم نشده. از پنل مدیریت، «تنظیم درگاه بلوپال» را بزن."
        )
    if db.get_setting("blupal_payment_enabled", "0") != "1":
        raise BluPalPaymentError("پرداخت بلوپال برای این فروشگاه فعال نیست.")

    existing = db.get_pending_blupal_invoice_for_ref(kind, ref_id)
    if existing:
        return {"payment_url": existing["payment_url"], "invoice_id": existing["invoice_id"]}

    amount_rial = toman_to_rial(amount_toman)
    try:
        data = await blupal_client.create_invoice(api_key=api_key, amount_rial=amount_rial)
    except blupal_client.BluPalError as e:
        raise BluPalPaymentError(f"خطا از درگاه پرداخت: {e}")

    invoice_id = data.get("invoice_id")
    if invoice_id is None:
        raise BluPalPaymentError("پاسخ درگاه پرداخت ناقص بود (بدون شناسه‌ی فاکتور).")

    payment_url = data.get("payment_link")
    db.create_blupal_invoice(
        invoice_id=str(invoice_id), kind=kind, ref_id=ref_id, user_id=tg_id,
        amount_toman=amount_toman, amount_rial=amount_rial,
        final_amount_rial=data.get("final_amount"),
        payment_url=payment_url,
    )
    return {"payment_url": payment_url, "invoice_id": str(invoice_id)}


async def try_verify_and_finalize(db, invoice_row) -> str:
    """منبع حقیقت برای تأیید یک فاکتور بلوپال. بدون توجه به این‌که از کجا صدا زده
    شده (وب‌هوک یا دکمه‌ی «بررسی وضعیت» در بات)، وضعیت واقعی را از خودِ API استعلام
    می‌کند. برخلاف آبان گیت وی، بلوپال مرحله‌ی «verify» جدا ندارد؛ همین که وضعیت
    PAID باشد، یک‌بار (با claim روی رکورد خودمان) تحویل انجام می‌شود.

    خروجی یکی از این مقادیر است:
      'already_delivered' - قبلاً تحویل داده شده؛ کاری نکن
      'verified_now'      - همین الان تأیید شد؛ باید سفارش/شارژ را تحویل بدهی
      'not_paid_yet'       - هنوز واریزی تشخیص داده نشده
      'expired' / 'cancelled' - فاکتور دیگر معتبر نیست
      شروع‌شونده با 'error:' - خطای ارتباط با درگاه
    """
    invoice_id = invoice_row["invoice_id"]
    if invoice_row["status"] == "completed":
        return "already_delivered"

    api_key = resolve_api_key(db)
    if not api_key:
        return "error:کلید API بلوپال تنظیم نشده است."

    try:
        remote = await blupal_client.get_invoice(api_key, invoice_id)
    except blupal_client.BluPalError as e:
        return f"error:{e}"

    remote_status = _REMOTE_STATUS_MAP.get(remote.get("status"), None)

    if remote_status in ("expired", "cancelled"):
        db.update_blupal_invoice_status(invoice_id, remote_status)
        return remote_status

    if remote_status != "paid":
        return "not_paid_yet"

    # علامت‌گذاری یک‌بارمصرف: اگر بین لحظه‌ی خواندن وضعیت و این‌جا، یک مسیر دیگر
    # (مثلاً وب‌هوک) هم‌زمان همین فاکتور را completed کرده باشد، claim دومی هیچ‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
    # ردیفی را تغییر نمی‌دهد و ما آن را already_delivered در نظر می‌گیریم.
    if not db.claim_blupal_invoice(invoice_id):
        return "already_delivered"

    return "verified_now"


from payment_delivery import finalize_paid_order, finalize_paid_topup  # noqa: F401



def extract_invoice_id_from_webhook(body: dict) -> str:
    """شناسه‌ی فاکتور را از بدنه‌ی وب‌هوک بلوپال استخراج می‌کند. خودِ محتوای بدنه
    هرگز به‌عنوان منبع حقیقتِ وضعیت پرداخت استفاده نمی‌شود (نگاه کن به
    try_verify_and_finalize) - فقط برای پیدا کردن شناسه است."""
    val = body.get("invoice_id")
    if val is not None:
        return str(val)
    return None
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
