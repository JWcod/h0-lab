"""新使用者一鍵建立回測資料(scripts/setup_research.py;規格 docs/安裝與上手規格.md §2.1):不連網的部分。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from lab import setup_research as LR
from lab import setup_status

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("setup_research", ROOT / "scripts" / "setup_research.py")
SR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SR)


@pytest.fixture(autouse=True)
def _results_stay_in_tmp(monkeypatch, tmp_path):
    """main 會寫上次建立的結果,不能寫到真實的資料夾。"""
    monkeypatch.setattr(LR.config, "OUT_DIR", tmp_path)


def _t(n_yahoo=86, n_tr=155, n_fm=240, n_sec=40):
    return {"yahoo": [0] * n_yahoo, "twse_tr": [0] * n_tr, "finmind": [0] * n_fm, "sec": [0] * n_sec}


def test_estimate_matches_status_api(monkeypatch):
    """Verifier 2026-11-02:--plan 和網站 / 文件的時間要一致;240 次在免費一小時額度內不用等。"""
    monkeypatch.setattr(SR.config, "FINMIND_TOKEN", "")
    m = SR.estimate_minutes(_t())
    assert m["finmind"] < 10 and abs(sum(m.values()) - setup_status.RESEARCH["minutes"]) <= 3
    assert SR.estimate_minutes(_t(n_fm=600))["finmind"] >= 60          # 超過額度才加上等待
    assert SR.n_sec(_t(n_sec=0)) == 0 and SR.n_sec(_t()) == 41


def test_refuses_to_overwrite_full_research(tmp_path):
    assert SR.has_full_research(tmp_path) is False
    (tmp_path / "bt_pit").mkdir()
    (tmp_path / "bt_pit" / "rows_tw_pit50.csv").write_text("", encoding="utf-8")
    assert SR.has_full_research(tmp_path) is True


def test_plan_symbols_filter(monkeypatch):
    """backtest_pit.plan 的 universes / symbols 只過濾,不改其他股票池的內容。"""
    import pandas as pd

    from lab.research import backtest_pit as BP
    monkeypatch.setattr(BP.PITStore, "load", staticmethod(lambda p: None))
    monkeypatch.setattr(BP, "decision_dates", lambda m, macro: [pd.Timestamp("2020-01-31")])
    out = BP.plan(["tw_large40", "us_large40"], ["2330.TW", "AAPL"])
    assert out == {"tw_large40": [(pd.Timestamp("2020-01-31"), "2330.TW")],
                   "us_large40": [(pd.Timestamp("2020-01-31"), "AAPL")]}
    full = BP.plan(["tw_large40"])
    assert len(full["tw_large40"]) == len(BP.TW_LARGE)


def test_plan_drops_a_universe_left_with_no_symbols(monkeypatch):
    """2026-11-06 乾淨機器測試:只建台股時 us_large40 過濾後是空的,judge 合併空表時崩潰。"""
    import pandas as pd

    from lab.research import backtest_pit as BP
    monkeypatch.setattr(BP.PITStore, "load", staticmethod(lambda p: None))
    monkeypatch.setattr(BP, "decision_dates", lambda m, macro: [pd.Timestamp("2020-01-31")])
    assert list(BP.plan(["tw_large40", "us_large40"], ["2330.TW"])) == ["tw_large40"]


def test_compute_only_judges_the_markets_being_built(monkeypatch):
    calls = []

    class Done:
        returncode = 0
    monkeypatch.setattr(LR.subprocess, "run", lambda cmd, **k: calls.append(cmd) or Done())
    assert LR.wanted_universes(["2330.TW"], []) == "tw_large40"
    assert LR.wanted_universes([], ["AAPL"]) == "us_large40"
    assert LR.wanted_universes(["2330.TW"], ["AAPL"]) == LR.UNIVERSES
    assert LR.compute(["2330.TW"], LR.wanted_universes(["2330.TW"], [])) == 0
    judge = next(c for c in calls if "judge" in c)
    assert judge[judge.index("--universes") + 1] == "tw_large40"


@pytest.mark.parametrize("bad,ready,expected", [
    (["Yahoo 2330.TW"], ("TW", "US"), "partial"),
    ([f"{LR.SEC_REFUSED}(HTTP 403)"], ("TW",), "sec_refused"),
    (["Yahoo AAPL"], ("TW",), "incomplete"),
])
def test_unfinished_build_keeps_the_retry_step_after_a_restart(monkeypatch, tmp_path, bad, ready, expected):
    """檔案都在時步驟會算完成、整張卡片消失,使用者就再也按不到「再試一次」。"""
    _run_main(monkeypatch, tmp_path, bad, ready=ready)
    assert setup_status.last_research_result(tmp_path) == expected
    st = setup_status.status(root=tmp_path, data_dir=tmp_path, out_dir=tmp_path, private_dir=tmp_path, snapshot_dir=tmp_path)
    step = next(s for s in st["steps"] if s["id"] == "research")
    assert step["done"] is False and step["last_result"] == expected and "research" in st["pending"]


def test_successful_rebuild_clears_the_earlier_result(monkeypatch, tmp_path):
    _run_main(monkeypatch, tmp_path, ["Yahoo 2330.TW"])
    assert setup_status.last_research_result(tmp_path) == "partial"
    _run_main(monkeypatch, tmp_path, [])
    assert setup_status.last_research_result(tmp_path) is None
    st = setup_status.status(root=tmp_path, data_dir=tmp_path, out_dir=tmp_path, private_dir=tmp_path, snapshot_dir=tmp_path)
    assert next(s for s in st["steps"] if s["id"] == "research")["done"] is True


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"other": 1}', '{"result": "<script>"}'])
def test_bad_result_file_is_ignored(tmp_path, content):
    (tmp_path / setup_status.RESULT_FILE).write_text(content, encoding="utf-8")
    assert setup_status.last_research_result(tmp_path) is None


def test_retry_results_all_have_interface_text():
    text = (ROOT / "web" / "js" / "update_text.js").read_text(encoding="utf-8")
    for result in setup_status.RETRY_RESULTS:
        assert text.count(f'"research.err.{result}"') == 2, result


def _http_error(code):
    import requests
    resp = requests.Response()
    resp.status_code = code
    return requests.HTTPError(response=resp)


_computed = []


def _run_main(monkeypatch, tmp_path, bad, ready=("TW", "US"), args=("--tw", "2330", "--us", "AAPL")):
    """跑 main 但不連網也不計算:下載結果與資料是否齊全都由測試決定。"""
    monkeypatch.setattr(LR.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(LR.config, "OFFLINE", False)
    monkeypatch.setattr(LR, "todo", lambda tw, us: _t())
    monkeypatch.setattr(LR, "download", lambda t: list(bad))
    monkeypatch.setattr(LR, "compute", lambda symbols, universes: _computed.append(universes) or 0)
    for market, files in setup_status.RESEARCH_FILES.items():
        if market in ready:
            d = tmp_path / "bt_pit"
            d.mkdir(exist_ok=True)
            for f in files:
                (d / f).write_text("", encoding="utf-8")
            (d / f"rows_{setup_status.RESEARCH_UNIVERSES[market][-1]}.csv").write_text("", encoding="utf-8")
    return LR.main(list(args))


def test_sec_refusal_is_reported_as_refusal(monkeypatch):
    """2026-11-06 乾淨機器測試:SEC 回 403 時原本只說「對照表找不到」,使用者無從修起。"""
    from lab.sources import sec

    def refuse():
        raise _http_error(403)
    monkeypatch.setattr(sec, "tickers", refuse)
    msg = LR.sec_access_error()
    assert msg.startswith(LR.SEC_REFUSED) and "403" in msg


def test_sec_network_failure_is_not_called_a_refusal(monkeypatch):
    from lab.sources import sec

    def offline():
        raise ConnectionError("down")
    monkeypatch.setattr(sec, "tickers", offline)
    assert not LR.sec_access_error().startswith(LR.SEC_REFUSED)


def test_tickers_raises_on_http_error_instead_of_parsing_the_error_page(monkeypatch):
    import requests

    from lab.sources import sec
    resp = requests.Response()
    resp.status_code = 403
    resp._content = b"<html>denied</html>"
    monkeypatch.setattr(sec.requests, "get", lambda *a, **k: resp)
    monkeypatch.setattr(sec, "cached", lambda ns, key, ttl, fetch: fetch())
    monkeypatch.setattr(sec, "_require_user_agent", lambda: None)
    with pytest.raises(requests.HTTPError):
        sec.tickers()


def test_missing_market_is_not_reported_as_done(monkeypatch, tmp_path, capsys):
    """整個美股財報失敗時,原本結束碼 0 + 「完成」,介面卻顯示沒完成。"""
    rc = _run_main(monkeypatch, tmp_path, [f"{LR.SEC_REFUSED}(HTTP 403)"], ready=("TW",))
    out = capsys.readouterr().out
    assert rc == LR.EXIT_SEC_REFUSED and "沒有完成" in out and "US" in out and "完成。" not in out


def test_incomplete_without_sec_refusal_has_its_own_code(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, ["Yahoo AAPL"], ready=("TW",)) == LR.EXIT_INCOMPLETE


def test_partial_failure_with_complete_markets_says_so(monkeypatch, tmp_path, capsys):
    assert _run_main(monkeypatch, tmp_path, ["Yahoo 2330.TW"]) == LR.EXIT_PARTIAL
    assert "有 1 項沒抓到" in capsys.readouterr().out


def test_clean_run_is_done(monkeypatch, tmp_path, capsys):
    assert _run_main(monkeypatch, tmp_path, []) == 0
    assert "完成。" in capsys.readouterr().out


def test_single_market_subset_ignores_the_other_market(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, [], ready=("TW",), args=("--tw", "2330")) == 0
    assert _computed[-1] == "tw_large40"


def _research_step(tmp_path):
    st = setup_status.status(root=tmp_path, data_dir=tmp_path, out_dir=tmp_path, private_dir=tmp_path, snapshot_dir=tmp_path)
    return next(s for s in st["steps"] if s["id"] == "research")


def test_same_items_missing_twice_stops_asking_to_retry(monkeypatch, tmp_path):
    """永遠抓不到的資料(例如下市代號)不能讓引導卡永遠關不掉;代號換了就還是要提醒。"""
    assert _run_main(monkeypatch, tmp_path, ["Yahoo X"]) == LR.EXIT_PARTIAL
    assert _research_step(tmp_path)["done"] is False
    assert _run_main(monkeypatch, tmp_path, ["Yahoo X"]) == LR.EXIT_PARTIAL
    assert _research_step(tmp_path)["done"] is True
    assert _run_main(monkeypatch, tmp_path, ["Yahoo X"]) == LR.EXIT_PARTIAL
    assert _research_step(tmp_path)["done"] is True
    assert _run_main(monkeypatch, tmp_path, ["Yahoo Y"]) == LR.EXIT_PARTIAL
    assert _research_step(tmp_path)["done"] is False


def test_repeated_sec_refusal_keeps_asking(monkeypatch, tmp_path):
    bad = [f"{LR.SEC_REFUSED}(HTTP 403)"]
    for _ in range(2):
        assert _run_main(monkeypatch, tmp_path, bad) == LR.EXIT_SEC_REFUSED
    assert _research_step(tmp_path)["last_result"] == "sec_refused"


def test_full_research_clears_an_earlier_failure(monkeypatch, tmp_path):
    _run_main(monkeypatch, tmp_path, ["Yahoo X"])
    (tmp_path / "bt_pit" / "rows_tw_pit50.csv").write_text("", encoding="utf-8")
    assert _run_main(monkeypatch, tmp_path, []) == 3
    assert setup_status.last_research_result(tmp_path) is None


def test_unexpected_error_after_download_is_recorded_as_failed(monkeypatch, tmp_path):
    def boom(symbols, universes):
        raise RuntimeError("x")
    monkeypatch.setattr(LR.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(LR.config, "OFFLINE", False)
    monkeypatch.setattr(LR, "todo", lambda tw, us: _t())
    monkeypatch.setattr(LR, "download", lambda t: [])
    monkeypatch.setattr(LR, "compute", boom)
    with pytest.raises(RuntimeError):
        LR.main(["--tw", "2330"])
    assert setup_status.last_research_result(tmp_path) == "failed"


def test_failing_to_record_does_not_fail_a_good_build(monkeypatch, tmp_path, capsys):
    def denied(*a, **k):
        raise PermissionError("read-only")
    monkeypatch.setattr(setup_status, "save_research_result", denied)
    assert _run_main(monkeypatch, tmp_path, []) == 0
    assert "無法記錄" in capsys.readouterr().out


def test_judge_explains_when_no_symbol_matches(monkeypatch):
    from lab.research import backtest_pit as BP
    monkeypatch.setattr(BP, "plan", lambda universes, symbols: {})
    with pytest.raises(SystemExit, match="沒有要計算的公司"):
        BP.judge(1, ["tw_large40"], ["ZZZ"])


def test_every_exit_code_has_interface_text():
    from pathlib import Path

    from lab import setup_job
    text = (Path(LR.__file__).resolve().parent.parent / "web" / "js" / "update_text.js").read_text(encoding="utf-8")
    for result in setup_job.EXIT_MESSAGES.values():
        if result != "done":
            assert text.count(f'"research.err.{result}"') == 2, result   # 中文、英文各一份


class _FakeSources:
    """download() 用到的資料源:除了 SEC 都成功,不連網。"""

    @staticmethod
    def install(monkeypatch, tickers=None, companyfacts=None):
        from lab.sources import finmind, sec, twse_hist, yahoo
        calls = []
        monkeypatch.setattr(yahoo, "history_since", lambda s: object())
        monkeypatch.setattr(twse_hist, "total_return_month", lambda y, m: object())
        monkeypatch.setattr(finmind, "get", lambda *a, **k: object())
        monkeypatch.setattr(finmind, "budget_status", lambda: {"cooldown_remaining": 0, "used_last_hour": 0, "budget": 280})
        monkeypatch.setattr(sec, "tickers", tickers or (lambda: object()))
        monkeypatch.setattr(sec, "cik_for", lambda s, overrides=False: 1)

        def facts(cik):
            calls.append(cik)
            return companyfacts() if companyfacts else object()
        monkeypatch.setattr(sec, "companyfacts", facts)
        monkeypatch.setattr(LR.time, "sleep", lambda s: None)
        return calls


def _sec_only(n=3):
    return {"yahoo": [], "twse_tr": [], "finmind": [], "sec": [f"S{i}" for i in range(n)]}


@pytest.mark.parametrize("code", [401, 403, 429])
def test_download_stops_before_any_filing_when_the_ticker_list_is_refused(monkeypatch, code):
    def refuse():
        raise _http_error(code)
    calls = _FakeSources.install(monkeypatch, tickers=refuse)
    bad = LR.download(_sec_only())
    assert calls == [] and len(bad) == 1 and bad[0].startswith(LR.SEC_REFUSED) and str(code) in bad[0]


@pytest.mark.parametrize("code", [401, 403, 429])
def test_download_stops_when_filings_are_refused_after_a_cached_ticker_list(monkeypatch, code):
    """Verifier 2026-11-06:對照表有快取時,拒絕要到抓財報才看得到,原本會逐檔重試、結束碼也不對。"""
    def refuse():
        raise _http_error(code)
    calls = _FakeSources.install(monkeypatch, companyfacts=refuse)
    bad = LR.download(_sec_only())
    assert len(calls) == 1 and len(bad) == 1 and bad[0].startswith(LR.SEC_REFUSED)


def test_download_keeps_going_when_one_filing_has_an_ordinary_error(monkeypatch):
    def not_refused():
        raise _http_error(500)
    calls = _FakeSources.install(monkeypatch, companyfacts=not_refused)
    bad = LR.download(_sec_only())
    assert len(calls) == 3 and len(bad) == 3 and not any(x.startswith(LR.SEC_REFUSED) for x in bad)


def test_exit_codes_map_to_their_interface_messages():
    from lab import setup_job
    assert setup_job.EXIT_MESSAGES[LR.EXIT_INCOMPLETE] == "incomplete"
    assert setup_job.EXIT_MESSAGES[LR.EXIT_SEC_REFUSED] == "sec_refused"
    assert setup_job.EXIT_MESSAGES[LR.EXIT_PARTIAL] == "partial"
    assert LR.EXIT_FAILED not in setup_job.EXIT_MESSAGES   # 沒有對應就顯示「建立失敗」


def test_refusal_with_missing_tw_is_not_blamed_on_the_sec(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, [f"{LR.SEC_REFUSED}(HTTP 403)"], ready=("US",)) == LR.EXIT_INCOMPLETE


def test_compute_failure_never_leaks_its_own_exit_code(monkeypatch, tmp_path):
    """計算的結束碼和這裡的結束碼是兩回事:傳回去介面會把 argparse 的 2 顯示成「離線模式」。"""
    monkeypatch.setattr(LR.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(LR.config, "OFFLINE", False)
    monkeypatch.setattr(LR, "todo", lambda tw, us: _t())
    monkeypatch.setattr(LR, "compute", lambda symbols, universes: 2)
    monkeypatch.setattr(LR, "download", lambda t: [])
    assert LR.main(["--tw", "2330"]) == LR.EXIT_FAILED
    monkeypatch.setattr(LR, "download", lambda t: [f"{LR.SEC_REFUSED}(HTTP 403)"])
    assert LR.main(["--us", "AAPL"]) == LR.EXIT_SEC_REFUSED    # 只建美股、SEC 拒絕:judge 沒有資料可算


def test_module_can_be_run_with_python_m():
    """lab/setup_job.py 在原始碼模式用 python -m lab.setup_research:沒有入口時什麼都不做、結束碼 0,介面會顯示完成。"""
    import subprocess
    import sys
    env = {"PATH": "/usr/bin:/bin", "INVEST_LAB_OFFLINE": "1", "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-m", "lab.setup_research", "--plan"], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "要下載" in r.stdout
