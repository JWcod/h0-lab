"""H0 Lab.app 的入口:在 import lab 之前設好路徑,再啟動本機伺服器與原生視窗(拿不到視窗才退回瀏覽器)。

App 本體唯讀,使用者的東西全部放在 ~/Library/Application Support/H0 Lab/(更新不會碰);
服務只綁 127.0.0.1。特殊參數:
  --self-test          檢查這一包能不能跑(更新器驗證新版用),不碰使用者資料
  --update-helper F    更新 helper(lab/update_helper.py)
  --h0-run MODULE ...  跑白名單裡的模組(打包版沒有 python -m)
測試用環境變數:H0_LAB_NO_BROWSER=1 不開視窗也不開瀏覽器、H0_LAB_HEADLESS=1 不建立 Dock 圖示。
"""
from __future__ import annotations

import fcntl
import json
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8765
PORT_TRIES = 20
ENV_TEMPLATE = """# H0 Lab 本機設定(只存在這台電腦,不會上傳)。也可以在網頁的「設定」填寫。
# SEC 要求每位使用者宣告自己的名字與聯絡信箱,例如 SEC_USER_AGENT=Jane Doe jane@your-domain.tld
SEC_USER_AGENT=
# FinMind 免費註冊後可拿 token,額度較高(不填也能用免費額度)
FINMIND_TOKEN=
"""
ENV_KEYS = ("SEC_USER_AGENT", "FINMIND_TOKEN", "INVEST_LAB_PORT", "INVEST_LAB_UPDATE_CHECK", "INVEST_LAB_RECORD", "INVEST_LAB_USER_HOLDOUT")


def support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "H0 Lab"


