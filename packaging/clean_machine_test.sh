#!/bin/sh
# 把 DMG 當成新使用者的下載檔裝一次:掛載、安裝、啟動、呼叫本機 API;online 模式再抓少量資料、看第一份報告。
#
#   sh packaging/clean_machine_test.sh <H0-Lab-macOS.dmg> [offline|online]
#
# 給 .github/workflows/clean-machine.yml 在全新的 macOS runner 上用,會寫進 /Applications,不要在自己的電腦跑。
# 只用系統內建工具,不依賴 Python。公開 repo 的執行紀錄別人看得到,所以只印通過或失敗、數量,不印任何抓到的資料。
set -u

DMG="$1"
MODE="${2:-offline}"
export PATH=/usr/bin:/bin:/usr/sbin:/sbin

WORK="$(mktemp -d)"
export HOME="$WORK/home"
mkdir -p "$HOME"
MNT="$WORK/mnt"
APP="/Applications/H0 Lab.app"
BIN="$APP/Contents/MacOS/H0 Lab"
PORT=18765
BASE="http://127.0.0.1:$PORT"
SUPPORT="$HOME/Library/Application Support/H0 Lab"
FAILS=0
APP_PID=""

ok() { echo "PASS  $1"; }
bad() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
info() { echo "INFO  $1"; }
limit() { secs="$1"; shift; /usr/bin/perl -e 'alarm shift; exec @ARGV' "$secs" "$@"; }
# 讀 JSON 裡的欄位,只回傳判斷結果;$1 是 perl 運算式,$d 是解析後的內容(macOS 內建 perl 就有 JSON::PP)
jq_perl() { /usr/bin/perl -MJSON::PP -0777 -e '
  my $x = shift; my $t = <STDIN>; my $d = eval { JSON::PP->new->allow_nonref->decode($t) };
  exit 3 unless defined $d; my $r = eval $x; print defined $r ? $r : "";' "$1" 2>/dev/null; }

http_code() { curl -s --noproxy '*' -o /dev/null -w '%{http_code}' -H 'x-requested-with: invest-lab' --max-time "${2:-30}" "$BASE$1"; }
get() { curl -fsS --noproxy '*' -H 'x-requested-with: invest-lab' --max-time "${2:-30}" "$BASE$1"; }
post() { curl -sS --noproxy '*' --max-time "${3:-60}" -H 'x-requested-with: invest-lab' -H "Origin: $BASE" -H 'Content-Type: application/json' -d "$2" "$BASE$1"; }

show_log() {
  [ -f "$SUPPORT/logs/app.log" ] || return 0
  echo "--- app.log(最後 20 行)"
  tail -n 20 "$SUPPORT/logs/app.log"
  echo "---"
}

start_app() {
  INVEST_LAB_PORT="$PORT" H0_LAB_NO_BROWSER=1 H0_LAB_HEADLESS=1 "$BIN" >/dev/null 2>&1 &
  APP_PID=$!
  n=0
  while [ "$n" -lt 120 ]; do
    if [ "$(get /api/app/identity 3 2>/dev/null | jq_perl '$d->{app}')" = "h0-lab" ]; then
      return 0
    fi
    kill -0 "$APP_PID" 2>/dev/null || return 1
    sleep 1
    n=$((n + 1))
  done
  return 1
}

stop_app() {
  [ -n "$APP_PID" ] || return 0
  kill -TERM "$APP_PID" 2>/dev/null
  n=0
  while kill -0 "$APP_PID" 2>/dev/null && [ "$n" -lt 20 ]; do sleep 1; n=$((n + 1)); done
  kill -0 "$APP_PID" 2>/dev/null && { kill -KILL "$APP_PID" 2>/dev/null; bad "App 收到結束訊號後 20 秒還沒結束"; }
  APP_PID=""
}

finish() {
  stop_app
  hdiutil detach "$MNT" -quiet 2>/dev/null
  echo
  if [ "$FAILS" -eq 0 ]; then echo "全部通過($MODE)"; else echo "$FAILS 項失敗($MODE)"; show_log; fi
  exit "$([ "$FAILS" -eq 0 ] && echo 0 || echo 1)"
}

echo "== 環境"
info "macOS $(sw_vers -productVersion),$(uname -m)"
info "Gatekeeper:$(spctl --status 2>&1)"

echo "== 安裝"
# 模擬瀏覽器下載:DMG 帶隔離標記,裡面的 App 會跟著帶
xattr -w com.apple.quarantine "0081;$(printf '%x' "$(date +%s)");Safari;" "$DMG" || info "無法加上隔離標記"
if hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MNT" -quiet; then ok "DMG 能掛載"; else bad "DMG 無法掛載"; finish; fi
[ -d "$MNT/H0 Lab.app" ] && ok "DMG 裡有 App" || { bad "DMG 裡沒有 H0 Lab.app"; finish; }
[ -L "$MNT/Applications" ] && ok "DMG 裡有 Applications 捷徑" || bad "DMG 裡沒有 Applications 捷徑"
if ditto "$MNT/H0 Lab.app" "$APP"; then ok "App 能裝進 /Applications"; else bad "App 無法裝進 /Applications"; finish; fi
hdiutil detach "$MNT" -quiet 2>/dev/null
info "隔離標記:$(xattr -p com.apple.quarantine "$APP" >/dev/null 2>&1 && echo 有 || echo 沒有)"
info "Gatekeeper 評估:$(spctl --assess --type execute "$APP" 2>&1 | head -n 1)"
if codesign --verify --deep --strict "$APP" 2>/dev/null; then ok "程式碼簽章完整"; else bad "程式碼簽章不完整"; fi
# 使用者第一次打開時會在系統設定按「仍要打開」;這裡沒有人可以按,所以拿掉標記再測 App 本身
xattr -dr com.apple.quarantine "$APP" 2>/dev/null

echo "== 啟動"
out="$(limit 300 "$BIN" --self-test 2>/dev/null | tail -n 1)"
if [ "$(printf '%s' "$out" | jq_perl '$d->{ok} ? "yes" : "no"')" = "yes" ]; then ok "self-test"; else bad "self-test:$out"; fi
if start_app; then ok "App 能啟動,API 有回應"; else bad "App 啟動後 API 沒有回應"; finish; fi
[ -d "$SUPPORT" ] && ok "使用者資料夾建好了" || bad "使用者資料夾不在預期位置"
[ "$(http_code /)" = "200" ] && ok "首頁" || bad "首頁打不開"
[ "$(http_code /api/setup/status)" = "200" ] && ok "第一次使用狀態" || bad "/api/setup/status"
[ "$(http_code /api/models)" = "200" ] && ok "模型清單" || bad "/api/models"
[ "$(http_code /api/update/status 60)" = "200" ] && ok "更新狀態" || bad "/api/update/status"
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | grep -q "127.0.0.1:$PORT"; then ok "只綁 127.0.0.1"; else bad "監聽位址不是 127.0.0.1"; fi
code="$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 10 -H 'Host: evil.example' "$BASE/api/models")"
case "$code" in 4*) ok "外來 Host 被擋($code)" ;; *) bad "外來 Host 沒被擋($code)" ;; esac

