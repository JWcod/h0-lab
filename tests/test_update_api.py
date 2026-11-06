"""更新與本機設定 API 的來源檢查:真的啟動伺服器(離線、假資料夾、隨機埠),直接送原始 HTTP(可以偽造 Host)。"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
LIVE_PORT = 8765


def _free_port() -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            p = s.getsockname()[1]
        if p != LIVE_PORT:
            return p


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("upd")
    port = _free_port()
    env = dict(os.environ, INVEST_LAB_OFFLINE="1", INVEST_LAB_RECORD="0", INVEST_LAB_PORT=str(port), INVEST_LAB_DATA_DIR=str(tmp),
               INVEST_LAB_OUT_DIR=str(tmp), INVEST_LAB_PRIVATE_DIR=str(tmp / "p"), INVEST_LAB_REGISTRY=str(tmp / "r.jsonl"),
               INVEST_LAB_BUDGET_FILE=str(tmp / "b.json"), INVEST_LAB_ENV_FILE=str(tmp / ".env"), PYTHONPATH=str(ROOT))
    p = subprocess.Popen([sys.executable, "-m", "app.server"], cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(300):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    yield port, tmp
    p.terminate()
    p.wait(timeout=10)


def _req(port, method, path, headers=None, body=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host or f"127.0.0.1:{port}")
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    raw = json.dumps(body).encode() if body is not None else b""
    c.putheader("Content-Length", str(len(raw)))
    c.endheaders(raw)
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        return r.status, json.loads(data)
    except ValueError:
        return r.status, data


def _good_headers(port):
    return {"Origin": f"http://127.0.0.1:{port}", "X-Requested-With": "invest-lab", "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}


GET_PATHS = ["/api/update/status", "/api/settings/local", "/api/setup/research/status", "/api/app/identity"]


@pytest.mark.parametrize("path", GET_PATHS)
def test_get_rejects_rebinding_host(srv, path):
    port, _ = srv
    assert _req(port, "GET", path)[0] == 200
    for host in ("evil.example", f"evil.example:{port}", "127.0.0.1", f"127.0.0.1:{port + 1}", f"127.0.0.1.evil.example:{port}", "0.0.0.0:" + str(port)):
        assert _req(port, "GET", path, host=host)[0] == 403, host


def test_localhost_host_allowed(srv):
    port, _ = srv
    assert _req(port, "GET", "/api/update/status", host=f"localhost:{port}")[0] == 200


@pytest.mark.parametrize("path", ["/", "/static/index.html", "/api/watchlist", "/api/notes?symbol=2330.TW", "/api/models", "/api/settings", "/api/no-such-route"])
def test_every_path_rejects_foreign_host(srv, path):
    """全域檢查:一般 API 與靜態檔也要擋 DNS rebinding,不只更新 API。"""
    port, _ = srv
    for host in ("evil.example", f"evil.example:{port}", "127.0.0.1", f"127.0.0.1:{port + 1}", f"127.0.0.1.evil.example:{port}", f"0.0.0.0:{port}"):
        assert _req(port, "GET", path, host=host)[0] == 403, host
    assert _req(port, "GET", path)[0] != 403
    assert _req(port, "GET", path, host=f"localhost:{port}")[0] != 403


def test_host_variants_and_stream_are_rejected(srv):
    port, _ = srv
    variants = (f"localhost.:{port}", f"127.0.0.1.:{port}", f"[::ffff:127.0.0.1]:{port}", f"u@127.0.0.1:{port}", f"127.0.0.1:{port}@evil.example",
                f"127.0.0.1:{port}#evil", f"127.0.0.1:0{port}", f"foo.localhost:{port}", "127.1", "2130706433", " ")
    for path in ("/api/watchlist", "/api/report/stream?symbol=2330.TW"):
        for host in variants:
            assert _req(port, "GET", path, host=host)[0] == 403, (path, host)


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_other_sites_cannot_trigger_api_gets(srv, site):
    port, _ = srv
    for path in ("/api/watchlist", "/api/report/stream?symbol=2330.TW&refresh=1"):
        assert _req(port, "GET", path, {"Sec-Fetch-Site": site})[0] == 403, path


def test_external_link_to_home_and_own_page_still_work(srv):
    port, _ = srv
    assert _req(port, "GET", "/", {"Sec-Fetch-Site": "cross-site"})[0] == 200
    for site in ("same-origin", "none"):
        assert _req(port, "GET", "/api/watchlist", {"Sec-Fetch-Site": site})[0] == 200, site


def test_foreign_host_403_keeps_security_headers(srv):
    port, _ = srv
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.putrequest("GET", "/api/watchlist", skip_host=True)
    c.putheader("Host", "evil.example")
    c.endheaders()
    r = c.getresponse()
    r.read()
    c.close()
    assert r.status == 403 and r.getheader("X-Frame-Options") == "DENY"


@pytest.mark.parametrize("path", ["/api/watchlist", "/api/notes", "/api/models", "/api/settings", "/api/scan/start?universe=tw_large"])
def test_general_writes_reject_forged_host_and_foreign_origin(srv, path):
    port, _ = srv
    good = {"X-Requested-With": "invest-lab", "Content-Type": "application/json"}
    # 舊漏洞:偽造 Host 和 Origin 就能寫
    forged = {**good, "Origin": "http://evil.example"}
    assert _req(port, "POST", path, forged, {}, host="evil.example")[0] == 403
    assert _req(port, "POST", path, forged, {})[0] == 403
    assert _req(port, "POST", path, {**good, "Origin": "null"}, {})[0] == 403
    assert _req(port, "POST", path, {**good, "Origin": f"http://127.0.0.1:{port + 1}"}, {})[0] == 403
    assert _req(port, "POST", path, {"Content-Type": "application/json"}, {})[0] == 403


def test_general_write_still_works_from_the_page_and_cli(srv):
    port, _ = srv
    body = {"symbol": "2330.TW", "action": "remove"}
    page = {"X-Requested-With": "invest-lab", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
    assert _req(port, "POST", "/api/watchlist", page, body)[0] != 403
    cli = {"X-Requested-With": "invest-lab", "Content-Type": "application/json"}
    assert _req(port, "POST", "/api/watchlist", cli, body)[0] != 403


POST_PATHS = ["/api/update/apply","/api/settings/local", "/api/setup/research/start", "/api/setup/research/stop"]


@pytest.mark.parametrize("path", POST_PATHS)
def test_post_csrf_matrix(srv, path):
    port, _ = srv
    good = _good_headers(port)

    def without(k):
        return {a: b for a, b in good.items() if a != k}

    cases = [without("Origin"), {**good, "Origin": "https://evil.example"}, {**good, "Origin": "null"},
             {**good, "Origin": f"http://127.0.0.1:{port + 1}"}, {**good, "Sec-Fetch-Site": "cross-site"}, {**good, "Sec-Fetch-Site": "same-site"},
             without("X-Requested-With")]
    for h in cases:
        assert _req(port, "POST", path, h, {})[0] == 403, h
    assert _req(port, "POST", path, {**good, "Content-Type": "text/plain"}, {})[0] == 415
    assert _req(port, "POST", path, good, {}, host="evil.example")[0] == 403
    # DNS rebinding:Origin 與 Host 都是 evil.example 也不行
    assert _req(port, "POST", path, {**good, "Origin": "http://evil.example"}, {}, host="evil.example")[0] == 403


def test_apply_requires_valid_single_use_token(srv):
    port, _ = srv
    h = _good_headers(port)
    assert _req(port, "POST", "/api/update/apply", h, {"version": "9.9.9"})[0] == 403
    assert _req(port, "POST", "/api/update/apply", h, {"token": "bad", "version": "9.9.9"})[0] == 403
    tok = _req(port, "GET", "/api/update/status")[1]["token"]
    st, j = _req(port, "POST", "/api/update/apply", h, {"token": tok, "version": "9.9.9"})
    assert st == 409 and j["detail"] == "offline"        # 離線模式不動
    assert _req(port, "POST", "/api/update/apply", h, {"token": tok, "version": "9.9.9"})[0] == 403   # 權杖用過了
    assert _req(port, "GET", "/api/update/status")[1]["token"] != tok


def test_offline_status_never_connects(srv):
    port, _ = srv
    j = _req(port, "GET", "/api/update/status")[1]
    assert j["enabled"] is False and j["disabled_reason"] == "offline" and j["update_available"] is False and j["last_check"] is None


def _tok(port):
    return _req(port, "GET", "/api/settings/local")[1]["token"]


def test_settings_roundtrip_hides_values(srv):
    port, tmp = srv
    h = _good_headers(port)
    body = {"sec_user_agent": "Jane Doe jane@my-domain.org", "finmind_token": "abcdefgh123"}
    assert _req(port, "POST", "/api/settings/local", h, body)[0] == 403                     # 沒有權杖
    assert _req(port, "POST", "/api/settings/local", h, {**body, "token": "bad"})[0] == 403
    assert not (tmp / ".env").exists()
    st, j = _req(port, "POST", "/api/settings/local", h, {"sec_user_agent": "bad", "token": _tok(port)})
    assert st == 400 and j["detail"] == "sec_ua_format"
    tok = _tok(port)
    st, j = _req(port, "POST", "/api/settings/local", h, {**body, "token": tok})
    assert st == 200 and j["has_sec_user_agent"] and j["has_finmind_token"]
    assert "jane@" not in json.dumps(j) and "abcdefgh123" not in json.dumps(j)
    assert "jane@my-domain.org" in (tmp / ".env").read_text(encoding="utf-8")
    assert _req(port, "POST", "/api/settings/local", h, {**body, "token": tok})[0] == 403    # 權杖用過了
    j = _req(port, "GET", "/api/settings/local")[1]
    assert "jane@" not in json.dumps(j) and "abcdefgh123" not in json.dumps(j)
    assert set(j["folders"]) >= {"data", "private", "env_file"}
    st, j = _req(port, "POST", "/api/settings/local", h, {"finmind_token": "", "token": _tok(port)})
    assert st == 200 and not j["has_finmind_token"]
    assert _req(port, "POST", "/api/settings/local", h, {"auto_check": "yes", "token": _tok(port)})[0] == 400
    st, j = _req(port, "POST", "/api/settings/local", h, {"auto_check": False, "token": _tok(port)})
    assert st == 200 and j["auto_check"] is False
    assert _req(port, "GET", "/api/update/status")[1]["auto_check"] is False


def test_research_job_refuses_offline(srv):
    port, _ = srv
    st, j = _req(port, "POST", "/api/setup/research/start", _good_headers(port), {})
    assert st == 409 and j["detail"] == "offline"


def test_body_size_limit(srv):
    port, _ = srv
    st, _ = _req(port, "POST", "/api/settings/local", _good_headers(port), {"x": "a" * 20000, "token": _tok(port)})
    assert st == 413


# ---------- 前端 ----------
def test_frontend_never_uses_innerhtml_for_external_text():
    js = (WEB / "js" / "update.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js
    assert re.search(r"body\.textContent\s*=\s*latest\.notes", js)
    assert "http://" not in js and "https://" not in js      # 對外連線只在後端(CSP connect-src 'self')


def test_index_loads_update_module_and_csp_unchanged():
    assert '/static/js/update.js' in (WEB / "index.html").read_text(encoding="utf-8")
    assert "connect-src 'self'" in (ROOT / "app" / "server.py").read_text(encoding="utf-8")


def test_text_has_both_languages():
    node = shutil.which("node")
    if not node:
        pytest.skip("沒有 node")
    src = (WEB / "js" / "update_text.js").resolve()
    code = f"""globalThis.document={{documentElement:{{lang:"zh-Hant"}}}};
    const m=await import({json.dumps(src.as_uri())});
    const z=Object.keys(m.UPDATE_TEXT.zh).sort(),e=Object.keys(m.UPDATE_TEXT.en).sort();
    if(JSON.stringify(z)!==JSON.stringify(e)){{console.error(z.filter(k=>!e.includes(k)),e.filter(k=>!z.includes(k)));process.exit(1)}}
    if(!m.ut("banner.available",{{version:"1"}}).includes("有新版本"))process.exit(2);
    document.documentElement.lang="en";
    if(!m.ut("banner.available",{{version:"1"}}).includes("New version"))process.exit(3);
    if(m.errText("nope").indexOf("nope")<0)process.exit(4);"""
    r = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


def test_every_backend_error_code_has_text():
    """後端會丟的更新錯誤代碼前端都要有文字。"""
    text = (WEB / "js" / "update_text.js").read_text(encoding="utf-8")
    src = "".join((ROOT / "lab" / f).read_text(encoding="utf-8") for f in ("updater.py", "update_install.py", "update_helper.py"))
    codes = set(re.findall(r'InstallError\("([a-z_]+)"', src)) | set(re.findall(r'_fail\("([a-z_]+)"', src)) | set(re.findall(r'code="([a-z_]+)"', src))
    codes -= {"updated"}
    missing = [c for c in sorted(codes) if f'"err.{c}"' not in text]
    assert not missing, missing


def test_settings_dialog_has_four_tabs_and_safe_rendering():
    js = (WEB / "js" / "settings.js").read_text(encoding="utf-8")
    text = (WEB / "js" / "update_text.js").read_text(encoding="utf-8")
    assert 'TABS = ["general", "models", "data", "privacy"]' in js
    for tab in ("general", "models", "data", "privacy"):
        assert text.count(f'"settings.tab.{tab}"') == 2, tab
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js
    assert "http://" not in js and "https://" not in js
    keys = set(re.findall(r'ut\("(settings\.[a-z_.]+)"\)', js))
    assert keys and not [k for k in keys if f'"{k}"' not in text]
    assert "投資建議" in text and "investment advice" in text


def test_sec_placeholder_email_is_rejected():
    from lab import user_settings
    with pytest.raises(user_settings.SettingError):
        user_settings.clean_sec_user_agent("Jane Doe jane@your-domain.tld")
    with pytest.raises(user_settings.SettingError):
        user_settings.clean_sec_user_agent("Jane Doe jane@mail.your-domain.tld")
    with pytest.raises(user_settings.SettingError):
        user_settings.clean_sec_user_agent("Jane Doe jane@sub.example.com")


def test_finmind_is_not_listed_as_a_news_source():
    """FinMind 的新聞沒有接(lab/sources/finmind.py),介面不能說有。"""
    for f in ("js/update_text.js", "js/views/news.js", "js/i18n/catalog.en.js"):
        text = (WEB / f).read_text(encoding="utf-8")
        assert "Yahoo / FinMind" not in text and "Yahoo/FinMind" not in text, f
    assert "籌碼、新聞" not in (WEB / "js" / "update_text.js").read_text(encoding="utf-8")
    assert "Yahoo/FinMind" not in (ROOT / "lab" / "report.py").read_text(encoding="utf-8")


def test_global_shortcuts_stop_while_a_dialog_is_open():
    for f in ("shortcuts.js", "palette.js"):
        assert 'document.querySelector("dialog[open]")' in (WEB / "js" / f).read_text(encoding="utf-8"), f


def test_sec_user_agent_rejects_reserved_domains_and_second_email():
    import pytest as _pytest

    from lab import user_settings as US
    for bad in ("Jane jane@" + "foo.invalid", "Jane jane@" + "foo.test", "Jane jane@" + "x.example", "a@" + "ok.org b@" + "example.com"):
        with _pytest.raises(US.SettingError):
            US.clean_sec_user_agent(bad)
    good = "Jane jane@" + "ok.org"
    assert US.clean_sec_user_agent(good) == good

