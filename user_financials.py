"""Tenant-scoped financial history and read-only summaries of owned shops."""
import html
import sqlite3
from contextlib import closing
from pathlib import Path


ORDER_AMOUNT = ('MAX(COALESCE(o.final_price,COALESCE(o.base_price,0)-COALESCE(o.discount_amount,0)'
                '-COALESCE(o.wallet_used,0)),0)+COALESCE(o.wallet_used,0)')


def order_filter(conn, alias='o'):
    clauses = [f'COALESCE({alias}.managed_internal,0)=0',
               f"NOT EXISTS (SELECT 1 FROM settings s WHERE s.key='managed_trial_billing_product_id' AND CAST(s.value AS INTEGER)={alias}.product_id)",
               f"{alias}.id NOT IN (SELECT order_id FROM custom_configs WHERE source='test' AND order_id IS NOT NULL)"]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='managed_trials'").fetchone():
        clauses.append(f'{alias}.id NOT IN (SELECT order_id FROM managed_trials WHERE order_id IS NOT NULL)')
    return ' AND '.join(clauses)


def count_orders(conn, user_id=None):
    where = order_filter(conn)
    args = ()
    if user_id is not None:
        where += ' AND o.user_id=?'
        args = (user_id,)
    row = conn.execute(f"""SELECT COUNT(*) total,
        COALESCE(SUM(status='approved'),0) approved,
        COALESCE(SUM(status IN ('pending','processing')),0) pending,
        COALESCE(SUM(status='rejected'),0) rejected,
        COALESCE(SUM(status='expired'),0) expired,
        COALESCE(SUM(CASE WHEN status='approved' THEN {ORDER_AMOUNT} ELSE 0 END),0) spent
        FROM orders o WHERE {where}""", args).fetchone()
    return dict(row)


def owned_shop_orders(db, user_id):
    """Root admins see owned shops; a reseller sees itself and direct children."""
    bot_id = db.get_setting('managed_reseller_bot_id', '')
    root_path = db.get_setting('managed_root_db_path', '') if bot_id else db.db_path
    result = dict(shops=[], total=0, approved=0, pending=0, rejected=0, expired=0, unavailable=0)
    try:
        with closing(sqlite3.connect(Path(root_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=4)) as root:
            root.row_factory = sqlite3.Row
            query = 'SELECT id,bot_username,db_path,is_active FROM reseller_bots WHERE owner_telegram_id=?'
            args = [user_id]
            if bot_id:
                query += ' AND (managed_parent_bot_id=? OR id=?)'
                args += [int(bot_id), int(bot_id)]
            rows = root.execute(query + ' ORDER BY id', args).fetchall()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        result['unavailable'] = 1
        return result
    for row in rows:
        shop = dict(id=row['id'], username=row['bot_username'], active=bool(row['is_active']), available=False)
        try:
            path = Path(row['db_path'])
            if not path.is_absolute():
                path = Path(root_path).resolve().parent / path
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=4)) as conn:
                conn.row_factory = sqlite3.Row
                counts = count_orders(conn)
            shop.update(available=True, counts=counts)
            for key in ('total','approved','pending','rejected','expired'):
                result[key] += counts[key]
        except (sqlite3.Error, OSError, ValueError, TypeError):
            result['unavailable'] += 1
        result['shops'].append(shop)
    return result


