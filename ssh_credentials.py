# -*- coding: utf-8 -*-
"""Encrypt/decrypt SSH passwords at rest for user self-service delivery.

The Fernet key is supplied through SHOPVPN_SSH_CREDENTIAL_KEY and must be the
same for the bot, web panel and maintenance CLI. Never store the key in SQLite.
"""
from __future__ import annotations
import os
from cryptography.fernet import Fernet, InvalidToken


_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ssh_credential_key")


def _auto_key() -> bytes:
    """بدون تنظیم دستی: اگر env خالی بود، یک کلید یک‌بار ساخته و در فایل 0600 کنار پروژه نگه داشته می‌شود
    (همه‌ی پروسه‌های ShopVPN روی همین سرور آن را مشترکاً می‌خوانند). برای کلید جدا، env را تنظیم کنید."""
    try:
        with open(_KEY_FILE, "rb") as fh:
            data = fh.read().strip()
            if data:
                return data
    except FileNotFoundError:
        pass
    key = Fernet.generate_key()
    fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
    except Exception:
        raise
    return key


def _fernet() -> Fernet:
    key = os.getenv("SHOPVPN_SSH_CREDENTIAL_KEY", "").strip().encode("ascii", "ignore")
    if not key:
        try:
            key = _auto_key()
        except FileExistsError:  # race: another process created it first
            key = _auto_key()
        except OSError as exc:
            raise RuntimeError(
                "کلید رمزنگاری SSH ساخته نشد؛ SHOPVPN_SSH_CREDENTIAL_KEY را تنظیم کنید یا "
                "دسترسی نوشتن پوشه پروژه را بررسی کنید."
            ) from exc
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("SHOPVPN_SSH_CREDENTIAL_KEY باید یک کلید معتبر Fernet باشد.") from exc


def encrypt_credential(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("رمز SSH خالی است.")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_credential(value: str) -> str:
    if not value:
        raise RuntimeError("برای این حساب رمز رمزگذاری‌شده‌ای ذخیره نشده است؛ با پشتیبانی تماس بگیرید.")
    try:
        return _fernet().decrypt(str(value).encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("رمز SSH قابل بازیابی نیست؛ کلید رمزگذاری تغییر کرده یا نامعتبر است.") from exc
