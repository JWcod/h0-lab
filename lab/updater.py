"""更新檢查與一鍵更新(只有這一個對外查詢,不帶任何使用者資料、不做遙測)。

檢查:每天最多一次查 GitHub Releases 最新版(ETag、短逾時、失敗安靜降級);
      離線模式、INVEST_LAB_UPDATE_CHECK=0、使用者在設定關閉時完全不連線。
打包版更新:HTTPS 下載發布檔案 → 大小上限 → Ed25519 驗證 manifest(含版本、大小、SHA-256)→ SHA-256 →
            安全解壓到 App 旁邊 → 驗證新版 → helper 替換(lab/update_install.py、lab/update_helper.py)。
            任何一步失敗就中止,舊版完全沒動過。
原始碼安裝:不自動改程式,只顯示更新指令。
release notes 是外部文字,這裡只限制長度,顯示時前端一律當純文字。
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import platform
import re
import secrets
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config, ed25519, runtime, update_install
from .update_install import InstallError
from .version import DATA_FORMAT_VERSION, RELEASE_REPO, __version__

CHECK_INTERVAL = 24 * 3600
TIMEOUT_CHECK = 8
TIMEOUT_DOWNLOAD = 30
MAX_API_BYTES = 2 * 1024**2
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SIG_BYTES = 1024
MAX_DOWNLOAD_BYTES = 1024**3
MAX_NOTES_CHARS = 20_000
MANIFEST_NAME = "update-manifest.json"
SIG_NAME = MANIFEST_NAME + ".sig"
PRODUCT = "h0-lab"
ALLOWED_HOSTS = ("api.github.com", "github.com")
ALLOWED_HOST_SUFFIX = ".githubusercontent.com"
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_lock = threading.RLock()
_check_running = False
_job: dict = {"phase": "idle"}
_token = secrets.token_urlsafe(32)
_startup_result: dict | None = None


# ---------- 版本 ----------
def parse_version(v: object) -> tuple | None:
    """語意化版本 → 可比較的 tuple;正式版排在同號的 prerelease 之後。格式不對回傳 None。"""
    m = _SEMVER.match(v) if isinstance(v, str) else None
    if not m:
        return None
    pre = m.group(4)
    if pre is None:
        return (int(m[1]), int(m[2]), int(m[3]), 1, ())
    parts = tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in pre.split("."))
    return (int(m[1]), int(m[2]), int(m[3]), 0, parts)


def is_prerelease(v: str) -> bool:
    p = parse_version(v)
    return p is None or p[3] == 0


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return bool(a and b and a > b)


# ---------- 設定與狀態檔 ----------
def _state_path() -> Path:
    return config.DATA_DIR / "update" / "state.json"


def _load_state() -> dict:
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(d: dict) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".state-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        pass   # 狀態檔寫不進去只會多查一次,不影響使用


def _update_state(**kw) -> dict:
    """鎖內重讀再改:背景檢查進行中使用者改了設定,不能被舊內容蓋掉。"""
    with _lock:
        st = _load_state()
        st.update(kw)
        _save_state(st)
        return st


def disabled_reason() -> str | None:
    if config.OFFLINE:
        return "offline"
    if os.environ.get("INVEST_LAB_UPDATE_CHECK") == "0":
        return "env"
    if _load_state().get("auto_check") is False:
        return "user"
    return None


def auto_check_enabled() -> bool:
    return _load_state().get("auto_check") is not False


def set_auto_check(enabled: bool) -> None:
    _update_state(auto_check=bool(enabled))


# ---------- 連線(只允許 GitHub + HTTPS;測試版才能指到本機假伺服器) ----------
def _test_build() -> bool:
    return runtime.FROZEN and runtime.build_info().get("test_build") is True


def _api_base() -> str:
    if _test_build():
        return runtime.build_info().get("update_api_base") or "https://api.github.com"
    return "https://api.github.com"


def _url_allowed(url: str) -> bool:
    if not isinstance(url, str) or "\\" in url:
        return False
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if _test_build() and u.scheme == "http" and host == "127.0.0.1":
        return True
    if u.username is not None or u.password is not None or port not in (None, 443):
        return False
    return u.scheme == "https" and (host in ALLOWED_HOSTS or host.endswith(ALLOWED_HOST_SUFFIX))


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _url_allowed(newurl):
            raise urllib.error.URLError(f"不允許的轉址:{urllib.parse.urlsplit(newurl).hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        return ssl.create_default_context()


def _opener() -> urllib.request.OpenerDirector:
    # 不用系統代理設定以外的東西;Cookie、帳號都不帶
    return urllib.request.build_opener(_Redirect(), urllib.request.HTTPSHandler(context=_ssl_context()))


def _request(url: str, headers: dict | None = None, timeout: float = TIMEOUT_CHECK):
    if config.OFFLINE:
        raise urllib.error.URLError("離線模式")
    if not _url_allowed(url):
        raise urllib.error.URLError("不允許的網址")
    req = urllib.request.Request(url, headers={"User-Agent": f"H0-Lab-Updater/{__version__}", **(headers or {})})
    return _opener().open(req, timeout=timeout)


def _read_limited(resp, limit: int) -> bytes:
    data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError("回應太大")
    return data


# ---------- 檢查最新版 ----------
def _release_from_api(j: dict) -> dict | None:
    """GitHub release JSON → 只留要用的欄位;draft、prerelease、格式不對都回傳 None。"""
    if not isinstance(j, dict) or j.get("draft") or j.get("prerelease"):
        return None
    tag = j.get("tag_name")
    if parse_version(tag) is None or is_prerelease(tag):
        return None
    assets = {}
    for a in j.get("assets") or []:
        if isinstance(a, dict) and isinstance(a.get("name"), str) and isinstance(a.get("browser_download_url"), str):
            if _url_allowed(a["browser_download_url"]):
                assets[a["name"]] = a["browser_download_url"]
    url = j.get("html_url")
    tag_url = rf"https://github\.com/{re.escape(RELEASE_REPO)}/releases/tag/[0-9A-Za-z.+-]+"
    return {"version": tag.lstrip("v"), "tag": tag, "notes": str(j.get("body") or "")[:MAX_NOTES_CHARS],
            "url": url if isinstance(url, str) and re.fullmatch(tag_url, url) else f"https://github.com/{RELEASE_REPO}/releases",
            "published_at": str(j.get("published_at") or "")[:32], "assets": assets}


def check_now() -> dict:
    """查一次最新版並更新狀態檔,回傳 {"ok": bool, ...}。呼叫前要先確認沒有被關閉。"""
    st = _load_state()
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if st.get("etag"):
        headers["If-None-Match"] = str(st["etag"])
    now = time.time()
    try:
        try:
            with _request(f"{_api_base()}/repos/{RELEASE_REPO}/releases/latest", headers) as r:
                raw = _read_limited(r, MAX_API_BYTES)
                etag = r.headers.get("ETag")
        except urllib.error.HTTPError as e:
            if e.code == 304:
                _update_state(last_check=now, last_error=None)
                return {"ok": True, "not_modified": True}
            raise
        rel = _release_from_api(json.loads(raw))
        _update_state(last_check=now, last_error=None, etag=etag if rel else None, latest=rel)
        return {"ok": True}
    except (OSError, ValueError, http.client.HTTPException) as e:   # 離線、404、限流、JSON 壞掉:安靜降級,明天再試
        code = getattr(e, "code", None)
        error = f"http_{code}" if code else "network"
        _update_state(last_check=now, last_error=error)
        return {"ok": False, "error": error}


def _due(st: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    last = st.get("last_check")
    return not isinstance(last, (int, float)) or last > now or now - last >= CHECK_INTERVAL


def maybe_check_async() -> bool:
    """到期才在背景查一次;status API 每次呼叫都會走這裡,開啟時查、之後每天最多一次。"""
    global _check_running
    if disabled_reason() is not None or not _due(_load_state()):
        return False
    with _lock:
        if _check_running:
            return False
        _check_running = True

    def work():
        global _check_running
        try:
            check_now()
        finally:
            with _lock:
                _check_running = False

    threading.Thread(target=work, daemon=True, name="h0-update-check").start()
    return True


# ---------- 安裝方式 ----------
def install_kind() -> str:
    """frozen:打包的 App;git:主 checkout 的 git clone;manual:下載 ZIP 或 worktree,只能手動更新。"""
    if runtime.FROZEN:
        return "frozen"
    git = config.ROOT / ".git"
    return "git" if git.is_dir() else "manual"


def public_key() -> bytes | None:
    """App 內嵌的更新公鑰(打包時寫進 _build_info.json);格式不對當成沒有。"""
    k = runtime.build_info().get("update_public_key")
    try:
        raw = base64.b64decode(k, validate=True) if isinstance(k, str) and k else b""
    except ValueError:
        return None
    return raw if len(raw) == 32 else None


COMMANDS = ["git pull --ff-only", "./install.sh"]


def _blocker(kind: str) -> str | None:
    if kind == "manual":
        return "manual_install"
    if kind == "git":
        return "source_install"
    if public_key() is None:
        return "no_public_key"
    app = runtime.app_bundle_path()
    if app is None:
        return "not_app_bundle"
    return update_install.location_blocker(app)


# ---------- 狀態 ----------
def _read_result_once() -> dict | None:
    """helper 留下的結果:這次執行讀一次就記在記憶體,前端可以告訴使用者更新成功或已回復舊版。"""
    global _startup_result
    if _startup_result is None:
        _startup_result = {}
        if runtime.FROZEN:
            p = update_install.update_dir() / "last_result.json"
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict) and time.time() - float(d.get("time", 0)) < 7 * 24 * 3600:
                    _startup_result = {k: d.get(k) for k in ("status", "code", "from_version", "to_version", "time")}
            except (OSError, ValueError, TypeError):
                pass
    return _startup_result or None


def data_format_state() -> dict:
    """App 的使用者資料格式:資料比這一版新(裝回舊版)時提示。"""
    out = {"current": DATA_FORMAT_VERSION, "stored": None, "newer_than_app": False}
    if runtime.FROZEN:
        try:
            out["stored"] = int(json.loads((runtime.app_support_dir() / "data_format.json").read_text(encoding="utf-8"))["version"])
            out["newer_than_app"] = out["stored"] > DATA_FORMAT_VERSION
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return out


def status() -> dict:
    runtime.note_api_hit()
    maybe_check_async()
    st = _load_state()
    reason = disabled_reason()
    kind = install_kind()
    rel = st.get("latest") if isinstance(st.get("latest"), dict) else None
    available = bool(rel and reason is None and is_newer(rel.get("version")))
    with _lock:
        job = dict(_job)
    out = {"current": __version__, "repo": RELEASE_REPO, "install_kind": kind, "enabled": reason is None, "disabled_reason": reason,
           "auto_check": auto_check_enabled(), "last_check": st.get("last_check"), "update_available": available,
           "latest": None, "job": job, "token": _token, "last_result": _read_result_once(), "data_format": data_format_state(),
           "can_apply": False, "blocker": None, "commands": [], "releases_url": f"https://github.com/{RELEASE_REPO}/releases"}
    if available:
        out["latest"] = {k: rel.get(k) for k in ("version", "notes", "url", "published_at")}
        out["blocker"] = _blocker(kind)
        out["can_apply"] = out["blocker"] is None
        if kind == "git":
            out["commands"] = COMMANDS
    return out


# ---------- 權杖 ----------
def current_token() -> str:
    with _lock:
        return _token


def consume_token(tok: object) -> bool:
    """一次性權杖(套用更新與改設定共用):用對了就換一個新的。權杖只透過驗過 Host 的 GET API 取得。"""
    global _token
    with _lock:
        if not isinstance(tok, str) or not secrets.compare_digest(tok.encode("utf-8", "replace"), _token.encode()):
            return False
        _token = secrets.token_urlsafe(32)
        return True


# ---------- 更新工作 ----------
def _set_job(**kw) -> None:
    with _lock:
        _job.update(kw)


def _fail(code: str, detail: str = "") -> None:
    print(f"[更新] 中止:{code} {detail}", flush=True)
    _set_job(phase="failed", error=code)


def job_active() -> bool:
    with _lock:
        return _job.get("phase") in ("starting", "manifest", "downloading", "verifying", "unpacking", "testing", "backup", "restarting")


def start_apply(version: str, confirm_data_format: bool = False) -> dict:
    """開始更新(背景執行),進度看 status()["job"]。呼叫前 API 層已驗過來源與權杖。"""
    with _lock:
        if job_active():
            return {"ok": False, "error": "already_running"}
        st = _load_state()
        rel = st.get("latest") if isinstance(st.get("latest"), dict) else None
        if disabled_reason() == "offline":
            return {"ok": False, "error": "offline"}
        if not rel or rel.get("version") != version or not is_newer(version):
            return {"ok": False, "error": "version_mismatch"}
        kind = install_kind()
        blocker = _blocker(kind)
        if blocker:
            return {"ok": False, "error": blocker}
        _job.clear()
        _job.update(phase="starting", version=version, started=time.time())
    threading.Thread(target=_apply_frozen, args=(rel, confirm_data_format), daemon=True, name="h0-update-apply").start()
    return {"ok": True}


def verify_manifest(raw: bytes, sig_b64: bytes, key: bytes, *, tag_version: str, arch: str) -> dict:
    """簽章對了才解析內容;版本要和 release 的 tag 一致(簽過的版本號防降版與重放)。回傳 {manifest, asset}。"""
    try:
        sig = base64.b64decode(sig_b64.strip(), validate=True)
    except ValueError as e:
        raise InstallError("signature_invalid", "簽章不是 base64") from e
    if not ed25519.verify(key, raw, sig):
        raise InstallError("signature_invalid")
    try:
        m = json.loads(raw)
    except ValueError as e:
        raise InstallError("manifest_invalid", str(e)) from e
    if not isinstance(m, dict) or m.get("schema") != 1 or m.get("product") != PRODUCT:
        raise InstallError("manifest_invalid", "schema / product 不符")
    if m.get("version") != tag_version or not is_newer(tag_version):
        raise InstallError("manifest_invalid", "版本與 release 不符,或不比目前版本新")
    asset = (m.get("assets") or {}).get(arch) if isinstance(m.get("assets"), dict) else None
    if not isinstance(asset, dict):
        raise InstallError("no_asset_for_arch", arch)
    name, size, sha = asset.get("name"), asset.get("size"), asset.get("sha256")
    if not (isinstance(name, str) and "/" not in name and isinstance(size, int) and not isinstance(size, bool)
            and 0 < size <= MAX_DOWNLOAD_BYTES and isinstance(sha, str) and _SHA256.match(sha)):
        raise InstallError("manifest_invalid", "asset 欄位不合法")
    return {"manifest": m, "asset": asset}


def download_verified(url: str, dest: Path, size: int, sha256: str, on_progress=None) -> None:
    """串流下載:超過宣告大小立刻中止、長度與 SHA-256 都要相符;失敗時不留下半個檔案。"""
    h = hashlib.sha256()
    got = 0
    try:
        with _request(url, timeout=TIMEOUT_DOWNLOAD) as r, open(dest, "xb") as f:
            while chunk := r.read(256 * 1024):
                got += len(chunk)
                if got > size:
                    raise InstallError("download_too_large")
                h.update(chunk)
                f.write(chunk)
                if on_progress:
                    on_progress(got)
    except InstallError:
        dest.unlink(missing_ok=True)
        raise
    except (OSError, ValueError, http.client.HTTPException) as e:
        dest.unlink(missing_ok=True)
        raise InstallError("download_failed", str(e)) from e
    if got != size:
        dest.unlink(missing_ok=True)
        raise InstallError("download_incomplete", f"{got}/{size}")
    if not secrets.compare_digest(h.hexdigest(), sha256):
        dest.unlink(missing_ok=True)
        raise InstallError("hash_mismatch")


def _fetch_small(url: str, limit: int) -> bytes:
    try:
        with _request(url, timeout=TIMEOUT_DOWNLOAD) as r:
            return _read_limited(r, limit)
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise InstallError("download_failed", str(e)) from e


def _arch() -> str:
    return platform.machine()


def _apply_frozen(rel: dict, confirm_data_format: bool) -> None:
    app = runtime.app_bundle_path()
    stage = None
    version = rel["version"]
    info = runtime.build_info()
    test_fail = os.environ.get("H0_TEST_UPDATE_FAIL") if _test_build() else None
    try:
        key = public_key()
        assets = rel.get("assets") or {}
        if key is None or app is None:
            raise InstallError("no_public_key")
        if MANIFEST_NAME not in assets or SIG_NAME not in assets:
            raise InstallError("release_incomplete")
        _set_job(phase="manifest")
        raw = _fetch_small(assets[MANIFEST_NAME], MAX_MANIFEST_BYTES)
        sig = _fetch_small(assets[SIG_NAME], MAX_SIG_BYTES)
        v = verify_manifest(raw, sig, key, tag_version=version, arch=_arch())
        m, asset = v["manifest"], v["asset"]
        new_format = m.get("data_format")
        if not isinstance(new_format, int) or isinstance(new_format, bool):
            raise InstallError("manifest_invalid", "data_format")
        needs_backup = new_format != DATA_FORMAT_VERSION
        if needs_backup and not confirm_data_format:
            _set_job(phase="failed", error="data_format_confirm", data_format=new_format)
            return
        if asset["name"] not in assets:
            raise InstallError("release_incomplete")
        stage = update_install.make_stage_dir(app)
        zip_path = stage / "update.zip"
        _set_job(phase="downloading", total=asset["size"], done=0)
        download_verified(assets[asset["name"]], zip_path, asset["size"], asset["sha256"], lambda n: _set_job(done=n))
        _set_job(phase="unpacking")
        dest = stage / "staged"
        dest.mkdir()
        staged = update_install.safe_extract(zip_path, dest)
        zip_path.unlink(missing_ok=True)
        _set_job(phase="testing")
        update_install.verify_staged(staged, version)
        if needs_backup:
            _set_job(phase="backup")
            backup = update_install.backup_user_data(runtime.app_support_dir(), version)
            _set_job(backup=str(backup))
        if not runtime.can_shutdown():
            raise InstallError("restart_unavailable")
        _set_job(phase="restarting")
        update_install.launch_helper(staged, app, from_version=__version__, to_version=version,
                                     reopen="exec" if info.get("test_build") else "open", test_fail=test_fail)
        (update_install.update_dir() / "restart_marker").write_text(version, encoding="utf-8")
        runtime.request_shutdown()
    except InstallError as e:
        if stage is not None:
            update_install.discard_stage(stage, app)
        _fail(e.code, str(e))
    except Exception as e:  # noqa: BLE001  任何意外都中止、保留舊版,不要讓伺服器掛掉
        if stage is not None:
            update_install.discard_stage(stage, app)
        _fail("unexpected", f"{type(e).__name__}: {e}")
