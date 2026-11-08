"""Public payment entry points for standalone hosting; verify with gateway APIs."""
import asyncio
import base64
import hashlib
import html
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

_AUTHORITY = re.compile(r'A[a-zA-Z0-9]{20,64}')
_REDIRECT_SCRIPT = """const countdown = document.getElementById('countdown');
let remaining = 7;
const timer = setInterval(() => {
  remaining -= 1;
  countdown.textContent = remaining.toLocaleString('fa-IR');
  if (remaining <= 0) {
    clearInterval(timer);
    window.location.assign(document.getElementById('payment-link').href);
  }
}, 1000);"""
_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_REDIRECT_SCRIPT.encode()).digest()).decode()


def payment_start_page(authority):
    if not _AUTHORITY.fullmatch(authority):
        raise HTTPException(400, 'شناسه پرداخت معتبر نیست.')
    url = 'https://payment.zarinpal.com/pg/StartPay/' + authority
    return HTMLResponse(
        '''<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ادامه پرداخت</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:#f5f7fc;color:#18265a;font-family:Tahoma,Arial,sans-serif;line-height:1.8}
.page{width:100%;max-width:520px;margin:auto;padding:24px 18px 35px}
.header{background:#202f83;border-radius:20px;padding:22px;color:white;display:flex;align-items:center;justify-content:space-between;gap:12px;position:relative;overflow:hidden}
.header:after{content:"";position:absolute;width:150px;height:150px;background:#ffffff0f;border-radius:50%;left:-50px;top:-65px}
.brand{display:flex;align-items:center;gap:12px;position:relative;z-index:1}.logo{width:46px;height:46px;background:#ffd447;border-radius:14px;color:#202f83;display:grid;place-items:center}
.logo svg,.payment-icon svg{width:25px;height:25px}.brand-name{font-size:16px;font-weight:900}.brand-sub{font-size:10px;color:#d9def8;margin-top:2px}.secure{font-size:11px;background:#ffffff1f;padding:8px 11px;border-radius:9px;z-index:1;white-space:nowrap}
.progress{display:flex;align-items:center;justify-content:space-between;padding:27px 5px}.step{display:flex;align-items:center;gap:7px;font-size:11px;color:#202f83;font-weight:700;white-space:nowrap}
.step-number{width:25px;height:25px;border-radius:50%;background:#ffd447;display:grid;place-items:center;font-weight:800}.active .step-number{background:#202f83;color:white}.line{height:2px;flex:1;background:#dfe3ee;margin:0 8px}
.card{background:white;border-radius:22px;border:1px solid #e9ecf4;overflow:hidden;box-shadow:0 8px 30px #1f2f6f10}.card-top{padding:27px 23px 22px;text-align:center}
.yellow-mark{width:58px;height:6px;background:#ffd447;border-radius:20px;margin:0 auto 19px}h1{font-size:23px;font-weight:900;color:#202f83;margin:0 0 11px}.description{font-size:13px;color:#68748d;line-height:2;margin:0}
.payment{margin:0 20px;padding:17px;border:1.5px solid #e8eaf2;border-radius:15px;display:flex;justify-content:space-between;align-items:center;gap:12px}.payment-right{display:flex;align-items:center;gap:12px}
.payment-icon{width:47px;height:47px;border-radius:13px;background:#fff3db;color:#b56d00;display:grid;place-items:center;flex:none}.payment-title{font-size:14px;font-weight:800;color:#202f83}.payment-caption{font-size:10px;color:#78849a;margin-top:5px}.selected{width:22px;height:22px;border-radius:50%;background:#202f83;color:white;display:grid;place-items:center;font-size:12px;flex:none}
.action{padding:22px 20px 20px}.pay-button{width:100%;min-height:57px;background:#ffd447;border-radius:13px;color:#202f83;text-decoration:none;display:flex;align-items:center;justify-content:center;text-align:center;font-size:15px;font-weight:900;box-shadow:0 5px 0 #e8b92d;transition:transform .2s,box-shadow .2s}.pay-button:active{transform:translateY(3px);box-shadow:0 2px 0 #e8b92d}.pay-button:focus-visible{outline:3px solid #202f83;outline-offset:4px}
.countdown{text-align:center;font-size:11px;color:#68748b;margin-top:19px}.countdown strong{color:#202f83;font-size:15px;padding:0 3px}.info{background:#f4f6fc;margin:0 20px 20px;border-radius:13px;padding:15px}.info-title{font-size:12px;font-weight:800;color:#202f83;margin-bottom:9px}.info-text{font-size:11px;line-height:2;color:#68748b}.footer{text-align:center;margin-top:21px;font-size:10px;color:#78849a;line-height:2}
@media(max-width:370px){.step{font-size:9px;gap:4px}.line{margin:0 4px}h1{font-size:20px}.secure{font-size:9px}.header{padding:18px}}@media(prefers-reduced-motion:reduce){.pay-button{transition:none}}
</style></head><body><div class="page">
<header class="header"><div class="brand"><div class="logo" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 20 6v5c0 4.6-3.1 8-8 10-4.9-2-8-5.4-8-10V6l8-3Z"/><path d="m9 12 2 2 4-4"/></svg></div><div><div class="brand-name">پرداخت آنلاین</div><div class="brand-sub">ادامه سفارش</div></div></div><div class="secure">🔒 پرداخت آنلاین</div></header>
<div class="progress" aria-label="مراحل سفارش"><div class="step"><span class="step-number">✓</span><span>انتخاب محصول</span></div><div class="line"></div><div class="step"><span class="step-number">✓</span><span>ثبت سفارش</span></div><div class="line"></div><div class="step active"><span class="step-number">۳</span><span>پرداخت</span></div></div>
<main class="card"><section class="card-top"><div class="yellow-mark"></div><h1>سفارش شما آماده پرداخت است</h1><p class="description">برای تکمیل خرید، از طریق درگاه زرین‌پال پرداخت را انجام دهید.</p></section>
<div class="payment"><div class="payment-right"><div class="payment-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3 10h18M7 15h3"/></svg></div><div><div class="payment-title">درگاه پرداخت زرین‌پال</div><div class="payment-caption">ادامه پرداخت در صفحه درگاه</div></div></div><span class="selected" aria-hidden="true">✓</span></div>
''' + f'<div class="action"><a class="pay-button" id="payment-link" href="{url}">ادامه و ورود به درگاه پرداخت</a>' + '''
<div class="countdown" aria-live="polite">انتقال خودکار تا <strong id="countdown" class="countdown-number">۷</strong> ثانیه دیگر</div></div>
<div class="info"><div class="info-title">راهنمای پرداخت</div><div class="info-text">پس از ورود به درگاه، پرداخت را تکمیل کنید و برای مشاهده نتیجه به ربات بازگردید. اگر انتقال خودکار انجام نشد، دکمه بالا را بزنید.</div></div></main>
<footer class="footer">پرداخت از طریق درگاه زرین‌پال</footer></div><script>''' + _REDIRECT_SCRIPT + '''</script></body></html>''',
        headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'origin',
                 'Content-Security-Policy': f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{_SCRIPT_HASH}'; frame-ancestors 'none'"},
    )


