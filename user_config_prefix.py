"""Per-customer prefix for newly provisioned configuration names."""
import re


def get_prefix(db, user_id):
    if user_id is None:
        return ''
    return db.get_setting(f'user_config_prefix:{int(user_id)}', '') or str(int(user_id))


def set_prefix(db, user_id, value):
    value = (value or '').strip()
    if value and not re.fullmatch(r'[A-Za-z0-9]{1,12}', value):
        raise ValueError('پیشوند باید ۱ تا ۱۲ کاراکتر و فقط شامل حروف انگلیسی یا عدد باشد.')
    db.set_setting(f'user_config_prefix:{int(user_id)}', value)
    return value or str(int(user_id))
