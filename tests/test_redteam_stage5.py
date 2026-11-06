"""階段 5 處理的延後 Red-team 項目:L4(只綁本機)、M5(閘門差異序列 AR(1) 警告)。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from lab.research import evaluate as E

ROOT = Path(__file__).resolve().parent.parent


def _import_config(host: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "INVEST_LAB_HOST": host, "INVEST_LAB_OFFLINE": "1"}
    return subprocess.run([sys.executable, "-c", "import lab.config"], cwd=ROOT, env=env, capture_output=True, text=True)


def test_host_must_be_local():
    for bad in ("0.0.0.0", "192.168.1.5", "::"):
        r = _import_config(bad)
        assert r.returncode != 0 and "只能綁本機" in r.stderr
    for ok in ("127.0.0.1", "localhost"):
        assert _import_config(ok).returncode == 0


def test_ar1():
    rng = np.random.default_rng(0)
    e = rng.standard_normal(2000)
    x = np.zeros_like(e)
    for i in range(1, len(e)):
        x[i] = 0.8 * x[i - 1] + e[i]
    assert abs(E.ar1(x) - 0.8) < 0.05
    assert abs(E.ar1(e)) < 0.06
    assert E.ar1([1, 2, 3]) is None and E.ar1([1, 1, 1, 1, 1]) is None


def _panel(scores):
    import pandas as pd
    return pd.DataFrame({"date": pd.Timestamp("2020-01-31"), "symbol": [f"S{i:02d}" for i in range(len(scores))], "_score": scores})


def test_fixed_coverage_ties_and_coverage():
    # 20 檔全部同分:平均名次會讓 20 檔全部落在中間(覆蓋率 0);打散後剛好 4 檔看多、4 檔看空
    d = E.fixed_coverage_flags(_panel([50.0] * 20), "t")
    assert int(d["_fbull"].sum()) == 4 and int(d["_fbear"].sum()) == 4
    # 同一個 seed 結果固定
    assert d.equals(E.fixed_coverage_flags(_panel([50.0] * 20), "t"))
    # 缺分數的列不參與排名
    d = E.fixed_coverage_flags(_panel([float(i) for i in range(10)] + [float("nan")] * 5), "t")
    assert int(d["_fbull"].sum()) == 2 and int(d["_fbear"].sum()) == 2
    assert not d.loc[d["_score"].isna(), ["_fbull", "_fbear"]].any().any()


def test_fixed_coverage_ranks_before_dropping_missing_returns():
    # 切點由全部有判斷的列決定:之後沒有報酬的列(例如下市)也要先算進排名
    scores = [float(i) for i in range(10)]
    d = E.fixed_coverage_flags(_panel(scores), "t")
    assert d.loc[d["_score"] >= 8, "_fbull"].all() and int(d["_fbull"].sum()) == 2


def test_winsorize_and_spread_w():
    x = np.array([0.0] * 98 + [5.0, -0.9])
    w = E.winsorize(x)
    assert w.max() < 5.0 and w.min() > -0.9 and np.median(w) == 0.0
    # 合成:分數最高的一檔有 +500% 的極端報酬,截尾後價差應該明顯變小
    rng = np.random.default_rng(1)
    J, N = 30, 60
    S = rng.standard_normal((J, N))
    R = rng.normal(0, 0.05, (J, N))
    top = S.argmax(axis=1)
    R[np.arange(1, J), top[:-1]] = 5.0
    out = E.cohort_series(S, R, 1, np.ones(J, dtype=bool))
    assert (out["spread_w"].dropna() < out["spread"].dropna()).all()


def test_delisted_codes_union_and_fallback(monkeypatch, tmp_path):
    import pandas as pd

    from lab import cache
    from lab.research import backtest_pit as BP
    f = tmp_path / "dl.pkl"
    f.write_bytes(b"x")
    monkeypatch.setattr(BP, "_delisted_cache", None)
    monkeypatch.setattr(cache, "_path", lambda ns, key: f)
    monkeypatch.setattr(cache, "_read", lambda p: {"t": 0, "v": pd.DataFrame({"stock_id": ["9999", "2311"]})})
    assert BP.delisted_codes() == set(BP.DELISTED) | {"9999"}       # 快取過期也照讀,不連網
    monkeypatch.setattr(BP, "_delisted_cache", None)
    monkeypatch.setattr(cache, "_read", lambda p: None)
    assert BP.delisted_codes() == set(BP.DELISTED)                  # 沒有快取:退回已知清單
    assert BP._delisted_cache is None                               # 而且不記住退回的結果


def test_fixed_coverage_symmetric():
    # Verifier39 問題 1:看多、看空名額一樣多
    for n in range(1, 120):
        d = E.fixed_coverage_flags(_panel([float(i) for i in range(n)]), "t")
        k = int(n * E.FIXED_COVERAGE + 1e-9)
        assert int(d["_fbull"].sum()) == int(d["_fbear"].sum()) == k, n


def test_hit_rates_rank_before_dropping_missing(monkeypatch):
    # Verifier39 問題 2:_hit_rates 必須把「之後沒有報酬」的列也送進排名
    import pandas as pd
    dates = [pd.Timestamp("2020-01-31")]
    rows = pd.DataFrame({"date": dates[0], "symbol": [f"S{i:02d}" for i in range(10)], "lt_level": 2.0,
                         "lt_score": [float(i) for i in range(10)], "st_code": 0.0, "st_score": 50.0})
    ex = rows[["date", "symbol"]].assign(x=[0.01] * 8 + [float("nan")] * 2)   # 分數最高的兩檔之後沒有報酬
    seen = {}
    real = E.fixed_coverage_flags

    def spy(d, name):
        seen["n"] = len(d)
        return real(d, name)

    class Ctx:
        def dates_in(self, H, split):
            return dates
    monkeypatch.setattr(E, "_excess", lambda ctx, hz: ex)
    monkeypatch.setattr(E, "fixed_coverage_flags", spy)
    monkeypatch.setattr(E, "block_bootstrap", lambda *a, **k: None)
    out = E._hit_rates(Ctx(), rows, "12m", "dev", "TW", "lt")
    assert seen["n"] == 10                                  # 排名時 10 列都在
    assert out["fixed_coverage_20"]["right"] + out["fixed_coverage_20"]["wrong"] + out["fixed_coverage_20"]["ties"] == 2