def load_env_file(path: Path) -> None:
    """使用者的 .env:只讀白名單的鍵,不覆蓋已經設定的環境變數,不執行任何東西。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key in ENV_KEYS and val:
            os.environ.setdefault(key, val)


def setup_env(support: Path | None = None) -> Path:
    """建立使用者資料夾、設定環境變數。必須在 import lab.config 之前呼叫。"""
    support = support or support_dir()
    data = support / "data"
    for d in (support, data, data / "research", support / "private", support / "logs", support / "run"):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(support, 0o700)
    env_file = support / ".env"
    if not env_file.exists():
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ENV_TEMPLATE)
    load_env_file(env_file)
    os.environ.setdefault("INVEST_LAB_DATA_DIR", str(data))
    os.environ.setdefault("INVEST_LAB_OUT_DIR", str(data))
    os.environ.setdefault("INVEST_LAB_PRIVATE_DIR", str(support / "private"))
    os.environ.setdefault("INVEST_LAB_BUDGET_FILE", str(data / "finmind_calls.json"))
    os.environ.setdefault("INVEST_LAB_REGISTRY", str(data / "research" / "experiments.jsonl"))
    os.environ.setdefault("INVEST_LAB_ENV_FILE", str(env_file))
    os.environ.setdefault("INVEST_LAB_USER_HOLDOUT", "1")   # 和 start.sh 相同:使用者模型的 holdout 開啟
    return support


def sync_data_format(support: Path, current: int) -> None:
    """記下資料格式版本。資料比這個版本新(裝回舊版)就不改寫,讓更新狀態提示使用者。"""
    p = support / "data_format.json"
    try:
        stored = int(json.loads(p.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError):
        stored = None
    if stored is None or stored < current:
        p.write_text(json.dumps({"version": current}), encoding="utf-8")


def apply_build_info() -> None:
    """把打包資訊填回 lab:版本(測試版可覆寫)與 commit。不去呼叫 git:沒裝開發工具的 Mac 會跳出安裝視窗。"""
    from lab import runtime, version
    info = runtime.build_info()
    if info.get("test_build") and info.get("version"):
        version.__version__ = info["version"]
    from lab import config
    config._GIT_SHA = str(info.get("commit") or "unknown")


def bind_port(preferred: int) -> tuple[socket.socket, int]:
    """從 preferred 往後找能綁的埠,都不行就交給系統挑。回傳已綁定的 socket(之後交給 uvicorn,不會被搶走)。"""
    for port in list(range(preferred, preferred + PORT_TRIES)) + [0]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            s.close()
            continue
        s.listen(128)
        s.set_inheritable(False)
        return s, s.getsockname()[1]
    raise OSError("找不到可用的埠")


def acquire_instance_lock(run_dir: Path) -> int | None:
    """同一個使用者資料夾只跑一份;已有一份在跑回傳 None。"""
    fd = os.open(run_dir / "instance.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _identity(port: int, timeout: float = 1.5) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/app/identity", timeout=timeout) as r:
            return json.loads(r.read(4096)).get("app") == "h0-lab"
    except (OSError, ValueError):
        return False


def open_browser(url: str) -> None:
    if os.environ.get("H0_LAB_NO_BROWSER") == "1":
        print(f"[H0 Lab] 不開瀏覽器(測試):{url}", flush=True)
        return
    # 不用 webbrowser 模組:它在 macOS 走 osascript
    subprocess.run(["/usr/bin/open", url], capture_output=True, timeout=30, check=False)


def redirect_logs(support: Path) -> None:
    """從 Finder 開時沒有終端機:輸出寫進 logs/app.log,太大就換一份。"""
    log = support / "logs" / "app.log"
    try:
        if log.exists() and log.stat().st_size > 5 * 1024**2:
            os.replace(log, log.with_suffix(".log.1"))
        f = open(log, "a", buffering=1, encoding="utf-8", errors="replace")
    except OSError:
        return
    sys.stdout = sys.stderr = f
    os.dup2(f.fileno(), 1)
    os.dup2(f.fileno(), 2)


def self_test() -> int:
    """更新器在替換之前與之後都會跑:所有資料放暫存、離線,不碰使用者資料。"""
    import shutil
    import tempfile
    from concurrent.futures import ProcessPoolExecutor
    tmp = tempfile.mkdtemp(prefix="h0lab-selftest-")
    out = {"ok": False}
    try:
        os.environ.update(INVEST_LAB_OFFLINE="1", INVEST_LAB_RECORD="0", INVEST_LAB_DATA_DIR=tmp, INVEST_LAB_OUT_DIR=tmp,
                          INVEST_LAB_PRIVATE_DIR=tmp, INVEST_LAB_BUDGET_FILE=f"{tmp}/fm.json",
                          INVEST_LAB_REGISTRY=f"{tmp}/reg.jsonl", INVEST_LAB_ENV_FILE=f"{tmp}/.env")
        os.environ.pop("INVEST_LAB_PORT", None)
        apply_build_info()
        from lab import config, runtime, updater, version
        from lab.models import store
        if runtime.build_info().get("test_build") and os.environ.get("H0_TEST_SELFTEST_FAIL") == "1":
            raise RuntimeError("測試:self-test 失敗")
        import AppKit  # noqa: F401  原生視窗的依賴也要在
        import certifi
        import curl_cffi  # noqa: F401
        import lxml  # noqa: F401
        import numpy  # noqa: F401
        import pandas  # noqa: F401
        import scipy.stats  # noqa: F401
        import WebKit  # noqa: F401
        import webview  # noqa: F401
        import yfinance  # noqa: F401

        from app import server, update_api  # noqa: F401
        from lab import setup_research  # noqa: F401
        from lab.research import backtest_pit  # noqa: F401
        checks = {"web": (config.WEB_DIR / "index.html").is_file() and (config.WEB_DIR / "js" / "update.js").is_file(),
                  "params": bool(config.load_params()), "models": bool(store.list_models()),
                  "certifi": Path(certifi.where()).is_file(), "version": updater.parse_version(version.__version__) is not None,
                  "routes": any(getattr(r, "path", "") == "/api/update/status" for r in update_api.router.routes)}
        bad = [k for k, v in checks.items() if not v]
        if bad:
            raise RuntimeError("檢查沒過:" + ", ".join(bad))
        with ProcessPoolExecutor(max_workers=1) as ex:   # 回測用 ProcessPool:確認打包後能 spawn 子程序
            assert ex.submit(pow, 2, 10).result(timeout=60) == 1024
        out = {"ok": True, "version": version.__version__}
    except BaseException as e:  # noqa: BLE001  self-test 任何失敗都要回報,不能讓例外吃掉結果
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0 if out["ok"] else 1


def run_module(argv: list[str]) -> int:
    from lab import runtime
    module, rest = argv[0], argv[1:]
    if module not in runtime.RUNNABLE_MODULES:
        print(f"不能執行 {module}", file=sys.stderr)
        return 64
    apply_build_info()
    import importlib
    return int(importlib.import_module(module).main(rest) or 0)


def run_app() -> int:
    support = setup_env()
    run_dir = support / "run"
    redirect_logs(support)
    lock_fd = acquire_instance_lock(run_dir)
    if lock_fd is None:
        try:
            port = int(json.loads((run_dir / "instance.json").read_text(encoding="utf-8"))["port"])
        except (OSError, ValueError, KeyError, TypeError):
            print("[H0 Lab] 已經在執行,但讀不到位址", flush=True)
            return 0
        if _identity(port):
            open_browser(f"http://127.0.0.1:{port}/")
        return 0

    sock, port = bind_port(int(os.environ.get("INVEST_LAB_PORT") or DEFAULT_PORT))
    os.environ["INVEST_LAB_PORT"] = str(port)
    apply_build_info()
    import uvicorn

    from app.server import app
    from lab import runtime, setup_job, update_install
    from lab.version import DATA_FORMAT_VERSION
    sync_data_format(support, DATA_FORMAT_VERSION)
    url = f"http://127.0.0.1:{port}/"

    # loop / http 固定用純 Python 版:打包時不帶 uvloop、httptools,只綁 127.0.0.1
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", loop="asyncio", http="h11", ws="none", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True, name="h0-server")
    thread.start()
    (run_dir / "instance.json").write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")
    threading.Thread(target=update_install.cleanup_after_update, args=(runtime.app_bundle_path(),), daemon=True).start()

    def wait_ready() -> None:
        for _ in range(300):
            if server.started or not thread.is_alive():
                return
            time.sleep(0.1)

    def first_open() -> None:
        """第一次開網頁。更新後重開時舊分頁通常還在、會自己重新整理:等一下,沒有分頁連上才開新的。"""
        wait_ready()
        marker = update_install.update_dir() / "restart_marker"
        if marker.exists():
            marker.unlink(missing_ok=True)
            started = time.time()
            while time.time() - started < 8:
                if runtime.last_api_hit() >= started:
                    return
                time.sleep(0.25)
        open_browser(url)

    def cleanup() -> None:
        server.should_exit = True
        setup_job.stop()
        thread.join(timeout=6)
        try:
            (run_dir / "instance.json").unlink(missing_ok=True)
        except OSError:
            pass
        try:
            sys.stdout.flush()
        except (OSError, ValueError):
            pass

    print(f"[H0 Lab] {url}", flush=True)
    if os.environ.get("H0_LAB_NO_BROWSER") != "1" and os.environ.get("H0_LAB_HEADLESS") != "1":
        wait_ready()
        (update_install.update_dir() / "restart_marker").unlink(missing_ok=True)   # 視窗是新開的,不用等舊分頁
        import mac_window
        mac_window.run(url, port, on_quit=cleanup, is_alive=thread.is_alive, set_shutdown_hook=runtime.set_shutdown_hook)
        # 走到這裡表示開不了視窗,退回瀏覽器
    threading.Thread(target=first_open, daemon=True, name="h0-open").start()

    shell = None
    if os.environ.get("H0_LAB_HEADLESS") != "1":
        try:
            import mac_shell
            shell = mac_shell.MacShell(on_open=lambda: open_browser(url), on_quit=cleanup,
                                       is_alive=thread.is_alive)
        except Exception as e:  # noqa: BLE001  沒有 Dock 圖示也要能用
            print(f"[H0 Lab] 無法建立 Dock 圖示:{type(e).__name__}: {e}", flush=True)
    if shell is not None:
        runtime.set_shutdown_hook(shell.terminate)
        shell.run()     # 不會回來:結束時 mac_shell 呼叫 on_quit 後直接結束程序
        return 0

    import signal
    stop = threading.Event()
    runtime.set_shutdown_hook(stop.set)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set() and thread.is_alive():
        stop.wait(0.5)
    cleanup()
    os._exit(0)   # 背景執行緒可能還在等網路,不等它們


def main() -> int:
    multiprocessing.freeze_support()   # 回測的 ProcessPool 子程序走到這裡就會接手,不會往下跑
    argv = sys.argv[1:]
    if argv[:1] == ["--update-helper"]:
        from lab import update_helper
        return update_helper.main(argv[1:])
    if argv[:1] == ["--self-test"]:
        return self_test()
    if argv[:1] == ["--h0-run"] and len(argv) >= 2:
        setup_env()
        return run_module(argv[1:])
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
