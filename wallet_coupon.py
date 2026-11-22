"""Atomic reservation of discount codes for wallet top-ups."""
from datetime import datetime


class WalletCouponError(ValueError):
    pass


def _release_abandoned(conn):
    rows = conn.execute("SELECT id,discount_code_id FROM wallet_topups WHERE discount_code_id IS NOT NULL "
                        "AND status='pending' AND payment_started=0 AND datetime(created_at) < datetime('now','-30 minutes')").fetchall()
    for row in rows:
        changed = conn.execute("UPDATE wallet_topups SET status='rejected',close_reason='coupon_abandoned',updated_at=? "
                               "WHERE id=? AND status='pending' AND payment_started=0", (datetime.utcnow().isoformat(), row['id']))
        if changed.rowcount:
            deleted = conn.execute('DELETE FROM discount_redemptions WHERE topup_id=?', (row['id'],))
            if deleted.rowcount:
                conn.execute('UPDATE discount_codes SET used_count=MAX(used_count-1,0) WHERE id=?', (row['discount_code_id'],))


def _check(conn, code, user_id, credit_amount):
    if not isinstance(credit_amount, int) or isinstance(credit_amount, bool) or credit_amount <= 0:
        raise WalletCouponError('مبلغ شارژ نامعتبر است.')
    row = conn.execute('SELECT * FROM discount_codes WHERE code=?', (str(code or '').strip().upper(),)).fetchone()
    if not row:
        raise WalletCouponError('کد تخفیف یافت نشد.')
    if is_product_prize(row):
        raise WalletCouponError('این کد جایزه فقط برای محصول مشخص‌شده قابل استفاده است.')
    if not row['is_active'] or (row['max_uses'] and row['used_count'] >= row['max_uses']):
        raise WalletCouponError('این کد غیرفعال است یا ظرفیت آن به پایان رسیده است.')
    if row['expires_at'] and row['expires_at'] < datetime.utcnow().isoformat():
        raise WalletCouponError('این کد تخفیف منقضی شده است.')
    if row['min_purchase'] and credit_amount < row['min_purchase']:
        raise WalletCouponError(f"حداقل مبلغ شارژ برای این کد {row['min_purchase']:,} تومان است.")
    if row['max_purchase'] and credit_amount > row['max_purchase']:
        raise WalletCouponError(f"حداکثر مبلغ شارژ برای این کد {row['max_purchase']:,} تومان است.")
    user = conn.execute('SELECT * FROM users WHERE telegram_id=?', (int(user_id),)).fetchone()
    if not user or user['is_blocked']:
        raise WalletCouponError('حساب کاربری معتبر نیست.')
    audience = row['audience'] or 'all'
    is_reseller = bool(user['is_reseller'] or user['reseller_tier'])
    if (audience == 'normal' and is_reseller) or (audience == 'reseller' and not is_reseller):
        raise WalletCouponError('این کد برای حساب شما معتبر نیست.')
    if row['first_purchase_only'] and conn.execute("SELECT 1 FROM orders WHERE user_id=? AND status IN ('pending','processing','approved') LIMIT 1", (user_id,)).fetchone():
        raise WalletCouponError('این کد فقط برای اولین خرید معتبر است.')
    if row['per_user_limit']:
        used = conn.execute('SELECT COUNT(*) FROM discount_redemptions WHERE code_id=? AND user_id=?', (row['id'], user_id)).fetchone()[0]
        if used >= row['per_user_limit']:
            raise WalletCouponError('سقف استفاده شما از این کد به پایان رسیده است.')
    discount = (credit_amount * int(row['percent'] or 0)) // 100 if row['percent'] else int(row['fixed_amount'] or 0)
    if row['max_discount_amount'] and row['percent']:
        discount = min(discount, row['max_discount_amount'])
    discount = max(0, min(discount, credit_amount))
    if not discount:
        raise WalletCouponError('مبلغ تخفیف این کد برای شارژ انتخابی صفر است.')
    return row, discount


def quote(db, code, user_id, credit_amount):
    with db._get_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _release_abandoned(conn)
        row, discount = _check(conn, code, user_id, credit_amount)
        return {'code_id': row['id'], 'code': row['code'], 'credit_amount': credit_amount,
                'discount_amount': discount, 'pay_amount': credit_amount - discount}


def reserve(db, code, user_id, credit_amount):
    """Reserve one use and create a linked pending top-up in one transaction."""
    with db._get_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _release_abandoned(conn)
        row, discount = _check(conn, code, user_id, credit_amount)
        paid = credit_amount - discount
        topup_id = conn.execute(
            "INSERT INTO wallet_topups(user_id,amount,credit_amount,discount_code_id,discount_amount,status) VALUES(?,?,?,?,?,'pending')",
            (int(user_id), paid, credit_amount, row['id'], discount)).lastrowid
        conn.execute('UPDATE discount_codes SET used_count=used_count+1 WHERE id=?', (row['id'],))
        conn.execute('INSERT INTO discount_redemptions(code_id,user_id,topup_id) VALUES(?,?,?)', (row['id'], user_id, topup_id))
        return topup_id


def begin_payment(db, topup_id, user_id, amount, method):
    """Allow at most one payment attempt for a coupon top-up."""
    with db._get_conn() as conn:
        cur = conn.execute("UPDATE wallet_topups SET payment_started=1,payment_method=? "
                           "WHERE id=? AND user_id=? AND amount=? AND status='pending' AND payment_started=0",
                           (method, topup_id, user_id, amount))
        if cur.rowcount == 1:
            return True
        if method == 'card':
            return bool(conn.execute("SELECT 1 FROM wallet_topups WHERE id=? AND user_id=? AND amount=? "
                                     "AND status='pending' AND payment_method='card' AND receipt_file_id IS NULL",
                                     (topup_id, user_id, amount)).fetchone())
        return False


def is_product_prize(row):
    return bool(row and row['source'] in ('lootbox_prize', 'season_prize') and row['product_id'] and row['percent'] == 100)


def has_product_prize(db, user_id, product_id, code=None):
    """Retain the existing transferable, product-specific prize-code behavior."""
    with db._get_conn() as conn:
        sql = ("SELECT 1 FROM discount_codes d WHERE d.source IN ('lootbox_prize','season_prize') "
               "AND d.product_id=? AND d.percent=100 AND d.is_active=1 "
               "AND (COALESCE(d.max_uses,0)=0 OR d.used_count<d.max_uses) "
               "AND (d.expires_at IS NULL OR d.expires_at='' OR d.expires_at>=?)")
        args = [product_id, datetime.utcnow().isoformat()]
        if code is not None:
            sql += ' AND d.code=?'
            args.append(code)
        return bool(conn.execute(sql + ' LIMIT 1', args).fetchone())


def topup_for_payment(db, user_id, data, amount, method):
    topup_id = data.get('coupon_topup_id')
    if not topup_id:
        return db.create_topup(user_id, amount)
    if not begin_payment(db, int(topup_id), user_id, amount, method):
        raise WalletCouponError('برای این درخواست روش پرداخت انتخاب شده است؛ دوباره پرداخت نکنید.')
    return int(topup_id)


def conditions(row):
    minimum = int(row['min_purchase'] or 0) if row else 0
    return (f'این کد فقط برای شارژ کیف پول از {minimum:,} تومان قابل استفاده است.'
            if minimum else 'این کد فقط برای شارژ کیف پول قابل استفاده است و حداقل مبلغ اختصاصی ندارد.')
