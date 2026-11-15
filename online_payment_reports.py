"""Best-effort admin reports for verified online payments."""
import asyncio
import html
import logging
import report_router

log = logging.getLogger(__name__)


def _order_text(order_id, order):
    keys=order.keys()
    lines=[f'✅ پرداخت آنلاین تأیید شد - سفارش #{int(order_id)}',
           f"👤 آیدی کاربر: {int(order['user_id'])}"]
    if 'product_id' in keys and order['product_id']:
        lines.append(f"📦 شناسه محصول: {int(order['product_id'])}")
    if 'wallet_used' in keys and order['wallet_used']:
        lines.append(f"👛 از کیف پول: {int(order['wallet_used']):,} تومان")
    if 'discount_amount' in keys and order['discount_amount']:
        lines.append(f"🎟 تخفیف: {int(order['discount_amount']):,} تومان")
    lines.append(f"💳 مبلغ پرداخت آنلاین: {int(order['final_price'] or 0):,} تومان")
    return '\n'.join(lines)


async def order_completed(db, bot, order_id):
    try:
        order=await asyncio.to_thread(db.get_order,order_id)
        if not order:return
        topic='renewal' if order['is_renewal'] else 'purchase'
        await report_router.report(bot,db,topic,_order_text(order_id,order))
    except Exception:
        log.exception('Online payment order report failed for order %s',order_id)


async def topup_completed(db, bot, topup_id, topup, method=None):
    try:
        lines=[f'✅ پرداخت آنلاین شارژ کیف پول تأیید شد - #{int(topup_id)}',
               f"👤 آیدی کاربر: {int(topup['user_id'])}",f"💰 مبلغ: {int(topup['amount']):,} تومان"]
        if method:lines.append('💳 روش پرداخت: '+html.escape(str(method)))
        await report_router.report(bot,db,'finance','\n'.join(lines))
    except Exception:
        log.exception('Online topup report failed for topup %s',topup_id)


def reseller_text(request_id, invoice):
    keys=invoice.keys()
    amount=int(invoice['amount_toman'] or 0) if 'amount_toman' in keys else 0
    text=f"✅ پرداخت هزینه نمایندگی تأیید شد - درخواست #{int(request_id)}\n👤 آیدی کاربر: {int(invoice['user_id'])}\n💰 مبلغ: {amount:,} تومان"
    if 'gateway' in keys and invoice['gateway']:
        text+='\n💳 روش پرداخت: '+html.escape(str(invoice['gateway']))
    return text


async def reseller_completed(db, bot, request_id, invoice):
    try:
        await report_router.report(bot,db,'reseller',reseller_text(request_id,invoice))
    except Exception:
        log.exception('Reseller payment report failed for request %s',request_id)


async def reseller_completed_raw(db, token, request_id, invoice):
    try:
        await report_router.report_raw(token,db,'reseller',reseller_text(request_id,invoice),senior_only=False)
    except Exception:
        log.exception('Reseller payment report failed for request %s',request_id)


async def order_payment_problem(db, bot, order_id):
    try:
        order=await asyncio.to_thread(db.get_order,order_id)
        text=f'⚠️ پرداخت آنلاین سفارش #{int(order_id)} تأیید شد اما تکمیل سفارش به بررسی نیاز دارد.'
        if order:
            text+=f"\n👤 آیدی کاربر: {int(order['user_id'])}\n💳 مبلغ پرداخت آنلاین: {int(order['final_price'] or 0):,} تومان"
        await report_router.report(bot,db,'gateway',text)
    except Exception:
        log.exception('Online payment issue report failed for order %s',order_id)


async def order_payment_pending_raw(db, token, order_id, order, method):
    try:
        text = (f"✅ پرداخت آنلاین سفارش #{int(order_id)} تأیید شد.\n"
                f"👤 آیدی کاربر: {int(order['user_id'])}\n"
                f"💳 روش پرداخت: {html.escape(str(method))}\n"
                f"💰 مبلغ: {int(order['final_price'] or 0):,} تومان\n"
                "⏳ تحویل کانفیگ شخصی هنوز تکمیل نشده است.")
        await report_router.report_raw(token,db,'purchase',text,senior_only=False)
    except Exception:
        log.exception('Pending paid order report failed for order %s',order_id)
