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


def _http_error(code):
    import requests
    resp = requests.Response()
    resp.status_code = code
    return requests.HTTPError(response=resp)


def _run_main(monkeypatch, tmp_path, bad, ready=("TW", "US"), args=("--tw", "2330", "--us", "AAPL")):
    """跑 main 但不連網也不計算:下載結果與資料是否齊全都由測試決定。"""
    monkeypatch.setattr(LR.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(LR.config, "OFFLINE", False)
    monkeypatch.setattr(LR, "todo", lambda tw, us: _t())
    monkeypatch.setattr(LR, "download", lambda t: list(bad))
    monkeypatch.setattr(LR, "compute", lambda symbols: 0)
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
    assert _run_main(monkeypatch, tmp_path, ["Yahoo 2330.TW"]) == 0
    assert "有 1 項沒抓到" in capsys.readouterr().out


def test_clean_run_is_done(monkeypatch, tmp_path, capsys):
    assert _run_main(monkeypatch, tmp_path, []) == 0
    assert "完成。" in capsys.readouterr().out


def test_single_market_subset_ignores_the_other_market(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, [], ready=("TW",), args=("--tw", "2330")) == 0


def test_every_exit_code_has_interface_text():
    from pathlib import Path

    from lab import setup_job
    text = (Path(LR.__file__).resolve().parent.parent / "web" / "js" / "update_text.js").read_text(encoding="utf-8")
    for result in setup_job.EXIT_MESSAGES.values():
        if result != "done":
            assert text.count(f'"research.err.{result}"') == 2, result   # 中文、英文各一份
