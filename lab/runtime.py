"""執行環境:原始碼執行或打包成 App(packaging/launcher.py),兩邊共用的小工具。

這個模組不 import lab.config:launcher 要在 config 載入之前就能用它。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

FROZEN = bool(getattr(sys, "frozen", False))
APP_NAME = "H0 Lab"
APP_BUNDLE = "H0 Lab.app"
LAB_DIR = Path(__file__).resolve().parent
ROOT = LAB_DIR.parent

# 打包後要在子程序跑的模組:只開放這幾個(launcher 的 --h0-run;不把 App 變成通用直譯器)
RUNNABLE_MODULES = ("lab.setup_research", "lab.research.backtest_pit")

_build_info: dict | None = None
_shutdown_hook: Callable[[], None] | None = None
_last_api_hit = 0.0
_lock = threading.Lock()
sec_blocked = False   # 沒填 SEC 聯絡方式、又真的要抓 SEC 資料時設成 True(前端提示填寫)


def app_support_dir() -> Path:
    """App 的使用者資料夾(路徑跟著 HOME 走,測試可以用空資料夾)。"""
    return Path.home() / "Library" / "Application Support" / APP_NAME


def env_file() -> Path:
    """使用者的 .env。App 由 launcher 指到使用者資料夾;原始碼執行就是專案根目錄的 .env。"""
    v = os.environ.get("INVEST_LAB_ENV_FILE")
    return Path(v) if v else ROOT / ".env"


def build_info() -> dict:
    """打包時寫入的資訊(版本、commit、更新公鑰);原始碼執行沒有這個檔,回傳空 dict。"""
    global _build_info
    if _build_info is None:
        try:
            _build_info = json.loads((LAB_DIR / "_build_info.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _build_info = {}
    return _build_info


def app_bundle_path() -> Path | None:
    """正在執行的 .app 路徑(sys.executable = <App>.app/Contents/MacOS/<exe>);不是打包版回傳 None。"""
    if not FROZEN:
        return None
    p = Path(sys.executable).resolve()
    try:
        app = p.parents[2]
    except IndexError:
        return None
    return app if app.suffix == ".app" else None


def module_argv(module: str) -> list[str]:
    """用子程序跑 lab 內某個模組的命令:原始碼用 python -m,打包版用 App 執行檔的 --h0-run。"""
    if FROZEN:
        if module not in RUNNABLE_MODULES:
            raise ValueError(f"打包版不能執行 {module}")
        return [sys.executable, "--h0-run", module]
    return [sys.executable, "-m", module]


def set_shutdown_hook(fn: Callable[[], None] | None) -> None:
    global _shutdown_hook
    _shutdown_hook = fn


def can_shutdown() -> bool:
    return _shutdown_hook is not None


def request_shutdown() -> bool:
    """請 launcher 乾淨結束(更新時用);沒有 launcher(原始碼執行)回傳 False。"""
    if _shutdown_hook is None:
        return False
    _shutdown_hook()
    return True


def note_api_hit() -> None:
    """網頁打開的證據:launcher 更新後重開時,舊分頁還在就不再多開一個(lab/update_install 的 restart 標記)。"""
    global _last_api_hit
    with _lock:
        _last_api_hit = time.time()


def last_api_hit() -> float:
    with _lock:
        return _last_api_hit
