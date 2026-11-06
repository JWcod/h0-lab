"""模擬帳戶策略 C(風控版,階段 4.2):備份、A / B 不動、上限、每月再平衡、賣出條件、含息大盤連乘。"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from lab import paper, portfolio


@pytest.fixture
def book_path(tmp_path, monkeypatch):
    p = tmp_path / "paper.json"
    monkeypatch.setattr(paper, "PATH", p)
    return p


def _rows(n=12, sector=None, vol=0.2, lt=3):
    return [{"symbol": f"{1000 + i}.TW", "name": f"S{i}", "price": 100.0, "lt_level": lt, "lt_rating": "買進", "st_code": 0,
             "st_signal": "中性", "confidence": 80, "red_flags": [], "lt_score": 90 - i, "st_score": 50, "vol_1y": vol,
             "industry": (sector or {}).get(i, f"產業{i}")} for i in range(n)]


def test_backup_and_old_books_untouched(book_path):
    old = {"created": "2026-01-01", "books": {"TW": {"cash": 5.0, "positions": {"X": {"shares": 1, "cost": 1}}, "trades": [{"a": 1}],
                                                     "equity": [{"date": "2026-01-02", "value": 6}], "start": "2026-01-01", "bench_start": 1}}}
    book_path.write_text(json.dumps(old), encoding="utf-8")
    paper.update_risk("TW", _rows(), None, today="2026-10-26")
    backups = list(book_path.parent.glob("paper.backup-*.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text(encoding="utf-8")) == old
    st = json.loads(book_path.read_text(encoding="utf-8"))
    assert st["books"]["TW"] == old["books"]["TW"]                      # A 的歷史一字不動
    assert {"TW_risk", "US_risk", "TW_ex"} <= set(st["books"])
    # 已經有 C 帳本之後不再備份(同一天也不覆蓋)
    paper.update_risk("TW", _rows(), None, today="2026-10-27")
    assert len(list(book_path.parent.glob("paper.backup-*.json"))) == 1


def test_first_rebalance_respects_caps(book_path):
    sector = {i: "半導體" for i in range(5)}
    r = paper.update_risk("TW", _rows(12, sector, vol=0.2), None, today="2026-10-26")
    b = paper.load()["books"]["TW_risk"]
    init = paper.RULES["initial"]["TW"]
    # 上限以交易前的帳戶價值計算(買進手續費讓交易後的權重多出約 0.04%)
    w = {s: p["shares"] * 100.0 / init for s, p in b["positions"].items()}
    assert len(w) == 12 and b["cash"] >= 0
    assert max(w.values()) <= portfolio.RISK["max_weight"] + 1e-9
    assert sum(w[f"{1000 + i}.TW"] for i in range(5)) <= portfolio.RISK["max_sector"] + 1e-9
    assert r["value"] == pytest.approx(init - sum(t["fee"] for t in b["trades"]), rel=1e-9)   # 價格不變:只少了手續費
    assert b["risk_note"]["est_vol"] <= portfolio.RISK["vol_target"] + 1e-9


def test_vol_target_holds_cash(book_path):
    paper.update_risk("TW", _rows(12, vol=0.9), None, today="2026-10-26")
    b = paper.load()["books"]["TW_risk"]
    assert b["risk_note"]["scale"] < 1
    assert b["cash"] > 0.3 * paper.RULES["initial"]["TW"]


def test_monthly_rebalance_and_sell_trigger(book_path):
    paper.update_risk("TW", _rows(), None, today="2026-10-01")
    n0 = len(paper.load()["books"]["TW_risk"]["trades"])
    rows = _rows()
    rows[0]["lt_score"] = -100                                         # 排名改變,但還沒到再平衡日
    rows[1].update(red_flags=["Altman"])                               # 紅旗:隨時賣
    r = paper.update_risk("TW", rows, None, today="2026-10-10")
    assert [t["symbol"] for t in r["actions"]] == ["1001.TW"] and r["actions"][0]["side"] == "賣出"
    b = paper.load()["books"]["TW_risk"]
    assert len(b["trades"]) == n0 + 1 and b["last_rebalance"] == "2026-10-01"
    r = paper.update_risk("TW", rows, None, today="2026-10-30")       # 29 天後:再平衡
    assert paper.load()["books"]["TW_risk"]["last_rebalance"] == "2026-10-30"


def test_tr_level_survives_dividend_rescale():
    book = {}
    d = pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-03"])
    s1 = pd.Series([100.0, 102.0, 104.0], index=d)
    assert paper.tr_level(book, s1, "2026-10-01") == 1.0
    book["bench_tr"] = {"date": "2026-10-02", "level": 1.02}
    # 10-03 除息後還原價整段往下重算(乘 0.97),但相鄰兩天的比值不變
    s2 = s1 * 0.97
    assert paper.tr_level(book, s2, "2026-10-03") == pytest.approx(1.02 * 104 / 102)
    # 接不上上次日期(停了很久):從這裡重新接,水準不變,標出中斷的期間
    book2 = {"bench_tr": {"date": "2026-09-01", "level": 1.1}}
    assert paper.tr_level(book2, s1, "2026-10-03") == 1.1
    assert book2["bench_tr"] == {"date": "2026-10-03", "level": 1.1} and book2["bench_gap"]["from"] == "2026-09-01"
    assert paper.tr_level({}, None, "2026-10-03") is None


def test_tr_level_with_lagged_series():
    # Verifier38 問題 1:10-03 呼叫時序列只到 10-02;隔天拿到完整序列,10-02 → 10-04 的報酬不能漏掉
    book = {}
    d = pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"])
    full = pd.Series([100.0, 101.0, 110.0, 111.0], index=d)
    assert paper.tr_level(book, full.iloc[:2], "2026-10-03") == 1.0
    assert book["bench_tr"]["date"] == "2026-10-02"
    assert paper.tr_level(book, full, "2026-10-04") == pytest.approx(111 / 101)


def test_summary_includes_c(book_path):
    paper.update_risk("US", [{**r, "symbol": r["symbol"].replace(".TW", "")} for r in _rows(3)], None, today="2026-10-26")
    sm = paper.summary()
    assert sm["rules_risk"] and sm["books"]["US_risk"]["bench_name"] == "SPY 含息"
    assert sm["books"]["US_risk"]["risk_note"]["gross"] == pytest.approx(0.3, abs=0.01)
