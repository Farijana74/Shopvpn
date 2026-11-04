#!/bin/bash
# Interactive local-only migration; no tokens or .env contents are printed.
set -euo pipefail
migration_dir="${SHOPVPN_INSTALL_DIR:-$(cd "$(dirname "$0")" && pwd)}"
source_path="${SHOPVPN_LEGACY_DB:-$HOME/v2ray_bot/bot_database.db}"
read -r -p "مسیر دیتابیس قدیمی [$source_path]: " migration_answer
source_path="${migration_answer:-$source_path}"
if [ ! -f "$source_path" ]; then echo 'دیتابیس قدیمی پیدا نشد.'; exit 1; fi
printf '%s\n' '1) جایگزینی کامل: اطلاعات فعلی ربات جدید حذف و دیتابیس قدیمی وارد شود (پیش‌فرض)' '2) ادغام پیشرفته بدون حذف اطلاعات موجود' '3) انتقال فقط به نصب خالی'
read -r -p 'انتخاب: ' migration_answer
mode=replace
if [ "$migration_answer" = 2 ]; then mode=merge; fi
if [ "$migration_answer" = 3 ]; then mode=upgrade; fi
old_service="${SHOPVPN_LEGACY_SERVICE:-v2raybot}"
read -r -p "نام سرویس قدیمی [$old_service]: " migration_answer
old_service="${migration_answer:-$old_service}"
new_service="${SHOPVPN_SERVICE_NAME:-shopvpn-independent}"
if [ "$old_service" = "$new_service" ]; then echo "نام سرویس قدیمی و جدید نباید یکسان باشد."; exit 1; fi
if [[ ! "$old_service" =~ ^[A-Za-z0-9_][A-Za-z0-9_.@:-]*$ ]]; then echo "نام سرویس قدیمی معتبر نیست."; exit 1; fi
printf '%s\n' 'برای ساخت پیش‌نمایش ثابت، سرویس قدیمی و سرویس‌های نصب جدید موقتاً متوقف می‌شوند.'
read -r -p 'ادامه بدهم؟ [y/N]: ' migration_answer
[[ "$migration_answer" =~ ^[Yy]$ ]] || exit 0
stopped_services=()
applied=0
restore_services() {
    for migration_unit in "${stopped_services[@]}"; do
        if [ "$applied" = 1 ]; then
            case "$migration_unit" in
                "$old_service"|"$old_service-adminpanel"|"$old_service-miniapp"|"$old_service-api"|"$old_service-sub"|"$old_service-store") continue ;;
            esac
        fi
        systemctl start "$migration_unit" || true
    done
}
trap restore_services EXIT
for migration_unit in "$old_service" "$old_service-adminpanel" "$old_service-miniapp" "$old_service-api" "$old_service-sub" "$old_service-store" "$new_service" "$new_service-adminpanel" "$new_service-miniapp" "$new_service-api" "$new_service-sub" "$new_service-store"; do
    if systemctl is-active --quiet "$migration_unit"; then
        systemctl stop "$migration_unit"
        stopped_services+=("$migration_unit")
    fi
done
migration_python="$migration_dir/venv/bin/python"
if [ ! -x "$migration_python" ]; then echo 'ابتدا محیط Python پروژه را نصب کنید.'; exit 1; fi
mkdir -p "$migration_dir/backups"
migration_plan=$(mktemp -d "$migration_dir/backups/legacy-import-XXXXXXXX")
rmdir "$migration_plan"
cd "$migration_dir"
"$migration_python" legacy_migration.py prepare --source "$source_path" --mode "$mode" --plan-dir "$migration_plan"
printf '%s\n' "گزارش و بکاپ‌ها: $migration_plan" 'در صورت تعارض، هیچ داده‌ای بازنویسی نمی‌شود. توکن و فایل .env نصب جدید تغییر نمی‌کنند.'
if [ "$mode" = replace ]; then
    printf '%s\n' 'اطلاعات فعلی ربات جدید جایگزین می‌شود. نسخه پیش از انتقال در پوشه گزارش نگهداری شده است.'
    read -r -p 'برای تأیید جایگزینی کامل، REPLACE بنویسید: ' migration_answer
    [ "$migration_answer" = REPLACE ] || exit 0
else
    read -r -p 'این گزارش را تأیید و انتقال را اعمال کنم؟ [y/N]: ' migration_answer
    [[ "$migration_answer" =~ ^[Yy]$ ]] || exit 0
fi
"$migration_python" legacy_migration.py apply --plan-dir "$migration_plan" --services-stopped
applied=1
printf '%s\n' 'انتقال تکمیل شد. سرویس قدیمی خاموش می‌ماند؛ سرویس‌های جدیدِ قبلاً فعال دوباره راه‌اندازی می‌شوند.'
