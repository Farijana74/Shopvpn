#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""Provider پنل Alireza x-ui (alireza0/x-ui): لاگین با کوکی و مسیرهای /xui/API. فقط یک inbound پشتیبانی می‌شود."""
import json
import secrets
import time
import uuid

from . import auth_cache
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
from ._http import new_session, request_json, http_error, load_json, server_value, inbound_ids, new_limit_bytes
from .base import BasePanelProvider, PanelUserResult, PanelError, PanelUsernameTakenError

_SETTINGS_TAIL = {"decryption": "none", "fallbacks": []}


class AlirezaProvider(BasePanelProvider):
    supports_user_limit = True

    def _session(self):
        return new_session(cookies=True, server=self.server)

    def _base(self) -> str:
        return self.server["api_url"].rstrip("/")

    def _sub_base(self) -> str:
        return (server_value(self.server, "xui_sub_base_url", "") or "").rstrip("/")

    def _sub_url(self, sub_id) -> str:
        base = self._sub_base()
        return f"{base}/{sub_id}" if (base and sub_id) else ""

    async def _login(self, session) -> None:
        status, data, text = await request_json(
            session, "POST", f"{self._base()}/login",
            data={"username": self.server["api_username"], "password": self.server["api_password"]},
        )
        if status >= 400:
            raise http_error("ورود به پنل", status, text)
        if not isinstance(data, dict) or not data.get("success"):
            raise PanelError("نام کاربری یا رمز عبور ادمین پنل نادرست است.")

    async def _authed_session(self):
        """سشن جدید؛ در صورت وجود کوکی لاگینِ معتبر در کش از آن استفاده می‌شود
        (بدون درخواست /login تکراری)، وگرنه لاگین واقعی انجام و کوکی کش می‌شود."""
        session = self._session()
        key = auth_cache.cache_key("alireza", self.server)
        cached = auth_cache.get_cookies(key)
        if cached:
            session.cookie_jar.update_cookies(cached)
            return session
        await self._login(session)
        auth_cache.set_cookies(key, {c.key: c.value for c in session.cookie_jar})
        return session

    async def _api(self, session, method: str, path: str, action: str, **kwargs) -> dict:
        status, data, text = await request_json(session, method, f"{self._base()}/xui/API/{path}", **kwargs)
        if status >= 400:
            raise http_error(action, status, text)
        if not isinstance(data, dict):
            raise PanelError(f"پاسخ نامعتبر از پنل در {action}.")
        if data.get("success") is False:
            raise PanelError(data.get("msg") or f"{action} ناموفق بود.")
        return data

    async def _inbounds(self, session) -> list:
        data = await self._api(session, "GET", "inbounds", "دریافت لیست inbound")
        return data.get("obj") or []

    async def list_inbounds(self) -> list:
        async with await self._authed_session() as session:
            inbounds = await self._inbounds(session)
        return [
            {"id": ib["id"], "remark": ib.get("remark", ""), "protocol": ib.get("protocol", ""), "port": ib.get("port")}
            for ib in inbounds
        ]

    async def _find(self, session, username: str) -> tuple:
        """خروجی: (client, inbound_id, clientStats)."""
        for ib in await self._inbounds(session):
            settings = load_json(ib.get("settings"), {})
            if not isinstance(settings, dict):
                continue
            for client in settings.get("clients") or []:
                if client.get("email") == username:
                    stats = next((s for s in (ib.get("clientStats") or []) if s.get("email") == username), {})
                    return client, ib["id"], stats
        raise PanelError(f"کاربری با نام «{username}» روی پنل پیدا نشد.")

    async def find_username_by_sub_id(self, sub_id: str):
        """email کلاینتی که subId آن برابر sub_id است (یا None)."""
        async with await self._authed_session() as session:
            for ib in await self._inbounds(session):
                settings = load_json(ib.get("settings"), {})
                if not isinstance(settings, dict):
                    continue
                for client in settings.get("clients") or []:
                    if client.get("subId") == sub_id and client.get("email"):
                        return client["email"]
        return None

    @staticmethod
    def _payload(inbound_id, client: dict) -> dict:
        return {"id": int(inbound_id), "settings": json.dumps({"clients": [client], **_SETTINGS_TAIL})}

    async def _update_client(self, session, inbound_id, old_client_id: str, client: dict, action: str) -> None:
        await self._api(
            session, "POST", f"inbounds/updateClient/{old_client_id}", action,
            json=self._payload(inbound_id, client),
        )

    async def create_user(self, username: str, volume_gb: int, duration_days: int, user_limit: int = 0) -> PanelUserResult:
        ids = inbound_ids(self.server)
        if not ids or not self._sub_base():
            raise PanelError("این سرور هنوز کامل تنظیم نشده (inbound یا آدرس Subscription خالی است).")
        sub_id = secrets.token_hex(8)
        client = {
            "id": str(uuid.uuid4()),
            "flow": "",
            "email": username,
            "limitIp": int(user_limit or 0),
            "totalGB": int(volume_gb * (1024 ** 3)),
            "expiryTime": int((time.time() + duration_days * 86400) * 1000) if duration_days else 0,
            "enable": True,
            "tgId": "",
            "subId": sub_id,
            "reset": 0,
        }
        async with await self._authed_session() as session:
            try:
                await self._api(session, "POST", "inbounds/addClient", "ساخت کاربر", json=self._payload(ids[0], client))
            except PanelError as exc:
                lowered = str(exc).lower()
                if "duplicate" in lowered or "exist" in lowered or "already" in lowered:
                    raise PanelUsernameTakenError(f"نام کاربری «{username}» روی پنل تکراری است") from exc
                raise
        return PanelUserResult(username=username, subscription_url=self._sub_url(sub_id), raw=client)

    async def delete_user(self, username: str) -> bool:
        async with await self._authed_session() as session:
            try:
                client, inbound_id, _ = await self._find(session, username)
            except PanelError as exc:
                if "پیدا نشد" in str(exc):
                    return False
                raise
            status, data, _ = await request_json(
                session, "POST", f"{self._base()}/xui/API/inbounds/{inbound_id}/delClient/{client['id']}",
            )
            return status < 400 and isinstance(data, dict) and bool(data.get("success"))

    @staticmethod
    def _status(client: dict, used: int) -> str:
        total = int(client.get("totalGB") or 0)
        expiry = int(client.get("expiryTime") or 0)
        if total and used >= total:
            return "limited"
        if expiry > 0 and expiry / 1000 < time.time():
            return "expired"
        return "active" if client.get("enable", True) else "disabled"

    async def get_user_usage(self, username: str) -> dict:
        async with await self._authed_session() as session:
            client, _, stats = await self._find(session, username)
        used = int(stats.get("up") or 0) + int(stats.get("down") or 0)
        return {
            "used_bytes": used,
            "data_limit_bytes": int(client.get("totalGB") or 0),
            "status": self._status(client, used),
        }

    async def get_user(self, username: str) -> PanelUserResult:
        async with await self._authed_session() as session:
            client, _, _ = await self._find(session, username)
        url = self._sub_url(client.get("subId"))
        if not url:
            raise PanelError("لینک اشتراک این کاربر روی پنل یافت نشد.")
        return PanelUserResult(username=username, subscription_url=url, raw=client)

    supports_data_limit_sync = True

    async def set_data_limit_bytes(self, username: str, limit_bytes: int, reenable: bool = False) -> None:
        """فقط totalGB کلاینت را عوض می‌کند (انقضا/مصرف دست‌نخورده)؛ reenable برای کلاینتی که
        پنل چون به سقف رسیده بود غیرفعالش کرده."""
        async with await self._authed_session() as session:
            client, inbound_id, _ = await self._find(session, username)
            updated = dict(client)
            updated["totalGB"] = int(limit_bytes)
            if reenable:
                updated["enable"] = True
            await self._update_client(session, inbound_id, client["id"], updated, "اعمال سقف حجم")

    async def update_user(self, username: str, add_volume_gb: float = 0, add_days: int = 0,
                           reset_usage: bool = False, preserve_remaining: bool = False,
                           unlimited_volume: bool = False,
                           user_limit: int = None) -> PanelUserResult:
        async with await self._authed_session() as session:
            client, inbound_id, stats = await self._find(session, username)
            now_ms = int(time.time() * 1000)
            current_expiry = int(client.get("expiryTime") or 0)
            updated = dict(client)
            if add_days:
                updated["expiryTime"] = max(current_expiry, now_ms) + add_days * 86400000
            used = int(stats.get("up") or 0) + int(stats.get("down") or 0)
            updated["totalGB"] = new_limit_bytes(client.get("totalGB"), used, add_volume_gb, reset_usage, preserve_remaining,
                                                    unlimited=unlimited_volume)
            updated["enable"] = True
            if user_limit is not None:
                updated["limitIp"] = int(user_limit)
            await self._update_client(session, inbound_id, client["id"], updated, "بروزرسانی کاربر")
            if reset_usage:
                try:
                    await self._api(
                        session, "POST", f"inbounds/{inbound_id}/resetClientTraffic/{username}", "ریست مصرف",
                    )
                except PanelError:
                    pass
        return PanelUserResult(username=username, subscription_url=self._sub_url(updated.get("subId")), raw=updated)

    async def revoke_credentials(self, username: str) -> PanelUserResult:
        async with await self._authed_session() as session:
            client, inbound_id, _ = await self._find(session, username)
            updated = dict(client)
            updated["id"] = str(uuid.uuid4())
            if "password" in updated:
                updated["password"] = updated["id"]
            updated["subId"] = secrets.token_hex(8)
            await self._update_client(session, inbound_id, client["id"], updated, "قطع دسترسی/تولید لینک جدید")
        return PanelUserResult(username=username, subscription_url=self._sub_url(updated["subId"]), raw=updated)

    async def set_enabled(self, username: str, enabled: bool) -> None:
        async with await self._authed_session() as session:
            client, inbound_id, _ = await self._find(session, username)
            updated = dict(client)
            updated["enable"] = bool(enabled)
            await self._update_client(session, inbound_id, client["id"], updated, "تغییر وضعیت کاربر")

    async def rename_user(self, username: str, new_username: str) -> None:
        async with await self._authed_session() as session:
            client, inbound_id, _ = await self._find(session, username)
            updated = dict(client)
            updated["email"] = new_username
            await self._update_client(session, inbound_id, client["id"], updated, "تغییر نام کاربر")

    async def test_connection(self) -> bool:
        """همیشه واقعاً لاگین می‌کند (نه از کش) تا واقعاً یوزر/پس فعلی را تست کند."""
        try:
            async with self._session() as session:
                await self._login(session)
                await self._inbounds(session)
                auth_cache.set_cookies(
                    auth_cache.cache_key("alireza", self.server),
                    {c.key: c.value for c in session.cookie_jar},
                )
            return True
        except PanelError as e:
            self.last_error = str(e)
            return False
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
