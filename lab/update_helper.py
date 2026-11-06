"""獨立的更新 helper:等主程式結束 → 原子替換 .app → 驗證新版 → 失敗回滾 → 重新開啟。

由「已通過簽章與 self-test 的新版 App」執行(<暫存>/H0 Lab.app/Contents/MacOS/H0 Lab --update-helper <JSON 檔>)。
只用標準函式庫、一開始就 import 完:替換之後自己所在的資料夾會變成舊版,不能再延遲載入模組。
這裡不刪任何東西:舊版留在暫存資料夾,由新版啟動時整理(lab/update_install.py cleanup_after_update)。
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RENAME_SWAP = 0x00000002
EXE_NAME = "H0 Lab"
WAIT_SECONDS = 60
SELF_TEST_SECONDS = 180


def _renamex_swap(a: str, b: str) -> bool:
    """macOS renamex_np(RENAME_SWAP):兩個路徑一次對調,任何時刻目標都存在。不支援(其他系統、檔案系統)回傳 False。"""
    try:
        fn = ctypes.CDLL(None, use_errno=True).renamex_np
    except (OSError, AttributeError):
        return False
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    if fn(os.fsencode(a), os.fsencode(b), RENAME_SWAP) == 0:
        return True
    e = ctypes.get_errno()
    if e in (errno.ENOTSUP, errno.EINVAL, errno.ENOSYS):
        return False
    raise OSError(e, os.strerror(e), a)


def swap_paths(a: Path, b: Path) -> None:
    """對調兩個路徑;沒有 RENAME_SWAP 時退回三次 rename,中途失敗會還原。"""
    if _renamex_swap(str(a), str(b)):
        return
    tmp = a.with_name(a.name + f".swap-{os.getpid()}")
    os.rename(a, tmp)
    try:
        os.rename(b, a)
    except OSError:
        os.rename(tmp, a)
        raise
    os.rename(tmp, b)


def _is_zombie(pid: int) -> bool:
    """已結束但父行程還沒回收:kill(pid, 0) 仍然成功,其實已經不會再動 .app 了。"""
    try:
        r = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.stdout.strip().startswith("Z")


def wait_for_exit(pid: int, seconds: float = WAIT_SECONDS) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        if _is_zombie(pid):
            return True
        time.sleep(0.2)
    return False


def self_test(app: Path, env: dict | None = None, seconds: float = SELF_TEST_SECONDS) -> bool:
    """跑新版的 --self-test(不開網頁、不碰使用者資料)。"""
    exe = app / "Contents" / "MacOS" / EXE_NAME
    try:
        r = subprocess.run([str(exe), "--self-test"], capture_output=True, timeout=seconds, env=env, cwd=str(app.parent),
                           start_new_session=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def write_result(path: Path, **kw) -> None:
    """結果寫成 JSON(原子替換),新版或回滾後的舊版啟動時讀。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".result-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({**kw, "time": time.time()}, f, ensure_ascii=False)
    os.replace(tmp, path)


def reopen(target: Path, mode: str, log: Path | None = None) -> None:
    """重新開啟 App。open:一般情況;exec:測試版直接執行(環境變數才傳得過去);none:不開。"""
    if mode == "open":
        subprocess.run(["/usr/bin/open", str(target)], capture_output=True, timeout=30)
    elif mode == "exec":
        out = open(log, "ab") if log else subprocess.DEVNULL
        subprocess.Popen([str(target / "Contents" / "MacOS" / EXE_NAME)], stdin=subprocess.DEVNULL, stdout=out,
                         stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)


def run(plan: dict) -> int:
    """plan:pid、target、staged、result、reopen、from_version、to_version、log(選用)、test_fail(只有測試版會帶)。"""
    target, staged, result = Path(plan["target"]), Path(plan["staged"]), Path(plan["result"])
    reopen_mode, log = plan.get("reopen", "open"), plan.get("log")
    log = Path(log) if log else None
    versions = {"from_version": plan.get("from_version"), "to_version": plan.get("to_version")}
    test_fail = plan.get("test_fail")

    if not wait_for_exit(int(plan["pid"]), float(plan.get("wait_seconds", WAIT_SECONDS))):
        write_result(result, status="failed", code="app_still_running", **versions)
        return 1
    # 把 target 換成新版;之後 staged 路徑放的是舊版
    try:
        if test_fail == "swap":
            raise OSError("測試:替換失敗")
        swap_paths(target, staged)
    except OSError:
        write_result(result, status="failed", code="swap_failed", **versions)
        reopen(target, reopen_mode, log)
        return 1
    ok = test_fail != "selftest" and self_test(target, plan.get("self_test_env"))
    if not ok:
        try:
            swap_paths(target, staged)
        except OSError:
            write_result(result, status="failed", code="rollback_failed", previous=str(staged), **versions)
            return 2
        write_result(result, status="failed", code="new_version_failed_rolled_back", **versions)
        reopen(target, reopen_mode, log)
        return 1
    write_result(result, status="ok", code="updated", **versions)
    reopen(target, reopen_mode, log)
    return 0


def main(argv: list[str]) -> int:
    signal.signal(signal.SIGHUP, signal.SIG_IGN)   # 主程式結束不要帶走 helper
    plan = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    return run(plan)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
