"""台股 PIT 管線(lab/pit/build_tw.py):法定期限可用日、股數基準換算、竄改測試、緯穎除權前本益比歸 1。

需要凍結資料的測試會用子程序(離線模式 + 凍結快取)建一份小 store,沒有凍結資料就 skip。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lab import config
from lab.pit import PITStore
from lab.pit import build_tw as B
from lab.pit.tamper import assert_pit_invariant

T = pd.Timestamp
ROOT = Path(__file__).resolve().parents[1]
CODES = ["6669", "2330", "2327", "2395"]


def _frozen() -> Path | None:
    env = os.environ.get("INVEST_LAB_DATA_DIR")
    for p in ([Path(env)] if env else []) + [config.MAIN_ROOT / "data" / "frozen" / "v1.1_2026-10-15"]:
        if (p / "freeze_date.txt").exists() and (p / "cache").exists():
            return p
    return None


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    frozen = _frozen()
    if frozen is None:
        pytest.skip("沒有凍結資料(data/ 不進 git)")
    tmp = tmp_path_factory.mktemp("tw_pit")
    env = dict(os.environ, INVEST_LAB_OFFLINE="1", INVEST_LAB_DATA_DIR=str(frozen), INVEST_LAB_OUT_DIR=str(tmp / "out"),
               INVEST_LAB_PRIVATE_DIR=str(tmp / "private_fake"), INVEST_LAB_RECORD="0")
    r = subprocess.run([sys.executable, "-m", "lab.pit.build_tw", "--codes", ",".join(CODES), "--out", str(tmp / "pit.sqlite"),
                        "--no-quality", "--per-json", str(tmp / "per.json")], cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    store = PITStore.load(tmp / "pit.sqlite")
    per = {c: pd.Series(v).rename(index=pd.Timestamp).sort_index() for c, v in
           json.loads((tmp / "per.json").read_text(encoding="utf-8")).items()}
    return store, per


# ---------------------------------------------------------------- 不需要資料

def test_statutory_deadlines():
    assert B.statutory_deadline("2024-03-31", False) == T("2024-05-15")
    assert B.statutory_deadline("2024-06-30", False) == T("2024-08-14")
    assert B.statutory_deadline("2024-09-30", False) == T("2024-11-14")
    assert B.statutory_deadline("2024-12-31", False) == T("2025-03-31")
    assert B.statutory_deadline("2024-06-30", True) == T("2024-08-31")
    assert B.statutory_deadline("2024-03-31", True) == T("2024-05-31")
    assert B.statutory_deadline("2024-06-30", False, foreign=True) == T("2024-08-31")


def test_next_trading_day_is_strictly_after_deadline():
    cal = pd.bdate_range("2024-01-01", "2026-12-31").drop([T("2024-05-16")])  # 假裝 5/16 休市
    assert B.next_trading_day(cal, "2024-05-15") == T("2024-05-17")
    assert B.next_trading_day(cal, "2024-08-14") == T("2024-08-15")
    assert B.next_trading_day(cal, "2026-10-10") == T("2026-10-12")  # 週六 → 週一
    # 期限落在非交易日:先順延到下一個交易日(週一),再加 1 個交易日
    assert B.deadline_available_at("2026-10-10", cal) == T("2026-10-13")
    assert B.month_revenue_available_at(2026, 9, cal) == T("2026-10-13")
    assert B.quarter_available_at("2021-06-30", False, cal) == T("2021-08-17")  # 8/14 週六 → 8/16 → 8/17
    assert B.quarter_available_at("2024-06-30", False, cal) == T("2024-08-15")  # 8/14 週三 → 8/15
    # 日曆範圍外退回平日曆
    assert B.next_trading_day(cal, "2013-05-15") == T("2013-05-16")


def test_share_basis_detects_eps_filed_before_split():
    """面額變更 1 拆 4 發生在期末後、法定期限前:財報若在分割前就通過(EPS 舊基準),要換到 available_at 基準。"""
    idx = pd.DatetimeIndex(["2025-03-31", "2025-06-30", "2025-09-30"])
    q = pd.DataFrame({"eps_diluted": [10.0, 10.0, 2.5], "net_income": [1000.0, 1000.0, 1000.0],
                      "shares_out": [100.0, 100.0, 100.0]}, index=idx)  # 股本 ÷ 10 不變(面額變更)
    ev = pd.DataFrame({"entity": "X", "date": [T("2025-08-14")], "kind": ["split"], "ratio": [4.0]})
    avail = pd.Series([T("2025-05-16"), T("2025-08-15"), T("2025-11-17")], index=idx)
    audit = B.EntityAudit("X", par_events=[(T("2025-08-14"), 4.0)])
    out = B._normalize_share_basis(q, ev, avail, audit)
    assert out.loc["2025-06-30", "eps_diluted"] == pytest.approx(2.5)  # 舊基準 10 → available_at(分割後)基準 2.5
    assert out.loc["2025-06-30", "shares_out"] == pytest.approx(400.0)
    assert out.loc["2025-09-30", "eps_diluted"] == pytest.approx(2.5)  # 分割後財報本來就是新基準
    assert out.loc["2025-09-30", "shares_out"] == pytest.approx(400.0)
    assert out.loc["2025-03-31", "eps_diluted"] == pytest.approx(10.0)  # 分割前公布,維持當時基準
    assert out.loc["2025-06-30", "eps_as_reported"] == 10.0


# ---------------------------------------------------------------- 凍結資料

CASES = [("6669", "2026-08-31"), ("6669", "2026-09-02"), ("6669", "2026-09-30"),
         ("2330", "2019-06-28"), ("2330", "2022-10-31"), ("2330", "2025-08-29"),
         ("2327", "2017-09-29"), ("2327", "2022-10-31"), ("2327", "2025-08-29")]


@pytest.mark.parametrize("code,d", CASES)
def test_tw_fdat_asof_is_pit(built, code, d):
    store, _ = built
    assert_pit_invariant(lambda v: B.tw_fdat_asof(v, code), store, d)


@pytest.mark.parametrize("code,d", CASES)
def test_price_asof_is_pit(built, code, d):
    store, _ = built
    assert_pit_invariant(lambda v: B.price_asof(v, code), store, d)


def test_fdat_structure_matches_backtest(built):
    store, _ = built
    d = T("2024-06-28")
    f = B.tw_fdat_asof(store.as_of(d), "2330")
    assert set(f) == {"q", "mr", "pe"}
    assert list(f["q"].columns) == B.Q_COLS + ["_avail"]
    assert (f["q"]["_avail"] <= d).all() and (f["mr"]["_avail"] <= d).all()
    assert {"revenue", "revenue_year", "revenue_month"} <= set(f["mr"].columns)
    assert f["pe"].index.max() <= d and f["pe"].dropna().between(5, 60).all()
    px = B.price_asof(store.as_of(d), "2330")
    assert {"Open", "High", "Low", "Close", "Adj Close", "Volume"} <= set(px.columns) and px.index.max() <= d


def test_6669_pe_matches_twse_before_ex_rights(built):
    """Verifier 的 0.335 案例:除權(2026-09-02,股數 × 2.98)前,split 調整價 ÷ TTM EPS 要和 TWSE 本益比一致。"""
    store, per = built
    d = T("2026-08-31")
    v = store.as_of(d)
    close = float(v.prices("6669")["close"].iloc[-1])
    ttm = float(B.ttm_eps_steps(v, "6669").iloc[-1])
    ratio = (close / ttm) / per["6669"].loc[d]
    assert 0.9 <= ratio <= 1.1, ratio


def test_6669_stock_dividend_only_applied_after_it_happens(built):
    store, _ = built
    before = store.as_of("2026-08-31")
    after = store.as_of("2026-09-30")
    raw_0831 = float(before.prices("6669", adjust="none")["close"].loc["2026-08-31"])
    assert raw_0831 == pytest.approx(7095, rel=1e-3)  # 原始成交價(Yahoo 已往回除以 2.98,還原回來)
    assert float(before.prices("6669")["close"].loc["2026-08-31"]) == pytest.approx(raw_0831)
    r = before.prices("6669")["close"].iloc[-1] / after.prices("6669")["close"].loc["2026-08-31"]
    assert r == pytest.approx(2.982795, rel=1e-4)
    qa, qb = B.quarterly_asof(after, "6669"), B.quarterly_asof(before, "6669")
    assert qb.loc["2026-06-30", "eps_diluted"] / qa.loc["2026-06-30", "eps_diluted"] == pytest.approx(2.982795, rel=1e-4)
    assert qa.loc["2026-06-30", "shares_out"] / qb.loc["2026-06-30", "shares_out"] == pytest.approx(2.982795, rel=1e-4)


def test_2327_par_change_eps_and_shares_basis(built):
    """國巨 2025-08-14 面額 10 → 2.5 元(1 拆 4):Q2 財報在分割前通過(舊基準),股本 ÷ 10 也不反映分割。"""
    store, _ = built
    q = B.quarterly_asof(store.as_of("2025-12-31"), "2327")
    assert q.loc["2025-06-30", "eps_diluted"] == pytest.approx(9.74 / 4, rel=1e-3)
    assert q.loc["2025-09-30", "eps_diluted"] == pytest.approx(3.10, rel=1e-3)
    sh = q.loc["2025-09-30", "shares_out"]
    assert 2.0e9 < sh < 2.1e9
    # 每季淨利 ÷ 股數 ≈ EPS(同一基準)
    eps_impl = q["net_income"] / q["shares_out"]
    assert np.allclose(eps_impl.loc["2025-06-30":] / q["eps_diluted"].loc["2025-06-30":], 1, atol=0.05)


def test_basis_at_not_filed_drives_conversion(built):
    """規約:每股數字一律用 basis_at 換算;就算某筆 filed 比 available_at 早,結果也不能變(國巨 2025 Q2 曾被多除一次 4)。"""
    store, _ = built
    d = "2025-12-31"
    base = B.quarterly_asof(store.as_of(d), "2327")
    f = store.facts.copy()
    m = (f["entity"] == "2327") & (f["period_end"] == T("2025-06-30"))
    f.loc[m, "filed"] = T("2025-08-07")  # 分割(2025-08-14)前申報
    s2 = PITStore(f, store.prices, store.events, store.vintage)
    pd.testing.assert_frame_equal(base, B.quarterly_asof(s2.as_of(d), "2327"))
    assert base.loc["2025-06-30", "eps_diluted"] == pytest.approx(9.74 / 4, rel=1e-3)


def test_small_stock_dividend_old_basis_detected(built):
    """研華 2020-07-31 配股 × 1.0999,Q2 財報在除權前通過(舊基準):EPS 要再除以 1.0999,不能當成已追溯。"""
    store, _ = built
    q = B.quarterly_asof(store.as_of("2020-09-30"), "2395")
    assert q.loc["2020-06-30", "eps_diluted"] == pytest.approx(3.29 / 1.099863, rel=1e-3)
    assert q.loc["2020-06-30", "net_income"] / q.loc["2020-06-30", "shares_out"] == pytest.approx(
        q.loc["2020-06-30", "eps_diluted"], rel=0.02)


def test_zero_volume_trading_day_kept(built):
    """2021-08-17 Yahoo 全部台股成交量 0,但 TWSE 有交易:保留這一天(不刪列),成交量設缺值;價格是 Yahoo 沿用前一天的 → 缺值。"""
    store, _ = built
    px = store.prices["2330"]
    assert T("2021-08-17") in px.index
    assert pd.isna(px.loc["2021-08-17", "volume"]) and pd.isna(px.loc["2021-08-17", "close"])
    assert px.loc["2021-08-18", "volume"] > 0