def financial_history(db, user_id, page=0, kind='all', limit=6):
    if kind not in ('all','order','topup','wallet'):
        raise ValueError('نوع تراکنش معتبر نیست.')
    page, limit = max(0,int(page)), max(1,min(20,int(limit)))
    with db._get_conn() as conn:
        queries, args = [], []
        if kind in ('all','order'):
            queries.append(f"""SELECT 'order' event_kind,o.id,o.created_at,
                {ORDER_AMOUNT} amount,o.status,
                CASE WHEN o.is_renewal=1 THEN 'تمدید سرویس' WHEN o.is_custom_config=1 THEN 'کانفیگ شخصی'
                     ELSE COALESCE(p.name,'محصول حذف‌شده') END title,
                COALESCE(o.wallet_used,0) wallet_used,COALESCE(o.discount_amount,0) discount_amount,
                NULL balance_before,NULL balance_after,'' note,COALESCE(o.quantity,1) quantity
                FROM orders o LEFT JOIN products p ON p.id=o.product_id
                WHERE o.user_id=? AND {order_filter(conn)}""")
            args.append(user_id)
        if kind in ('all','topup'):
            queries.append("""SELECT 'topup' event_kind,id,created_at,amount,status,'شارژ کیف پول' title,
                0 wallet_used,0 discount_amount,NULL balance_before,NULL balance_after,'' note,1 quantity
                FROM wallet_topups WHERE user_id=?""")
            args.append(user_id)
        if kind in ('all','wallet'):
            queries.append("""SELECT 'wallet' event_kind,id,created_at,delta amount,'recorded' status,
                COALESCE(kind,'other') title,0 wallet_used,0 discount_amount,balance_before,balance_after,
                COALESCE(note,'') note,1 quantity FROM wallet_transactions WHERE user_id=?""")
            args.append(user_id)
        union = ' UNION ALL '.join(queries)
        total = conn.execute(f'SELECT COUNT(*) FROM ({union})', args).fetchone()[0]
        page = min(page,max(0,(total-1)//limit))
        rows = conn.execute(f'SELECT * FROM ({union}) ORDER BY datetime(created_at) DESC,id DESC,event_kind ASC LIMIT ? OFFSET ?',
                            [*args,limit,page*limit]).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        if item['event_kind'] == 'wallet':
            item['title'] = db.WALLET_TX_KIND_LABELS.get(item['title'], 'سایر تغییرات کیف پول')
    return dict(items=items,total=total,page=page,limit=limit,pages=max(1,(total+limit-1)//limit),kind=kind)


def history_text(user_id, data):
    from jalali import to_jalali_str
    esc = lambda value: html.escape(str(value))
    statuses = {'approved':'تأییدشده','pending':'در انتظار','processing':'در حال پردازش',
                'rejected':'ردشده','expired':'منقضی','recorded':'ثبت‌شده'}
    blocks = [f'📜 ریز تراکنش‌های کاربر {user_id}\nصفحه {data["page"]+1} از {data["pages"]} | {data["total"]:,} رکورد',
              'سفارش، درخواست شارژ و تغییر کیف پول رکوردهای جدا هستند؛ مبالغ آن‌ها با هم جمع نمی‌شوند.']
    for item in data['items']:
        event = {'order':'سفارش','topup':'شارژ','wallet':'کیف پول'}[item['event_kind']]
        lines = [f'<b>{event} #{item["id"]} — {esc(item["title"][:80])}</b>',
                 f'وضعیت: {esc(statuses.get(item["status"],item["status"]))}',
                 f'مبلغ: {item["amount"]:+,} تومان' if item['event_kind']=='wallet' else f'مبلغ: {item["amount"]:,} تومان']
        if item['event_kind']=='wallet':
            lines.append(f'موجودی قبل: {item["balance_before"]:,} | بعد: {item["balance_after"]:,}')
        if item['event_kind']=='order':
            lines.append(f'تعداد: {item["quantity"]} | از کیف پول: {item["wallet_used"]:,} | تخفیف: {item["discount_amount"]:,} تومان')
        if item['note']:
            lines.append(esc(item['note'][:120]))
        lines.append(esc(to_jalali_str(item['created_at'],with_time=True)))
        blocks.append('\n'.join(lines))
    if not data['items']:
        blocks.append('تراکنشی در این بخش ثبت نشده است.')
    return '\n\n'.join(blocks)


def order_cards(stats):
    personal = stats['order_counts']
    def lines(counts):
        return (f'کل: {counts["total"]:,} | تأییدشده: {counts["approved"]:,}\n'
                f'در انتظار/پردازش: {counts["pending"]:,} | ردشده: {counts["rejected"]:,} | منقضی: {counts["expired"]:,}')
    cards = [f'<blockquote>👤 سفارش‌های شخصی کاربر در این فروشگاه\n{lines(personal)}</blockquote>']
    shops = stats['shop_order_summary']
    if shops['shops'] or stats.get('is_reseller') or stats.get('agent_tier'):
        text = '🤖 سفارش‌های بات نمایندگی\n'
        if shops['shops']:
            text += lines(shops)
            text += '\n' + '، '.join(html.escape('@'+str(shop['username'])) for shop in shops['shops'][:10])
            if len(shops['shops']) > 10:
                text += f' و {len(shops["shops"])-10} بات دیگر'
        else:
            text += 'بات اختصاصی ثبت نشده است.' if not shops['unavailable'] else 'دریافت آمار بات ممکن نشد.'
        if shops['unavailable']:
            text += '\n⚠️ آمار کامل نیست؛ دیتابیس بعضی فروشگاه‌ها در دسترس نیست.'
        cards.append(f'<blockquote>{text}</blockquote>')
    return '\n\n'.join(cards)
