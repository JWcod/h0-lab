"""更新器:版本比對、檢查時機、manifest 驗證、下載檢查、安全解壓、helper 替換與回滾、.env 寫入。不連外網。"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import zipfile
from pathlib import Path

import pytest

from lab import runtime, update_helper, update_install, updater, user_settings
from _ed25519_sign import public_key, sign
from lab.update_install import InstallError

SEED = bytes(range(32))
PUB = public_key(SEED)
TOP = runtime.APP_BUNDLE


# ---------- 版本 ----------
def test_semver_ordering():
    assert updater.is_newer("0.2.0", "0.1.9") and updater.is_newer("1.0.0", "0.99.99") and updater.is_newer("v0.10.0", "0.9.0")
    assert not updater.is_newer("0.1.0", "0.1.0") and not updater.is_newer("0.1.0", "0.2.0")
    assert updater.is_newer("1.0.0", "1.0.0-rc.1") and not updater.is_newer("1.0.0-rc.1", "1.0.0")
    assert updater.is_prerelease("1.0.0-rc.1") and not updater.is_prerelease("v1.0.0")
    for bad in ("", "abc", "1.2", "1.2.3.4", None, 5):
        assert updater.parse_version(bad) is None and not updater.is_newer(bad, "0.1.0")


def _api_release(**kw):
    return {"tag_name": "v9.9.9", "draft": False, "prerelease": False, "body": "說明", "published_at": "2026-11-03T00:00:00Z",
            "html_url": f"https://github.com/{updater.RELEASE_REPO}/releases/tag/v9.9.9",
            "assets": [{"name": "update-manifest.json", "browser_download_url": f"https://github.com/{updater.RELEASE_REPO}/releases/download/v9.9.9/update-manifest.json"},
                       {"name": "evil", "browser_download_url": "http://evil.example/x"}], **kw}


def test_release_filter_skips_prerelease_and_draft():
    assert updater._release_from_api(_api_release())["version"] == "9.9.9"
    assert updater._release_from_api(_api_release(prerelease=True)) is None
    assert updater._release_from_api(_api_release(draft=True)) is None
    assert updater._release_from_api(_api_release(tag_name="v9.9.9-beta.1")) is None
    assert updater._release_from_api(_api_release(tag_name="latest")) is None


def test_release_keeps_only_allowed_urls_and_limits_notes():
    r = updater._release_from_api(_api_release(body="x" * (updater.MAX_NOTES_CHARS + 50), html_url="https://evil.example/"))
    assert set(r["assets"]) == {"update-manifest.json"}
    assert len(r["notes"]) == updater.MAX_NOTES_CHARS
    assert r["url"].startswith(f"https://github.com/{updater.RELEASE_REPO}/")


@pytest.mark.parametrize("url,ok", [
    ("https://github.com/a/b", True), ("https://api.github.com/x", True), ("https://objects.githubusercontent.com/x", True),
    ("http://github.com/a", False), ("https://github.com.evil.example/a", False), ("https://evilgithubusercontent.com/x", False),
    ("http://127.0.0.1:9/x", False), ("file:///etc/passwd", False), ("ftp://github.com/x", False)])
def test_url_allowlist(url, ok):
    assert updater._url_allowed(url) is ok


# ---------- 檢查時機與關閉 ----------
@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(updater.config, "OFFLINE", False)
    monkeypatch.delenv("INVEST_LAB_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(updater, "_startup_result", None)
    monkeypatch.setattr(updater, "_job", {"phase": "idle"})
    return tmp_path


def test_disabled_never_connects(state, monkeypatch):
    calls = []
    monkeypatch.setattr(updater, "_request", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("INVEST_LAB_UPDATE_CHECK", "0")
    assert updater.disabled_reason() == "env" and updater.maybe_check_async() is False
    monkeypatch.delenv("INVEST_LAB_UPDATE_CHECK")
    updater.set_auto_check(False)
    assert updater.disabled_reason() == "user" and updater.maybe_check_async() is False
    updater.set_auto_check(True)
    monkeypatch.setattr(updater.config, "OFFLINE", True)
    assert updater.disabled_reason() == "offline" and updater.maybe_check_async() is False
    assert calls == []


def test_offline_request_refuses(state, monkeypatch):
    monkeypatch.setattr(updater.config, "OFFLINE", True)
    with pytest.raises(urllib.error.URLError):
        updater._request("https://api.github.com/x")


def test_due_at_most_once_a_day():
    now = 1_000_000.0
    assert updater._due({}, now) and updater._due({"last_check": now - updater.CHECK_INTERVAL}, now)
    assert not updater._due({"last_check": now - 3600}, now)
    assert updater._due({"last_check": now + 3600}, now)     # 時鐘被調過也不要卡住


class _Resp:
    def __init__(self, body=b"", headers=None):
        self.body, self.headers = body, headers or {}

    def read(self, n=-1):
        d, self.body = (self.body, b"") if n < 0 else (self.body[:n], self.body[n:])
        return d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_check_uses_etag_and_handles_304(state, monkeypatch):
    seen = []

    def first(url, headers=None, timeout=0):
        seen.append(headers)
        return _Resp(json.dumps(_api_release()).encode(), {"ETag": 'W/"abc"'})

    monkeypatch.setattr(updater, "_request", first)
    assert updater.check_now()["ok"]
    assert updater._load_state()["latest"]["version"] == "9.9.9" and "If-None-Match" not in seen[0]

    def second(url, headers=None, timeout=0):
        seen.append(headers)
        raise urllib.error.HTTPError(url, 304, "not modified", {}, None)

    monkeypatch.setattr(updater, "_request", second)
    assert updater.check_now().get("not_modified")
    assert seen[1]["If-None-Match"] == 'W/"abc"'
    assert updater._load_state()["latest"]["version"] == "9.9.9"


def test_check_failure_is_quiet(state, monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(updater, "_request", boom)
    r = updater.check_now()
    assert r == {"ok": False, "error": "network"}
    assert updater._load_state()["last_check"]    # 失敗也算查過,不會一直重試


def test_status_exposes_no_user_paths_or_notes_html(state, monkeypatch):
    monkeypatch.setenv("INVEST_LAB_UPDATE_CHECK", "0")
    st = updater.status()
    assert st["update_available"] is False and st["current"] == updater.__version__ and st["token"]
    assert st["blocker"] is None and st["latest"] is None


# ---------- 權杖 ----------
def test_token_is_single_use():
    tok = updater._token
    assert not updater.consume_token("wrong") and not updater.consume_token(None) and updater._token == tok
    assert updater.consume_token(tok)
    assert not updater.consume_token(tok)


# ---------- manifest ----------
def _manifest(version="9.9.9", arch="arm64", **over):
    m = {"schema": 1, "product": "h0-lab", "version": version, "data_format": 1,
         "assets": {arch: {"name": "H0.zip", "size": 10, "sha256": "a" * 64}}}
    m.update(over)
    return json.dumps(m).encode()


def _sig(raw, seed=SEED):
    return base64.b64encode(sign(seed, raw))


def test_manifest_good():
    raw = _manifest()
    v = updater.verify_manifest(raw, _sig(raw), PUB, tag_version="9.9.9", arch="arm64")
    assert v["asset"]["name"] == "H0.zip"


@pytest.mark.parametrize("mutate,code", [
    (lambda raw, sig: (raw + b" ", sig), "signature_invalid"),
    (lambda raw, sig: (raw, base64.b64encode(b"\0" * 64)), "signature_invalid"),
    (lambda raw, sig: (raw, b"!!!not base64"), "signature_invalid"),
    (lambda raw, sig: (raw, _sig(raw, bytes(32))), "signature_invalid"),
])
def test_manifest_bad_signature(mutate, code):
    raw, sig = mutate(_manifest(), _sig(_manifest()))
    with pytest.raises(InstallError) as e:
        updater.verify_manifest(raw, sig, PUB, tag_version="9.9.9", arch="arm64")
    assert e.value.code == code


@pytest.mark.parametrize("raw,code", [
    (_manifest(version="9.9.8"), "manifest_invalid"),     # 版本和 tag 不符
    (_manifest(version="0.0.1"), "manifest_invalid"),     # 舊版(重放)
    (_manifest(product="other"), "manifest_invalid"),
    (_manifest(schema=2), "manifest_invalid"),
    (_manifest(arch="x86_64"), "no_asset_for_arch"),
    (b"not json", "manifest_invalid"),
    (json.dumps({"schema": 1, "product": "h0-lab", "version": "9.9.9", "assets": {"arm64": {"name": "../x", "size": 1, "sha256": "a" * 64}}}).encode(), "manifest_invalid"),
    (json.dumps({"schema": 1, "product": "h0-lab", "version": "9.9.9", "assets": {"arm64": {"name": "x", "size": 2**40, "sha256": "a" * 64}}}).encode(), "manifest_invalid"),
    (json.dumps({"schema": 1, "product": "h0-lab", "version": "9.9.9", "assets": {"arm64": {"name": "x", "size": 1, "sha256": "zz"}}}).encode(), "manifest_invalid"),
])
def test_manifest_content_checks_even_when_signed(raw, code):
    tag = "0.0.1" if b'"version": "0.0.1"' in raw else "9.9.9"
    with pytest.raises(InstallError) as e:
        updater.verify_manifest(raw, _sig(raw), PUB, tag_version=tag, arch="arm64")
    assert e.value.code == code


# ---------- 下載(本機假伺服器) ----------
class _Handler(http.server.BaseHTTPRequestHandler):
    body = b""
    declared = None
    truncate_at = None

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(self.declared if self.declared is not None else len(self.body)))
        self.end_headers()
        self.wfile.write(self.body[:self.truncate_at] if self.truncate_at is not None else self.body)
        self.wfile.flush()
        self.connection.close()

    def log_message(self, *a):
        pass


@pytest.fixture
def server(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(updater, "_url_allowed", lambda u: True)
    monkeypatch.setattr(updater.config, "OFFLINE", False)
    # 離線模式的 proxy 環境變數不能影響本機連線
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    yield f"http://127.0.0.1:{srv.server_address[1]}/f"
    srv.shutdown()
    _Handler.body, _Handler.declared, _Handler.truncate_at = b"", None, None


def test_download_ok(server, tmp_path):
    _Handler.body = b"hello" * 100
    dest = tmp_path / "f.zip"
    seen = []
    updater.download_verified(server, dest, len(_Handler.body), hashlib.sha256(_Handler.body).hexdigest(), seen.append)
    assert dest.read_bytes() == _Handler.body and seen[-1] == len(_Handler.body)


def test_download_hash_mismatch_removes_file(server, tmp_path):
    _Handler.body = b"hello"
    dest = tmp_path / "f.zip"
    with pytest.raises(InstallError) as e:
        updater.download_verified(server, dest, 5, "0" * 64)
    assert e.value.code == "hash_mismatch" and not dest.exists()


def test_download_too_large_stops(server, tmp_path):
    _Handler.body = b"x" * 1024 * 1024
    dest = tmp_path / "f.zip"
    with pytest.raises(InstallError) as e:
        updater.download_verified(server, dest, 100, "0" * 64)
    assert e.value.code == "download_too_large" and not dest.exists()


def test_download_interrupted(server, tmp_path):
    _Handler.body, _Handler.declared, _Handler.truncate_at = b"x" * 1000, 1000, 100
    dest = tmp_path / "f.zip"
    with pytest.raises(InstallError) as e:
        updater.download_verified(server, dest, 1000, hashlib.sha256(b"x" * 1000).hexdigest())
    assert e.value.code in ("download_failed", "download_incomplete") and not dest.exists()


def test_redirect_to_other_host_refused(monkeypatch):
    h = updater._Redirect()
    with pytest.raises(urllib.error.URLError):
        h.redirect_request(None, None, 302, "", {}, "https://evil.example/x")


# ---------- 安全解壓 ----------
def _zip(path, entries):
    """entries:(名稱, 內容, unix mode);mode 帶 S_IFLNK 就是 symlink。"""
    with zipfile.ZipFile(path, "w") as z:
        for name, data, mode in entries:
            zi = zipfile.ZipInfo(name)
            zi.external_attr = mode << 16
            z.writestr(zi, data)
    return path


REG = stat.S_IFREG | 0o644
LNK = stat.S_IFLNK | 0o777


def _good_entries():
    return [(f"{TOP}/", b"", stat.S_IFDIR | 0o755), (f"{TOP}/Contents/MacOS/H0 Lab", b"#!", stat.S_IFREG | 0o755),
            (f"{TOP}/Contents/Resources/a.txt", b"a", REG), (f"{TOP}/Contents/Frameworks/link", b"../Resources/a.txt", LNK)]


def test_extract_good(tmp_path):
    z = _zip(tmp_path / "a.zip", _good_entries())
    dest = tmp_path / "out"
    dest.mkdir()
    app = update_install.safe_extract(z, dest)
    assert (app / "Contents/Frameworks/link").is_symlink() and (app / "Contents/Frameworks/link").read_text() == "a"
    assert os.access(app / "Contents/MacOS/H0 Lab", os.X_OK)


@pytest.mark.parametrize("entries", [
    [(f"{TOP}/../../evil", b"x", REG)],                         # zip slip
    [("/etc/evil", b"x", REG)],                                 # 絕對路徑
    [("other/evil", b"x", REG)],                                # App 以外
    [(f"{TOP}\\..\\evil", b"x", REG)],                          # 反斜線
    [(f"{TOP}/a", b"x", REG), (f"{TOP}/A", b"y", REG)],         # 大小寫不同的同名
    [(f"{TOP}/fifo", b"", stat.S_IFIFO | 0o644)],               # 裝置檔
    [(f"{TOP}/l", b"/etc/passwd", LNK)],                        # symlink 絕對路徑
    [(f"{TOP}/l", b"../../etc", LNK)],                          # symlink 逃逸
    [(f"{TOP}/a/l", b"../../../x", LNK)],                       # 多層逃逸
    [(f"{TOP}/l", b"d/../../..", LNK)],                         # 中間穿插的逃逸
    [(f"{TOP}/a", b"b", LNK), (f"{TOP}/b", b"../..", LNK)],     # 連續 symlink 逃逸
    [(f"{TOP}/a", b"b", LNK), (f"{TOP}/b", b"a", LNK)],         # 循環
    [(f"{TOP}/l", b"sub", LNK), (f"{TOP}/l/evil", b"x", REG)],  # 經過 symlink 寫入
    [(f"{TOP}/L", b"sub", LNK), (f"{TOP}/l/evil", b"x", REG)],  # 大小寫繞過 symlink 檢查
])
def test_extract_rejects(tmp_path, entries):
    z = _zip(tmp_path / "a.zip", entries)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(InstallError) as e:
        update_install.safe_extract(z, dest)
    assert e.value.code == "bad_archive"
    assert not (tmp_path / "evil").exists()


def test_extract_strips_setuid_and_world_write(tmp_path):
    z = _zip(tmp_path / "a.zip", [(f"{TOP}/x", b"x", stat.S_IFREG | stat.S_ISUID | 0o777)])
    dest = tmp_path / "out"
    dest.mkdir()
    app = update_install.safe_extract(z, dest)
    m = (app / "x").stat().st_mode
    assert not m & stat.S_ISUID and not m & 0o022


def test_extract_limits(tmp_path, monkeypatch):
    z = _zip(tmp_path / "a.zip", [(f"{TOP}/x", b"x" * 2000, REG)])
    dest = tmp_path / "out"
    dest.mkdir()
    monkeypatch.setattr(update_install, "MAX_UNPACKED", 1000)
    with pytest.raises(InstallError):
        update_install.safe_extract(z, dest)
    monkeypatch.setattr(update_install, "MAX_UNPACKED", 10**9)
    monkeypatch.setattr(update_install, "MAX_ENTRIES", 0)
    dest2 = tmp_path / "out2"
    dest2.mkdir()
    with pytest.raises(InstallError):
        update_install.safe_extract(z, dest2)


def test_extract_corrupt_zip(tmp_path):
    z = tmp_path / "a.zip"
    z.write_bytes(b"not a zip")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(InstallError):
        update_install.safe_extract(z, dest)


@pytest.mark.parametrize("path,code", [("/Volumes/H0 Lab/H0 Lab.app", "move_to_applications"),
                                       ("/private/var/folders/x/AppTranslocation/y/d/H0 Lab.app", "move_to_applications")])
def test_location_blocker(path, code):
    assert update_install.location_blocker(Path(path)) == code


def test_location_blocker_writable(tmp_path):
    app = tmp_path / TOP
    app.mkdir()
    assert update_install.location_blocker(app) is None
    os.chmod(tmp_path, 0o555)
    try:
        assert update_install.location_blocker(app) == "app_not_writable"
    finally:
        os.chmod(tmp_path, 0o755)


# ---------- helper:替換與回滾 ----------
def _fake_app(path: Path, version: str, selftest_rc: int = 0) -> Path:
    exe = path / "Contents" / "MacOS" / runtime.APP_NAME
    exe.parent.mkdir(parents=True)
    exe.write_text(f"#!/bin/sh\nif [ \"$1\" = --self-test ]; then exit {selftest_rc}; fi\nexit 0\n# {version}\n", encoding="utf-8")
    exe.chmod(0o755)
    return path


def _plan(tmp_path, **kw):
    target = _fake_app(tmp_path / "Apps" / TOP, "old")
    staged = _fake_app(tmp_path / "Apps" / ".stage" / "staged" / TOP, "new", kw.pop("selftest_rc", 0))
    return {"pid": 2**22 + 12345, "target": str(target), "staged": str(staged), "result": str(tmp_path / "result.json"), "reopen": "none",
            "from_version": "1", "to_version": "2", "wait_seconds": 1, **kw}


def _ver(p):
    return (Path(p) / "Contents/MacOS" / runtime.APP_NAME).read_text().rstrip().rsplit(" ", 1)[1]


def test_swap_paths(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "1").touch()
    (b / "2").touch()
    update_helper.swap_paths(a, b)
    assert (a / "2").exists() and (b / "1").exists()


def test_wait_for_exit_counts_unreaped_child_as_exited():
    """父行程還沒回收的殭屍行程 kill(pid, 0) 仍會成功;2026-11 打包實測踩到,helper 因此白等 60 秒後放棄更新。"""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        time.sleep(1)
        assert update_helper.wait_for_exit(child.pid, seconds=10)
    finally:
        child.wait()


def test_wait_for_exit_times_out_while_running():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert not update_helper.wait_for_exit(child.pid, seconds=0.5)
    finally:
        child.kill()
        child.wait()


def test_helper_success(tmp_path):
    p = _plan(tmp_path)
    assert update_helper.run(p) == 0
    assert _ver(p["target"]) == "new" and _ver(p["staged"]) == "old"
    r = json.loads(Path(p["result"]).read_text())
    assert r["status"] == "ok" and r["to_version"] == "2"


def test_helper_new_version_fails_rolls_back(tmp_path):
    p = _plan(tmp_path, selftest_rc=1)
    assert update_helper.run(p) == 1
    assert _ver(p["target"]) == "old"
    assert json.loads(Path(p["result"]).read_text())["code"] == "new_version_failed_rolled_back"


def test_helper_swap_failure_keeps_old(tmp_path):
    p = _plan(tmp_path, test_fail="swap")
    assert update_helper.run(p) == 1
    assert _ver(p["target"]) == "old" and _ver(p["staged"]) == "new"
    assert json.loads(Path(p["result"]).read_text())["code"] == "swap_failed"


def test_helper_missing_staged_keeps_old(tmp_path):
    p = _plan(tmp_path)
    Path(p["staged"]).rename(tmp_path / "gone")
    assert update_helper.run(p) == 1
    assert _ver(p["target"]) == "old"


def test_helper_aborts_if_app_keeps_running(tmp_path):
    p = _plan(tmp_path, pid=os.getpid())
    assert update_helper.run(p) == 1
    assert _ver(p["target"]) == "old"
    assert json.loads(Path(p["result"]).read_text())["code"] == "app_still_running"


def test_cleanup_keeps_previous_and_refuses_foreign_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "app_support_dir", lambda: tmp_path / "support")
    ud = update_install.update_dir()
    ud.mkdir(parents=True)
    apps = tmp_path / "Apps"
    app = _fake_app(apps / TOP, "new")
    stage = apps / (update_install.STAGE_PREFIX + "x")
    _fake_app(stage / "staged" / TOP, "old")
    (ud / "last_result.json").write_text(json.dumps({"status": "ok"}))
    (ud / "pending.json").write_text(json.dumps({"stage_dir": str(stage), "helper_pid": 0}))
    update_install.cleanup_after_update(app)
    assert not stage.exists() and _ver(ud / "previous" / TOP) == "old" and not (ud / "pending.json").exists()
    # pending.json 指到別的地方:不能刪
    victim = tmp_path / "victim"
    victim.mkdir()
    (ud / "pending.json").write_text(json.dumps({"stage_dir": str(victim), "helper_pid": 0}))
    update_install.cleanup_after_update(app)
    assert victim.exists() and not (ud / "pending.json").exists()


def test_backup_copies_user_data_only(tmp_path):
    s = tmp_path / "support"
    (s / "private").mkdir(parents=True)
    (s / "private" / "watch.json").write_text("{}")
    (s / ".env").write_text("A=1")
    (s / "data" / "cache").mkdir(parents=True)
    (s / "data" / "cache" / "huge.pkl").write_text("x")
    (s / "data" / "snapshots").mkdir()
    (s / "data" / "snapshots" / "a.json").write_text("{}")
    b = update_install.backup_user_data(s, "9.9.9")
    assert (b / "private/watch.json").exists() and (b / ".env").exists() and (b / "data/snapshots/a.json").exists()
    assert not (b / "data/cache").exists()


def test_verify_staged_rejects_wrong_version(tmp_path):
    import plistlib
    app = _fake_app(tmp_path / TOP, "new")
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "1.0.0"}))
    with pytest.raises(InstallError) as e:
        update_install.verify_staged(app, "2.0.0")
    assert e.value.code == "verify_failed"


# ---------- 原始碼安裝 ----------
def test_source_install_only_shows_commands(state, monkeypatch):
    monkeypatch.setattr(updater, "install_kind", lambda: "git")
    monkeypatch.setattr(updater, "maybe_check_async", lambda: False)
    updater._save_state({"latest": {"version": "9.9.9", "notes": "n", "url": "u", "published_at": "p"}, "last_check": time.time()})
    st = updater.status()
    assert st["update_available"] and not st["can_apply"] and st["blocker"] == "source_install"
    assert st["commands"] == ["git pull --ff-only", "./install.sh"]
    assert updater.start_apply("9.9.9")["error"] == "source_install"
    assert updater.start_apply("8.8.8")["error"] == "version_mismatch"
    assert updater._blocker("manual") == "manual_install"


def test_launch_helper_records_stage_root_for_cleanup(tmp_path, monkeypatch):
    """真實的暫存結構:<App 旁>/.H0 Lab Update-x/staged/H0 Lab.app;整理時要找得到,舊版留到 previous。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    apps = tmp_path / "Applications"
    apps.mkdir()
    app = apps / "H0 Lab.app"
    app.mkdir()
    stage = update_install.make_stage_dir(app)
    staged = stage / "staged" / "H0 Lab.app"
    staged.mkdir(parents=True)

    class P:
        pid = 0
    monkeypatch.setattr(update_install.subprocess, "Popen", lambda *a, **k: P)
    update_install.launch_helper(staged, app, from_version="0.1.0", to_version="0.2.0")
    ud = update_install.update_dir()
    assert json.loads((ud / "pending.json").read_text())["stage_dir"] == str(stage)
    assert (stage / "plan.json").is_file()
    (ud / "last_result.json").write_text(json.dumps({"status": "ok", "to_version": "0.2.0"}))
    update_install.cleanup_after_update(app)
    assert (ud / "previous" / "H0 Lab.app").is_dir() and not stage.exists() and not (ud / "pending.json").exists()