def public_payment_router(resolve_tenant, include_callbacks=True):
    router = APIRouter()

    @router.get('/api/pay-start/zarinpal/{authority}', response_class=HTMLResponse)
    async def start(authority: str):
        return payment_start_page(authority)

    if not include_callbacks:
        return router

    async def tenant_for(request):
        tenant = await asyncio.to_thread(resolve_tenant, request.query_params.get('b', ''))
        if not tenant:
            raise HTTPException(404, 'فروشگاه یافت نشد.')
        return tenant

    @router.get('/api/pay-return/{gateway}', response_class=HTMLResponse, operation_id='public_payment_return_get')
    @router.post('/api/pay-return/{gateway}', response_class=HTMLResponse, operation_id='public_payment_return_post')
    async def returned(gateway: str, request: Request):
        import extra_gateway_registry
        if gateway not in extra_gateway_registry.GATEWAYS:
            raise HTTPException(404, 'درگاه یافت نشد.')
        text = 'برای بررسی نتیجه پرداخت به ربات برگردید و «بررسی وضعیت پرداخت» را بزنید.'
        if gateway == 'zarinpal' and request.query_params.get('Status') == 'OK':
            authority = request.query_params.get('Authority', '')
            if not _AUTHORITY.fullmatch(authority):
                raise HTTPException(400, 'شناسه پرداخت معتبر نیست.')
            tenant = await tenant_for(request)
            invoice = await asyncio.to_thread(tenant.db.get_extra_invoice_by_remote, gateway, authority)
            if invoice:
                import extra_gateway_payment
                from aiogram import Bot
                async with Bot(tenant.bot_token) as bot:
                    result, message = await extra_gateway_payment.process_invoice(tenant.db, bot, invoice)
                if result in ('verified_now', 'already_delivered'):
                    text = message or 'پرداخت بررسی شد. نتیجه و سرویس را در ربات ببینید.'
        return HTMLResponse('<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><title>نتیجه پرداخت</title><p>' + html.escape(text) + '</p></html>', headers={'Cache-Control': 'no-store'})

    @router.post('/api/webhooks/blupal')
    async def blupal_webhook(request: Request):
        import blupal_payment
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, 'بدنه وب‌هوک معتبر نیست.')
        invoice_id = blupal_payment.extract_invoice_id_from_webhook(body)
        if not invoice_id:
            invoice_id = request.query_params.get('invoice_id')
        if not invoice_id:
            raise HTTPException(400, 'شناسه فاکتور یافت نشد.')
        tenant = await tenant_for(request)
        db = tenant.db
        invoice = await asyncio.to_thread(db.get_blupal_invoice_by_invoice_id, invoice_id)
        if not invoice:
            return {'status': 'ignored'}
        result = await blupal_payment.try_verify_and_finalize(db, invoice)
        if result.startswith('error:'):
            raise HTTPException(502, 'بررسی پرداخت فعلاً ممکن نیست.')
        if result != 'verified_now':
            return {'status': result}
        from aiogram import Bot
        try:
            async with Bot(tenant.bot_token) as bot:
                if invoice['kind'] == 'wallet_topup':
                    text = await blupal_payment.finalize_paid_topup(db, invoice['ref_id'])
                    await bot.send_message(invoice['user_id'], text)
                elif invoice['kind'] == 'reseller_request':
                    from extra_gateway_payment import _finalize_reseller
                    text = await _finalize_reseller(db, bot, invoice)
                else:
                    text = await blupal_payment.finalize_paid_order(db, bot, invoice['ref_id'])
            if text.startswith(('⚠️', '⛔️')):
                await asyncio.to_thread(db.update_blupal_invoice_status, invoice_id, 'pending')
                raise HTTPException(503, 'پرداخت ثبت شد؛ تحویل نیاز به تلاش مجدد دارد.')
        except Exception:
            await asyncio.to_thread(db.update_blupal_invoice_status, invoice_id, 'pending')
            raise
        return {'status': 'ok'}

    return router
