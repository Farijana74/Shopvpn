"""Happ link selection for automatically provisioned product orders."""
import logging

from panel_providers import get_provider

logger = logging.getLogger("happ_delivery")


async def get_product_delivery_links(db, product, provisioned_items, order_id=None):
    """Return links according to a product's delivery mode, safely falling back."""
    try:
        mode = product["delivery_mode"] if "delivery_mode" in product.keys() else "normal"
    except (AttributeError, KeyError, TypeError):
        mode = "normal"
    if mode not in ("normal", "happ", "both"):
        mode = "normal"

    links = []
    for item in provisioned_items:
        normal_link = item["subscription_url"]
        happ_link = None
        server_id = item.get("panel_server_id")
        if mode in ("happ", "both") and server_id:
            try:
                server = db.get_panel_server(server_id)
                if server and server["panel_type"] == "3xui":
                    provider = get_provider(server)
                    get_happ = getattr(provider, "get_happ_link", None)
                    if get_happ:
                        happ_link = await get_happ(item["username"])
            except Exception:
                # Never include credentials or subscription/Happ links in logs.
                logger.warning("Happ link generation failed for order #%s", order_id)

        if mode == "happ" and happ_link:
            links.append(happ_link)
        else:
            links.append(normal_link)
            if mode == "both" and happ_link:
                links.append(happ_link)
    return links
