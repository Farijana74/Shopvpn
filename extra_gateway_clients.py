# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""کلاینت‌های سبک درگاه‌های زرین‌پال، آقای پرداخت، تترا۹۸، کیوب‌پی و NowPayments."""

import asyncio
import hashlib
import hmac
import json
import logging

import aiohttp

logger = logging.getLogger("extra_gateway_clients")

ZARINPAL_BASE = "https://payment.zarinpal.com/pg/v4/payment"
ZARINPAL_STARTPAY = "https://www.zarinpal.com/pg/StartPay"
AQAYE_BASE = "https://panel.aqayepardakht.ir"
TETRA98_BASE = "https://tetra98.com"
CUBEPAY_BASE = "https://cubevps.ir/smspay/api"
NOWPAYMENTS_BASE = "https://api.nowpayments.io/v1"

PAID = "paid"
PENDING = "pending"
FAILED = "failed"
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍


class GatewayError(Exception):
    """خطای قابل‌نمایش از سمت درگاه یا شبکه."""


async def _request(method: str, url: str, headers: dict = None, json_body: dict = None, timeout: int = 25):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, headers=headers, json=json_body, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {}
                return resp.status, data if isinstance(data, dict) else {}
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning("خطای شبکه در ارتباط با %s: %s", url, e)
        raise GatewayError(f"خطای شبکه در ارتباط با درگاه پرداخت: {e}")


async def zarinpal_create(merchant_id: str, amount_toman: int, callback_url: str, description: str, order_number: str) -> dict:
    status, data = await _request("POST", f"{ZARINPAL_BASE}/request.json", json_body={
        "merchant_id": merchant_id,
        "currency": "IRT",
        "amount": int(amount_toman),
        "callback_url": callback_url,
        "description": str(description)[:500],
        "metadata": {"order_id": order_number},
    })
    body = data.get("data")
    if isinstance(body, dict) and body.get("code") == 100 and body.get("authority"):
        authority = str(body["authority"])
        return {"remote_id": authority, "payment_url": f"{ZARINPAL_STARTPAY}/{authority}", "meta": {}}
    err = data.get("errors")
    if isinstance(err, dict):
        raise GatewayError(f"زرین‌پال: {err.get('message') or 'خطای نامشخص'} (کد {err.get('code')})")
    raise GatewayError(f"زرین‌پال پاسخ نامعتبر داد (HTTP {status}).")


async def zarinpal_verify(merchant_id: str, amount_toman: int, authority: str) -> str:
    status, data = await _request("POST", f"{ZARINPAL_BASE}/verify.json", json_body={
        "merchant_id": merchant_id,
        "amount": int(amount_toman),
        "authority": authority,
    })
    body = data.get("data")
    if isinstance(body, dict) and body.get("code") in (100, 101):
        return PAID
    err = data.get("errors")
    code = err.get("code") if isinstance(err, dict) else None
    if code == -51:
        return PENDING
    if code in (-54, -53):
        return FAILED
    if code is None and status < 500:
        return PENDING
    raise GatewayError(f"زرین‌پال: خطا در تایید (کد {code}, HTTP {status}).")


async def aqayepardakht_create(pin: str, amount_toman: int, callback_url: str, description: str, order_number: str) -> dict:
    status, data = await _request("POST", f"{AQAYE_BASE}/api/v2/create", json_body={
        "pin": pin,
        "amount": int(amount_toman),
        "callback": callback_url,
        "invoice_id": order_number,
        "description": str(description)[:200],
    })
    if data.get("status") == "success" and data.get("transid"):
        transid = str(data["transid"])
        return {"remote_id": transid, "payment_url": f"{AQAYE_BASE}/startpay/{transid}", "meta": {}}
    raise GatewayError(f"آقای پرداخت: خطا در ساخت تراکنش (کد {data.get('code')}, HTTP {status}).")


async def aqayepardakht_verify(pin: str, amount_toman: int, transid: str) -> str:
    status, data = await _request("POST", f"{AQAYE_BASE}/api/v2/verify", json_body={
        "pin": pin,
        "amount": int(amount_toman),
        "transid": transid,
    })
    code = str(data.get("code")) if data.get("code") is not None else ""
    if code in ("1", "2") and status == 200:
        return PAID
    if code in ("0", ""):
        if status >= 500:
            raise GatewayError(f"آقای پرداخت: خطای سرور (HTTP {status}).")
        return PENDING
    if code == "-8":
        return FAILED
    raise GatewayError(f"آقای پرداخت: خطا در تایید (کد {code}).")


async def tetra98_create(api_key: str, amount_toman: int, callback_url: str, description: str, order_number: str) -> dict:
    status, data = await _request("POST", f"{TETRA98_BASE}/api/create_order", json_body={
        "ApiKey": api_key,
        "Hash_id": order_number,
        "Amount": int(amount_toman) * 10,
        "Description": str(description)[:200],
        "CallbackURL": callback_url,
    })
    authority = data.get("Authority")
    if str(data.get("status")) == "100" and authority:
        return {
            "remote_id": str(authority),
            "payment_url": data.get("payment_url_web") or f"{TETRA98_BASE}/payment/{authority}",
            "meta": {"bot_url": data.get("payment_url_bot") or ""},
        }
    raise GatewayError(f"تترا۹۸: {data.get('message') or 'خطا در ساخت سفارش'} (HTTP {status}).")


