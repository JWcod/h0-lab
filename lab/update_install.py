"""更新檔安裝:安全解壓、驗證暫存的新版、準備 helper、更新後整理。

下載與簽章在 lab/updater.py;這裡處理已通過 SHA-256 與簽章的 zip,仍然當成不可信資料解壓(縱深防禦)。
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

from . import runtime

MAX_ENTRIES = 60_000
MAX_UNPACKED = 3 * 1024**3
MAX_SYMLINK_TARGET = 1024
STAGE_PREFIX = ".H0 Lab Update-"
CHUNK = 1024 * 1024
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


class InstallError(Exception):
    """更新無法繼續;code 是前端對應文字的代碼。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def _clean_member(name: str) -> PurePosixPath:
    """zip 內的路徑只能是相對、不含 ..、不含反斜線與控制字元;不合格一律中止整個更新。"""
    if not name or "\\" in name or "\0" in name or any(ord(c) < 32 for c in name):
        raise InstallError("bad_archive", f"不合法的路徑:{name!r}")
    p = PurePosixPath(name)
    if p.is_absolute() or any(part in ("..", ".", "") for part in p.parts):
        raise InstallError("bad_archive", f"不合法的路徑:{name!r}")
    return p


def _fs_key(parts: tuple[str, ...]) -> str:
    """APFS 不分大小寫,也不分 Unicode 正規化(NFC / NFD 是同一個檔名):比對路徑要用同樣的規則。"""
    return unicodedata.normalize("NFD", "/".join(parts)).casefold()


def _inside(root: str, path: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def safe_extract(zip_path: Path, dest: Path, top: str = runtime.APP_BUNDLE) -> Path:
    """把 zip 解到 dest(必須是空資料夾),回傳 dest/top。
    防線:zip slip、絕對路徑、重複路徑、大小寫不同的同名路徑、裝置檔、setuid、zip bomb;
    symlink 只能是相對路徑且最後落在 App 裡面,任何檔案都不能經過 symlink 寫入。"""
    dest = Path(os.path.realpath(dest))
    if not dest.is_dir() or any(dest.iterdir()):
        raise InstallError("bad_archive", "解壓目的地必須是空資料夾")
    root = str(dest / top)
    seen: set[str] = set()
    links: dict[PurePosixPath, str] = {}
    total = 0
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as e:
        raise InstallError("bad_archive", str(e)) from e
    with zf:
        infos = zf.infolist()
        if not infos or len(infos) > MAX_ENTRIES:
            raise InstallError("bad_archive", f"項目數不合理:{len(infos)}")
        plan = []
        for info in infos:
            is_dir_name = info.filename.endswith("/")
            p = _clean_member(info.filename[:-1] if is_dir_name else info.filename)
            if p.parts[0] != top:
                raise InstallError("bad_archive", f"壓縮檔裡有 {top} 以外的東西:{info.filename!r}")
            key = _fs_key(p.parts)
            if key in seen:
                raise InstallError("bad_archive", f"重複的路徑:{info.filename!r}")
            seen.add(key)
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
                raise InstallError("bad_archive", f"不允許的檔案類型:{info.filename!r}")
            if kind == stat.S_IFLNK:
                if info.file_size > MAX_SYMLINK_TARGET:
                    raise InstallError("bad_archive", f"symlink 目標太長:{info.filename!r}")
                target = zf.read(info).decode("utf-8", "strict")
                if not target or target.startswith("/") or "\0" in target:
                    raise InstallError("bad_archive", f"symlink 指向絕對路徑:{info.filename!r}")
                links[p] = target
            total += info.file_size
            if total > MAX_UNPACKED:
                raise InstallError("bad_archive", "解壓後大小超過上限")
            plan.append((info, p, kind, mode, is_dir_name))
        _check_links(links, top)
        link_keys = {_fs_key(p.parts) for p in links}
        written = 0
        for info, p, kind, mode, is_dir_name in plan:
            for i in range(1, len(p.parts)):
                if _fs_key(p.parts[:i]) in link_keys:
                    raise InstallError("bad_archive", f"路徑經過 symlink:{info.filename!r}")
            out = dest.joinpath(*p.parts)
            parent = os.path.realpath(out.parent)    # 不管上面怎麼比對,實際寫入前再看一次檔案系統
            if parent != str(dest) and not _inside(root, parent):
                raise InstallError("bad_archive", f"路徑會寫到 App 外面:{info.filename!r}")
            if kind == stat.S_IFLNK:
                out.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(links[p], out)
            elif kind == stat.S_IFDIR or is_dir_name:
                out.mkdir(parents=True, exist_ok=True)
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                perm = (mode & 0o755) or 0o644    # 拿掉 setuid / setgid / sticky 與其他人可寫
                fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, perm)
                with os.fdopen(fd, "wb") as f, zf.open(info) as src:
                    while chunk := src.read(CHUNK):
                        written += len(chunk)
                        if written > MAX_UNPACKED:     # 不相信 zip 宣告的大小
                            raise InstallError("bad_archive", "解壓後大小超過上限")
                        f.write(chunk)
    # 事後再走一次:每個 symlink 的最終位置都要在 App 裡面
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            full = os.path.join(dirpath, n)
            if os.path.islink(full) and not _inside(root, os.path.realpath(full)):
                raise InstallError("bad_archive", f"symlink 指到 App 外面:{full}")
    if not os.path.isdir(root):
        raise InstallError("bad_archive", f"壓縮檔裡沒有 {top}")
    return Path(root)


