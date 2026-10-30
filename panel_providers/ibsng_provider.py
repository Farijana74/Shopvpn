#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""Provider IBSng: کلاینت مبتنی بر وب‌اسکرپینگ پنل ادمین (/IBSng/admin) با لاگین کوکی.

گروه (group) IBSng به‌جای «کاربر نمونه» گرفته می‌شود و حجم/مدت را همان گروه تعیین می‌کند (مقدار ورودی create_user اعمال نمی‌شود).
خروجی به‌جای لینک اشتراک، متن نام کاربری و رمز است.
"""
import re
import secrets
import time
from datetime import datetime
from html import unescape

from . import auth_cache
from ._http import new_session, request, load_json
from .base import BasePanelProvider, PanelUserResult, PanelError, PanelUsernameTakenError
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

_UNSUPPORTED = "این نوع پنل از این عملیات پشتیبانی نمی‌کند."
_UNITS = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
_DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d")


def credentials_text(username: str, password: str) -> str:
    return f"Username: {username}\nPassword: {password}"


def _cell_after(text: str, label: str, css_class: str) -> str:
    start = text.find(label)
    if start < 0:
        return ""
    match = re.search(rf'<td[^>]*class="[^"]*{css_class}[^"]*"[^>]*>(.*?)</td>', text[start:], re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", match.group(1)))).strip()


def _size_bytes(text: str, default_unit: str) -> int:
    match = re.match(r"\s*([\d.,]+)\s*([KMGT])?", text or "", re.I)
    if not match:
        return 0
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return 0
    return int(number * _UNITS[(match.group(2) or default_unit).upper()])


def _date_epoch(text: str) -> int:
    for fmt in _DATE_FORMATS:
        try:
            return int(datetime.strptime((text or "").strip(), fmt).timestamp())
        except ValueError:
            continue
    return 0


class IBSngProvider(BasePanelProvider):

    def _base(self) -> str:
        return self.server["api_url"].rstrip("/")

    def _session(self):
        return new_session(headers={"User-Agent": "phpIBSng web Api"}, cookies=True, server=self.server)

    async def _page(self, session, path: str, data: dict = None, params: dict = None) -> str:
        """پاسخ کامل (هدرها + بدنه)؛ مثل curl با HEADER=true و بدون دنبال‌کردن redirect."""
        status, text, headers = await request(
            session, "POST", f"{self._base()}/IBSng/admin/{path}",
            data=data or {}, params=params, allow_redirects=False,
        )
        head = "\n".join(f"{key}: {value}" for key, value in headers.items())
        return f"{head}\n\n{text}"

    async def _login(self, session) -> None:
        page = await self._page(
            session, "", {"username": self.server["api_username"], "password": self.server["api_password"]},
        )
        if "admin_index" not in page:
            raise PanelError("نام کاربری یا رمز عبور ادمین IBSng نادرست است.")

    async def _authed_session(self):
        """سشن جدید؛ در صورت وجود کوکی لاگینِ معتبر در کش از آن استفاده می‌شود
        (بدون درخواست لاگین تکراری)، وگرنه لاگین واقعی انجام و کوکی کش می‌شود."""
        session = self._session()
        key = auth_cache.cache_key("ibsng", self.server)
        cached = auth_cache.get_cookies(key)
        if cached:
            session.cookie_jar.update_cookies(cached)
            return session
        await self._login(session)
        auth_cache.set_cookies(key, {c.key: c.value for c in session.cookie_jar})
        return session

    async def _user_id(self, session, username: str):
        page = await self._page(session, "user/user_info.php", params={"normal_username_multi": username})
        if "does not exists" in page:
            return None
        match = re.search(r"change_credit\.php\?user_id=([^\"&\s]+)", page)
        return match.group(1) if match else None

    def _group(self) -> str:
        name = load_json(self.server["group_ids"])
        if not isinstance(name, str) or not name:
            raise PanelError("گروه این سرور تنظیم نشده. اول از «تعیین کاربر نمونه» استفاده کن.")
        return name

    async def fetch_template_from_user(self, sample_username: str) -> dict:
        async with await self._authed_session():
            pass
        return {"group_ids": sample_username.strip(), "proxy_settings": {}}

    async def create_user(self, username: str, volume_gb: int, duration_days: int) -> PanelUserResult:
        group = self._group()
        owner = self.server["api_username"]
        password = secrets.token_hex(6)
        async with await self._authed_session() as session:
            if await self._user_id(session, username):
                raise PanelUsernameTakenError(f"نام کاربری «{username}» روی پنل تکراری است")
            page = await self._page(session, "user/add_new_users.php", {
                "submit_form": 1, "add": 1, "count": 1, "credit": 1,
                "owner_name": owner, "group_name": group, "edit__normal_username": 1,
            })
            match = re.search(r"user_id=(\d+)", page)
            if not match:
                raise PanelError("ساخت کاربر روی IBSng ناموفق بود (شناسه کاربر دریافت نشد؛ نام گروه را بررسی کن).")
            uid = match.group(1)
            page = await self._page(
                session, "plugins/edit.php",
                data={
                    "target": "user", "target_id": uid, "update": 1, "edit_tpl_cs": "normal_username",
                    "attr_update_method_0": "normalAttrs", "has_normal_username": "t", "current_normal_username": "",
                    "normal_username": username, "password": password, "normal_save_user_add": "t", "credit": 1,
                },
                params={
                    "edit_user": 1, "user_id": uid, "submit_form": 1, "add": 1, "count": 1, "credit": 1,
                    "owner_name": owner, "group_name": group, "x": 35, "y": 1,
                    "edit__normal_username": "normal_username",
                },
            )
            if "exist" in page:
                raise PanelUsernameTakenError(f"نام کاربری «{username}» روی پنل تکراری است")
            if "IBSng/admin/user/user_info.php?user_id_multi" not in page:
                raise PanelError("ساخت کاربر روی IBSng ناموفق بود.")
        return PanelUserResult(username=username, subscription_url=credentials_text(username, password), raw={})

    async def delete_user(self, username: str) -> bool:
        async with await self._authed_session() as session:
            uid = await self._user_id(session, username)
            if not uid:
                return False
            page = await self._page(session, "user/del_user.php", {
                "user_id": uid, "delete": 1, "delete_comment": "",
                "delete_connection_logs": "on", "delete_audit_logs": "on",
            })
        return "Successfully" in page

    async def _password(self, session, uid: str) -> str:
        page = await self._page(session, "plugins/edit.php", {
            "user_id": uid, "edit_user": 1, "attr_edit_checkbox_2": "normal_username",
        })
        match = re.search(r'<input[^>]*id="password"[^>]*value="([^"]*)"', page, re.S)
        return match.group(1).strip() if match else ""

    async def get_user(self, username: str) -> PanelUserResult:
        async with await self._authed_session() as session:
            uid = await self._user_id(session, username)
            if not uid:
                raise PanelError(f"کاربری با نام «{username}» روی پنل پیدا نشد.")
            password = await self._password(session, uid)
        return PanelUserResult(username=username, subscription_url=credentials_text(username, password), raw={})

    async def get_user_usage(self, username: str) -> dict:
        async with await self._authed_session() as session:
            page = await self._page(session, "user/user_info.php", params={"normal_username_multi": username})
        if "does not exists" in page:
            raise PanelError(f"کاربری با نام «{username}» روی پنل پیدا نشد.")
        limit = _size_bytes(_cell_after(page, "Traffic Limit", "Form_Content_Row_Right_userinfo_light"), "G")
        used = _size_bytes(_cell_after(page, "Traffic Limit", "Form_Content_Row_Right_userinfo_dark"), "M")
        expire = _date_epoch(_cell_after(page, "Nearest Expiration Date:", "Form_Content_Row_Right_userinfo_light"))
        if limit and used >= limit:
            status = "limited"
        elif expire and expire < time.time():
            status = "expired"
        else:
            status = "active"
        return {"used_bytes": used, "data_limit_bytes": limit, "status": status}

    async def update_user(self, username: str, add_volume_gb: float = 0, add_days: int = 0,
                           reset_usage: bool = False, preserve_remaining: bool = False,
                           unlimited_volume: bool = False) -> PanelUserResult:
        raise PanelError(_UNSUPPORTED)

    async def revoke_credentials(self, username: str) -> PanelUserResult:
        raise PanelError(_UNSUPPORTED)

    async def test_connection(self) -> bool:
        """همیشه واقعاً لاگین می‌کند (نه از کش) تا واقعاً یوزر/پس فعلی را تست کند."""
        try:
            async with self._session() as session:
                await self._login(session)
                auth_cache.set_cookies(
                    auth_cache.cache_key("ibsng", self.server),
                    {c.key: c.value for c in session.cookie_jar},
                )
            return True
        except PanelError as e:
            self.last_error = str(e)
            return False
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