if [ "$MODE" != "online" ]; then finish; fi

echo "== 線上流程(少量資料)"
out="$(post /api/setup/research/start '{}')"
case "$out" in
  *sec_ua_required*) ok "沒填 SEC 聯絡方式時不會開始下載" ;;
  *) bad "沒填 SEC 聯絡方式也能開始下載" ;;
esac
stop_app

export SEC_USER_AGENT="H0 Lab clean machine test ${GITHUB_REPOSITORY_OWNER:-ci}@users.noreply.github.com"
log="$WORK/setup.log"
started="$(date +%s)"
limit 2400 "$BIN" --h0-run lab.setup_research --tw 2330,2317 --us AAPL,MSFT >"$log" 2>&1
code=$?
info "建立資料花了 $(( ($(date +%s) - started) / 60 )) 分鐘"
if [ "$code" -eq 0 ]; then ok "建立回測資料"; else bad "建立回測資料(結束碼 $code)"; echo "--- 進度最後 15 行"; tail -n 15 "$log"; echo "---"; fi

if start_app; then ok "建好資料後能重新啟動"; else bad "建好資料後無法重新啟動"; finish; fi
done_flag="$(get /api/setup/status 30 | jq_perl 'my ($s) = grep { $_->{id} eq "research" } @{$d->{steps}}; $s->{done} ? "yes" : "no"')"
[ "$done_flag" = "yes" ] && ok "第一次使用狀態顯示資料已建好" || bad "資料建好後狀態還是沒完成($done_flag)"

for sym in 2330 AAPL; do
  body="$WORK/report.json"
  code="$(curl -s --noproxy '*' -o "$body" -w '%{http_code}' -H 'x-requested-with: invest-lab' --max-time 300 "$BASE/api/report?symbol=$sym")"
  keys="$(jq_perl 'ref $d eq "HASH" ? scalar(keys %$d) : 0' <"$body")"
  if [ "$code" = "200" ] && [ "${keys:-0}" -gt 5 ]; then ok "$sym 報告(HTTP $code,$keys 個欄位)"; else bad "$sym 報告(HTTP $code,${keys:-0} 個欄位)"; fi
  rm -f "$body"
done

out="$(post /api/wizard/preview '{"template":"value","market":"TW"}' 120)"
valid="$(printf '%s' "$out" | jq_perl '$d->{ok} ? "yes" : "no"')"
count="$(printf '%s' "$out" | jq_perl 'ref $d->{ranking} eq "ARRAY" ? scalar(@{$d->{ranking}}) : (ref $d->{ranking} eq "HASH" && ref $d->{ranking}{rows} eq "ARRAY" ? scalar(@{$d->{ranking}{rows}}) : -1)')"
if [ "$valid" = "yes" ]; then ok "精靈預覽(排名 $count 筆)"; else bad "精靈預覽沒有成功"; fi

finish
