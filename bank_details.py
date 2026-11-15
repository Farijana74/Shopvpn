"""Validation for optional Iranian account details."""
import unicodedata


def normalize_sheba(value: str) -> str:
    value = ''.join(str(unicodedata.decimal(c)) if c.isdecimal() else c
                    for c in value if not c.isspace() and c not in '-\u200e\u200f').upper()
    if value.isascii() and value.isdigit() and len(value) == 24:
        value = 'IR' + value
    if len(value) != 26 or not value.startswith('IR') or not value[2:].isascii() or not value[2:].isdigit():
        raise ValueError('شماره شبا باید IR و ۲۴ رقم باشد.')
    if int(value[4:] + '1827' + value[2:4]) % 97 != 1:
        raise ValueError('شماره شبا معتبر نیست؛ لطفاً دوباره بررسی کنید.')
    return value
