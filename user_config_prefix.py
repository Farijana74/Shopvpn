"""Per-customer prefix for newly provisioned configuration names."""
import re


def get_prefix(db, user_id):
    if user_id is None:
        return ''
    saved = db.get_setting(f'user_config_prefix:{int(user_id)}', '')
    if saved:
        return saved
    default = db.get_custom_config_prefix()
    if default:
        return default
    user = db.get_user(int(user_id))
    username = (user['username'] or '').lstrip('@') if user else ''
    return username if re.fullmatch(r'[A-Za-z0-9_]{1,32}', username) else str(int(user_id))


def set_prefix(db, user_id, value):
    value = (value or '').strip()
    if value and not re.fullmatch(r'[A-Za-z0-9_]{1,32}', value):
        raise ValueError('پیشوند باید ۱ تا ۳۲ کاراکتر و فقط شامل حروف انگلیسی، عدد یا آندرلاین باشد.')
    db.set_setting(f'user_config_prefix:{int(user_id)}', value)
    return value or get_prefix(db, user_id)


def reserve_order_name(db, user_id, order_id):
    """Atomically allocate a per-customer sequence and persist it on the order."""
    prefix = get_prefix(db, user_id)
    key = f'user_config_sequence:{int(user_id)}'
    with db._get_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = conn.execute('SELECT user_id, config_name FROM orders WHERE id=?', (int(order_id),)).fetchone()
        if not order or int(order['user_id']) != int(user_id):
            raise ValueError('سفارش یا صاحب سفارش معتبر نیست.')
        if order['config_name']:
            return order['config_name']
        row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        sequence = int(row['value']) if row and str(row['value']).isdigit() else 0
        while True:
            sequence += 1
            name = f'{prefix}_{sequence}'
            exists = conn.execute('SELECT 1 FROM orders WHERE config_name=? COLLATE NOCASE LIMIT 1', (name,)).fetchone()
            if not exists:
                exists = conn.execute('SELECT 1 FROM custom_configs WHERE username=? COLLATE NOCASE LIMIT 1', (name,)).fetchone()
            if not exists:
                break
        conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(sequence)))
        conn.execute('UPDATE orders SET config_name=?,provision_username_generated=2 WHERE id=?', (name, int(order_id)))
        return name
