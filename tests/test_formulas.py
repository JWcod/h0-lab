"""公式單元測試:確認每個計算跟教科書定義一致(不連網)。"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import resolver, snapshots  # noqa: E402
from lab.analysis import financials as FN  # noqa: E402
from lab.analysis import fundamentals as FU  # noqa: E402
from lab.analysis import scoring as SC  # noqa: E402
from lab.analysis import technical as TE  # noqa: E402
from lab.sources import finmind, news  # noqa: E402
from lab.valuation import dcf as D  # noqa: E402
from lab.valuation import model as VM  # noqa: E402


def test_dcf_matches_gordon_when_growth_constant():
    # 成長率一路都是 g、利潤率固定時,DCF 應等於 Gordon:FCF1 / (r − g)
    rev0, g, m, r, n = 1000.0, 0.03, 0.2, 0.09, 10
    v = D.dcf_value(rev0, g, m, r, g, n, net_debt=0, shares=1)
    fcf1 = rev0 * (1 + g) * m
    assert v["per_share"] == pytest.approx(fcf1 / (r - g), rel=1e-9)


def test_dcf_net_debt_and_shares():
    v1 = D.dcf_value(1000, 0.05, 0.2, 0.09, 0.02, 10, net_debt=0, shares=10)
    v2 = D.dcf_value(1000, 0.05, 0.2, 0.09, 0.02, 10, net_debt=100, shares=10)
    assert v1["per_share"] - v2["per_share"] == pytest.approx(10.0)


def test_reverse_dcf_roundtrip():
    v = D.dcf_value(1000, 0.12, 0.2, 0.09, 0.02, 10, 0, 10)
    g = D.reverse_dcf(v["per_share"], 1000, 0.2, 0.09, 0.02, 10, 0, 10)
    assert g == pytest.approx(0.12, abs=1e-6)


def test_dcf_rejects_wacc_below_growth():
    assert D.dcf_value(1000, 0.05, 0.2, 0.03, 0.025, 10, 0, 1) is None


def test_monte_carlo_centered():
    mc = D.monte_carlo(1000, 0.05, 0.2, 0.09, 0.02, 10, 0, 1, draws=4000)
    base = D.dcf_value(1000, 0.05, 0.2, 0.09, 0.02, 10, 0, 1)["per_share"]
    assert mc["p10"] < base < mc["p90"]


def test_touch_probability_edges():
    assert VM.touch_probability(100, 100, 0.3, 1) == pytest.approx(1.0, abs=1e-6)
    far = VM.touch_probability(100, 400, 0.2, 1)
    near = VM.touch_probability(100, 110, 0.2, 1)
    assert 0 <= far < near <= 1


def test_scenarios_neutral_and_convergence():
    s = VM.scenarios(100, 100, 0.08, 0.08, 0.3)
    assert s["12m"]["center"] == pytest.approx(100)
    s2 = VM.scenarios(100, 200, 0.0, 0.0, 0.3)
    assert s2["12m"]["center"] == pytest.approx(100 * 2 ** (1 / 3))
    assert s2["12m"]["low90"] < s2["12m"]["low68"] < s2["12m"]["center"] < s2["12m"]["high68"] < s2["12m"]["high90"]


def test_pb_roe():
    # ROE = COE 時,合理 P/B = 1
    assert D.pb_roe(0.1, 0.1, 0.02, 50) == pytest.approx(50)


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert TE.rsi(up).iloc[-1] > 99
    dn = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert TE.rsi(dn).iloc[-1] < 1


def test_max_drawdown():
    s = pd.Series([100, 120, 60, 90, 130])
    assert TE.max_drawdown(s) == pytest.approx(-0.5)


def test_decumulate_cashflow():
    idx = pd.to_datetime(["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"])
    w = pd.DataFrame({"cfo": [10, 25, 45, 70, 12]}, index=idx)
    q = finmind.decumulate(w)
    assert q["cfo"].tolist() == [10, 15, 20, 25, 12]


def test_decumulate_missing_quarter_is_nan():
    idx = pd.to_datetime(["2025-03-31", "2025-09-30"])
    q = finmind.decumulate(pd.DataFrame({"cfo": [10, 45]}, index=idx))
    assert q["cfo"].iloc[0] == 10 and math.isnan(q["cfo"].iloc[1])


def test_ttm_requires_consecutive():
    idx = pd.to_datetime(["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"])
    q = pd.DataFrame({"revenue": [1.0, 2, 3, 4], "equity": [5.0, 6, 7, 8]}, index=idx)
    t = FN.ttm(q)
    assert t["revenue"] == 10 and t["equity"] == 8
    q2 = q.drop(pd.Timestamp("2025-06-30"))
    assert FN.ttm(q2) is None


def test_altman_z_formula():
    src = {"total_assets": 100, "total_liabilities": 50, "current_assets": 40, "current_liabilities": 20,
           "retained_earnings": 30, "ebit": 10, "revenue": 120}
    z, parts = FU.altman_z(src, market_cap=100)
    expect = 1.2 * 0.2 + 1.4 * 0.3 + 3.3 * 0.1 + 0.6 * 2.0 + 1.0 * 1.2
    assert z == pytest.approx(expect)


def test_piotroski_perfect_score():
    idx = pd.to_datetime(["2023-12-31", "2024-12-31", "2025-12-31"])
    a = pd.DataFrame({
        "net_income": [5, 10, 20], "total_assets": [100, 100, 100], "cfo": [6, 12, 30], "long_term_debt": [30, 20, 10],
        "current_assets": [40, 40, 50], "current_liabilities": [30, 30, 25], "shares_diluted": [10, 10, 9.5],
        "gross_profit": [30, 32, 40], "revenue": [100, 100, 110],
    }, index=idx)
    assert FU.piotroski(a)["score"] == 9


def test_lin_and_grade():
    assert SC.lin(0.5, 0, 1) == 50
    assert SC.lin(2, 0, 1) == 100
    assert SC.lin(0.1, 0.1, 0.0) == 0  # 越低越好的方向
    assert SC.grade(91) == "A+" and SC.grade(25) == "F"


def test_month_revenue_record_flags():
    months = pd.date_range("2023-01-01", periods=30, freq="MS")
    df = pd.DataFrame({"revenue_year": months.year, "revenue_month": months.month,
                       "revenue": np.linspace(100, 200, 30)})
    r = FU.month_revenue(df)
    assert r["record_high"] and r["yoy"] > 0 and r["yoy_streak"] > 0


def test_symbol_validation_blocks_traversal():
    assert resolver.valid_symbol("2330.TW") and resolver.valid_symbol("BRK-B")
    assert not resolver.valid_symbol("../../etc/passwd")
    assert not resolver.valid_symbol("AAPL;rm")
    with pytest.raises(ValueError):
        snapshots._dir("../x")


def test_learning_ic_detects_signal():
    rows = []
    for i in range(20):
        rows.append({"symbol": f"S{i}", "date": "2026-01-01", "days": 60, "excess": i / 100, "lt_level": 3 if i > 10 else 1,
                     "lt_score": i, "st_score": 50, "pillars": {"momentum": i * 5, "quality": 50 + (i % 3)}})
    L = snapshots.learning(rows)
    mom = next(p for p in L["pillars"] if p["key"] == "momentum")
    assert L["ready"] and mom["ic"] == pytest.approx(1.0)
    assert L["buckets"][0]["avg_excess"] > L["buckets"][-1]["avg_excess"]


def test_snapshot_diff():
    prev = {"date": "2026-10-01", "price": 100, "fair_value": 120, "lt_score": 60, "st_score": 50, "lt_rating": "持有",
            "st_signal": "中性", "pillars": {"quality": 70, "momentum": 40}}
    cur = {"price": 110, "fair_value": 121, "lt_score": 70, "st_score": 51, "lt_rating": "買進", "st_signal": "中性",
           "pillars": {"quality": 71, "momentum": 60}}
    d = snapshots.diff(prev, cur)
    labels = [c["label"] for c in d["changes"]]
    assert "長期分數" in labels and "技術動能" in labels and "品質" not in labels and "短期分數" not in labels
    assert d["rating_change"] == {"from": "模型等級 C", "to": "模型等級 B"} and "signal_change" not in d


def test_notes_reject_bad_symbol():
    from lab import notes
    with pytest.raises(ValueError):
        notes.load("../../etc/passwd")


def test_paper_trading_buy_then_sell(tmp_path, monkeypatch):
    from lab import paper
    monkeypatch.setattr(paper, "PATH", tmp_path / "paper.json")
    rows = [{"symbol": "2330.TW", "name": "台積電", "price": 1000.0, "lt_level": 3, "lt_rating": "買進", "st_code": 1,
             "st_signal": "偏多", "confidence": 80, "red_flags": [], "lt_score": 80, "st_score": 70, "max_position_pct": 0.2}]
    r = paper.update("TW", rows, 20000)
    assert r["actions"][0]["side"] == "買進"
    pos = paper.load()["books"]["TW"]["positions"]["2330.TW"]
    assert 0 < pos["shares"] * 1000 <= 1_000_000 * 0.10  # 單檔不超過 10%
    rows[0].update({"lt_level": 1, "lt_rating": "減碼", "price": 1100.0})
    r2 = paper.update("TW", rows, 21000)
    assert r2["actions"][0]["side"] == "賣出" and r2["actions"][0]["pnl"] > 0
    assert "2330.TW" not in paper.load()["books"]["TW"]["positions"]


def test_news_sentiment():
    s, pos, neg = news.score_title("Nvidia beats estimates, raises guidance")
    assert s > 0
    s2, _, _ = news.score_title("台積電法說會後重挫 外資大砍目標價下修")
    assert s2 < 0
    s3, _, neg3 = news.score_title("聯發科第四季營收成長趨緩")
    assert s3 < 0 and "成長趨緩" in neg3
    s4, _, _ = news.score_title("鴻海 9 月營收創新高 優於預期")
    assert s4 > 0
