# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
کلاینت سبک برای درگاه پرداخت کارت‌به‌کارت بلوپال (https://www.blupal.net)
ساخت فاکتور و استعلام وضعیت.
مستندات: https://www.blupal.net/documentation

نکته‌ی مهم درباره‌ی مبلغ: API بلوپال مبلغ را فقط به ریال (عدد صحیح) قبول می‌کند،
اما بقیه‌ی این پروژه مبالغ را به تومان نگه می‌دارد. تبدیل (ضرب/تقسیم بر ۱۰) وظیفه‌ی
ماژول blupal_payment.py است، نه این فایل؛ این فایل فقط با ریال کار می‌کند.

نکته درباره‌ی وب‌هوک: بلوپال برخلاف بعضی درگاه‌های دیگر، برای هر فاکتور به‌صورت
جداگانه callback_url نمی‌گیرد؛ آدرس وب‌هوک یک‌بار در تنظیمات همان API Key (از
داشبورد بلوپال) ثبت می‌شود و برای همه‌ی فاکتورهای ساخته‌شده با آن کلید استفاده
می‌شود.
"""

import logging

import asyncio
import aiohttp

BLUPAL_BASE_URL = "https://www.blupal.net/api"
logger = logging.getLogger("blupal")
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


class BluPalError(Exception):
    """خطای عمومی از سمت بلوپال. code همان مقدار فیلد error در پاسخ است (ممکن است None باشد)."""

    def __init__(self, message: str, code: str = None, http_status: int = None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _headers(api_key: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }


async def _request(method: str, api_key: str, path: str, json_body: dict = None) -> dict:
    if not api_key:
        raise BluPalError("کلید API بلوپال تنظیم نشده است.")

    url = f"{BLUPAL_BASE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, headers=_headers(api_key), json=json_body, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    data = {}
                status = resp.status
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning("خطای شبکه در ارتباط با بلوپال: %s", e)
        raise BluPalError(f"خطای شبکه در ارتباط با درگاه پرداخت: {e}")

    if status >= 400 or data.get("success") is False:
        code = data.get("error")
        message = data.get("message") or "خطای نامشخص از بلوپال"
        logger.warning("خطای بلوپال (%s): %s - %s", status, code, message)
        raise BluPalError(message, code=code, http_status=status)

    return data


async def create_invoice(api_key: str, amount_rial: int, card_number: str = None) -> dict:
    """یک فاکتور می‌سازد و دیکشنری کامل پاسخ (شامل invoice_id، payment_link،
    final_amount، card_number و ...) را برمی‌گرداند."""
    body = {"amount": int(amount_rial)}
    if card_number:
        body["card_number"] = card_number
    return await _request("POST", api_key, "/v1/invoices/create", json_body=body)


async def get_invoice(api_key: str, invoice_id) -> dict:
    """وضعیت فعلی فاکتور را برمی‌گرداند (PENDING/PAID/EXPIRED/CANCELED)."""
    return await _request("GET", api_key, f"/v1/invoices/{invoice_id}")
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
