"""Per-customer prefix for newly provisioned configuration names."""
import re


def _highest_saved_number(conn, prefix):
    pattern = re.compile(re.escape(prefix) + r'_(\d+)(?:_\d+)?$', re.IGNORECASE)
    highest = 0
    for table, column in (('orders', 'config_name'), ('custom_configs', 'username')):
        rows = conn.execute(f'SELECT {column} FROM {table} WHERE substr({column},1,?)=? COLLATE NOCASE', (len(prefix) + 1, prefix + '_'))
        for saved in rows:
            match = pattern.fullmatch(saved[0] or '')
            if match:
                highest = max(highest, int(match.group(1)))
    return highest


def _next_global_name(conn, prefix, minimum=0):
    """Allocate the next number for a shared prefix inside a write transaction."""
    key = f'global_config_sequence:{prefix.lower()}'
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    number = int(row['value']) if row and str(row['value']).isdigit() else 0
    if not row:
        # Include names created by earlier versions before starting the counter.
        number = max(number, _highest_saved_number(conn, prefix))
    number = max(number, minimum)
    while True:
        number += 1
        name = f'{prefix}_{number}'
        if not conn.execute('SELECT 1 FROM orders WHERE config_name=? COLLATE NOCASE LIMIT 1', (name,)).fetchone() and not conn.execute('SELECT 1 FROM custom_configs WHERE username=? COLLATE NOCASE LIMIT 1', (name,)).fetchone():
            break
    conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(number)))
    return name


def global_sequence_start(db, prefix):
    """Return the first safe next number without consuming it."""
    with db._get_conn() as conn:
        row = conn.execute('SELECT value FROM settings WHERE key=?', (f'global_config_sequence:{prefix.lower()}',)).fetchone()
        saved = int(row['value']) if row and str(row['value']).isdigit() else 0
        return max(saved, _highest_saved_number(conn, prefix)) + 1


def set_global_sequence_start(db, expected_prefix, start):
    """Set the next number atomically without moving behind existing names."""
    if not isinstance(start, int) or not 1 <= start <= 2_000_000_000:
        raise ValueError('عدد شروع باید بین ۱ و ۲٬۰۰۰٬۰۰۰٬۰۰۰ باشد.')
    with db._get_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute("SELECT value FROM settings WHERE key='custom_config_prefix'").fetchone()
        prefix = str(current['value'] or '').strip() if current else ''
        if not prefix or prefix != expected_prefix:
            raise ValueError('پیشوند عمومی تغییر کرده یا غیرفعال است؛ دوباره وارد این بخش شوید.')
        key = f'global_config_sequence:{prefix.lower()}'
        row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        saved = int(row['value']) if row and str(row['value']).isdigit() else 0
        minimum = max(saved, _highest_saved_number(conn, prefix)) + 1
        if start < minimum:
            raise ValueError(f'برای جلوگیری از تکرار نام، عدد شروع باید حداقل {minimum:,} باشد.')
        conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(start - 1)))
    return start


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
    global_prefix = db.get_custom_config_prefix()
    uses_global = bool(global_prefix and prefix == global_prefix and not db.get_setting(f'user_config_prefix:{int(user_id)}', ''))
    key = f'user_config_sequence:{int(user_id)}'
    with db._get_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = conn.execute('SELECT user_id, config_name FROM orders WHERE id=?', (int(order_id),)).fetchone()
        if not order or int(order['user_id']) != int(user_id):
            raise ValueError('سفارش یا صاحب سفارش معتبر نیست.')
        if order['config_name']:
            return order['config_name']
        if uses_global:
            name = _next_global_name(conn, prefix)
            conn.execute('UPDATE orders SET config_name=?,provision_username_generated=3 WHERE id=?', (name, int(order_id)))
            return name
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


def advance_global_order_name(db, order_id, previous):
    """Advance only after a definite name collision, without adopting that account."""
    prefix, sep, suffix = previous.rpartition('_')
    if not sep or not suffix.isdigit():
        raise ValueError('نام رزروشده قابل شماره‌گذاری مجدد نیست.')
    with db._get_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = conn.execute('SELECT config_name,provision_username_generated FROM orders WHERE id=?', (int(order_id),)).fetchone()
        if not order or order['provision_username_generated'] != 3:
            raise ValueError('نام سفارش برای شماره‌گذاری عمومی رزرو نشده است.')
        if order['config_name'] != previous:
            return order['config_name']
        name = _next_global_name(conn, prefix, int(suffix))
        conn.execute('UPDATE orders SET config_name=? WHERE id=?', (name, int(order_id)))
        return name
