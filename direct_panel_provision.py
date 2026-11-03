# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
تحویل خودکار محصولاتی که مستقیماً به یک پنل VPN مشخص (products.provision_server_id)
وصل هستند - بدون دخالت اعتبار حجمی نماینده.

برخلاف reseller_auto_provision.provision_auto_config (که مخصوص بات‌های نمایندگی
و اعتبار حجمی آن‌هاست)، این ماژول برای فروش عادی محصولات در بات اصلی/بانک‌کاربران
است: ادمین موقع ساخت محصول یک پنل مشخص می‌کند، و همان لحظه‌ی خرید یک کاربر واقعی
روی همان پنل ساخته می‌شود - از دید خریدار دقیقاً مثل خرید از بانک کانفیگ.
"""

import logging
import random
import string
import urllib.parse
from datetime import datetime, timedelta, timezone

from panel_providers import get_provider, PanelError, PanelUsernameTakenError
from user_limit import order_user_limit, provider_kwargs
from ssh_uri import build_ssh_uri
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

logger = logging.getLogger(__name__)


class ProvisionError(Exception):
    pass


def _random_username(prefix: str = "") -> str:
    suffix = "d" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}-{suffix}" if prefix else suffix


def planned_usernames(base: str, quantity: int) -> list:
    return [base] if quantity == 1 else [f"{base}_{i}" for i in range(1, quantity + 1)]


def _order_config_name(order) -> str:
    """Read config_name from either sqlite3.Row or a mapping without assuming .get()."""
    if order is None:
        return ""
    try:
        value = order["config_name"]
    except (KeyError, IndexError, TypeError):
        value = ""
    return str(value or "")


async def provision_direct(db, product, quantity: int = 1, user_id: int = None, order_id: int = None) -> list:
    """محصول باید provision_server_id معتبر داشته باشد. برای هر واحد یک کاربر واقعی
    روی همان پنل ساخته می‌شود. برمی‌گرداند: لیستی از
    {"username": ..., "subscription_url": ..., "volume_gb": ..., "duration_days": ...}
    در صورت هر نوع خطا (حتی بعد از ساخت موفق چند واحد) ProvisionError پرتاب می‌شود
    و این تابع همه‌یا-هیچ است: واحدهایی که تا آن لحظه واقعاً روی پنل ساخته شده‌اند
    قبل از پرتاب خطا تلاش می‌شود پاک شوند تا اکانت یتیم روی پنل باقی نماند.

    اگر user_id داده شود، هر واحد ساخته‌شده در custom_configs (source='direct_product')
    هم ثبت می‌شود تا هشدار اتمام حجم/زمان و «سرویس‌های من» آن را ببینند."""
    if "upstream_product_id" in product.keys() and product["upstream_product_id"]:
        from managed_resellers import provision, AgencyError
        try:
            return await provision(db, product, quantity, user_id, order_id)
        except AgencyError as exc:
            raise ProvisionError(str(exc)) from None
    ssh_node_id = None
    try:
        ssh_node_id = product["provision_ssh_node_id"]
    except (KeyError, IndexError):
        pass
    if ssh_node_id and product["provision_server_id"]:
        return await _provision_combined(db, product, quantity=quantity, user_id=user_id, order_id=order_id)
    if ssh_node_id:
        return await provision_ssh_direct(db, product, quantity=quantity, user_id=user_id, order_id=order_id)
    return await _provision_vpn(db, product, quantity=quantity, user_id=user_id, order_id=order_id)


async def _provision_vpn(db, product, quantity: int = 1, user_id: int = None, order_id: int = None,
                         shared_pool: bool = False) -> list:
    """ساخت کاربر روی پنل VPN. با shared_pool=True حجم روی خود پنل نامحدود ساخته می‌شود
    (سقف حجم را استخر مشترک VPN+SSH اعمال می‌کند) ولی حجم محصول در custom_configs ثبت می‌شود."""
    if not isinstance(quantity, int) or quantity < 1:
        raise ProvisionError("تعداد درخواستی نامعتبر است.")

    server_id = product["provision_server_id"]
    if not server_id:
        raise ProvisionError("این محصول به هیچ پنلی وصل نشده است.")

    server = db.get_panel_server(server_id)
    if not server or not server["is_active"]:
        raise ProvisionError("پنل متصل به این محصول یافت نشد یا غیرفعال است؛ با پشتیبانی تماس بگیرید.")

    volume_gb = product["auto_provision_volume_gb"]
    if volume_gb is None or volume_gb < 0:
        raise ProvisionError("حجم این محصول تنظیم نشده است.")
    # 0 یعنی نامحدود (هم برای حجم و هم برای مدت) - نباید با مقدار پیش‌فرض جایگزین شود
    duration_days = product["duration_days"] if product["duration_days"] is not None else 30

    provider = get_provider(server)
    prefix = db.get_custom_config_prefix()
    planned = []
    order = db.get_order(order_id) if order_id is not None else None
    generated_order_name = False
    if order is not None:
        base_name = _order_config_name(order)
        if not base_name and hasattr(db, "reserve_order_config_name"):
            base_name = db.reserve_order_config_name(order_id, _random_username(prefix))
            generated_order_name = bool(base_name)
        elif "provision_username_generated" in order.keys():
            generated_order_name = bool(order["provision_username_generated"])
        if base_name:
            planned = planned_usernames(base_name, quantity)
    existing_by_username = {}
    if order_id is not None and user_id is not None:
        with db._get_conn() as conn:
            saved = conn.execute(
                "SELECT username, subscription_url, volume_gb, duration_days, user_limit "
                "FROM custom_configs WHERE order_id=? AND panel_server_id=? AND source='direct_product' ORDER BY id",
                (int(order_id), int(server_id)),
            ).fetchall()
        existing_by_username = {str(row["username"]): row for row in saved}
        if planned:
            allowed_names = set(planned)
            existing_by_username = {name: row for name, row in existing_by_username.items() if name in allowed_names}
    needed_count = quantity - len(existing_by_username)
    if needed_count > 0 and not db.panel_has_capacity(server_id, needed_count):
        info = db.get_panel_capacity_info(server_id) or {}
        limit = info.get("max_services") or 0
        used = info.get("active_services", 0)
        raise ProvisionError(f"ظرفیت پنل تکمیل است ({used}/{limit} سرویس فعال).")
    extra_kwargs = provider_kwargs(provider, order_user_limit(order, product))
    built = []

    async def _rollback_built():
        # رفع باگ: برخلاف ادعای docstring («واحدهای ساخته‌شده گم نمی‌شوند»)، قبلاً
        # اگر واحد Nام از چند واحدِ یک خرید با خطا مواجه می‌شد، واحدهای ۱..N-۱ که
        # واقعاً روی پنل ساخته شده بودند نه پاک می‌شدند و نه در ProvisionError پرتاب‌شده
        # به‌جایی اشاره می‌شدند - یعنی اکانت‌های واقعی روی پنل مشتری برای همیشه یتیم‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
        # می‌ماندند (نه در custom_configs ثبت می‌شدند، نه قابل پیگیری بودند) درحالی‌که
        # خودِ خرید با خطا مواجه شده بود. دقیقاً مثل reseller_auto_provision._rollback_built،
        # الان قبل از پرتاب ProvisionError هر واحدِ تا این لحظه ساخته‌شده حذف می‌شود.
        for item in built:
            if not item.get("_created_this_attempt"):
                continue
            try:
                await provider.delete_user(item["username"])
            except Exception:
                pass

    try:
        for index in range(quantity):
            username = None
            result = None
            wanted = planned[index] if planned else None
            if wanted and wanted in existing_by_username:
                saved = existing_by_username[wanted]
                built.append({
                    "username": wanted,
                    "subscription_url": saved["subscription_url"],
                    "volume_gb": saved["volume_gb"],
                    "duration_days": saved["duration_days"],
                    "user_limit": saved["user_limit"],
                    "panel_server_id": server_id,
                    "_existing_local": True,
                    "_created_this_attempt": False,
                })
                continue
            # ظرفیت دوباره و دقیقاً پیش از ساخت بررسی می‌شود تا خرید هم‌زمان از سقف عبور نکند.
            if not db.panel_has_capacity(server_id, 1):
                await _rollback_built()
                raise ProvisionError("ظرفیت پنل در همین لحظه تکمیل شد؛ مبلغ سفارش به شما برگردانده می‌شود.")
            for attempt in range(5):
                if wanted and attempt == 0:
                    candidate = wanted
                elif wanted:
                    candidate = wanted + "_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=3))
                else:
                    candidate = _random_username(prefix)
                if db.is_custom_username_taken(candidate):
                    continue
                try:
                    result = await provider.create_user(candidate, 0 if shared_pool else volume_gb, duration_days, **extra_kwargs)
                    username = candidate
                    created_this_attempt = True
                    break
                except PanelUsernameTakenError:
                    if wanted and attempt == 0 and order_id is not None and generated_order_name:
                        try:
                            result = await provider.get_user(candidate)
                            username = candidate
                            created_this_attempt = False
                            break
                        except PanelError:
                            pass
                    continue
            if username is None:
                await _rollback_built()
                raise ProvisionError("ساخت نام کاربری یکتا روی پنل ناموفق بود؛ دوباره تلاش کنید.")
            built.append({
                "username": result.username,
                "subscription_url": result.subscription_url,
                "volume_gb": volume_gb,
                "duration_days": duration_days,
                "user_limit": extra_kwargs.get("user_limit"),
                "panel_server_id": server_id,
                "_existing_local": False,
                "_created_this_attempt": created_this_attempt,
            })
    except ProvisionError:
        raise
    except PanelError as e:
        await _rollback_built()
        raise ProvisionError(f"خطا در ساخت کانفیگ روی پنل: {e}")

    if user_id is not None:
        for item in built:
            if item.get("_existing_local"):
                continue
            try:
                item["custom_config_id"] = db.add_custom_config(
                    user_id, server["id"], item["username"], item["volume_gb"], item["duration_days"],
                    item["subscription_url"], order_id=order_id, source="direct_product",
                    user_limit=item.get("user_limit"),
                )
            except Exception:
                # مسیر خودِ ساخت روی پنل قبلاً موفق شده (والا اصلاً به این بلوک
                # نمی‌رسیدیم)؛ اینجا فقط ثبت محلی سرویس است، پس raise نمی‌کنیم
                # تا مشتری از خریدی که واقعاً تحویل گرفته محروم نشود - ولی لاگ
                # می‌کنیم تا این‌جور شکست‌ها دیگر بی‌صدا گم نشوند.
                logger.exception(
                    "add_custom_config برای واحد direct-provision ناموفق بود "
                    "(user_id=%s, username=%s, order_id=%s) - این سرویس در «سرویس‌های من» دیده نخواهد شد.",
                    user_id, item.get("username"), order_id,
                )

    for item in built:
        item.pop("_existing_local", None)
        item.pop("_created_this_attempt", None)
    return built
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 


async def _provision_combined(db, product, quantity: int = 1, user_id: int = None, order_id: int = None) -> list:
    """محصول ترکیبی VPN+SSH با سهمیه مشترک: هر دو سرویس عضو یک quota pool هستند
    (حجم و انقضا مشترک؛ ‌quota_reconciler مصرف هر دو را جمع و در صورت اتمام هر دو را قطع می‌کند).
    همه‌یا‌هیچ: اگر هر مرحله‌ای شکست بخورد، آنچه تا آن لحظه ساخته شده پاک می‌شود."""
    if user_id is None:
        raise ProvisionError("شناسه کاربر برای ساخت محصول ترکیبی VPN+SSH الزامی است.")
    # ابتدا SSH (شکست‌پذیرتر: نیاز به شمارنده معتبر)، بعد VPN.
    ssh_items = await provision_ssh_direct(db, product, quantity=quantity, user_id=user_id, order_id=order_id)
    pool_id = ssh_items[0]["quota_pool_id"]

    async def _rollback_ssh():
        try:
            from ssh_providers import get_ssh_provider
            from quota_reconciler import _env_secret_resolver
            node = next((n for n in db.list_ssh_nodes() if int(n["id"]) == int(product["provision_ssh_node_id"])), None)
            provider = get_ssh_provider(node, secret_resolver=_env_secret_resolver)
            for item in ssh_items:
                try:
                    await provider.delete_account(item["account_id"])
                except Exception:
                    logger.exception("combined rollback: SSH delete failed for %s", item["account_id"])
        except Exception:
            logger.exception("combined rollback: SSH provider unavailable")
        db.delete_quota_pool(pool_id)

    try:
        vpn_items = await _provision_vpn(db, product, quantity=quantity, user_id=user_id,
                                         order_id=order_id, shared_pool=True)
    except Exception:
        await _rollback_ssh()
        raise

    try:
        for item in vpn_items:
            cc_id = item.get("custom_config_id")
            if not cc_id:
                raise ProvisionError("ثبت سرویس VPN برای اتصال به سهمیه مشترک ناموفق بود.")
            db.attach_custom_config_to_quota_pool(pool_id, cc_id)
        for vpn_item, ssh_item in zip(vpn_items, ssh_items):
            db.link_ssh_account_to_config(ssh_item["account_id"], vpn_item["custom_config_id"])
    except Exception as exc:
        provider = get_provider(db.get_panel_server(product["provision_server_id"]))
        for item in vpn_items:
            try:
                await provider.delete_user(item["username"])
            except Exception:
                logger.exception("combined rollback: VPN delete failed for %s", item["username"])
            if item.get("custom_config_id"):
                with db._get_conn() as conn:
                    conn.execute("DELETE FROM quota_pool_members WHERE service_kind='vpn' AND service_id=?",
                                 (f"custom_config:{int(item['custom_config_id'])}",))
                    conn.execute("DELETE FROM custom_configs WHERE id=?", (int(item["custom_config_id"]),))
        await _rollback_ssh()
        if isinstance(exc, ProvisionError):
            raise
        raise ProvisionError(f"اتصال سرویس‌ها به سهمیه مشترک ناموفق بود: {exc}") from exc

    for item in vpn_items:
        item["quota_pool_id"] = int(pool_id)
    import dedicated_sub
    if dedicated_sub.is_enabled(db):
        return vpn_items
    return vpn_items + ssh_items


async def provision_ssh_direct(db, product, quantity: int = 1, user_id: int = None, order_id: int = None) -> list:
    """Provision a paid SSH product on a Linux Agent and create one shared quota pool.

    Commercial delivery fails closed unless the Agent reports a valid per-account
    counter. This prevents selling a quota-limited SSH product that cannot enforce
    its advertised volume.
    """
    if not isinstance(quantity, int) or quantity < 1 or user_id is None:
        raise ProvisionError("تعداد یا شناسه کاربر برای ساخت محصول SSH نامعتبر است.")
    node_id = product["provision_ssh_node_id"]
    node = next((n for n in db.list_ssh_nodes(active_only=True) if int(n["id"]) == int(node_id)), None)
    if not node or node.get("node_type") != "linux":
        raise ProvisionError("نود Linux Agent فعال پیدا نشد؛ پنل SSH هنوز آداپتور عملیاتی ندارد.")
    volume_gb = float(product["auto_provision_volume_gb"] or 0)
    duration_days = int(product["duration_days"] if product["duration_days"] is not None else 30)
    if volume_gb < 0 or duration_days < 0:
        raise ProvisionError("حجم یا مدت محصول SSH نامعتبر است.")
    expires_at = (datetime.now(timezone.utc) + timedelta(days=duration_days)).isoformat() if duration_days else None

    from ssh_providers import get_ssh_provider, SSHProviderError
    from quota_reconciler import _env_secret_resolver
    from ssh_credentials import encrypt_credential
    try:
        encrypt_credential("preflight-check")
    except RuntimeError as exc:
        raise ProvisionError(str(exc))
    try:
        provider = get_ssh_provider(node, secret_resolver=_env_secret_resolver)
        pool_id = db.create_quota_pool(int(user_id), volume_gb, expires_at,
            label=f"{product['name']} SSH" + (f" · order #{order_id}" if order_id else ""))
    except Exception as exc:
        raise ProvisionError(f"آماده‌سازی محصول SSH ناموفق بود: {exc}") from exc
    built = []
    order = db.get_order(order_id) if order_id is not None else None
    # Keep the SSH login recognizable and tied to the name shown for the order.
    # SSH usernames are restricted to portable ASCII account-name characters.
    requested_base = _order_config_name(order).strip().lower()
    requested_base = "".join(ch for ch in requested_base if ch in string.ascii_lowercase + string.digits + "_-").strip("_-")
    requested_base = requested_base[:12]
    try:
        for item_index in range(quantity):
            result = None
            for attempt in range(5):
                suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=7))
                unit_base = requested_base if quantity == 1 else f"{requested_base[:9]}{item_index + 1}"
                username = f"{unit_base}_{suffix}" if unit_base else "sv" + suffix
                try:
                    # The shared pool is the sole volume authority. A duplicate
                    # per-account cap would prevent a renewed/increased pool from
                    # restoring accounts and would break shared usage semantics.
                    result = await provider.create_account(username, 0, expires_at)
                    break
                except SSHProviderError as exc:
                    if "already exists" in str(exc).lower() and attempt < 4:
                        continue
                    raise
            if result is None:
                raise ProvisionError("ساخت نام کاربری SSH یکتا ناموفق بود.")
            try:
                usage = await provider.get_usage(result.account_id)
                reliable_sources = {"nftables_port_counters", "external-counter", "ssh-traffic-accountant"}
                if volume_gb > 0 and (not usage.get("accounting_ready") or usage.get("source") not in reliable_sources):
                    raise ProvisionError("فروش محصول حجمی SSH متوقف شد: شمارنده‌ی دوطرفه‌ی معتبر آماده نیست. nftables را فعال کنید و مطمئن شوید منبع برابر nftables_port_counters یا یک external-counter معتبر است.")
                result.metadata["accounting_ready"] = bool(usage.get("accounting_ready"))
                credential_enc = encrypt_credential(result.metadata.get("password", ""))
                db.register_ssh_account(int(node_id), int(user_id), result.account_id, result.username,
                    result.host, result.port, int(pool_id), expires_at, credential_enc)
                if usage.get("accounting_ready"):
                    db.record_usage_snapshot("ssh", result.account_id, int(usage["used_bytes"]),
                        uploaded_bytes=usage.get("uploaded_bytes", 0), downloaded_bytes=usage.get("downloaded_bytes", 0),
                        source=str(usage.get("source") or "ssh-agent"), reported_at=usage.get("reported_at"))
                built.append(result)
            except Exception:
                try:
                    await provider.delete_account(result.account_id)
                except Exception:
                    logger.exception("SSH provisioning rollback failed for %s", result.account_id)
                raise
    except Exception as exc:
        for item in built:
            try:
                await provider.delete_account(item.account_id)
            except Exception:
                logger.exception("SSH provisioning rollback failed for %s", item.account_id)
        db.delete_quota_pool(pool_id)
        if isinstance(exc, ProvisionError):
            raise
        raise ProvisionError(f"ساخت محصول SSH ناموفق بود: {exc}") from exc

    output = []
    for item in built:
        password = str(item.metadata.get("password") or "")
        host = str(item.host or "")
        uri = build_ssh_uri(item.username, password, host, int(item.port), str(node.get("name") or "SSH"))
        output.append({"username": item.username, "host": host, "port": int(item.port),
            "password": password, "account_id": item.account_id, "quota_pool_id": int(pool_id),
            "subscription_url": uri, "volume_gb": volume_gb, "duration_days": duration_days,
            "accounting_ready": bool(item.metadata.get("accounting_ready"))})
    return output
