#!/bin/bash
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# اسکریپت نصب/آپدیت خودکار بات فروش کانفیگ V2Ray
#
# استفاده (بعد از اینکه این فایل را در مخزن گیت‌هاب خودت گذاشتی و REPO_URL را
# با آدرس ریپازیتوری خودت جایگزین کردی):
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/USERNAME/v2ray-bot/main/install.sh)
#
# این اسکریپت هم برای نصب اولیه کار می‌کند و هم برای آپدیت‌های بعدی (idempotent است).

set -e

# جلوگیری از گیر کردن apt پشت پنجره‌های تعاملی (مثل پرسش needrestart برای ری‌استارت سرویس‌ها)
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export NEEDRESTART_SUSPEND=1

# L <persian> <english>: prints the text in the language chosen in manage.sh
# (SHOPVPN_UI_LANG; default fa when install.sh is run on its own)
L() { if [ "${SHOPVPN_UI_LANG:-fa}" = "fa" ]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }


# ----------------------------------------------------------------------------
# رابط پیشرفت زنده: مراحل، درصد، زمان سپری‌شده و پیام‌های دوستانه
# ----------------------------------------------------------------------------
JOKES_FA=(
  "اوه اوه، پردازنده رو دارم می‌خورم 😅"
  "یک لحظه... دارم با سرور گپ می‌زنم ☕"
  "نگران نباش؛ هنوز هنگ نکردیم، داریم کار می‌کنیم 👀"
  "پکیج‌ها دارن صف می‌کشن 🚪"
  "اینترنت گفت صبر کن؛ منم دارم صبر می‌کنم 😂"
  "دارم پیچ و مهره‌های سرور رو سفت می‌کنم 🔧"
)
JOKES_EN=(
  "Oh oh, I'm eating the CPU 😅"
  "One moment... I'm chatting with the server ☕"
  "Don't worry; I'm not frozen, I'm working 👀"
  "Packages are lining up at the door 🚪"
  "The internet said wait, so I'm waiting 😂"
  "Tightening the server's nuts and bolts 🔧"
)

progress_bar() {
  local pct="$1" width="${PB_WIDTH:-28}" filled empty
  filled=$(( pct * width / 100 )); empty=$(( width - filled ))
  printf '['
  printf '%*s' "$filled" '' | tr ' ' '#'
  printf '%*s' "$empty" '' | tr ' ' '-'
  printf '] %3d%%' "$pct"
}

draw_line() {
  local pct="$1" mark="$2" text="$3" cols bw rest
  cols="$(tput cols 2>/dev/null || echo 80)"
  [[ "$cols" =~ ^[0-9]+$ ]] || cols=80
  if [ "$cols" -lt 70 ]; then bw=12; else bw=28; fi
  rest=$(( cols - bw - 12 ))
  [ -n "$mark" ] && rest=$(( rest - 2 ))
  [ "$rest" -lt 8 ] && rest=8
  [ "${#text}" -gt "$rest" ] && text="${text:0:$((rest - 1))}…"
  printf '\r\033[K  '
  PB_WIDTH=$bw progress_bar "$pct"
  [ -n "$mark" ] && printf ' %b' "$mark"
  printf ' %s' "$text"
}

