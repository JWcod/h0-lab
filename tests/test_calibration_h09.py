"""P2-H09:2 參數 logistic 校準(walk-forward,不偷看)與 Brier 比較。"""
import numpy as np
import pandas as pd
import pytest

from lab.analysis import calibration as CAL


def _panel(seed=0, months=60, n=40):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2015-01-31", periods=months, freq="ME")
    rows = []
    for d in dates:
        s = rng.standard_normal(n)
        rank = pd.Series(s).rank(pct=True).to_numpy()
        y = (rng.random(n) < 1 / (1 + np.exp(-(-0.5 + 1.0 * rank)))).astype(float)
        for i in range(n):
            rows.append({"date": d, "exit": d + pd.DateOffset(months=12), "rank": rank[i], "y": y[i]})
    return pd.DataFrame(rows)


def test_fit_logistic_recovers_parameters():
    rng = np.random.default_rng(1)
    x = rng.random(20000)
    y = (rng.random(20000) < CAL.prob(-0.5, 1.0, x)).astype(float)
    a, b = CAL.fit_logistic(x, y)
    assert a == pytest.approx(-0.5, abs=0.08) and b == pytest.approx(1.0, abs=0.15)
    assert CAL.fit_logistic(x, np.ones_like(x)) is None


def test_walk_forward_uses_only_known_outcomes():
    df = _panel()
    wf = CAL.walk_forward(df, min_train=200)
    d = pd.Timestamp("2018-06-30")
    first = wf.loc[wf["p"].notna(), "date"].min()
    # 第一個有機率的日期:之前已出場的樣本 ≥ 200(12 個月後才開始有結果)
    assert first >= pd.Timestamp("2016-01-31")
    # 竄改:把 d 之後才出場的結果全部反轉 → d 以前(含 d)的機率完全不變
    t = df.copy()
    fut = t["exit"] > d
    t.loc[fut, "y"] = 1 - t.loc[fut, "y"]
    wt = CAL.walk_forward(t, min_train=200)
    m = wf["date"] <= d
    pd.testing.assert_series_equal(wf.loc[m, "p"], wt.loc[m, "p"])
    assert not wf.loc[~m, "p"].equals(wt.loc[~m, "p"])          # 對照:之後的日期確實會變


def test_bucket():
    assert CAL.bucket(0.57) == "55–60%" and CAL.bucket(None) is None
