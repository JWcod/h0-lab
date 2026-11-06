"""復盤的前瞻樣本外儀表板(lab/snapshots.forward_stats,階段 4.4):起點、去重、到期、美股不算命中率、樣本不足不給區間。"""
from __future__ import annotations

import pandas as pd
import pytest

from lab import snapshots as S


def _series(prices: dict):
    idx = pd.bdate_range("2026-01-01", "2028-01-31")
    data = {k: pd.Series([f(i) for i in range(len(idx))], index=idx, dtype=float) for k, f in prices.items()}
    return lambda sym: data.get(sym, pd.Series(dtype=float))


def _snap(sym, date, lvl=3, market="TW", fwd=True, **kw):
    return {"symbol": sym, "date": date, "lt_level": lvl, "market": market, "forward_record": fwd,
            "benchmark": "^TWII" if market == "TW" else "^GSPC", **kw}


def test_wilson():
    lo, hi = S.wilson(15, 30)
    assert lo < 0.5 < hi and hi - lo == pytest.approx(0.34, abs=0.02)
    assert S.wilson(0, 0) is None


def test_forward_filters_and_horizons():
    ser = _series({"A.TW": lambda i: 100 * 1.001 ** i, "B.TW": lambda i: 100 * 0.999 ** i, "^TWII": lambda i: 100.0,
                   "AAPL": lambda i: 100 * 1.001 ** i, "^GSPC": lambda i: 100.0})
    snaps = [
        _snap("A.TW", "2026-10-01"),                  # 前瞻起點以前:不算
        _snap("A.TW", "2026-11-02"), _snap("A.TW", "2026-11-20"),   # 同一個月只取最早一筆
        _snap("B.TW", "2026-11-03", lvl=1),           # 看空、之後真的跌:命中
        _snap("B.TW", "2026-12-01", lvl=2),           # 持有:不算方向
        _snap("AAPL", "2026-11-05", market="US", info_only=True),   # 美股:算超額報酬檔數,不算命中率
        _snap("A.TW", "2027-01-04", fwd=False),       # 不是正式前瞻紀錄
    ]
    out = S.forward_stats(snaps, ser, today="2027-03-01")
    assert out["start"] == S.FORWARD_START and out["n_snapshots"] == 4
    h = {x["key"]: x for x in out["horizons"]}
    assert h["1m"]["n_matured"] == 4 and h["1m"]["n_us"] == 1 and h["1m"]["n_dir"] == 2
    assert h["1m"]["hit_rate"] == 1.0
    assert h["1m"]["mean_excess"] < h["1m"]["us_mean_excess"]      # 台股、美股分開平均(Verifier38 問題 6)
    assert h["1m"]["thin"] and h["1m"]["hit_ci"] is None and h["1m"]["excess_ci"] is None   # 樣本不足不給區間
    assert h["12m"]["n_matured"] == 0 and h["12m"]["hit_rate"] is None                       # 還沒到期


def test_forward_ci_when_enough():
    ser = _series({**{f"S{i}.TW": (lambda i: (lambda j: 100 * (1.001 if i % 3 else 0.999) ** j))(i) for i in range(40)},
                   "^TWII": lambda i: 100.0})
    snaps = [_snap(f"S{i}.TW", "2026-11-02") for i in range(40)]
    h = S.forward_stats(snaps, ser, today="2027-01-01")["horizons"][0]
    assert h["n_dir"] == 40 and not h["thin"]
    lo, hi = h["hit_ci"]
    assert lo < h["hit_rate"] < hi
    assert h["excess_ci"][0] < h["mean_excess"] < h["excess_ci"][1]
