#!/bin/sh
# macOS:在 Finder 雙擊這個檔案就會打開終端機,第一次自動安裝,之後直接啟動 H0 Lab(只在本機 127.0.0.1)。
# 要停止網站:關掉這個終端機視窗。研究用途,非投資建議。
cd "$(dirname "$0")" || exit 1
if ! sh ./install.sh; then
  echo ""
  echo "安裝沒有完成,請看上面的說明。按 Enter 關閉這個視窗。"
  read -r _
  exit 1
fi
echo ""
echo "啟動中…瀏覽器會自動打開。要停止網站就關掉這個視窗。"
exec sh ./start.sh
