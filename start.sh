#!/bin/sh
# 啟動 H0 Lab(只在本機 127.0.0.1)。第一次用請先跑 ./install.sh
cd "$(dirname "$0")" || exit 1
PORT="${INVEST_LAB_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"

open_url() { (command -v open >/dev/null 2>&1 && open "$1") || (command -v xdg-open >/dev/null 2>&1 && xdg-open "$1") ; } 2>/dev/null

# 已經在跑了就直接開網頁,不要重複啟動
if curl -s -o /dev/null --max-time 2 "$URL/"; then
  echo "H0 Lab 已經在執行 → $URL"
  open_url "$URL"
  exit 0
fi

if [ ! -x .venv/bin/python ]; then
  echo "還沒安裝,先執行 ./install.sh"
  ./install.sh || exit 1
fi
(sleep 2 && open_url "$URL") &
# 使用者模型的 holdout(模型工作台,每個模型族一次;2026-11-02 專案決定)
export INVEST_LAB_USER_HOLDOUT="${INVEST_LAB_USER_HOLDOUT:-1}"
exec .venv/bin/python -m app.server
