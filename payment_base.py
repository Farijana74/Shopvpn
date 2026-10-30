# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
هلپرهای مشترک ۷ درگاه پرداخت.

این ماژول هیچ رفتاری را عوض نمی‌کند؛ فقط توابعی که در همه‌ی *_payment.py ها
تکرار شده‌اند (تومان↔ریال، لاگ عمومی) را یک‌جا نگه می‌دارد تا فایل‌های
درگاه از آن reuse کنند. هر درگاه همچنان تابع‌های public خودش
(resolve_api_key, create_invoice_for, try_verify_and_finalize, finalize_*, …)
را با همان امضا و رفتار expose می‌کند — importهای موجود
(handlers_user.py, miniapp/server.py) بدون تغییر می‌مانند.

تفاوت‌های عمدی که در helper عمومی نمی‌روند:
- تبدیل مبلغ: ریال×۱۰ (abangateway/blupal) vs stars (noapay) vs USD (crypto)
- callback_url per-invoice (abangateway/noapay/crypto/extra/custom) vs
  webhook_url_hint سراسری (blupal)
- مرحله‌ی verify جدا (abangateway verify POST) vs بدون verify (blupal)
- احراز HMAC (noapay/plisio/custom) vs بدون امضا
- unique-amount (card_to_card)
"""
import logging

logger = logging.getLogger("payment_base")


def toman_to_rial(amount_toman: int) -> int:
    return int(amount_toman) * 10


def rial_to_toman(amount_rial: int) -> int:
    return int(amount_rial) // 10


def toman_to_irt(amount_toman: int) -> int:
    """Alias واضح‌تر برای toman_to_rial — بعضی API ها واحد را IRT می‌نامند."""
    return toman_to_rial(amount_toman)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


PAYMENT_RESULT_ALREADY_DELIVERED = "already_delivered"
PAYMENT_RESULT_VERIFIED_NOW = "verified_now"
PAYMENT_RESULT_NOT_PAID = "not_paid_yet"


def amounts_match(a: int, b: int, tolerance_rial: int = 0) -> bool:
    """مقایسه‌ی دو مبلغ با tolerance اختیاری (برای خطای گرد کردن ارز)."""
    return abs(int(a) - int(b)) <= tolerance_rial
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
