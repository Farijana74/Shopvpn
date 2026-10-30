#!/bin/bash
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# Fully automatic local translation setup for ShopVPN.
# Installs system/Python prerequisites, Argos Translate and the language models
# required by ShopVPN. No API key is required and public translation APIs are
# never enabled by this script.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/venv}"
TRANSLATION_VENV_DIR="${TRANSLATION_VENV_DIR:-$ROOT_DIR/translation-venv}"
TRANSLATION_HOME="${TRANSLATION_HOME:-$ROOT_DIR/.translation-home}"
PYTHON_BIN="${PYTHON_BIN:-$VENV_DIR/bin/python3}"
LIBRETRANSLATE_SERVICE="${SHOPVPN_SERVICE_NAME:-shopvpn-independent}-libretranslate"
LIBRETRANSLATE_PORT="${SHOPVPN_LIBRETRANSLATE_PORT:-5001}"
LT_PYTHON="${LT_PYTHON:-$TRANSLATION_VENV_DIR/bin/python3}"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONUNBUFFERED=1

as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "ERROR: root privileges are required to install system packages." >&2
    exit 1
  fi
}

#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
install_system_prereqs() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "[translation] Installing system prerequisites..."
    as_root env DEBIAN_FRONTEND=noninteractive apt-get update -qq
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      python3 python3-pip python3-venv ca-certificates curl >/dev/null
  elif ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is required on this operating system." >&2
    exit 1
  fi
}

ENV_FILE="$ROOT_DIR/.env"
touch "$ENV_FILE"

set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s#^${key}=.*#${key}=${value}#" "$ENV_FILE"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

# UI language: follow manage.sh (SHOPVPN_UI_LANG) or its saved choice; default English.
UI_LANG="${SHOPVPN_UI_LANG:-}"
if [ -z "$UI_LANG" ] && [ -f "$HOME/.shopvpn_manage_lang" ]; then
  UI_LANG="$(tr -d '[:space:]' < "$HOME/.shopvpn_manage_lang" 2>/dev/null || true)"
fi
[ "$UI_LANG" = "fa" ] || UI_LANG="en"

#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
has_tty() {
  ( : </dev/tty ) 2>/dev/null
}

ASK_ONLY=0
[ "${1:-}" = "--ask" ] && ASK_ONLY=1

ask_enable() {
  local has_key=0 saved=""
  if grep -q '^SHOPVPN_TRANSLATION_ENABLE=' "$ENV_FILE"; then
    has_key=1
    saved="$(grep -m1 '^SHOPVPN_TRANSLATION_ENABLE=' "$ENV_FILE" | cut -d= -f2- | tr -d ' ')"
  fi
  if [ -n "${SHOPVPN_TRANSLATION_ENABLE+x}" ]; then
    ENABLE="$SHOPVPN_TRANSLATION_ENABLE"
  elif [ "$has_key" -eq 1 ] && [ "${SHOPVPN_TRANSLATION_CHOOSE:-0}" != "1" ]; then
    ENABLE="$saved"
  elif has_tty; then
    local answer RED=$'\033[1;31m' RST=$'\033[0m'
    echo "" >/dev/tty
    if [ "$UI_LANG" = "fa" ]; then
      printf '%s⚠️  هشدار: موتور ترجمه محلی (Argos) چند گیگابایت فضای دیسک می‌گیرد (torch و ...).%s\n' "$RED" "$RST" >/dev/tty
      printf '%s    روی سرورهای کم‌حجم ممکن است دیسک پر شود و ربات اجرا نشود.%s\n' "$RED" "$RST" >/dev/tty
      printf '%s    فقط برای ترجمه‌ی خودکار به زبان‌هایی غیر از فارسی و انگلیسی لازم است.%s\n' "$RED" "$RST" >/dev/tty
      printf '[translation] موتور ترجمه محلی نصب شود؟ [y/N] (پیش‌فرض: خیر): ' >/dev/tty
    else
      printf '%s⚠️  WARNING: the local translation engine (Argos) needs several GB of disk space (torch, etc.).%s\n' "$RED" "$RST" >/dev/tty
      printf '%s    On small servers the disk can fill up and the bot may fail to start.%s\n' "$RED" "$RST" >/dev/tty
      printf '%s    It is only needed for automatic translation to languages other than Persian and English.%s\n' "$RED" "$RST" >/dev/tty
      printf '[translation] Install the local translation engine? [y/N] (default: No): ' >/dev/tty
    fi
    read -r answer </dev/tty || answer=""
    answer="$(echo "$answer" | tr 'A-Z' 'a-z' | tr -d ' ')"
    case "$answer" in
      y|yes|بله|آره) ENABLE=1 ;;
      *) ENABLE=0 ;;
    esac
  else
    # No terminal: never install the heavy engine unless explicitly enabled.
    ENABLE=0
  fi
  case "$ENABLE" in 1|true|yes) ENABLE=1 ;; *) ENABLE=0 ;; esac
  set_env SHOPVPN_TRANSLATION_ENABLE "$ENABLE"
}

