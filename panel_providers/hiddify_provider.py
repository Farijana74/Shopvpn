#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
Provider پنل Hiddify Manager - کاملاً متفاوت از خانواده‌ی Marzban/PasarGuard.

✅ تایید‌شده با بررسی سورس واقعی یک بات معروف و پرکاربرد فارسی که به
Hiddify وصل می‌شود؛ endpoint ها و نام فیلدهای create/delete/list همگی از
روی همان پیاده‌سازی واقعی گرفته شده‌اند.

- احراز هویت: نه یوزر/پس + توکن موقت، بلکه یک «Hiddify-API-Key» ثابت
  (همان UUID ادمین) که در هدر هر درخواست فرستاده می‌شود. از ستون موجود
  api_password برای نگه‌داشتن همین کلید استفاده می‌کنیم (api_username
  عملاً استفاده نمی‌شود)؛ نیازی به تغییر اسکیمای دیتابیس نیست.
- هویت کاربر روی پنل یک UUID است، نه یک username معمولی؛ فیلد «name» فقط
  یک برچسب نمایشی است. موقع ساخت یک UUID تازه تولید می‌کنیم و لینک
  اشتراک را از روی همان UUID می‌سازیم؛ برای عملیات‌های بعدی (حذف/دریافت
  مصرف) که فقط username پروژه را داریم، لیست کاربرهای پنل خوانده می‌شود
  و بر اساس «name» تطبیق‌شده با username پیدا می‌شود.
- بدون گروه/inbound/قالب: نیازی به «تعیین کاربر نمونه» نیست.
- ⚠️ نکته‌ی مهم: روی اکثر نصب‌های Hiddify، آدرس API ادمین (که معمولاً یک
  admin_proxy_path مخفی/تصادفی دارد) با آدرس عمومیِ لینک اشتراک (sub link)
  یکی نیست. به همین دلیل، درست مثل 3X-UI، یک «آدرس پایه‌ی Subscription»
  جدا از آدرس ادمین گرفته می‌شود و در همان ستون xui_sub_base_url ذخیره
  می‌شود (بازاستفاده از ستون موجود؛ نیازی به تغییر اسکیما نیست). اگر خالی
  بماند، به‌عنوان آخرین راه‌حل از همان آدرس API استفاده می‌شود.
