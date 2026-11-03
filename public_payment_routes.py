"""Public payment entry points for standalone hosting; verify with gateway APIs."""
import asyncio
import html
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

_AUTHORITY = re.compile(r'A[a-zA-Z0-9]{20,64}')


def payment_start_page(authority):
    if not _AUTHORITY.fullmatch(authority):
        raise HTTPException(400, 'شناسه پرداخت معتبر نیست.')
    url = 'https://payment.zarinpal.com/pg/StartPay/' + authority
    return HTMLResponse(
        '<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>ادامه پرداخت</title><body style="font-family:sans-serif;text-align:center;padding:48px">'
        '<h2>ادامه پرداخت با زرین‌پال</h2><p>برای ورود به درگاه پرداخت، دکمه زیر را بزنید.</p>'
        f'<a href="{url}" style="display:inline-block;padding:16px;background:#ffd60a;color:#111">ورود به درگاه پرداخت</a>'
        '</body></html>',
        headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'origin',
                 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"},
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