optional_languages() {
  SHOPVPN_ROOT="$ROOT_DIR" python3 - <<'PY'
import os
import sys
sys.path.insert(0, os.environ["SHOPVPN_ROOT"])
from i18n import LANGUAGE_CATALOG
for code, meta in LANGUAGE_CATALOG.items():
    if code not in {"en", "fa"}:
        print(code, meta["native_name"], meta["name"], sep="|")
PY
}

#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
choose_languages() {
  local saved="" has_key=0
  if grep -q '^SHOPVPN_TRANSLATION_LANGS=' "$ENV_FILE"; then
    has_key=1
    saved="$(grep -m1 '^SHOPVPN_TRANSLATION_LANGS=' "$ENV_FILE" | cut -d= -f2-)"
  fi
  if [ -n "${SHOPVPN_TRANSLATION_LANGS+x}" ]; then
    SELECTED="$SHOPVPN_TRANSLATION_LANGS"
  elif [ "$has_key" -eq 1 ] && [ "${SHOPVPN_TRANSLATION_CHOOSE:-0}" != "1" ]; then
    SELECTED="$saved"
    return
  elif has_tty; then
    local codes=() line code native name idx=1 answer
    echo "" >/dev/tty
    if [ "$UI_LANG" = "fa" ]; then
      echo "[translation] زبان‌هایی که می‌خواهید نصب شوند را انتخاب کنید (فارسی و انگلیسی همیشه فعال‌اند)." >/dev/tty
    else
      echo "[translation] Choose the languages to install (Persian and English are always available)." >/dev/tty
    fi
    while IFS='|' read -r code native name; do
      codes+=("$code")
      printf '  %2d) %s - %s\n' "$idx" "$native" "$name" >/dev/tty
      idx=$((idx + 1))
    done < <(optional_languages)
    if [ "$UI_LANG" = "fa" ]; then
      echo "   a) همه" >/dev/tty
      echo "   Enter) هیچ‌کدام" >/dev/tty
      printf 'شماره‌ها یا کد زبان‌ها را با کاما یا فاصله وارد کنید (مثال: 1,3,5 یا tr,de): ' >/dev/tty
    else
      echo "   a) all" >/dev/tty
      echo "   Enter) none" >/dev/tty
      printf 'Enter numbers or language codes separated by comma or space (e.g. 1,3,5 or tr,de): ' >/dev/tty
    fi
    read -r answer </dev/tty || answer=""
    answer="$(echo "$answer" | sed 's/،/,/g' | tr 'A-Z' 'a-z' | tr ',' ' ')"
    local picked=()
    if [ "$answer" = "a" ] || [ "$answer" = "all" ]; then
      picked=("${codes[@]}")
    else
      local tok
      for tok in $answer; do
        if [[ "$tok" =~ ^[0-9]+$ ]] && [ "$tok" -ge 1 ] && [ "$tok" -le "${#codes[@]}" ]; then
          picked+=("${codes[$((tok - 1))]}")
        else
          for code in "${codes[@]}"; do
            [ "$code" = "$tok" ] && picked+=("$code")
          done
        fi
      done
    fi
    SELECTED="$(printf '%s\n' ${picked[@]+"${picked[@]}"} | awk 'NF && !seen[$0]++' | paste -sd, -)"
  else
    SELECTED="$saved"
    echo "[translation] No language selection found and no terminal available; installing no extra languages." >&2
    echo "[translation] Set SHOPVPN_TRANSLATION_LANGS=tr,de,... or run manage.sh to choose." >&2
  fi
  SELECTED="$(echo "$SELECTED" | tr -d ' ' | tr 'A-Z' 'a-z')"
  set_env SHOPVPN_TRANSLATION_LANGS "$SELECTED"
}

ask_enable
if [ "$ENABLE" != "1" ]; then
  echo "[translation] Translation engine skipped."
  exit 0
fi

if [ "$ASK_ONLY" -eq 1 ]; then
  command -v python3 >/dev/null 2>&1 || install_system_prereqs
else
  install_system_prereqs
fi

SELECTED=""
choose_languages
if [ "$ASK_ONLY" -eq 1 ]; then
  echo "[translation] Selected languages: ${SELECTED:-none}"
  exit 0
fi
echo "[translation] Selected languages: ${SELECTED:-none}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "[translation] Creating Python virtual environment..."
  python3 -m venv "$VENV_DIR"
