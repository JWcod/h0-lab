"""因子實驗室只算開發集(階段 3 D0 稽核 B2b):結果月份 ≥ holdout 起點的資料不得計算或回傳。

用合成股價(2018–2026)測:①回傳的日期與曲線都在邊界之前;②竄改 holdout 期間的股價,輸出完全不變。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from lab.research import factors
from lab.research.splits import HOLDOUT_START


def _synthetic(seed: int = 0, tamper: bool = False):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", "2026-09-30")
    n = 24
    rets = rng.normal(0.0004, 0.02, size=(len(idx), n))
    close = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=idx, columns=[f"S{i}" for i in range(n)])
    vol = pd.DataFrame(rng.integers(1_000, 50_000, size=(len(idx), n)).astype(float), index=idx, columns=close.columns)
    if tamper:   # holdout 期間的股價亂改:輸出不該有任何變化
        m = close.index >= HOLDOUT_START
        close.loc[m] = close.loc[m] * rng.uniform(0.2, 5.0, size=(m.sum(), n))
        vol.loc[m] = vol.loc[m] * 10
    return close, vol


def _run(monkeypatch, tamper: bool, members=None) -> dict:
    close, vol = _synthetic(tamper=tamper)
    return factors.compute("synthetic", "合成", close, vol, members)


def test_factor_lab_never_reaches_holdout(monkeypatch):
    res = _run(monkeypatch, tamper=False)
    assert res["split"] == "dev" and res["holdout_start"] == str(HOLDOUT_START.date())
    last = pd.Timestamp(res["dates"][-1] + "-01")
    assert last < HOLDOUT_START - pd.DateOffset(months=1) + pd.offsets.MonthEnd(0)   # 最後一個判斷月的結果月份在邊界前
    for f in res["factors"]:
        assert len(f["cum_ls"]) == len(f["rolling_ic"]) == len(res["dates"])
        assert f["recent_end"] is None or f["recent_end"] < f"{HOLDOUT_START:%Y-%m}"
    assert any("只用開發期" in c for c in res["caveats"])


def test_factor_lab_ignores_holdout_prices(monkeypatch):
    a = _run(monkeypatch, tamper=False)
    b = _run(monkeypatch, tamper=True)
    for k in ("computed",):
        a.pop(k), b.pop(k)
    assert a == b


def test_holm():
    assert factors.holm([0.01, 0.04, None, 0.03]) == [0.03, 0.06, None, 0.06]
    assert factors.holm([0.5, 0.9]) == [1.0, 1.0]
    assert factors.holm([]) == []


def test_multiple_testing_fields(monkeypatch):
    res = _run(monkeypatch, tamper=False)
    assert res["multiple_testing"]["method"] == "Holm" and res["multiple_testing"]["m"] == len(factors.FACTORS)
    for f in res["factors"]:
        assert f["holm_p"] >= f["nw_p"] and isinstance(f["holm_sig"], bool)
    # 純雜訊的合成股價:7 個因子校正後都不該顯著
    assert not any(f["holm_sig"] for f in res["factors"])


def test_pit_membership_restricts_cross_section(monkeypatch):
    close, vol = _synthetic()
    months = sorted({d.to_period("M") for d in close.index})
    half = {m: set(close.columns[:12]) for m in months}
    a = factors.compute("synthetic", "合成", close, vol, half)
    b = factors.compute("synthetic", "合成", close[close.columns[:12]], vol[close.columns[:12]], None)
    for k in ("computed",):
        a.pop(k), b.pop(k)
    a["caveats"], b["caveats"] = a["caveats"][2:], b["caveats"][2:]   # 第 2 條說明股票池的偏誤,兩者本來就不同
    assert a["factors"] == b["factors"]             # 不在當月名單裡的股票完全不影響結果