def test_cleanup_ignores_stale_ok_result(tmp_path, monkeypatch):
    """舊的成功紀錄不代表這次換過:暫存裡的 App 不能被當成舊版搬進 previous。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    app = tmp_path / "H0 Lab.app"
    app.mkdir()
    stage = update_install.make_stage_dir(app)
    (stage / "staged" / "H0 Lab.app").mkdir(parents=True)
    ud = update_install.update_dir()
    ud.mkdir(parents=True)
    (ud / "pending.json").write_text(json.dumps({"stage_dir": str(stage), "helper_pid": 0, "to_version": "0.3.0"}))
    (ud / "last_result.json").write_text(json.dumps({"status": "ok", "to_version": "0.2.0"}))
    update_install.cleanup_after_update(app)
    assert not (ud / "previous").exists() and not stage.exists()


def test_cleanup_keeps_old_app_after_rollback_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    app = tmp_path / "H0 Lab.app"
    app.mkdir()
    stage = update_install.make_stage_dir(app)
    (stage / "staged" / "H0 Lab.app").mkdir(parents=True)
    ud = update_install.update_dir()
    ud.mkdir(parents=True)
    (ud / "pending.json").write_text(json.dumps({"stage_dir": str(stage), "helper_pid": 0, "to_version": "0.3.0"}))
    (ud / "last_result.json").write_text(json.dumps({"status": "failed", "code": "rollback_failed", "to_version": "0.3.0"}))
    update_install.cleanup_after_update(app)
    assert (ud / "previous" / "H0 Lab.app").is_dir() and not stage.exists()


def test_cleanup_rejects_absurd_helper_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    app = tmp_path / "H0 Lab.app"
    app.mkdir()
    stage = update_install.make_stage_dir(app)
    ud = update_install.update_dir()
    ud.mkdir(parents=True)
    (ud / "pending.json").write_text(json.dumps({"stage_dir": str(stage), "helper_pid": 2**70}))
    update_install.cleanup_after_update(app)
    assert stage.exists() and not (ud / "pending.json").exists()


def _zip_with(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        for name, mode, data in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            z.writestr(info, data)


def test_extract_symlink_check_covers_unicode_normalization(tmp_path):
    """APFS 上 NFC / NFD 是同一個檔名:用另一種寫法不能穿過 symlink 寫到 App 外面。"""
    nfc, nfd = "caf\u00e9", "cafe\u0301"
    z = tmp_path / "u.zip"
    _zip_with(z, [(f"H0 Lab.app/{nfc}", stat.S_IFLNK | 0o755, "Contents"), ("H0 Lab.app/Contents/x", stat.S_IFREG | 0o644, "x"),
                  (f"H0 Lab.app/{nfd}/y", stat.S_IFREG | 0o644, "y")])
    dest = tmp_path / "d"
    dest.mkdir()
    with pytest.raises(InstallError) as e:
        update_install.safe_extract(z, dest)
    assert e.value.code == "bad_archive"


def test_auto_check_survives_concurrent_check(state, monkeypatch):
    """檢查進行中使用者關掉自動檢查:檢查結束不能把設定蓋回去。"""
    class R:
        headers = {"ETag": "e"}
        def __enter__(self):
            updater.set_auto_check(False)
            return self
        def __exit__(self, *a):
            return False
        def read(self, n):
            return b"{}"
    monkeypatch.setattr(updater, "_request", lambda *a, **k: R())
    assert updater.check_now()["ok"]
    assert updater.auto_check_enabled() is False


def test_check_survives_http_client_errors(state, monkeypatch):
    import http.client
    def boom(*a, **k):
        raise http.client.IncompleteRead(b"")
    monkeypatch.setattr(updater, "_request", boom)
    assert updater.check_now() == {"ok": False, "error": "network"}
    assert updater._load_state()["last_check"]


@pytest.mark.parametrize("url", ["https://evil.com\\@github.com/", "https://user:pw@github.com/x", "https://github.com:8443/x", "https://github.com:bad/x"])
def test_url_allowlist_rejects_tricks(url):
    assert not updater._url_allowed(url)


def test_non_ascii_token_is_rejected_not_crash():
    assert updater.consume_token("權杖\ud800") is False


@pytest.mark.parametrize("tail", ["../../../attacker/repo", "%2e%2e/%2e%2e/%2e%2e/attacker/repo", ".%2E/.%2e/attacker", "tag/v1/../../../x", "tag/v1?x=1"])
def test_release_url_rejects_anything_but_tag_page(tail):
    r = updater._release_from_api({"tag_name": "v9.0.0", "html_url": f"https://github.com/{updater.RELEASE_REPO}/releases/{tail}"})
    assert r["url"] == f"https://github.com/{updater.RELEASE_REPO}/releases"


def test_release_url_keeps_real_tag_page():
    url = f"https://github.com/{updater.RELEASE_REPO}/releases/tag/v9.0.0"
    assert updater._release_from_api({"tag_name": "v9.0.0", "html_url": url})["url"] == url


def test_upsert_env_handles_duplicates_and_unicode_separators(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\nNOTE=x\u2028y\nA=2\n", encoding="utf-8")
    user_settings.upsert_env(p, {"A": "3"})
    assert p.read_text(encoding="utf-8") == "A=3\nNOTE=x\u2028y\n"


# ---------- .env ----------
@pytest.mark.parametrize("v", ["Jane Doe jane@my-domain.org", "J. Doe j.doe+sec@sub.domain.tw"])
def test_sec_ua_ok(v):
    assert user_settings.clean_sec_user_agent(v) == v


@pytest.mark.parametrize("v,code", [("jane@my-domain.org", "sec_ua_format"), ("Jane Doe", "sec_ua_format"), ("", "sec_ua_format"), (None, "sec_ua_format"),
                                    ("王小明 a@b.tw", "sec_ua_format"), ("Jane contact@example.com", "sec_ua_placeholder")])
def test_sec_ua_rejected(v, code):
    with pytest.raises(user_settings.SettingError) as e:
        user_settings.clean_sec_user_agent(v)
    assert str(e.value) == code


def test_sec_ua_newline_cannot_inject(tmp_path):
    cleaned = user_settings.clean_sec_user_agent("Jane Doe\njane@my-domain.org\nFINMIND_TOKEN=evil".replace("FINMIND_TOKEN=evil", "x"))
    assert "\n" not in cleaned
    with pytest.raises(user_settings.SettingError):
        user_settings.clean_finmind_token("abcdefgh\nSEC_USER_AGENT=evil")


def test_upsert_env_keeps_other_lines(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# 註解\nSEC_USER_AGENT=\nOTHER=1\n", encoding="utf-8")
    user_settings.upsert_env(p, {"SEC_USER_AGENT": "A b@c.tw", "FINMIND_TOKEN": "tok"})
    assert p.read_text(encoding="utf-8") == "# 註解\nSEC_USER_AGENT=A b@c.tw\nOTHER=1\nFINMIND_TOKEN=tok\n"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_save_updates_running_process_and_hides_values(tmp_path, monkeypatch):
    from lab.sources import sec
    env = tmp_path / ".env"
    monkeypatch.setenv("INVEST_LAB_ENV_FILE", str(env))
    monkeypatch.setattr(user_settings.config, "SEC_UA_IS_DEFAULT", True)
    monkeypatch.setitem(sec.HEADERS, "User-Agent", "old")
    monkeypatch.setattr(runtime, "sec_blocked", True)
    monkeypatch.setenv("SEC_USER_AGENT", "x")
    out = user_settings.save({"sec_user_agent": "Jane Doe jane@my-domain.org"})
    assert out["has_sec_user_agent"] and out["sec_blocked"] is False
    assert sec.HEADERS["User-Agent"] == "Jane Doe jane@my-domain.org"
    assert "jane@" not in json.dumps(out)
    assert "jane@my-domain.org" in env.read_text(encoding="utf-8")


@pytest.mark.parametrize("frozen", [True, False])   # App 和原始碼安裝都一樣:沒有共用的預設聯絡方式
def test_sec_guard_blocks_without_a_contact(monkeypatch, frozen):
    from lab.sources import sec
    monkeypatch.setattr(sec.config, "SEC_UA_IS_DEFAULT", True)
    monkeypatch.setattr(runtime, "sec_blocked", False)
    monkeypatch.setattr(runtime, "FROZEN", frozen)
    with pytest.raises(sec.SecUserAgentRequired):
        sec._require_user_agent()
    assert runtime.sec_blocked is True
    monkeypatch.setattr(sec.config, "SEC_UA_IS_DEFAULT", False)
    sec._require_user_agent()


# ---------- launcher ----------
@pytest.fixture
def launcher(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import launcher as L
    yield L
    sys.path.pop(0)


def test_launcher_env_stays_in_support_dir(launcher, tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("INVEST_LAB_") or k in ("SEC_USER_AGENT", "FINMIND_TOKEN"):
            monkeypatch.delenv(k)
    support = tmp_path / "H0 Lab"
    launcher.setup_env(support)
    for k in ("INVEST_LAB_DATA_DIR", "INVEST_LAB_OUT_DIR", "INVEST_LAB_PRIVATE_DIR", "INVEST_LAB_BUDGET_FILE", "INVEST_LAB_REGISTRY", "INVEST_LAB_ENV_FILE"):
        assert os.environ[k].startswith(str(support)), k
    env = (support / ".env").read_text(encoding="utf-8")
    assert stat.S_IMODE((support / ".env").stat().st_mode) == 0o600
    assert "@" not in env.replace("jane@your-domain.tld", "")     # 沒有任何真實信箱
    assert "SEC_USER_AGENT" not in os.environ                      # 不預設
    # 重複執行不覆寫使用者填的值
    (support / ".env").write_text("SEC_USER_AGENT=Jane j@a.tw\nPATH=/evil\n", encoding="utf-8")
    before = os.environ.get("PATH")
    launcher.setup_env(support)
    assert os.environ["SEC_USER_AGENT"] == "Jane j@a.tw" and os.environ.get("PATH") == before   # 白名單以外的鍵不讀
    assert "Jane" in (support / ".env").read_text(encoding="utf-8")
    monkeypatch.delenv("SEC_USER_AGENT")


def test_launcher_port_fallback(launcher):
    a, port = launcher.bind_port(0 or 18911)
    try:
        b, port2 = launcher.bind_port(port)
        try:
            assert port2 != port and a.getsockname()[0] == "127.0.0.1" and b.getsockname()[0] == "127.0.0.1"
        finally:
            b.close()
    finally:
        a.close()


def test_launcher_instance_lock(launcher, tmp_path):
    fd = launcher.acquire_instance_lock(tmp_path)
    assert fd is not None
    code = "import fcntl,os,sys\nf=os.open(sys.argv[1],os.O_RDWR)\ntry:\n fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept OSError:\n sys.exit(7)\n"
    assert subprocess.run([sys.executable, "-c", code, str(tmp_path / "instance.lock")]).returncode == 7
    os.close(fd)


def test_data_format_sync(launcher, tmp_path):
    launcher.sync_data_format(tmp_path, 2)
    assert json.loads((tmp_path / "data_format.json").read_text())["version"] == 2
    (tmp_path / "data_format.json").write_text('{"version": 5}')
    launcher.sync_data_format(tmp_path, 2)     # 資料比較新:不改寫
    assert json.loads((tmp_path / "data_format.json").read_text())["version"] == 5


# ---------- 發版工具 ----------
def test_release_manifest_fields(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    try:
        import release_tools as R
    finally:
        sys.path.pop(0)
    f = tmp_path / "a.zip"
    f.write_bytes(b"abc")
    m = R.build_manifest("9.9.9", {"arm64": f})
    assert m["assets"]["arm64"] == {"name": "a.zip", "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}
    raw = json.dumps(m).encode()
    assert updater.verify_manifest(raw, _sig(raw), PUB, tag_version="9.9.9", arch="arm64")["asset"]["size"] == 3


def test_no_private_key_material_in_repo():
    root = Path(__file__).resolve().parent.parent
    assert (root / "packaging" / "update_public_key.txt").read_text().strip() == "" or len(base64.b64decode((root / "packaging" / "update_public_key.txt").read_text().strip())) == 32
    assert "H0_UPDATE_SIGNING_KEY" not in (root / ".env.example").read_text(encoding="utf-8")


def test_release_tools_parse_minos_takes_the_highest_and_ignores_sdk():
    sys_path_tools = _release_tools()
    out = "x (architecture arm64):\nLoad command 9\n      cmd LC_BUILD_VERSION\n platform MACOS\n    minos 11.0\n      sdk 15.2\n   ntools 1\n     tool LD\n  version 1115.7.3\nb:\n    minos 14.0\n"
    assert sys_path_tools.parse_minos(out) == (14, 0)
    assert sys_path_tools.parse_minos("x:\n cmd LC_VERSION_MIN_MACOSX\n version 10.9\n") is None


def test_release_tools_outside_refs_flags_only_libraries_outside_the_app():
    tools = _release_tools()
    out = ("/a/b.so:\n\t@rpath/own.dylib (compatibility version 1.0.0, current version 1.0.0)\n"
           "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
           "\t/System/Library/Frameworks/AppKit.framework/AppKit (compatibility version 1.0.0)\n"
           "\t/opt/homebrew/lib/libssl.3.dylib (compatibility version 3.0.0)\n"
           "\t/usr/local/lib/own.dylib (compatibility version 1.0.0)\n")
    assert tools.outside_refs(out) == ["/opt/homebrew/lib/libssl.3.dylib", "/usr/local/lib/own.dylib"]
    assert tools.outside_refs(out, {"/usr/local/lib/own.dylib"}) == ["/opt/homebrew/lib/libssl.3.dylib"]
    # 多種架構的檔案每個架構各列一次自己的名字
    fat = "/a/b.so (architecture x86_64):\n/usr/local/lib/own.dylib\n/a/b.so (architecture arm64):\n/usr/local/lib/own-arm.dylib\n"
    assert tools.own_ids(fat) == {"/usr/local/lib/own.dylib", "/usr/local/lib/own-arm.dylib"}
    assert tools.own_ids("/a/b:\n") == set()
    # 白名單:沒列到的路徑一律算外部
    assert tools.outside_refs("x:\n\t/Applications/Xcode.app/lib/a.dylib (compatibility version 1.0.0)\n") == ["/Applications/Xcode.app/lib/a.dylib"]
    lc = "Load command 12\n          cmd LC_RPATH\n      cmdsize 32\n         path @loader_path/. (offset 12)\nLoad command 13\n          cmd LC_RPATH\n      cmdsize 40\n         path /opt/homebrew/lib (offset 12)\n"
    assert tools.outside_rpaths(lc) == ["/opt/homebrew/lib"]


def _portable_app(tmp_path, tools, *, minimum="14.0"):
    import plistlib
    contents = tmp_path / "H0 Lab.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"LSMinimumSystemVersion": minimum}))
    (contents / "MacOS" / "H0 Lab").write_bytes(next(iter(tools.MACHO_MAGIC)) + b"\0" * 16)
    (contents / "MacOS" / "note.txt").write_text("not a binary", encoding="utf-8")
    return contents.parent


def _fake_macho_tools(tools, monkeypatch, *, minos="14.0", refs="", rpaths=""):
    calls = []

    def fake(*cmd):
        calls.append(cmd[:2])
        if cmd[0].endswith("vtool"):
            return f"x:\n    minos {minos}\n"
        return {"-D": "x:\n", "-L": f"x:\n{refs}", "-l": rpaths}[cmd[1]]
    monkeypatch.setattr(tools, "_tool", fake)
    return calls


def test_check_portable_passes_a_clean_app_and_checks_only_binaries(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    tools = _release_tools()
    calls = _fake_macho_tools(tools, monkeypatch, refs="\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n")
    assert tools.cmd_check_portable(SimpleNamespace(app=str(_portable_app(tmp_path, tools)))) == 0
    assert len(calls) == 4 and "1 個執行檔" in capsys.readouterr().out


@pytest.mark.parametrize("fake,expect", [
    ({"minos": "15.0"}, "要 macOS 15.0"),
    ({"refs": "\t/opt/homebrew/lib/libssl.3.dylib (compatibility version 3.0.0)\n"}, "/opt/homebrew/lib/libssl.3.dylib"),
    ({"rpaths": "Load command 1\n          cmd LC_RPATH\n      cmdsize 40\n         path /opt/homebrew/lib (offset 12)\n"}, "rpath /opt/homebrew/lib"),
])
def test_check_portable_refuses_an_app_that_needs_something_outside(tmp_path, monkeypatch, fake, expect):
    from types import SimpleNamespace
    tools = _release_tools()
    _fake_macho_tools(tools, monkeypatch, **fake)
    with pytest.raises(SystemExit) as e:
        tools.cmd_check_portable(SimpleNamespace(app=str(_portable_app(tmp_path, tools))))
    assert expect in str(e.value)


def test_check_portable_stops_when_a_tool_fails_or_the_app_is_empty(tmp_path, monkeypatch):
    """工具失敗時輸出是空的,不能當成「沒有問題」。"""
    from types import SimpleNamespace
    tools = _release_tools()
    app = _portable_app(tmp_path, tools)
    failed = SimpleNamespace(returncode=1, stdout="", stderr="")
    monkeypatch.setattr(tools.subprocess, "run", lambda *a, **k: failed)
    with pytest.raises(SystemExit) as e:
        tools.cmd_check_portable(SimpleNamespace(app=str(app)))
    assert "失敗" in str(e.value)
    (app / "Contents" / "MacOS" / "H0 Lab").write_text("no binaries now", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        tools.cmd_check_portable(SimpleNamespace(app=str(app)))
    assert "找不到任何執行檔" in str(e.value)


def _release_tools():
    import importlib.util
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("release_tools", root / "packaging" / "release_tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