def _check_links(links: dict[PurePosixPath, str], top: str) -> None:
    """只看路徑字面就先擋:symlink 解析後(含連續 symlink)不能離開 App 根目錄。"""
    for start in links:
        cur = start
        for _ in range(40):
            if cur not in links:
                break
            parts: list[str] = list(cur.parent.parts)
            for piece in PurePosixPath(links[cur]).parts:
                if piece == "..":
                    if len(parts) <= 1:
                        raise InstallError("bad_archive", f"symlink 指到 App 外面:{start}")
                    parts.pop()
                elif piece != ".":
                    parts.append(piece)
            if not parts or parts[0] != top:
                raise InstallError("bad_archive", f"symlink 指到 App 外面:{start}")
            cur = PurePosixPath(*parts)
        else:
            raise InstallError("bad_archive", f"symlink 循環:{start}")


def location_blocker(app: Path) -> str | None:
    """App 能不能原地替換;不能就回傳前端文字代碼。"""
    s = str(app)
    if "/AppTranslocation/" in s or s.startswith("/Volumes/"):
        return "move_to_applications"
    if not os.access(app.parent, os.W_OK) or not os.access(app, os.W_OK):
        return "app_not_writable"
    return None


def make_stage_dir(app: Path) -> Path:
    """暫存資料夾放在 App 旁邊(同一個檔案系統,rename 才是原子的);權限 0700、名稱隨機。"""
    try:
        return Path(tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=str(app.parent)))
    except OSError as e:
        raise InstallError("app_not_writable", str(e)) from e


def verify_staged(staged: Path, expected_version: str) -> None:
    """解壓後、結束舊版之前:版本要對、程式碼簽章完整、新版自己能跑起來。"""
    try:
        with open(staged / "Contents" / "Info.plist", "rb") as f:
            info = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError) as e:
        raise InstallError("verify_failed", f"Info.plist:{e}") from e
    if info.get("CFBundleShortVersionString") != expected_version:
        raise InstallError("verify_failed", f"版本不符:{info.get('CFBundleShortVersionString')!r}")
    exe = staged / "Contents" / "MacOS" / runtime.APP_NAME
    if not exe.is_file() or exe.is_symlink():
        raise InstallError("verify_failed", "找不到主程式")
    if Path("/usr/bin/codesign").exists():
        r = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(staged)], capture_output=True, timeout=180)
        if r.returncode != 0:
            raise InstallError("verify_failed", "程式碼簽章不完整:" + r.stderr.decode("utf-8", "replace")[:300])
    # 已通過 Ed25519 與 SHA-256:使用者核准過這個 App,更新不再跳一次 Gatekeeper(Sparkle 也是這樣做)。
    # 只有 App 自己帶隔離標記時才會繼承到;沒有就什麼都不做。
    if Path("/usr/bin/xattr").exists():
        subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(staged)], capture_output=True, timeout=120, check=False)
    from . import update_helper
    if not update_helper.self_test(staged):
        raise InstallError("verify_failed", "新版無法在這台電腦上啟動")


BACKUP_ITEMS = (".env", "private", "data_format.json", "data/snapshots", "data/research", "data/scan", "data/finmind_calls.json")


