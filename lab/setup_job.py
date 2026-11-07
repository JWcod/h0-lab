"""在 App 內建立回測資料:背景子程序跑 lab/setup_research.py,網頁看進度(同一時間只跑一個)。

輸出寫進 OUT_DIR/setup_research.log;網頁只顯示最後一行進度。App 結束時一起停掉(已下載的快取會保留,重跑時略過)。
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import config, runtime

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_started: float | None = None
_finished: float | None = None
_exit_code: int | None = None
_cancelled = False
EXIT_MESSAGES = {0: "done", 2: "offline", 3: "has_full_research", 4: "no_network", 5: "incomplete", 6: "sec_refused", 8: "sec_ua_required",
                 7: "partial"}


def log_path() -> Path:
    return config.OUT_DIR / "setup_research.log"


def start() -> dict:
    global _proc, _started, _finished, _exit_code, _cancelled
    if config.OFFLINE:
        return {"ok": False, "error": "offline"}
    if config.SEC_UA_IS_DEFAULT:
        return {"ok": False, "error": "sec_ua_required"}
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return {"ok": False, "error": "already_running"}
        log = open(log_path(), "ab")
        try:
            _proc = subprocess.Popen(runtime.module_argv("lab.setup_research"), cwd=str(config.ROOT), stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                     env=dict(os.environ, PYTHONUNBUFFERED="1"))
        finally:
            log.close()
        _started, _finished, _exit_code, _cancelled = time.time(), None, None, False
    return {"ok": True}


def _last_line() -> str:
    try:
        with open(log_path(), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4096))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for ln in reversed(lines):
        if ln.strip():
            return ln.strip()[:300]
    return ""


def status() -> dict:
    global _finished, _exit_code
    with _lock:
        running = _proc is not None and _proc.poll() is None
        if _proc is not None and not running and _exit_code is None:
            _exit_code, _finished = _proc.returncode, time.time()
        rc = _exit_code
        out = {"running": running, "started": _started, "finished": _finished, "exit_code": rc, "cancelled": _cancelled,
               "result": None if rc is None else EXIT_MESSAGES.get(rc, "failed"), "line": _last_line() if _started else ""}
    return out


def stop() -> bool:
    """停掉建立工作(整個程序群組,包含它開的計算子程序)。"""
    global _cancelled
    with _lock:
        if _proc is None or _proc.poll() is not None:
            return False
        _cancelled = True
        try:
            os.killpg(_proc.pid, signal.SIGTERM)
        except OSError:
            return False
        return True