run_stage() {
  local idx="$1" total="$2" label="$3"; shift 3
  local log pid start_ms now_ms ms elapsed span base pct jokes_n joke status
  log="$(mktemp)"
  start_ms=$(( $(date +%s%N) / 1000000 ))
  "$@" >"$log" 2>&1 &
  pid=$!
  base=$(( (idx - 1) * 100 / total ))
  span=$(( idx * 100 / total - base ))
  pct=$base
  while kill -0 "$pid" 2>/dev/null; do
    now_ms=$(( $(date +%s%N) / 1000000 ))
    ms=$(( now_ms - start_ms ))
    elapsed=$(( ms / 1000 ))
    pct=$(( base + span * 95 * ms / ((ms + 15000) * 100) ))
    if [ "${SHOPVPN_UI_LANG:-fa}" = "fa" ]; then
      jokes_n=${#JOKES_FA[@]}; joke="${JOKES_FA[$(( ms / 4000 % jokes_n ))]}"
    else
      jokes_n=${#JOKES_EN[@]}; joke="${JOKES_EN[$(( ms / 4000 % jokes_n ))]}"
    fi
    draw_line "$pct" "" "$label · ${elapsed}s · $joke"
    sleep 0.25
  done
  wait "$pid"; status=$?
  elapsed=$(( ( $(date +%s%N) / 1000000 - start_ms ) / 1000 ))
  if [ "$status" -eq 0 ]; then
    draw_line "$(( idx * 100 / total ))" "✓" "$label · ${elapsed}s"
    printf '\n'
  else
    draw_line "$pct" "✗" "$label · ${elapsed}s"
    printf '\n'
    [ -s "$log" ] && tail -8 "$log" | sed 's/^/      /'
  fi
  rm -f "$log"
  return "$status"
}

# ============================================================================
# تنظیمات - این خط را با آدرس مخزن گیت‌هاب خودت جایگزین کن
# ============================================================================
GITHUB_OWNER="${SHOPVPN_REPO_OWNER:-farijana74}"
GITHUB_REPO="${SHOPVPN_REPO_NAME:-Shopvpn}"
GITHUB_BRANCH="${SHOPVPN_REPO_BRANCH:-main}"
REPO_URL="${SHOPVPN_REPO_URL:-https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}.git}"
INSTALL_DIR="${SHOPVPN_INSTALL_DIR:-$HOME/Shopvpn-independent}"
SERVICE_NAME="${SHOPVPN_SERVICE_NAME:-shopvpn-independent}"
CLI_NAME="${SHOPVPN_CLI_NAME:-shopvpn-independent}"
export SHOPVPN_INSTALL_DIR="$INSTALL_DIR" SHOPVPN_SERVICE_NAME="$SERVICE_NAME" SHOPVPN_CLI_NAME="$CLI_NAME"

echo "$(L "🚀 شروع نصب/آپدیت بات فروش کانفیگ V2Ray" "🚀 Starting install/update of the V2Ray config sales bot")"
echo "──────────────────────────────────────────"
echo "$(L "نمایش پیشرفت زنده فعال است؛ اگر یک مرحله طول کشید، ترمینال هنگ نکرده است." "Live progress is on; if a step takes a while, the terminal has not frozen.")"
echo ""

TOTAL_STEPS=6

# ۱. پیش‌نیازها
run_stage 1 "$TOTAL_STEPS" "$(L "📦 نصب پیش‌نیازهای سیستم" "📦 Installing system prerequisites")" bash -c   "sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a NEEDRESTART_SUSPEND=1 apt-get update -qq && timeout 120 sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a NEEDRESTART_SUSPEND=1 apt-get install -y -qq git python3 python3-pip python3-venv ca-certificates curl >/dev/null"

# ----------------------------------------------------------------------------
# ۲. دریافت یا آپدیت کد از گیت‌هاب
#    نکته: بعضی سرورها (خصوصاً VPSهای ارزان) با پروتکل git-over-https توسط
#    گیت‌هاب مسدود/محدود می‌شوند و به‌جای کلون عادی، درخواست یوزرنیم/پسورد
#    نشان داده می‌شود. برای جلوگیری از گیر کردن اسکریپت روی این پرامپت،
#    اول با گیت (بدون امکان پرامپت تعاملی) تلاش می‌کنیم و در صورت شکست،
#    به دانلود مستقیم آرشیو (tar.gz) که این محدودیت را ندارد سوییچ می‌کنیم.
# ----------------------------------------------------------------------------
export GIT_TERMINAL_PROMPT=0

#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
fetch_project_code() {
    local ok=0
    if [ -d "$INSTALL_DIR/.git" ]; then
        echo "$(L "🔄 مخزن از قبل موجود است، در حال دریافت آخرین تغییرات..." "🔄 Repository already exists, fetching the latest changes...")"
        if git -C "$INSTALL_DIR" pull --quiet; then ok=1; fi
    else
        echo "$(L "📥 دریافت پروژه از گیت‌هاب..." "📥 Downloading the project from GitHub...")"
        if git clone --quiet "$REPO_URL" "$INSTALL_DIR"; then ok=1; fi
    fi

    if [ "$ok" = "1" ]; then
        return 0
    fi

    echo "$(L "⚠️ دسترسی git مسدود شد، در حال دریافت از طریق آرشیو مستقیم..." "⚠️ git access was blocked, downloading via the direct archive...")"
    local tmp_tar tmp_dir
    tmp_tar=$(mktemp)
    tmp_dir=$(mktemp -d)
    if ! curl -fsSL "https://codeload.github.com/${GITHUB_OWNER}/${GITHUB_REPO}/tar.gz/refs/heads/${GITHUB_BRANCH}" -o "$tmp_tar"; then
        echo "$(L "❌ دانلود آرشیو پروژه هم ناموفق بود. اتصال اینترنت سرور را بررسی کن." "❌ Downloading the project archive failed too. Check the server internet connection.")"
        rm -f "$tmp_tar"; rm -rf "$tmp_dir"
        return 1
    fi
    tar -xzf "$tmp_tar" -C "$tmp_dir" --strip-components=1
    rm -f "$tmp_tar"
    mkdir -p "$INSTALL_DIR"
    if ! command -v rsync > /dev/null 2>&1; then
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq rsync > /dev/null
    fi
    rsync -a --exclude='.env' --exclude='*.db' --exclude='*.db-journal' \
        --exclude='*.sqlite3' --exclude='venv' --exclude='.git' --exclude='backups' \
        "$tmp_dir"/ "$INSTALL_DIR"/
    rm -rf "$INSTALL_DIR/.git" 2>/dev/null
    rm -rf "$tmp_dir"
    return 0
}

run_stage 2 "$TOTAL_STEPS" "$(L "📥 دریافت/آپدیت پروژه از GitHub" "📥 Fetching/updating the project from GitHub")" fetch_project_code
cd "$INSTALL_DIR"

# ----------------------------------------------------------------------------
# ۳. ساخت virtual environment و نصب پکیج‌ها
# ----------------------------------------------------------------------------
run_stage 3 "$TOTAL_STEPS" "$(L "🐍 آماده‌سازی Python و نصب پکیج‌ها" "🐍 Preparing Python and installing packages")" bash -c '
    if [ ! -d "venv" ]; then python3 -m venv venv; fi
    source venv/bin/activate
    pip install -r requirements.txt --quiet
    deactivate
'

# ----------------------------------------------------------------------------
# ۴. تنظیم فایل .env (فقط دفعه اول، چون این فایل هیچ‌وقت در گیت نیست)
# ----------------------------------------------------------------------------
echo ""
echo "$(L "  [مرحله ۴/$TOTAL_STEPS] 🔑 تنظیم اطلاعات بات" "  [Step 4/$TOTAL_STEPS] 🔑 Bot settings")"
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo ""
    echo "$(L "🔑 فایل .env پیدا نشد. اطلاعات زیر را وارد کن:" "🔑 The .env file was not found. Enter the following:")"
    read -rp "$(L "توکن بات (از BotFather): " "Bot token (from BotFather): ")" BOT_TOKEN_INPUT
    read -rp "$(L "آیدی عددی ادمین (مثلاً از @userinfobot): " "Admin numeric ID (e.g. from @userinfobot): ")" OWNER_ID_INPUT
    cat > "$INSTALL_DIR/.env" <<EOF
BOT_TOKEN=$BOT_TOKEN_INPUT
OWNER_ID=$OWNER_ID_INPUT
EOF

    echo ""
    echo "$(L "بات چطور آپدیت‌های تلگرام را دریافت کند؟" "How should the bot receive Telegram updates?")"
    echo "$(L "  1) Polling  (پیش‌فرض - ساده‌تر، نیاز به دامنه/SSL ندارد)" "  1) Polling  (default - simpler, no domain/SSL needed)")"
    echo "$(L "  2) Webhook  (نیاز به یک دامنه که DNS آن روی IP همین سرور تنظیم شده)" "  2) Webhook  (needs a domain whose DNS points to this server IP)")"
    read -rp "$(L "انتخاب [1]: " "Choice [1]: ")" BOT_MODE_CHOICE
    BOT_MODE_CHOICE="${BOT_MODE_CHOICE:-1}"

    if [ "$BOT_MODE_CHOICE" = "2" ]; then
        read -rp "$(L "دامنه‌ای که برای وب‌هوک بات استفاده می‌کنی (مثلاً bot.example.com): " "Domain to use for the bot webhook (e.g. bot.example.com): ")" WEBHOOK_DOMAIN
        if [ -z "$WEBHOOK_DOMAIN" ]; then
            echo "$(L "⚠️ دامنه وارد نشد؛ بات با Polling راه‌اندازی می‌شود (بعداً از منوی manage.sh می‌توانی وب‌هوک را فعال کنی)." "⚠️ No domain entered; the bot will start with Polling (you can enable the webhook later from the manage.sh menu).")"
        else
            WEBHOOK_PORT=8010
            echo "$(L "📦 نصب nginx و certbot..." "📦 Installing nginx and certbot...")"
            sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a NEEDRESTART_SUSPEND=1 \
                apt-get install -y -qq nginx certbot python3-certbot-nginx > /dev/null

            echo "$(L "🌐 تنظیم nginx برای $WEBHOOK_DOMAIN..." "🌐 Configuring nginx for $WEBHOOK_DOMAIN...")"
            sudo bash -c "cat > /etc/nginx/sites-available/${WEBHOOK_DOMAIN}.conf" <<NGXEOF
