"""新使用者一鍵建立回測資料(scripts/setup_research.py;規格 docs/安裝與上手規格.md §2.1):不連網的部分。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

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