fi

"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel >/dev/null
"$PYTHON_BIN" -m pip install -q 'argostranslate>=1.11.0'

if [ -n "$SELECTED" ]; then
  if [ ! -x "$LT_PYTHON" ]; then
    echo "[translation] Creating isolated LibreTranslate environment..."
    python3 -m venv "$TRANSLATION_VENV_DIR"
  fi
  "$LT_PYTHON" -m pip install --upgrade pip setuptools wheel >/dev/null
  "$LT_PYTHON" -m pip install -q --upgrade 'libretranslate>=1.9.0'
fi

IFS=',' read -r -a TARGETS <<< "$SELECTED"

# Argos package metadata is public/open and does not require an API key.
"$VENV_DIR/bin/argospm" update

install_pair() {
  local pair="$1"
  echo "[translation] Installing Argos model: $pair"
  if ! "$VENV_DIR/bin/argospm" install "translate-${pair}"; then
    echo "[translation] Warning: model translate-${pair} is unavailable; continuing." >&2
  fi
}

for lang in "${TARGETS[@]}"; do
  [ -n "$lang" ] || continue
  install_pair "en_${lang}"
done

# The admin panel can contain raw Persian strings that need direct fa -> en.
install_pair "fa_en"

# The local engine is the source of truth. Public providers are disabled by
# default so Google/MyMemory/OpenRouter rate limits can never break the UI.
set_env SHOPVPN_TRANSLATION_ALLOW_PUBLIC_APIS 0
if [ -n "$SELECTED" ]; then
  set_env SHOPVPN_TRANSLATION_PROVIDERS 'argos,libretranslate'
  set_env SHOPVPN_LIBRETRANSLATE_URL "http://127.0.0.1:$LIBRETRANSLATE_PORT"
else
  set_env SHOPVPN_TRANSLATION_PROVIDERS 'argos'
fi

# Run LibreTranslate locally as a second free/offline-capable fallback. Argos
# remains first and therefore avoids HTTP overhead for normal short UI strings.
if [ -n "$SELECTED" ] && command -v systemctl >/dev/null 2>&1; then
  LT_LOAD="en,fa,${SELECTED}"
  SERVICE_FILE="/etc/systemd/system/${LIBRETRANSLATE_SERVICE}.service"
  as_root mkdir -p "$TRANSLATION_HOME"
  as_root chmod 755 "$TRANSLATION_HOME"
  as_root bash -c "cat > '$SERVICE_FILE' <<EOF
[Unit]
Description=ShopVPN Local LibreTranslate
After=network.target

[Service]
Type=simple
WorkingDirectory=$ROOT_DIR
Environment=HOME=$TRANSLATION_HOME
Environment=PYTHONUNBUFFERED=1
ExecStart=$TRANSLATION_VENV_DIR/bin/libretranslate --host 127.0.0.1 --port ${LIBRETRANSLATE_PORT} --load-only ${LT_LOAD} --disable-web-ui
Restart=on-failure
RestartSec=5
TimeoutStartSec=15min
TimeoutStopSec=30s

[Install]
WantedBy=multi-user.target
EOF"
  as_root systemctl daemon-reload
  as_root systemctl enable "${LIBRETRANSLATE_SERVICE}.service" >/dev/null
  as_root systemctl restart "${LIBRETRANSLATE_SERVICE}.service" || true

  # Wait for the local API to become ready before the bot starts making lazy
  # translation requests. The documented health surface is /languages.
  ready=0
  for _ in $(seq 1 90); do
    if curl -fsS --max-time 3 http://127.0.0.1:${LIBRETRANSLATE_PORT}/languages >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 2
  done
  if [ "$ready" -ne 1 ]; then
    echo "[translation] WARNING: LibreTranslate did not become ready within 180s." >&2
    as_root systemctl --no-pager --full status "${LIBRETRANSLATE_SERVICE}.service" 2>&1 | tail -40 >&2 || true
    as_root journalctl -u "${LIBRETRANSLATE_SERVICE}.service" -n 40 --no-pager 2>&1 >&2 || true
  fi
fi

"$PYTHON_BIN" - <<'PY'
import argostranslate
print("[translation] Argos Translate: ready")
PY

if [ -n "$SELECTED" ] && command -v curl >/dev/null 2>&1; then
  if curl -fsS --max-time 5 http://127.0.0.1:${LIBRETRANSLATE_PORT}/languages >/dev/null 2>&1; then
    echo "[translation] Local LibreTranslate: ready"
  else
    echo "[translation] Local LibreTranslate is not reachable; Argos remains the primary provider."
  fi
fi

echo "[translation] Local translation setup completed."
echo "[translation] Public translation APIs are disabled by default."
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
