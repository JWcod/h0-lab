"""API 煙霧測試:真的啟動 `python -m app.server`,用 urllib 打幾個主要端點。

- 環境沒有 httpx,不能用 FastAPI TestClient,所以用子程序啟動伺服器。
- 子程序一律:離線模式、假的私人資料夾與輸出資料夾(pytest 暫存區)、不寫前瞻紀錄、
  只綁 127.0.0.1、隨機空閒埠(絕不用預設埠 8765,開發者自己的網站可能正在用)。
- 離線模式下需要網路的端點應該回傳錯誤或降級內容,而不是讓伺服器崩潰;每打一個端點都確認伺服器還活著。
- 回測端點(v2 起只顯示開發集):台股放 tests/baseline/outputs 的 v1.1 回測輸出(含 holdout 期間的舊格式)→ 必須不回傳;
  美股放一份由同一檔案截掉 holdout 期間、標成開發集格式的輸出 → 檢查前端(web/js/views/backtest.js)用到的欄位。
  POST 重跑必須回 403。
"""
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
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASELINE_OUT = ROOT / "tests" / "baseline" / "outputs"
LIVE_PORT = 8765  # 預設埠可能有開發者自己的網站在跑,測試不得使用
STARTUP_TIMEOUT = 90.0

# 不經過任何 proxy(離線模式會把 HTTP(S)_PROXY 指到 127.0.0.1:9,本機請求不能走它)
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _free_port() -> int | None:
    for _ in range(20):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
        except OSError:
            return None
        if port != LIVE_PORT:
            return port
    return None