"""
import uuid as uuid_lib
import asyncio
import aiohttp
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

from .base import BasePanelProvider, PanelUserResult, PanelError, PanelUsernameTakenError


class HiddifyProvider(BasePanelProvider):

    def _session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(connector=self._build_connector())

    def _base_url(self) -> str:
        return self.server["api_url"].rstrip("/")

    def _sub_base_url(self) -> str:
        try:
            url = self.server["xui_sub_base_url"]
        except (KeyError, IndexError):
            url = None
        return (url or self.server["api_url"]).rstrip("/")

    def _headers(self) -> dict:
        return {
            "Hiddify-API-Key": self.server["api_password"],
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _find_by_name(self, session: aiohttp.ClientSession, username: str) -> dict:
        """چون شناسه‌ی واقعی کاربر روی هیدیفای UUID است نه username پروژه،
        برای پیدا کردن کاربر لیست کاربرهای پنل را می‌خوانیم و بر اساس فیلد
        «name» (که موقع ساخت، همان username پروژه در آن ذخیره شده) می‌گردیم."""
        try:
            async with session.get(
                f"{self._base_url()}/api/v2/admin/user/",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise PanelError(f"خطا در دریافت لیست کاربران (کد {resp.status}): {text[:300]}")
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise PanelError(f"خطا در اتصال به پنل: {e or 'پاسخی از سرور در زمان مقرر دریافت نشد (timeout)'}") from e

        users = data if isinstance(data, list) else data.get("users", [])
        for u in users:
            if u.get("name") == username:
                return u
        raise PanelError(f"کاربری با نام «{username}» روی پنل پیدا نشد.")

    async def find_username_by_uuid(self, user_uuid: str):
        """نام (name) کاربری که uuid آن برابر user_uuid است (یا None)."""
        async with self._session() as session:
            try:
                async with session.get(
                    f"{self._base_url()}/api/v2/admin/user/",
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status >= 400:
                        raise PanelError(f"خطا در دریافت لیست کاربران (کد {resp.status}).")
                    data = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise PanelError(f"خطا در اتصال به پنل: {e or 'timeout'}") from e
        users = data if isinstance(data, list) else data.get("users", [])
        for u in users:
            if str(u.get("uuid")) == user_uuid and u.get("name"):
                return u["name"]
        return None

    async def fetch_template_from_user(self, sample_username: str) -> dict:
        raise PanelError(
            "پنل Hiddify نیازی به «کاربر نمونه»/قالب ندارد؛ این مرحله برای این نوع پنل لازم نیست."
        )

    async def create_user(self, username: str, volume_gb: int, duration_days: int) -> PanelUserResult:
        new_uuid = str(uuid_lib.uuid4())
        payload = {
            "uuid": new_uuid,
            "name": username,
            "added_by_uuid": self.server["api_password"],
            "current_usage_GB": 0,
            "usage_limit_GB": volume_gb,
            "package_days": duration_days,
            "comment": "ساخته‌شده توسط ShopVPN (کانفیگ شخصی)",
        }
        async with self._session() as session:
            try:
                async with session.post(
                    f"{self._base_url()}/api/v2/admin/user/",
                    json=payload,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        raise PanelError(f"خطا در ساخت کاربر روی پنل (کد {resp.status}): {text[:300]}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise PanelError(f"خطا در اتصال به پنل: {e or 'پاسخی از سرور در زمان مقرر دریافت نشد (timeout)'}") from e

        sub_url = f"{self._sub_base_url()}/{new_uuid}/"
        return PanelUserResult(username=username, subscription_url=sub_url, raw=payload)

    async def delete_user(self, username: str) -> bool:
        async with self._session() as session:
            try:
                user = await self._find_by_name(session, username)
            except PanelError:
                return False
            try:
                async with session.delete(
                    f"{self._base_url()}/api/v2/admin/user/{user['uuid']}/",
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    return resp.status < 400
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise PanelError(f"خطا در اتصال به پنل: {e or 'پاسخی از سرور در زمان مقرر دریافت نشد (timeout)'}") from e

    async def get_user_usage(self, username: str) -> dict:
        async with self._session() as session:
            user = await self._find_by_name(session, username)
        used_gb = user.get("current_usage_GB", 0) or 0
        limit_gb = user.get("usage_limit_GB", 0) or 0
        return {
            "used_bytes": int(float(used_gb) * (1024 ** 3)),
            "data_limit_bytes": int(float(limit_gb) * (1024 ** 3)),
            "status": "active",  # هیدیفای فیلد status جدا مثل Marzban ندارد
        }

    async def get_user(self, username: str) -> PanelUserResult:
        async with self._session() as session:
            user = await self._find_by_name(session, username)
        sub_url = f"{self._sub_base_url()}/{user.get('uuid')}/"
        return PanelUserResult(username=username, subscription_url=sub_url, raw=user)

    async def test_connection(self) -> bool:
        try:
            async with self._session() as session:
                async with session.get(
                    f"{self._base_url()}/api/v2/admin/user/",
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status >= 400:
                        self.last_error = f"پاسخ پنل با کد {resp.status}؛ کلید API یا آدرس پنل را بررسی کنید."
                        return False
                    return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self.last_error = f"خطا در اتصال به پنل: {e or 'پاسخی از سرور در زمان مقرر دریافت نشد (timeout)'}"
            return False

    supports_data_limit_sync = True

    async def set_data_limit_bytes(self, username: str, limit_bytes: int, reenable: bool = False) -> None:
        """فقط usage_limit_GB را عوض می‌کند؛ مصرف/انقضا/وضعیت دست‌نخورده می‌ماند. 0 = نامحدود."""
        async with self._session() as session:
            user = await self._find_by_name(session, username)
            payload = dict(user)
            limit_gb = round(int(limit_bytes) / (1024 ** 3), 6)
            if int(limit_bytes) > 0:
                limit_gb = max(limit_gb, 0.000001)  # هرگز 0 (= نامحدود) برای سقف مثبت
            payload["usage_limit_GB"] = limit_gb
            try:
                async with session.patch(
                    f"{self._base_url()}/api/v2/admin/user/{user['uuid']}/",
                    json=payload,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        raise PanelError(f"خطا در اعمال سقف حجم روی پنل (کد {resp.status}): {text[:300]}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise PanelError(f"خطا در اتصال به پنل: {e or 'پاسخی از سرور در زمان مقرر دریافت نشد (timeout)'}") from e

    async def update_user(self, username: str, add_volume_gb: float = 0, add_days: int = 0,
                           reset_usage: bool = False, preserve_remaining: bool = False,
                           unlimited_volume: bool = False) -> PanelUserResult:
        async with self._session() as session:
            user = await self._find_by_name(session, username)
            # تمدید «کامل» (reset_usage=True) یعنی مصرف صفر می‌شود. سقف حجم جدید به دو
            # شکل ممکن است محاسبه شود:
            #   - preserve_remaining=False (پیش‌فرض، مثلاً تمدید خودکار): سقف با بستهٔ
            #     تازه جایگزین می‌شود (نه رویش اضافه) - یعنی هر سیکل یک بستهٔ کاملاً تازه.‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
            #   - preserve_remaining=True (تمدید کامل دستی): حجم باقیمانده‌ی مصرف‌نشده‌ی
            #     قبلی حفظ و بستهٔ جدید رویش اضافه می‌شود؛ وگرنه مثلاً کاربری که از ۵۰ گیگ
            #     فقط ۱۰ گیگ مصرف کرده (۴۰ گیگ باقیمانده)، با تمدید کامل به ۵۰ گیگ جدید،
            #     سقفش دوباره فقط ۵۰ می‌شد و همان ۴۰ گیگ باقیمانده از دست می‌رفت - در حالی‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
            #     که زمان (package_days) همیشه جمعی محاسبه می‌شود و این ناهم‌خوانی همان
            #     مشکل گزارش‌شده بود.
            # تمدید «افزایشی» (reset_usage=False) همیشه روی سقف قبلی اضافه می‌شود.
            if unlimited_volume:
                new_limit = 0  # 0 = نامحدود
            elif reset_usage and preserve_remaining:
                remaining = max(
                    float(user.get("usage_limit_GB") or 0) - float(user.get("current_usage_GB") or 0), 0,
                )
                new_limit = remaining + float(add_volume_gb or 0)
            elif add_volume_gb:
                new_limit = add_volume_gb if reset_usage else float(user.get("usage_limit_GB") or 0) + add_volume_gb
            else:
                new_limit = user.get("usage_limit_GB")
            new_days = int(user.get("package_days") or 0) + add_days if add_days else user.get("package_days")
            payload = dict(user)
            payload["usage_limit_GB"] = new_limit
            payload["package_days"] = new_days
            if reset_usage:
                payload["current_usage_GB"] = 0
            try:
                async with session.patch(
                    f"{self._base_url()}/api/v2/admin/user/{user['uuid']}/",
                    json=payload,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        raise PanelError(f"خطا در بروزرسانی کاربر روی پنل (کد {resp.status}): {text[:300]}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise PanelError(f"خطا در اتصال به پنل: {e or 'پاسخی از سرور در زمان مقرر دریافت نشد (timeout)'}") from e

        sub_url = f"{self._sub_base_url()}/{user['uuid']}/"
        return PanelUserResult(username=username, subscription_url=sub_url, raw=payload)

    async def set_enabled(self, username: str, enabled: bool) -> None:
        async with self._session() as session:
            user = await self._find_by_name(session, username)
            payload = dict(user)
            payload["enable"] = bool(enabled)
            try:
                async with session.patch(
                    f"{self._base_url()}/api/v2/admin/user/{user['uuid']}/",
                    json=payload,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        raise PanelError(f"خطا در تغییر وضعیت کاربر (کد {resp.status}): {text[:300]}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise PanelError(f"خطا در اتصال به پنل: {e or 'پاسخی از سرور در زمان مقرر دریافت نشد (timeout)'}") from e

    async def rename_user(self, username: str, new_username: str) -> None:
        """روی هیدیفای «name» فقط یک برچسب نمایشی است (شناسه‌ی واقعی uuid
        است)، پس تغییر آن کاملاً بی‌خطر است و لینک/مصرف قبلی دست‌نخورده می‌ماند."""
        async with self._session() as session:
            user = await self._find_by_name(session, username)
            payload = dict(user)
            payload["name"] = new_username
            try:
                async with session.patch(
                    f"{self._base_url()}/api/v2/admin/user/{user['uuid']}/",
                    json=payload,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        raise PanelError(f"خطا در تغییر نام کاربر (کد {resp.status}): {text[:300]}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise PanelError(f"خطا در اتصال به پنل: {e or 'پاسخی از سرور در زمان مقرر دریافت نشد (timeout)'}") from e

    supports_fingerprints = True

    async def get_fingerprints(self, username: str) -> list:
        """UUID کاربر، که در هیدیفای همزمان شناسه‌ی کاربر و توکن لینک اشتراک است."""
        async with self._session() as session:
            user = await self._find_by_name(session, username)
        return [str(user["uuid"])] if user.get("uuid") else []

    async def revoke_credentials(self, username: str) -> PanelUserResult:
        """روی هیدیفای خودِ UUID هم شناسه‌ی کاربر و هم لینک اشتراک است، پس
        «قطع دسترسی و لینک جدید» یعنی یک UUID تازه بسازیم و در همان PUT که
        update_user استفاده می‌کند جایگزین کنیم؛ usage_limit_GB/package_days/
        current_usage_GB بدون تغییر می‌مانند (لینک قدیمی دیگر کار نمی‌کند چون
        دیگر با هیچ کاربری روی پنل match نمی‌شود).
        ⚠️ این رفتار (تغییر uuid از طریق فیلد uuid در بدنه‌ی درخواست) بر اساس
        همان الگوی به‌کاررفته در update_user این پروژه است. متد HTTP این
        درخواست از PUT به PATCH تغییر کرد چون یک نصب واقعی Hiddify روی PUT
        خطای «405 Method Not Allowed» می‌داد (یعنی API v2 هیدیفای اصلاً PUT
        را روی این مسیر ثبت نکرده و فقط PATCH/GET/POST/DELETE را می‌شناسد) -
        اگر باز هم خطای مشابه دیده شد، حتماً گزارش شود."""
        async with self._session() as session:
            user = await self._find_by_name(session, username)
            new_uuid = str(uuid_lib.uuid4())
            payload = dict(user)
            payload["uuid"] = new_uuid
            try:
                async with session.patch(
                    f"{self._base_url()}/api/v2/admin/user/{user['uuid']}/",
                    json=payload,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        raise PanelError(f"خطا در قطع دسترسی/تولید لینک جدید (کد {resp.status}): {text[:300]}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise PanelError(f"خطا در اتصال به پنل: {e or 'پاسخی از سرور در زمان مقرر دریافت نشد (timeout)'}") from e

        sub_url = f"{self._sub_base_url()}/{new_uuid}/"
        return PanelUserResult(username=username, subscription_url=sub_url, raw=payload)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
