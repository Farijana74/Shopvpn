"""Per-user, per-GB overrides for existing custom-volume plans."""
def personal_volume_rate(db,user_id,plan_id):
    value=db.get_setting(f'personal_volume_rate:{int(user_id)}:{int(plan_id)}','')
    return int(value) if value else None


def entries(db):
    from managed_native_volume import plans,rules
    from database import Database
    if not db.get_setting('managed_reseller_bot_id',''):return plans(db)
    root=Database(db.get_setting('managed_root_db_path',''))
    try:
        result=[]
        for plan in plans(root):
            try:result.append(rules(db,root,int(db.get_setting('managed_reseller_bot_id')),plan['id']))
            except ValueError:continue
        return result
    finally:root.close()


def floor(db,plan_id):
    from database import Database
    from managed_native_volume import costs
    plan=next((p for p in entries(db) if p['id']==plan_id),None)
    if plan is None:raise ValueError('این پلن حجم دلخواه در دسترس نیست.')
    if not db.get_setting('managed_reseller_bot_id',''):return 1
    root=Database(db.get_setting('managed_root_db_path',''))
    try:
        lo,hi=int(plan['min_gb']),int(plan['max_gb']);points={lo,hi}
        tiers=root.get_custom_config_product_tiers(plan_id) if plan_id else root.get_pricing_tiers()
        for tier in tiers:
            for value in (tier['from_gb'],tier['to_gb']):
                if value is not None:
                    points.update(x for x in (int(value)-1,int(value),int(value)+1) if lo<=x<=hi)
        return max(costs(root,int(db.get_setting('managed_reseller_bot_id')),plan_id,gb)[1][-1][1] for gb in points)
    finally:root.close()


def set_rate(db,user_id,plan_id,value):
    if not db.get_user(user_id):raise ValueError('کاربر در همین ربات یافت نشد.')
    if value is not None:
        minimum=floor(db,plan_id)
        if not minimum<=value<=2**63-1:raise ValueError(f'نرخ هر گیگ باید حداقل {minimum:,} تومان باشد.')
    db.set_setting(f'personal_volume_rate:{int(user_id)}:{int(plan_id)}','' if value is None else str(value))


def tier_info(db,user_id,price,plan_id):
    if personal_volume_rate(db,user_id,plan_id) is not None:
        return dict(percent=0,amount=0,total=price,total_after=price,title='قیمت اختصاصی',icon='')
    return db.get_tier_price_info(user_id,price,1)