def _get(base: str, path: str, timeout: float = 30.0) -> tuple[int, dict | str, dict]:
    """回傳 (狀態碼, JSON 或文字, 標頭(鍵一律小寫))。HTTP 錯誤碼也照樣回傳,不丟例外。"""
    req = urllib.request.Request(base + path, headers={"Accept": "application/json, text/html"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            status, body, headers = r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        status, body, headers = e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}
    text = body.decode("utf-8", errors="replace")
    if "json" in headers.get("content-type", ""):
        return status, json.loads(text), headers
    return status, text, headers


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = _free_port()
    if port is None:
        pytest.skip("無法在 127.0.0.1 取得空閒埠(8765 以外),跳過 API 煙霧測試")
    tmp = tmp_path_factory.mktemp("api_smoke")
    out_dir, private_dir = tmp / "out", tmp / "private_fake"
    (out_dir / "research").mkdir(parents=True)
    private_dir.mkdir()
    src = BASELINE_OUT / "backtest_TW.json"
    if src.exists():
        shutil.copy(src, out_dir / "research" / "backtest_TW.json")
    src = BASELINE_OUT / "backtest_US.json"
    if src.exists():  # 只為了檢查欄位形狀:截掉 2024-10 以後並標成開發集格式(數字不是真的開發集結果)
        d = json.loads(src.read_text(encoding="utf-8"))
        d.update({"split": "dev", "holdout_start": "2024-10-01"})
        d["by_year"] = [y for y in d["by_year"] if y["year"] <= 2024]
        c = d["portfolio"]["curve"]
        keep = [i for i, x in enumerate(c["date"]) if x < "2024-10"]
        d["portfolio"]["curve"] = {k: [v[i] for i in keep] for k, v in c.items()}
        (out_dir / "research" / "backtest_US.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "INVEST_LAB_OFFLINE": "1",
        "INVEST_LAB_OUT_DIR": str(out_dir),
        "INVEST_LAB_PRIVATE_DIR": str(private_dir),
        "INVEST_LAB_RECORD": "0",
        "INVEST_LAB_HOST": "127.0.0.1",
        "INVEST_LAB_PORT": str(port),
        "INVEST_LAB_AGENT": env.get("INVEST_LAB_AGENT", "pytest"),
        "PYTHONUNBUFFERED": "1",
    })
    log_path = tmp / "server.log"
    log = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen([sys.executable, "-m", "app.server"], cwd=str(ROOT), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    # Popen 之後的所有事(包括等待啟動)都在 try 裡:任何例外或 Ctrl-C 都會走 finally 關掉伺服器,不留孤兒程序
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + STARTUP_TIMEOUT
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                if _get(base, "/api/params", timeout=2)[0] == 200:
                    ready = True
                    break
            except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
                pass
            time.sleep(0.3)
        if not ready:
            log.flush()
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            if "address already in use" in tail.lower() or "error while attempting to bind" in tail.lower():
                pytest.skip(f"埠 {port} 綁不起來,跳過")
            pytest.fail(f"伺服器沒有在 {STARTUP_TIMEOUT:.0f} 秒內啟動(結束碼 {proc.poll()}):\n{tail}")
        yield {"base": base, "proc": proc, "log": log_path, "private": private_dir}
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


def _alive(server) -> None:
    assert server["proc"].poll() is None, "伺服器程序已經結束(崩潰):\n" + server["log"].read_text(
        encoding="utf-8", errors="replace")[-3000:]


def test_binds_only_localhost(server):
    assert server["base"].startswith("http://127.0.0.1:")
    assert not server["base"].endswith(f":{LIVE_PORT}")


def test_index_page(server):
    status, body, headers = _get(server["base"], "/")
    assert status == 200
    assert isinstance(body, str) and '<main id="app"' in body and 'class="u-shell app-shell"' in body
    assert "content-security-policy" in headers
    # 靜態備援聲明:JS 載入失敗時也看得到(app.js 沿用同一個 id,不會重複)
    assert body.count('id="siteDisclaimer"') == 1 and "不構成投資建議" in body
    # 前端的全站「非投資建議」頁尾由 app.js 產生
    s2, js, _ = _get(server["base"], "/static/js/app.js")
    assert s2 == 200 and "不構成投資建議" in js and "siteDisclaimer" in js
    _alive(server)


def test_dictionary(server):
    status, j, headers = _get(server["base"], "/api/dictionary")
    assert status == 200
    assert headers.get("cache-control") == "no-store"
    assert isinstance(j["items"], list) and j["items"]
    for it in j["items"]:
        assert {"key", "name"} <= set(it)
    _alive(server)


def test_params(server):
    status, j, _ = _get(server["base"], "/api/params")
    assert status == 200
    assert j["version"].startswith("JW-MF")
    pw = j["pillar_weights"]
    assert {"long_term", "short_term_us", "short_term_tw"} <= set(pw)
    th = j["short_term_thresholds"]
    assert th["bearish"] < th["bullish"]
    _alive(server)


def test_backtest_dev_only_shape(server):
    status, j, _ = _get(server["base"], "/api/research/backtest?market=US")
    assert status == 200
    assert set(j) >= {"current", "baseline", "notice"}
    assert j["baseline"] is None, "v1.0 對照檔含 holdout 期間,不得回傳"
    assert "backtest_pit" in j["notice"]
    cur = j["current"]
    if not (BASELINE_OUT / "backtest_US.json").exists():
        pytest.skip("沒有基準輸出(公開版不附含第三方資料衍生值的檔案)")
    assert cur is not None, "暫存輸出資料夾裡已放了開發集格式的回測,應該讀得到"
    # web/js/views/backtest.js 用到的欄位
    for k in ("start", "n_stocks", "n_judgments", "model_version", "by_year", "by_rating", "pillar_ic", "portfolio", "holdout_start"):
        assert k in cur, k
    for k in ("right", "wrong", "hit", "n_bull", "n_bear"):
        assert k in cur["long_term"]["12m"] and k in cur["short_term"]["3m"], k
    assert {"curve", "cost", "stats"} <= set(cur["portfolio"])
    _alive(server)


def test_backtest_withholds_holdout_results(server):
    """舊格式(含 2024-10 以後判斷的彙總)不得回傳;回傳的任何彙總都不得含 2024-10 以後的月份或年份。"""
    status, j, _ = _get(server["base"], "/api/research/backtest?market=TW")
    assert status == 200
    assert j["current"] is None and j["baseline"] is None and j["withheld"]
    for m in ("TW", "US"):
        _, j, _ = _get(server["base"], f"/api/research/backtest?market={m}")
        cur = j["current"]
        if cur is None:
            continue
        assert cur["split"] == "dev"
        assert all(y["year"] <= 2024 for y in cur["by_year"])
        assert all(d < "2024-10" for d in cur["portfolio"]["curve"]["date"])
    _alive(server)


def test_backtest_rerun_is_forbidden(server):
    req = urllib.request.Request(server["base"] + "/api/research/backtest/run?market=TW", data=b"", method="POST",
                                 headers={"X-Requested-With": "invest-lab", "Accept": "application/json"})
    try:
        with _OPENER.open(req, timeout=30) as r:
            status, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read()
    assert status == 403
    assert "backtest_pit" in json.loads(body.decode("utf-8"))["detail"]
    _alive(server)


def test_backtest_rejects_bad_market(server):
    status, j, _ = _get(server["base"], "/api/research/backtest?market=XX")
    assert status == 400
    assert "detail" in j
    _alive(server)


def test_paper_and_review_hidden(server):
    """模擬投資組合與預測追蹤暫停對外(用內建模型的評等);lab/paper.py 照常在掃描時運作(見 test_paper_summary_internal)。"""
    for path in ("/api/paper", "/api/review"):
        status, j, _ = _get(server["base"], path)
        assert status == 404 and "下一版" in j["detail"], path
    _alive(server)


def test_paper_summary_internal(tmp_path, monkeypatch):
    """程式碼保留:模擬帳戶摘要在內部照舊可以算(前瞻紀錄不中斷)。"""
    from lab import paper
    monkeypatch.setattr(paper, "PATH", tmp_path / "paper.json")     # 假的私人資料夾,不碰真實模擬帳戶
    j = paper.summary()
    assert isinstance(j["rules"], list) and isinstance(j["rules_ex"], list)
    assert isinstance(j["books"], dict)
    for b in j["books"].values():
        assert {"market", "currency", "initial", "value", "positions", "trades", "equity"} <= set(b)
    assert any("沒有證實有效" in r for r in j["rules_ex"])
    assert not any("勝過" in r for r in j["rules_ex"])


def test_scan_latest_needs_user_model(server):
    """市場掃描只用使用者的模型排名:沒指定模型不排名、不回傳任何列;找不到模型 404。"""
    s, j, _ = _get(server["base"], "/api/scan/latest?universe=tw_large")
    assert s == 200 and j["ranked"] is False and j["rows"] == [] and "模型" in j["message"]
    assert _get(server["base"], "/api/scan/latest?universe=tw_large&model=nope-x")[0] == 404
    _alive(server)


def test_watchlist_uses_fake_private_dir(server):
    status, j, _ = _get(server["base"], "/api/watchlist")
    assert status == 200
    assert j == {"items": []}, "假的私人資料夾是空的,觀察清單應該是空的(確認沒有讀到真實 private/)"
    _alive(server)


@pytest.mark.parametrize("path", ["/api/home", "/api/search?q=2330", "/api/scan/status", "/api/report?symbol=ZZZZQ"])
def test_network_endpoints_degrade_offline(server, path):
    """需要網路的端點在離線模式下:可以回錯誤碼或降級內容,但伺服器不能崩潰、不能回 HTML 錯誤頁。"""
    status, j, _ = _get(server["base"], path, timeout=60)
    assert status < 600
    assert isinstance(j, dict), f"{path} 應該回傳 JSON(狀態碼 {status})"
    _alive(server)


def test_factor_lab_needs_pit_store(server):
    # 測試站的輸出資料夾沒有 PIT 價格庫:回 409 和建立方法,不是 500;舊的股票池代號回 400
    status, j, _ = _get(server["base"], "/api/research/factors?universe=tw_pit50")
    assert status == 409 and "backtest_pit build" in j["detail"]
    assert _get(server["base"], "/api/research/factors?universe=tw_large")[0] == 400
    _alive(server)


def _post(base: str, path: str, body, csrf: bool = True) -> tuple[int, dict]:
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if csrf:
        h["X-Requested-With"] = "invest-lab"
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST", headers=h)
    try:
        with _OPENER.open(req, timeout=30) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        return status, json.loads(raw.decode("utf-8"))
    except ValueError:
        raise AssertionError(f"{path} 回傳的不是 JSON(狀態碼 {status}):{raw[:300]!r}") from None


def test_models_crud_in_fake_private_dir(server):
    """M2:模型清單、另存、修改、驗證、封存;全部寫在假的私人資料夾。"""
    b = server["base"]
    s, j, _ = _get(b, "/api/models")
    assert s == 200 and j["default"] == "jw-mf-2.0" and "roe" in j["base_vars"]
    assert j["models"] == [], "假的私人資料夾沒有我的模型;內建研究基準模型不列在介面清單(開源版不提供它的個股分數)"
    assert _post(b, "/api/models/jw-mf-2.0/fork", {"id": "smoke-a", "name": "測試"}, csrf=False)[0] == 403
    try:
        s, j = _post(b, "/api/models/jw-mf-2.0/fork", {"id": "smoke-a", "name": "測試"})
    except AssertionError as e:
        raise AssertionError(f"{e}\n伺服器紀錄:\n" + server["log"].read_text(encoding="utf-8")[-2500:]) from None
    assert s == 200 and j["saved"]["id"] == "smoke-a"
    s, j, _ = _get(b, "/api/models/smoke-a")
    m = j["model"]
    m["pillar_weights"]["long_term"]["quality"] = 40
    s, j = _post(b, "/api/models", {"model": m})
    assert s == 200 and j["saved"]["source"] == "mine"
    s, j = _post(b, "/api/models", {"model": {**m, "id": "jw-mf-2.0"}})
    assert s == 400 and "不能覆寫出廠模型" in j["detail"]                                # 不是「代號格式不正確」
    assert _post(b, "/api/models", [1, 2])[0] == 400
    assert _post(b, "/api/models", {"model": {"notes": "x" * 1_100_000}})[0] == 413
    deep = {}
    cur = deep
    for _ in range(5000):
        cur["a"] = {}
        cur = cur["a"]
    s_deep = _post(b, "/api/models/validate", {"model": deep})[0]
    assert s_deep in (200, 400), s_deep
    assert _post(b, "/api/models/jw-mf-2.0/fork", {"id": "smoke\n", "name": "x"})[0] == 400
    bad = json.loads(json.dumps(m))
    bad["metrics"][0]["label"] = "{__class__}"
    s, j = _post(b, "/api/models/validate", {"model": bad, "symbol": "2330.TW"})
    assert s == 200 and j["ok"] is False and "label" in j["error"]
    s, j = _post(b, "/api/models/validate", {"model": m, "symbol": "2330.TW"})
    assert s == 200 and j["ok"] is True and "preview_error" in j          # 沒開過報告:不會去抓資料
    s, j, _ = _get(b, "/api/models")
    assert next(x for x in j["models"] if x["id"] == "smoke-a")["n_trials"] == 0 and "37" in j["shipped_trials_note"]
    assert _post(b, "/api/models/smoke-a/backtest", {}, csrf=False)[0] == 403
    s, j = _post(b, "/api/models/smoke-a/holdout", {"universe": "tw_pit50", "confirm": True})
    assert s in (403, 409), s                                               # agent 模式或沒開啟:一律不算
    assert _post(b, "/api/models/jw-mf-2.0/backtest", {})[0] == 409       # 出廠模型不走模型工作台
    assert _post(b, "/api/models/smoke-a/backtest", {"universe": "nope"})[0] == 409
    assert _post(b, "/api/models/nope/backtest", {})[0] == 404
    assert _get(b, "/api/models/..%2F..%2Fetc")[0] in (400, 404)
    s, j = _post(b, "/api/models/smoke-a/archive", {})
    assert s == 200
    assert _get(b, "/api/models")[1]["models"] == []
    assert _post(b, "/api/models/jw-mf-2.0/archive", {})[0] == 400
    # 和內建模型同一組設定:不給個股分數、不能拿來排名
    s, j = _post(b, "/api/models/validate", {"model": _get(b, "/api/models/jw-mf-2.0")[1]["model"], "symbol": "2330.TW"})
    assert s == 200 and j["ok"] is True and "preview" not in j and "內建" in j["preview_error"]
    s, j, _ = _get(b, "/api/scan/latest?universe=tw_large&model=jw-mf-2.0")
    assert s == 200 and j["ranked"] is False and j["rows"] == []
    _alive(server)


# ---------- 上手精靈(S1;docs/上手精靈規格.md §8) ----------
def test_wizard_templates_and_catalog(server):
    b = server["base"]
    s, j, _ = _get(b, "/api/templates")
    assert s == 200 and len(j["templates"]) == 7 and "沒有證實有效" in j["notice"]["zh-TW"]
    assert "sample" not in {t["id"] for t in j["templates"]}, "綜合範本(= 內建模型)不在精靈列出"
    st = {t["id"]: t["status"] for t in j["templates"]}
    assert st["dividend"] == st["lowvol"] == st["value"] == "ready"                         # S4 合併後
    assert [x["value"] for x in j["levels"]] == ["off", "normal", "high"]
    s, j, _ = _get(b, "/api/templates?market=US")
    assert s == 200 and "flows_tw" not in {t["id"] for t in j["templates"]}
    s, j, _ = _get(b, "/api/templates?market=XX")
    assert s == 400 and "TW 或 US" in j["detail"]
    s, j, _ = _get(b, "/api/catalog?market=TW")
    assert s == 200 and j["market"] == "TW"
    sig = {x["key"]: x for x in j["signals"]}
    assert sig["roic"]["plain_name"]["zh-TW"] and sig["roic"]["backtestable"] is True
    assert sig["news7"]["backtestable"] is False and "insider" not in sig
    assert [p["key"] for p in j["pillars"]][:4] == ["quality", "growth", "valuation", "health"]
    assert _get(b, "/api/catalog?market=jp")[0] == 400
    _alive(server)


def test_wizard_preview(server, monkeypatch):
    b = server["base"]
    ok_body = {"template": "value", "market": "TW", "choices": {"pillars": {"growth": "off"}, "signals": {"roic": "很重要"}}}
    assert _post(b, "/api/wizard/preview", ok_body, csrf=False)[0] == 403
    s, j = _post(b, "/api/wizard/preview", {"choices": {}})
    assert s == 400 and "範本" in j["detail"]
    s, j = _post(b, "/api/wizard/preview", {"template": "nope"})
    assert s == 404 and "找不到範本" in j["detail"]
    s, j = _post(b, "/api/wizard/preview", {"template": "flows_tw", "market": "US"})
    assert s == 400 and "只能用在台股" in j["detail"]
    assert _post(b, "/api/wizard/preview", {"template": "value", "universe": "us_large40"})[0] == 400
    assert _post(b, "/api/wizard/preview", {"template": "value", "top": 0})[0] == 400
    assert _post(b, "/api/wizard/preview", {"template": "value", "market": "XX"})[0] == 400
    assert _post(b, "/api/wizard/preview", {"template": "value", "market": "TWX"})[0] == 400
    assert _post(b, "/api/wizard/preview", {"template": "value", "market": ["TW"]})[0] == 400
    s, j = _post(b, "/api/wizard/preview", {"template": "value", "choices": {"signals": {"roic": ["x"]}}})
    assert s == 200 and j["ok"] is False and "重要度" in j["error"]                    # 不是 500
    s, j = _post(b, "/api/wizard/preview", {"template": "value", "choices": {"pillars": {
        p: "off" for p in ("quality", "growth", "valuation", "health", "momentum", "flows", "revisions", "sentiment")}}})
    assert s == 200 and j["ok"] is False and j["error"] == "至少要看一個面向"
    s, j = _post(b, "/api/wizard/preview", {"template": "dividend"})
    assert s == 200 and j["ok"] is True                                                   # S4 合併後可選
    s, j = _post(b, "/api/wizard/preview", ok_body)
    assert s == 200 and j["ok"] is True and j["model"]["wizard"]["template"] == "value"
    assert j["model"]["pillar_weights"]["long_term"]["growth"] == 0
    assert "rows_tw_pit50" in j["ranking_error"] and "非投資建議" in j["disclaimer"]
    # 放一份合成的回測逐筆檔與原始值快取到測試站的輸出資料夾 → 有今天的排名
    from lab.research import backtest_pit as BP
    from lab.research import model_eval as ME
    from tests.test_model_honesty import _world
    allr, params, _ = _world(seed=5)
    out = server["private"].parent / "out" / "bt_pit"
    monkeypatch.setattr(BP, "OUT", out)
    ME._env_cache.clear()
    BP.write_raw_env(allr, params)
    allr.drop(columns=["_env", "market"]).to_csv(out / "rows_tw_pit50.csv", index=False)
    s, j = _post(b, "/api/wizard/preview", ok_body | {"top": 5})
    assert s == 200 and j["ok"] and "ranking_error" not in j, j.get("ranking_error")
    r = j["ranking"]
    assert r["as_of"] == max(allr["date"]) and r["n"] == 24 and len(r["top"]) == len(r["bottom"]) == 5
    assert r["top"][0]["rank"] == 1 and r["top"][0]["lt_score"] >= r["bottom"][-1]["lt_score"]
    assert all("name" in x and x["name"] is None for x in r["top"] + r["bottom"])  # 合成代號沒有公司名稱
    md = server["private"] / "models"                                         # 不存檔、不計入「試了幾次」
    assert not list(md.glob("wizard-*.json"))
    assert all("wizard-" not in p.read_text(encoding="utf-8") for p in md.glob("*.jsonl"))
    # 存檔:只填名稱(或留空)→ 代號由系統產生,兩次不重複;自訂代號撞號回繁中錯誤
    model = {**j["model"], "id": "", "name": ""}
    s1, a = _post(b, "/api/models", {"model": model, "auto_id": True})
    s2, c = _post(b, "/api/models", {"model": {**model, "name": "我的存股"}, "auto_id": True})
    assert s1 == s2 == 200, (a, c)
    assert re.fullmatch(r"value-\d{8}-[0-9a-f]{6}", a["saved"]["id"]) and a["saved"]["id"] != c["saved"]["id"]
    assert a["saved"]["name"].startswith("我的") and c["saved"]["name"] == "我的存股"
    assert (md / f"{a['saved']['id']}.json").is_file()
    s, j2 = _post(b, "/api/models", {"model": {**model, "id": c["saved"]["id"], "name": "x"}, "create_only": True})
    assert s == 400 and "已經有人用了" in j2["detail"]
    assert _post(b, "/api/models", {"model": model, "auto_id": True}, csrf=False)[0] == 403
    for bad in ([1], {**model, "wizard": "x"}):
        s, j2 = _post(b, "/api/models", {"model": bad, "auto_id": True})
        assert s == 400 and "JSON 物件" in j2["detail"]
    for p in md.glob("value-*.json"):                                          # 收掉,不影響其他測試的模型清單
        _post(b, f"/api/models/{p.stem}/archive", {})
    _alive(server)


def test_setup_status_new_user(server):
    """第一次執行引導:假的輸出資料夾是空的 → 回測資料一定還沒齊;不回 token 值。"""
    s, j, _ = _get(server["base"], "/api/setup/status")
    assert s == 200 and j["offline"] is True
    assert {x["id"] for x in j["steps"]} == {"install", "first_report", "sec_user_agent", "finmind_token", "research"}
    assert j["research"]["TW"]["ready"] is False and "research" in j["pending"]
    assert all(isinstance(v, bool) for v in j["checks"].values())
    _alive(server)
