"""Settings keys and validation for native Telegram custom button icons."""
import re


def validate_icon(value):
    value = str(value or '').strip()
    if value and not re.fullmatch(r'[0-9]{1,32}', value):
        raise ValueError('شناسه ایموجی پرمیوم باید فقط عدد باشد؛ برای حذف، خالی بگذارید.')
    return value


def icon_key(group, key):
    if group == 'main_menu':
        prefix = key
    elif group == 'admin_categories':
        prefix = f'catlbl_{key}'
    elif group.startswith('admin_items__') or group in ('buyflow', 'buyflow_confirm', 'account_hub'):
        prefix = key
    elif group == 'payment_methods':
        prefix = f'paymeth_customgw_{key.split(":", 1)[1]}' if key.startswith('customgw:') else f'paymeth_{key}'
    else:
        prefix = f'ubtn_{key}'
    return prefix + '_icon_custom_emoji_id'


def button_icon(db, style_key):
    key = style_key.removesuffix('_style') + '_icon_custom_emoji_id'
    value = str(db.get_setting(key, '') or '').strip()
    return value if re.fullmatch(r'[0-9]{1,32}', value) else None
