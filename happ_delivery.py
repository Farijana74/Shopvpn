"""Select safe delivery links for automatically provisioned product orders."""
import asyncio
import logging
import re

from panel_providers import get_provider

logger = logging.getLogger("happ_delivery")
_HAPP_LINK_RE = re.compile(r"happ://crypt5/[^\s\x00-\x1f\x7f]+")


class HappDeliveryError(RuntimeError):
    """A requested Happ-only delivery could not produce a valid Happ link."""


def validate_happ_product(db, product):
    """Reject invalid/legacy Happ product settings before provisioning anything."""
    try:
        mode = product["delivery_mode"] if "delivery_mode" in product.keys() else "normal"
    except (AttributeError, KeyError, TypeError):
        mode = "normal"
    if mode not in ("happ", "both"):
        return

    try:
        is_auto = bool(product["is_auto_provision"])
        server_id = product["provision_server_id"]
        ssh_node_id = product["provision_ssh_node_id"]
    except (KeyError, TypeError, IndexError):
        is_auto, server_id, ssh_node_id = False, None, None
    server = db.get_panel_server(server_id) if server_id else None
    if (
        not is_auto
        or ssh_node_id
        or not server
        or server["panel_type"] != "3xui"
        or not server["is_active"]
    ):
        raise HappDeliveryError(
            "تنظیم Happ این محصول معتبر نیست؛ فقط محصول خودکار VPN خالص روی پنل فعال 3X-UI پشتیبانی می‌شود."
        )


async def get_product_delivery_links(db, product, provisioned_items, order_id=None):
    """Return links for a product's delivery mode.

    Happ-only mode fails closed: it never substitutes a subscription URL. In
    ``both`` mode the normal link remains available if Happ generation fails.
    """
    if provisioned_items and provisioned_items[0].get("_managed_delivery_mode"):
        product = {**dict(product), "delivery_mode": provisioned_items[0]["_managed_delivery_mode"],
                   "provision_server_id": provisioned_items[0]["panel_server_id"]}
    try:
        mode = product["delivery_mode"] if "delivery_mode" in product.keys() else "normal"
    except (AttributeError, KeyError, TypeError):
        mode = "normal"
    if mode not in ("normal", "happ", "both"):
        mode = "normal"
    await asyncio.to_thread(validate_happ_product, db, product)

    links = []
    for item in provisioned_items:
        normal_link = item.get("subscription_url")
        if not normal_link:
            raise HappDeliveryError("پاسخ provisioning لینک اشتراک ندارد.")

        happ_link = None
        if mode in ("happ", "both"):
            try:
                server_id = item.get("panel_server_id")
                server = await asyncio.to_thread(db.get_panel_server, server_id) if server_id else None
                if not server or server["panel_type"] != "3xui":
                    raise ValueError("unsupported or missing 3X-UI server")
                username = item.get("username")
                if not username:
                    raise ValueError("missing provisioned client username")
                candidate = await get_provider(server).get_happ_link(username)
                if not isinstance(candidate, str) or not _HAPP_LINK_RE.fullmatch(candidate):
                    raise ValueError("panel returned an invalid Crypt5 link")
                happ_link = candidate
            except Exception as exc:
                # Never include credentials, subscription URLs, Happ links, or
                # raw provider errors in logs. The exception type is sufficient
                # to distinguish transport, response, and validation failures.
                logger.warning(
                    "Happ link generation failed for order #%s (%s)",
                    order_id, type(exc).__name__,
                )
                if mode == "happ":
                    raise HappDeliveryError(
                        "لینک Happ تولید نشد؛ لینک معمولی ارسال نشده و سفارش برای تلاش مجدد نگه داشته شد."
                    ) from None

        if mode == "happ":
            # Explicit fail-closed guard: this branch can only append a
            # validated encrypted link, never the normal subscription URL.
            if happ_link is None:
                raise HappDeliveryError("لینک Happ معتبر در دسترس نیست.")
            links.append(happ_link)
        else:
            links.append(normal_link)
            if mode == "both" and happ_link:
                links.append(happ_link)
    return links
