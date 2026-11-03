"""Render existing services in their original delivery format, including rotations."""
import asyncio
import logging
import re

from happ_delivery import HappDeliveryError
from panel_providers import get_provider

logger = logging.getLogger(__name__)
_HAPP = re.compile(r"happ://crypt5/[^\s\x00-\x1f\x7f]+")


def service_delivery_mode(db, service):
    service = dict(service)
    mode = service.get('delivery_mode')
    if mode in ('normal', 'happ', 'both'):
        return mode
    order_id = service.get('order_id')
    order = db.get_order(order_id) if order_id else None
    product = db.get_product(order['product_id']) if order and order['product_id'] else None
    mode = dict(product).get('delivery_mode') if product else 'normal'
    return mode if mode in ('normal', 'happ', 'both') else 'normal'


async def service_delivery_links(db, service):
    service = dict(service)
    mode = await asyncio.to_thread(service_delivery_mode, db, service)
    normal = service.get('subscription_url')
    if mode == 'normal':
        return [normal] if normal else []
    try:
        server_id = service.get('panel_server_id')
        server = await asyncio.to_thread(db.get_panel_server, server_id) if server_id else None
        if not server or server['panel_type'] != '3xui' or not server['is_active']:
            raise ValueError('Happ server unavailable')
        link = await get_provider(server).get_happ_link(service['username'])
        if not isinstance(link, str) or not _HAPP.fullmatch(link):
            raise ValueError('Invalid Happ response')
    except Exception as exc:
        logger.warning('Service Happ generation failed for config #%s (%s)', service.get('id'), type(exc).__name__)
        if mode == 'happ':
            raise HappDeliveryError('⚠️ دریافت لینک Happ فعلاً ممکن نیست؛ برای دریافت مجدد، بروزرسانی را بزنید.') from None
        return [normal] if normal else []
    return [normal, link] if mode == 'both' and normal else [link]


async def rotate_service_credentials(db, service, provider):
    """Persist the new upstream URL before any subsequent Happ generation."""
    result = await provider.revoke_credentials(service['username'])
    if not result.subscription_url:
        raise HappDeliveryError('پنل لینک جدیدی برنگرداند؛ با پشتیبانی تماس بگیرید.')
    await asyncio.to_thread(db.update_custom_config_subscription_url, service['id'], result.subscription_url)
    return result
