"""版本與發布來源的唯一定義(App、更新器、打包腳本都讀這裡)。"""
from __future__ import annotations

__version__ = "0.1.0"

# 公開 repo 名稱(2026-11-06 定案):要改名只改這一行(更新器查詢、下載網址驗證都由它衍生)
RELEASE_REPO = "JWcod/h0-lab"

# 使用者資料的格式版本:資料夾結構或檔案格式改到舊版讀不懂時才加 1;
# 新版的格式版本比目前高,更新器會提示並先備份(lab/updater.py)
DATA_FORMAT_VERSION = 1
