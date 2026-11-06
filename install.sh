#!/bin/sh
# H0 Lab 一鍵安裝(macOS / Linux)。規格:docs/安裝與上手規格.md §1
# 只動這個資料夾:建立 .venv、安裝套件、建立 private/ 與 .env。不用 sudo、不改系統設定。
# 可以重複執行:已經做過的步驟會略過。
set -u
cd "$(dirname "$0")" || exit 1

MIN_MAJOR=3
MIN_MINOR=13
REQ="requirements.lock.txt"
[ -f "$REQ" ] || REQ="requirements.txt"

say() { printf '%s\n' "$*"; }
fail() {
  say ""
  say "✗ $1"
  [ -n "${2:-}" ] && say "  → $2"
  exit 1
}

version_ok() {  # $1 = python 執行檔;版本 >= 3.13 回傳 0
  "$1" -c "import sys; sys.exit(0 if sys.version_info >= ($MIN_MAJOR, $MIN_MINOR) else 1)" 2>/dev/null
}

say "== H0 Lab 安裝(研究用途,非投資建議)=="

# 1. 找 Python 3.13 以上
PY=""
for c in python3.14 python3.13 python3; do
  if command -v "$c" >/dev/null 2>&1 && version_ok "$c"; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  found=""
  command -v python3 >/dev/null 2>&1 && found="(目前找到的是 $(python3 -V 2>&1))"
  fail "需要 Python $MIN_MAJOR.$MIN_MINOR 以上$found" \
    "到 https://www.python.org/downloads/ 下載 Python 3.13 安裝檔,裝好後關掉終端機重開,再執行一次 ./install.sh"
fi
say "✓ Python:$($PY -V 2>&1)($PY)"

# 2. 專案內 .venv(已經有、版本也夠就略過)
if [ -x .venv/bin/python ] && version_ok .venv/bin/python; then
  say "✓ .venv 已存在,略過建立"
else
  if [ -d .venv ]; then
    fail ".venv 已存在但版本不夠或壞掉了" "把 .venv 資料夾丟到垃圾桶後再執行一次 ./install.sh"
  fi
  say "… 建立 .venv"
  "$PY" -m venv .venv || fail "建立虛擬環境失敗" \
    "Linux 常見原因是缺 venv 模組(例如 Ubuntu 要先裝 python3-venv);macOS 請改用 python.org 的安裝檔"
  say "✓ .venv 建立完成"
fi

# 3. 安裝套件(pip 只裝進 .venv;已經裝好的會很快略過)
# pip 試跑:有缺就會印 "Would install";試跑本身失敗(例如裝到一半又斷網)也當成要裝,不誤判成已安裝
dry=$(.venv/bin/python -m pip install --disable-pip-version-check --dry-run -r "$REQ" 2>/dev/null) || dry="Would install"
case "$dry" in
  *"Would install"*) need=1 ;;
  *) need=0 ;;
esac
if [ "$need" = 0 ] && .venv/bin/python -c "import fastapi, uvicorn, pandas, numpy, scipy, yfinance" 2>/dev/null; then
  say "✓ 套件已安裝,略過"
else
  say "… 安裝套件($REQ,第一次約 2–5 分鐘,需要網路)"
  .venv/bin/python -m pip install --disable-pip-version-check -q -r "$REQ" || fail "套件安裝失敗" \
    "確認網路連線與磁碟空間後再執行一次 ./install.sh(已裝好的會略過)"
  say "✓ 套件安裝完成"
fi

# 4. private/ 與 .env(已經有就不動)
if [ -d private ]; then say "✓ private/ 已存在"; else mkdir private && say "✓ 建立 private/(觀察清單、筆記、模擬帳戶,只留在這台電腦)"; fi
if [ -f .env ]; then
  say "✓ .env 已存在,不覆寫"
elif [ -f .env.example ]; then
  cp .env.example .env && say "✓ 建立 .env(從 .env.example 複製;FinMind token 等選填項目可以之後再填)"
fi
chmod +x start.sh "H0 Lab.command" 2>/dev/null

say ""
say "安裝完成。啟動:./start.sh(會打開 http://127.0.0.1:8765,只有這台電腦連得到)"
say "第一次打開網頁,首頁會告訴你下一步做什麼。"