server {
    listen 80;
    server_name $WEBHOOK_DOMAIN;
    client_max_body_size 100m;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;

    location / {
        proxy_pass http://127.0.0.1:$WEBHOOK_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGXEOF
            sudo ln -sf "/etc/nginx/sites-available/${WEBHOOK_DOMAIN}.conf" "/etc/nginx/sites-enabled/${WEBHOOK_DOMAIN}.conf"

            if ! sudo nginx -t > /dev/null 2>&1; then
                echo "$(L "⛔️ کانفیگ nginx خطا دارد؛ بات با Polling راه‌اندازی می‌شود." "⛔️ The nginx config has an error; the bot will start with Polling.")"
            else
                sudo systemctl reload nginx
                echo "$(L "🔐 دریافت گواهی SSL (Let's Encrypt)..." "🔐 Getting an SSL certificate (Let's Encrypt)...")"
                if sudo certbot --nginx -d "$WEBHOOK_DOMAIN" --non-interactive --agree-tos \
                    --register-unsafely-without-email --redirect; then
                    WEBHOOK_SECRET_VAL=$(python3 -c "import secrets; print(secrets.token_hex(24))")
                    cat >> "$INSTALL_DIR/.env" <<EOF
BOT_MODE=webhook
WEBHOOK_BASE_URL=https://$WEBHOOK_DOMAIN
WEBHOOK_SECRET=$WEBHOOK_SECRET_VAL
WEBHOOK_LISTEN_HOST=127.0.0.1
WEBHOOK_LISTEN_PORT=$WEBHOOK_PORT
EOF
                    echo "$(L "✅ حالت Webhook تنظیم شد: https://$WEBHOOK_DOMAIN" "✅ Webhook mode is set: https://$WEBHOOK_DOMAIN")"
                else
                    echo "$(L "⛔️ دریافت SSL ناموفق بود؛ بات با Polling راه‌اندازی می‌شود (بعداً از منوی manage.sh دوباره امتحان کن)." "⛔️ Getting SSL failed; the bot will start with Polling (try again later from the manage.sh menu).")"
                fi
            fi
        fi
    fi
    echo "$(L "✅ فایل .env ساخته شد." "✅ The .env file was created.")"
else
    echo "$(L "✅ فایل .env از قبل موجود است، دست‌نخورده باقی می‌ماند." "✅ The .env file already exists and is left untouched.")"
fi

# ----------------------------------------------------------------------------
# ۵. نصب و راه‌اندازی خودکار موتور ترجمه محلی
#    کاربر نباید هیچ مدل Argos یا LibreTranslate را دستی نصب کند.
# ----------------------------------------------------------------------------
bash "$INSTALL_DIR/setup_local_translation.sh" --ask || true
run_stage 5 "$TOTAL_STEPS" "$(L "🌍 نصب موتور ترجمه و مدل‌های زبان" "🌍 Installing the translation engine and language models")" bash -c "bash '$INSTALL_DIR/setup_local_translation.sh'" ||     echo "$(L "⚠️ نصب موتور ترجمه کامل نشد؛ بات ادامه می‌دهد و در آپدیت بعدی دوباره تلاش می‌کند." "⚠️ The translation engine was not fully installed; the bot continues and will retry on the next update.")"

# ۶. ساخت و راه‌اندازی systemd
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
run_stage 6 "$TOTAL_STEPS" "$(L "⚙️ ساخت و راه‌اندازی سرویس دائمی" "⚙️ Creating and starting the permanent service")" bash -c "
sudo bash -c 'cat > "$SERVICE_FILE"' <<EOF
[Unit]
Description=V2Ray Telegram Sales Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
Environment=SHOPVPN_SERVICE_NAME=$SERVICE_NAME
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/main.py
Restart=always
RestartSec=5
User=$(whoami)

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable '$SERVICE_NAME' >/dev/null 2>&1
sudo systemctl restart '$SERVICE_NAME'
sleep 2
"

echo ""
echo "──────────────────────────────────────────"
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "$(L "✅ بات با موفقیت نصب/آپدیت شد و در حال اجراست." "✅ The bot was installed/updated successfully and is running.")"
else
    echo "$(L "⚠️ بات اجرا نشد. برای دیدن جزئیات خطا:" "⚠️ The bot did not start. To see the error details:")"
    echo "   sudo journalctl -u $SERVICE_NAME -n 50 --no-pager"
fi
sudo tee "/usr/local/bin/${CLI_NAME}" >/dev/null <<EOF
#!/bin/bash
export SHOPVPN_INSTALL_DIR="$INSTALL_DIR" SHOPVPN_SERVICE_NAME="$SERVICE_NAME" SHOPVPN_CLI_NAME="$CLI_NAME"
if [ "\$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    exec sudo env HOME="$HOME" SHOPVPN_INSTALL_DIR="$INSTALL_DIR" SHOPVPN_SERVICE_NAME="$SERVICE_NAME" SHOPVPN_CLI_NAME="$CLI_NAME" bash "$INSTALL_DIR/manage.sh" "\$@"
fi
exec bash "$INSTALL_DIR/manage.sh" "\$@"
EOF
sudo chmod 755 "/usr/local/bin/${CLI_NAME}" 2>/dev/null || true

echo ""
echo "$(L "پنل مدیریت:   $CLI_NAME" "Management panel:   $CLI_NAME")"
echo "$(L "دستورات مفید:" "Useful commands:")"
echo "$(L "  وضعیت بات:    sudo systemctl status $SERVICE_NAME" "  Bot status:   sudo systemctl status $SERVICE_NAME")"
echo "$(L "  لاگ زنده:      sudo journalctl -u $SERVICE_NAME -f" "  Live logs:     sudo journalctl -u $SERVICE_NAME -f")"
echo "$(L "  ری‌استارت:     sudo systemctl restart $SERVICE_NAME" "  Restart:       sudo systemctl restart $SERVICE_NAME")"
echo "$(L "  متوقف کردن:    sudo systemctl stop $SERVICE_NAME" "  Stop:          sudo systemctl stop $SERVICE_NAME")"
echo "──────────────────────────────────────────"
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