async def tetra98_verify(api_key: str, authority: str) -> str:
    status, data = await _request("POST", f"{TETRA98_BASE}/api/verify", json_body={
        "ApiKey": api_key,
        "authority": authority,
    })
    if status == 200 and str(data.get("status")) == "100":
        return PAID
    if status >= 500:
        raise GatewayError(f"تترا۹۸: خطای سرور (HTTP {status}).")
    return PENDING


def cubepay_payable_toman(amount_toman: int, fee: float) -> int:
    base = int(amount_toman)
    if fee <= 0:
        return base
    if fee <= 100:
        return int(-(-base * (100 + fee) // 100))
    return base + int(round(fee))


async def cubepay_create(token: str, payable_toman: int, callback_url: str, description: str,
                         order_number: str, customer_user_id: int) -> dict:
    status, data = await _request(
        "POST", f"{CUBEPAY_BASE}/create-payment.php",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json_body={
            "amount": int(payable_toman) * 10,
            "order_id": order_number,
            "callback_url": callback_url,
            "type": "card",
            "customer_user_id": str(customer_user_id),
            "description": str(description)[:200],
        },
    )
    if data.get("success") and data.get("authority"):
        link = data.get("payment_link") or data.get("pay_page_url")
        if not link:
            raise GatewayError("کیوب‌پی: لینک پرداخت در پاسخ نبود.")
        return {
            "remote_id": str(data["authority"]),
            "payment_url": link,
            "meta": {"pay_amount_toman": data.get("pay_amount_toman")},
        }
    raise GatewayError(f"کیوب‌پی: {data.get('message') or 'خطا در ساخت تراکنش'} (HTTP {status}).")


async def cubepay_verify(token: str, authority: str, order_number: str, expected_rial: int) -> str:
    status, data = await _request(
        "POST", f"{CUBEPAY_BASE}/verify-payment.php",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json_body={"authority": authority},
    )
    if status in (200, 409):
        if data.get("order_id") is not None and str(data.get("order_id")) != str(order_number):
            raise GatewayError("کیوب‌پی: شناسه‌ی سفارش در پاسخ تایید با فاکتور مطابقت ندارد.")
        if data.get("amount") is not None:
            try:
                if int(data["amount"]) < int(expected_rial):
                    raise GatewayError("کیوب‌پی: مبلغ پرداخت‌شده کمتر از مبلغ فاکتور است.")
            except (TypeError, ValueError):
                pass
        if status == 409 or data.get("success"):
            return PAID
    if status == 402:
        return PENDING
    if status in (404, 410):
        return FAILED
    if status >= 500:
        raise GatewayError(f"کیوب‌پی: خطای سرور (HTTP {status}).")
    return PENDING


async def nowpayments_create_invoice(api_key: str, price_usd: float, order_number: str, description: str,
                                     ipn_url: str) -> dict:
    status, data = await _request(
        "POST", f"{NOWPAYMENTS_BASE}/invoice",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json_body={
            "price_amount": price_usd,
            "price_currency": "usd",
            "order_id": order_number,
            "order_description": str(description)[:200],
            "ipn_callback_url": ipn_url,
        },
    )
    if data.get("id") and data.get("invoice_url"):
        return {"remote_id": str(data["id"]), "payment_url": data["invoice_url"], "meta": {"price_usd": price_usd}}
    raise GatewayError(f"NowPayments: {data.get('message') or 'خطا در ساخت فاکتور'} (HTTP {status}).")


async def nowpayments_payment_status(api_key: str, payment_id: str) -> dict:
    status, data = await _request(
        "GET", f"{NOWPAYMENTS_BASE}/payment/{payment_id}",
        headers={"x-api-key": api_key},
    )
    if status != 200:
        raise GatewayError(f"NowPayments: خطا در دریافت وضعیت پرداخت (HTTP {status}).")
    return data


def nowpayments_classify(payment: dict, remote_invoice_id: str, order_number: str, expected_usd: float) -> str:
    if str(payment.get("invoice_id") or "") != str(remote_invoice_id):
        return FAILED
    if str(payment.get("order_id") or "") != str(order_number):
        return FAILED
    state = str(payment.get("payment_status") or "")
    if state == "finished":
        try:
            if float(payment.get("price_amount") or 0) + 0.01 < float(expected_usd):
                return FAILED
        except (TypeError, ValueError):
            return FAILED
        return PAID
    if state in ("failed", "refunded", "expired"):
        return FAILED
    return PENDING


def nowpayments_verify_ipn_signature(secret: str, body: dict, signature: str) -> bool:
    if not secret or not signature:
        return False
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