def backup_user_data(support: Path, label: str) -> Path:
    """資料格式要變之前的備份:使用者自己的東西;快取與回測資料(可以重建、很大)不備份。"""
    safe = re.sub(r"[^0-9A-Za-z.-]", "_", label)
    dest = support / "backups" / f"before-{safe}-{time.strftime('%Y%m%d-%H%M%S')}"
    dest.mkdir(parents=True, exist_ok=False)
    for rel in BACKUP_ITEMS:
        src = support / rel
        out = dest / rel
        if src.is_dir():
            shutil.copytree(src, out, symlinks=True)
        elif src.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
    return dest


def update_dir() -> Path:
    return runtime.app_support_dir() / "update"


def launch_helper(staged: Path, target: Path, *, from_version: str, to_version: str, reopen: str = "open",
                  test_fail: str | None = None) -> int:
    """從已驗證的新版啟動 helper(獨立 session,主程式結束不影響它),回傳 helper 的 pid。"""
    ud = update_dir()
    ud.mkdir(parents=True, exist_ok=True)
    plan = {"pid": os.getpid(), "target": str(target), "staged": str(staged), "result": str(ud / "last_result.json"),
            "reopen": reopen, "from_version": from_version, "to_version": to_version, "log": str(ud / "helper.log")}
    if test_fail:
        plan["test_fail"] = test_fail
    stage = staged.parent.parent     # <暫存>/staged/H0 Lab.app
    plan_path = stage / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    with open(ud / "helper.log", "ab") as log:
        p = subprocess.Popen([str(staged / "Contents" / "MacOS" / runtime.APP_NAME), "--update-helper", str(plan_path)],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                             close_fds=True, cwd=str(stage))
    pending = {"stage_dir": str(stage), "helper_pid": p.pid, "from_version": from_version, "to_version": to_version,
               "time": time.time()}
    (ud / "pending.json").write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
    return p.pid


def _prune_stale_stages(app: Path, older_than: float = 24 * 3600) -> None:
    """下載到一半就被強制結束留下的暫存(沒有 pending.json,才會走到這裡)。"""
    try:
        for d in app.parent.iterdir():
            if d.name.startswith(STAGE_PREFIX) and d.is_dir() and not d.is_symlink() and time.time() - d.stat().st_mtime > older_than:
                shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


def cleanup_after_update(app: Path | None, wait_seconds: float = 90) -> None:
    """下次啟動時整理(launcher 在背景呼叫):等 helper 結束,舊版留一份在 update/previous 供手動回滾,刪掉暫存。
    只動 App 旁邊、名稱符合 STAGE_PREFIX 的資料夾;pending.json 內容不符就不動。"""
    ud = update_dir()
    pf = ud / "pending.json"
    if app is None:
        return
    if not pf.is_file():
        _prune_stale_stages(app)
        return
    try:
        pending = json.loads(pf.read_text(encoding="utf-8"))
        stage = Path(pending["stage_dir"])
        helper_pid = int(pending.get("helper_pid") or 0)
        if not 0 <= helper_pid < 2**31:
            raise ValueError("helper_pid")
    except (OSError, ValueError, KeyError, TypeError):
        pf.unlink(missing_ok=True)
        return
    if stage.parent != app.parent or not stage.name.startswith(STAGE_PREFIX) or stage.is_symlink():
        pf.unlink(missing_ok=True)
        return
    from . import update_helper
    if helper_pid and helper_pid != os.getpid() and not update_helper.wait_for_exit(helper_pid, wait_seconds):
        return
    old = stage / "staged" / runtime.APP_BUNDLE
    try:
        result = json.loads((ud / "last_result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = {}
    # 結果要是這次更新的:舊的成功紀錄不算,否則沒換過的新版會被當成舊版搬走
    # 回滾失敗時暫存裡的舊版是唯一能手動復原的一份,也要留下來
    swapped = (result.get("status") == "ok" or result.get("code") == "rollback_failed") and result.get("to_version") == pending.get("to_version")
    if swapped and old.is_dir() and not old.is_symlink():
        prev = ud / "previous"
        if prev.is_dir():
            shutil.rmtree(prev, ignore_errors=True)
        prev.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(prev / runtime.APP_BUNDLE))
    shutil.rmtree(stage, ignore_errors=True)
    pf.unlink(missing_ok=True)


def discard_stage(stage: Path, app: Path | None) -> None:
    """更新中止時丟掉暫存(舊版完全沒動過)。"""
    if app is not None and stage.parent == app.parent and stage.name.startswith(STAGE_PREFIX) and not stage.is_symlink():
        shutil.rmtree(stage, ignore_errors=True)


def valid_version(v: object) -> bool:
    return isinstance(v, str) and bool(_VERSION_RE.match(v))
