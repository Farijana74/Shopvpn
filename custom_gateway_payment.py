# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
منطق مشترک ساخت فاکتور «درگاه‌های پرداخت سفارشی/پویا» (payment_engine.GenericGateway)
که هم از سرور مینی‌اپ (miniapp/server.py، برای مدیریت و دریافت وب‌هوک) و هم
مستقیم از داخل بات اصلی (handlers_user.py، برای ساخت فاکتور از همان لحظه‌ی
خرید) قابل استفاده است.

نکته: وب‌هوک/بازگشت این درگاه‌ها (تکمیل نهایی سفارش/شارژ کیف‌پول) کاملاً در
سمت miniapp/server.py و بر اساس رکورد دیتابیس مدیریت می‌شود؛ برای آن مهم
نیست فاکتور از داخل بات ساخته شده یا از مینی‌اپ، پس این‌جا فقط «ساخت فاکتور»
پیاده‌سازی شده است.
"""

import json
import logging
import secrets
from datetime import datetime, timezone

from config import API_BASE_URL
import payment_engine
import crypto_payment

logger = logging.getLogger("custom_gateway_payment")
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


class CustomGatewayPaymentError(Exception):
    """خطای قابل‌نمایش به کاربر/ادمین در فلوی یک درگاه سفارشی."""
    pass


async def compute_send_amount(db, config: dict, amount_toman: int) -> dict:
    """مبلغ نهایی/ارزی که باید به {amount} این درگاه پاس داده بشه رو، بر اساس
    تنظیمات خودِ همین درگاه (config)، از روی مبلغ واقعی تومانیِ سفارش محاسبه
    می‌کنه. سه تنظیم قابل‌ترکیب:

    - amount_multiplier: ضریبی که مبلغ نهایی (بعد از تبدیل واحد/ارز) در آن
      ضرب می‌شود (پیش‌فرض ۱ - بدون تغییر). برای هر دو حالت ریالی/تومانی و
      ارزی/دلاری جدا قابل تنظیم است (چون هرکدام روی مبلغِ حالت خودشان اعمال
      می‌شود، نه روی یک مبلغ مشترک).
    - amount_currency: 'toman' (پیش‌فرض) | 'rial' | 'usd'
        * toman/rial فقط واحد شمارشِ همون مبلغ تومانی سفارش‌اند (rial = toman × ۱۰)
        * usd یعنی درگاه ارزی است: مبلغ بر اساس نرخ دلار تنظیم‌شده در
          «usd_to_toman_rate» (همون نرخی که برای پرداخت کریپتو هم استفاده
          می‌شود) به دلار تبدیل می‌شود و ضریب روی همون مبلغ دلاری اعمال می‌شود.

    خروجی: {"amount": <عدد ارسالی به {amount}>, "currency": <کد ارز برای {currency}>}
    توجه: amount_toman خودِ سفارش (بدون ضریب/تبدیل) هميشه جدا و دست‌نخورده به
    {amount_toman} پاس داده می‌شود و همان هم مبنای مقایسه‌ی مبلغ در verify/
    webhook (payment_engine.amounts_match) باقی می‌ماند."""
    try:
        multiplier = float(config.get("amount_multiplier") or 1)
    except (TypeError, ValueError):
        multiplier = 1.0
    if multiplier <= 0:
        multiplier = 1.0

    currency_mode = (config.get("amount_currency") or "toman").strip().lower()

    if currency_mode == "usd":
        try:
            usd = await crypto_payment.toman_to_usd(db, amount_toman)
        except crypto_payment.CryptoPaymentError as e:
            raise CustomGatewayPaymentError(str(e))
        return {"amount": round(usd * multiplier, 2), "currency": "USD"}

    if currency_mode == "rial":
        return {"amount": round(amount_toman * 10 * multiplier), "currency": "IRR"}

    return {"amount": round(amount_toman * multiplier), "currency": "IRT"}


def list_enabled_gateways(db):
    """لیست درگاه‌های سفارشی فعال، برای نمایش به‌عنوان دکمه‌ی روش پرداخت در بات."""
    if not API_BASE_URL:
        return []
    rows = db.list_custom_gateways(only_enabled=True)
    return [{"id": r["id"], "key": r["gateway_key"], "name": r["name"]} for r in rows]


def custom_gateway_payment_available(db) -> bool:
    return bool(list_enabled_gateways(db))


def _load_gateway(db, gateway_key: str):
    row = db.get_custom_gateway_by_key(gateway_key)
    if not row:
        raise CustomGatewayPaymentError("این درگاه پیدا نشد.")
    if not row["enabled"]:
        raise CustomGatewayPaymentError("این درگاه فعال نیست.")
    try:
        config = json.loads(row["config_json"])
    except Exception:
        config = {}
    return row, config


def gateway_requires_phone(db, gateway_key: str) -> bool:
    """آیا این درگاه سفارشی برای ساخت فاکتور به شماره موبایل مشتری نیاز دارد
    (پلیس‌هولدر {customer_phone})؟ این فقط برای تصمیم‌گیری در بات (نمایش یا
    عدم‌نمایش مرحله‌ی «اشتراک‌گذاری شماره») استفاده می‌شود؛ اگر درگاه پیدا
    نشود/غیرفعال باشد False برمی‌گردد (بررسی در دسترس بودن درگاه جای دیگری
    انجام می‌شود)."""
    try:
        _, config = _load_gateway(db, gateway_key)
    except CustomGatewayPaymentError:
        return False
    return bool(config.get("require_customer_phone"))


async def create_invoice_for(db, tenant_id: str, tg_id: int, gateway_key: str, kind: str,
                              ref_id: int, amount_toman: int, order_name: str,
                              customer_phone: str = None) -> dict:
    """یک فاکتور برای سفارش (kind='order') یا شارژ کیف پول (kind='wallet_topup') با
    درگاه سفارشی gateway_key می‌سازد و آن را در جدول custom_gateway_invoices ثبت می‌کند.
    خروجی: {"invoice_url": ..., "txn_id": ...}
    در صورت خطا CustomGatewayPaymentError صادر می‌شود.

    customer_phone: اگر درگاه در تنظیماتش «نیاز به شماره موبایل مشتری» را فعال
    کرده باشد (gateway_requires_phone)، شماره‌ای که از کاربر با دکمه‌ی
    اشتراک‌گذاری شماره گرفته شده اینجا پاس داده می‌شود؛ در غیر این صورت None/خالی.
    صرف‌نظر از این، آیدی عددی تلگرام کاربر همیشه و خودکار (بدون نیاز به پرسیدن
    از کاربر) به‌عنوان customer_user_id در دسترس قرار می‌گیرد."""
    if not API_BASE_URL:
        raise CustomGatewayPaymentError("آدرس مینی‌اپ (MINIAPP_URL) روی سرور تنظیم نشده است.")

    row, config = _load_gateway(db, gateway_key)

    existing = db.get_pending_custom_gateway_invoice_for_ref(row["id"], kind, ref_id)
    if existing:
        return {"invoice_url": existing["invoice_url"], "txn_id": existing["txn_id"]}

    tenant_slug = tenant_id or "main"
    # نکته‌ی امنیتی: یک قطعه‌ی تصادفی (نه فقط ref_id/زمان که برای خودِ کاربر
    # قابل‌حدس است) به txn_id داخلی اضافه می‌شود تا وقتی webhook_auth یک درگاه
    # روی "none" تنظیم شده (بعضی API‌ها اصلاً امضا/رمز پشتیبانی نمی‌کنند)، کاربر
    # نتواند با حدس‌زدن txn خودش، وب‌هوک را دستی صدا بزند و پرداخت جعلی ثبت کند.
    our_ref = f"{kind}-{tenant_slug}-{ref_id}-{int(datetime.now(timezone.utc).timestamp())}-{secrets.token_hex(6)}"
    # برخی درگاه‌ها (مثل TonPays) سقف طول کاراکتر برای order_id دارند (مثلاً حداکثر‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
    # ۲۰ کاراکتر)؛ چون ردیابی واقعی سفارش از طریق gateway_ref (شناسه‌ای که خودِ
    # درگاه در پاسخ create_invoice برمی‌گرداند) انجام می‌شود نه با پارس order_id،
    # اینجا یک نسخه‌ی کوتاه‌شده و یکتا فقط برای ارسال به درگاه می‌سازیم؛ our_ref
    # کامل همچنان برای callback_url/webhook_url و ذخیره‌ی txn_id داخلی حفظ می‌شود.‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
    short_order_id = f"{ref_id}-{int(datetime.now(timezone.utc).timestamp())}"[:20]
    computed = await compute_send_amount(db, config, amount_toman)
    gw = payment_engine.GenericGateway(config)
    try:
        result = await gw.create_invoice(
            amount=computed["amount"], amount_toman=amount_toman, order_id=short_order_id,
            currency=computed["currency"], description=order_name, tenant_id=tenant_slug,
            callback_url=f"{API_BASE_URL}/api/pay/custom/{gateway_key}/return?b={tenant_id or ''}&txn={our_ref}",
            webhook_url=f"{API_BASE_URL}/api/webhooks/custom/{gateway_key}?b={tenant_id or ''}",
            # همیشه در دسترس، بدون نیاز به پرسیدن از کاربر (مستقیم از پروفایل تلگرام):
            customer_user_id=str(tg_id),
            # فقط وقتی درگاه require_customer_phone را فعال کرده باشد پر می‌شود؛ در
            # غیر این صورت رشته‌ی خالی (تا در template‌های بدون این پلیس‌هولدر بی‌اثر باشد):
            customer_phone=customer_phone or "",
        )
    except payment_engine.PaymentEngineError as e:
        raise CustomGatewayPaymentError(str(e))

    invoice_id = db.create_custom_gateway_invoice(
        row["id"], our_ref, kind, ref_id, tg_id, amount_toman, invoice_url=result.get("invoice_url"),
    )
    if result.get("txn_id") and result.get("txn_id") != our_ref:
        db.set_custom_gateway_invoice_gateway_ref(invoice_id, result.get("txn_id"))
    return {"invoice_url": result.get("invoice_url"), "txn_id": our_ref}
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
