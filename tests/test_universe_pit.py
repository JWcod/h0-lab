"""PIT 股票池(lab/universe_pit.py)的測試:合成資料的公式測試 + 真實快取的竄改測試(快取不存在就 skip)。

竄改測試的精神同 lab.pit.tamper:把判斷日 d 之後的資料(成交金額、價格、成交量)亂改,d 當時的股票池必須完全不變。
"""
import numpy as np
import pandas as pd
import pytest

from lab import config
from lab import universe_pit as U
from lab.cache import CacheMiss


def _panel(rows):
    return pd.DataFrame(rows, columns=["code", "name", "volume", "value", "close", "date"]).assign(
        date=lambda x: pd.to_datetime(x["date"]))


def test_rank_missing_weeks_count_as_zero_and_must_be_listed():
    days = pd.date_range("2020-01-01", periods=10, freq="W-WED")
    rows = []
    for i, d in enumerate(days):
        rows.append(("1111", "老牌", 1, 100.0, 10.0, d))
        if i >= 8:  # 新上市,只有最後 2 週,每週 300 → 平均 60(沒掛牌的週算 0)
            rows.append(("2222", "新股", 1, 300.0, 10.0, d))
        if i < 9:  # 最後一週不在行情表(下市或停牌)→ 不入選
            rows.append(("3333", "下市", 1, 1000.0, 10.0, d))
    r = U.rank_liquidity(_panel(rows), days[-1], n=5)
    assert r["code"].tolist() == ["1111", "2222"]
    assert r["avg_value"].tolist() == pytest.approx([100.0, 60.0])


def test_rank_ignores_future_rows():
    days = pd.date_range("2020-01-01", periods=30, freq="W-WED")
    rng = np.random.default_rng(0)
    rows = [(f"{1000 + k}", "x", 1, float(rng.uniform(1, 100)), 10.0, d) for d in days for k in range(20)]
    p = _panel(rows)
    d = days[15]
    base = U.rank_liquidity(p, d, n=10)
    q = p.copy()
    fut = q["date"] > d
    q.loc[fut, "value"] = rng.uniform(1, 1e6, fut.sum())
    q = pd.concat([q, _panel([("9999", "假", 1, 1e12, 1.0, days[16])])])
    pd.testing.assert_frame_equal(base, U.rank_liquidity(q, d, n=10))


def test_period_return_dividends_delisting_and_corporate_action(monkeypatch):
    days = pd.date_range("2020-01-01", periods=6, freq="W-WED")
    rows = [("1111", "a", 1, 1.0, c, d) for d, c in zip(days, [100, 100, 95, 95, 95, 95])]
    rows += [("2222", "b", 1, 1.0, c, d) for d, c in zip(days[:3], [50, 50, 40])]  # 第 3 週後下市
    rows += [("3333", "c", 1, 1.0, c, d) for d, c in zip(days, [400, 400, 100, 100, 100, 100])]  # 面額變更
    px = U.TWPrices(_panel(rows))
    ev = {"1111": pd.DataFrame({"date": [days[2]], "cash": [5.0], "stock_ratio": [1.0]})}
    monkeypatch.setattr(U, "tw_dividend_events", lambda c: ev.get(c, pd.DataFrame(columns=["date", "cash", "stock_ratio"])))
    r, src, st = U.tw_period_return("1111", days[0], days[4], px, use_yahoo=False)
    assert (src, st) == ("twse+div", "ok") and r == pytest.approx((95 + 5) / 100 - 1)
    for h in U.DELIST_HAIRCUTS:
        r, _, st = U.tw_period_return("2222", days[0], days[4], px, haircut=h, use_yahoo=False)
        assert st == "delisted" and r == pytest.approx(40 / 50 * (1 + h) - 1)
    r, src, _ = U.tw_period_return("3333", days[0], days[4], px, use_yahoo=False)
    assert r is None and src == "corp_action"


# ---------------------------------------------------------------- 真實快取的竄改測試
def _tamper_daily(orig, d, seed):
    rng = np.random.default_rng(seed)

    def fake(day):
        df = orig(day)
        if df is None or pd.Timestamp(day) <= d:
            return df
        df = df.copy()
        df["value"] = df["value"] * rng.uniform(0.01, 100, len(df))
        df["volume"] = rng.permutation(df["volume"].to_numpy())
        df["close"] = df["close"] * rng.uniform(0.2, 5, len(df))
        extra = df.iloc[:1].copy()
        extra["code"], extra["value"] = "9998", 1e15
        return pd.concat([df, extra], ignore_index=True)
    return fake


@pytest.mark.parametrize("d", ["2016-06-29", "2021-03-31", "2023-11-29"])
def test_tw_universe_tamper(monkeypatch, d):
    if not (config.CACHE_DIR / "twse_daily_all").is_dir():
        pytest.skip("沒有證交所每週快取")  # 沒快取時不要讓測試去連網
    U.clear_cache()
    try:
        base = U.tw_universe(d)
    except CacheMiss:
        pytest.skip("沒有證交所每週快取")
    if len(base) < 50:
        pytest.skip("每週快取不完整")
    seen = []
    orig = U.T.daily_all

    def spy(day):
        seen.append(pd.Timestamp(day))
        return orig(day)
    for seed in (1, 2):
        U.clear_cache()
        monkeypatch.setattr(U.T, "daily_all", _tamper_daily(spy, pd.Timestamp(d), seed))
        assert U.tw_universe(d) == base
    assert max(seen) <= pd.Timestamp(d)  # 根本不會去讀 d 之後的檔
    # 就算 panel 裡混了竄改過的未來資料,排名也不變
    U.clear_cache()
    monkeypatch.setattr(U.T, "daily_all", _tamper_daily(orig, pd.Timestamp(d), 3))
    panel = U.weekly_panel(pd.Timestamp(d) + pd.DateOffset(months=6))
    assert U.rank_liquidity(panel, d, 50)["code"].tolist() == base
    U.clear_cache()


@pytest.mark.parametrize("d", ["2016-06-30", "2022-08-31"])
def test_us_universe_proxy_tamper(monkeypatch, d):
    if not (config.CACHE_DIR / "sp500_px").is_dir():
        pytest.skip("沒有 S&P 500 快取")
    U.clear_cache()
    try:
        px = U.sp500_prices()
    except (OSError, CacheMiss):
        pytest.skip("沒有 S&P 500 快取")
    if len(px) < 400:
        pytest.skip("S&P 500 快取不完整")
    base = U.us_universe_proxy(d)
    rng = np.random.default_rng(7)
    t = pd.Timestamp(d)
    fake = {}
    for s, df in px.items():
        q = df.astype({"Close": float, "Volume": float})
        m = q.index > t
        q.loc[m, "Close"] = q.loc[m, "Close"] * rng.uniform(0.2, 5, m.sum())
        q.loc[m, "Volume"] = q.loc[m, "Volume"] * rng.uniform(0.01, 100, m.sum())
        fake[s] = q
    monkeypatch.setattr(U, "sp500_prices", lambda: fake)
    assert U.us_universe_proxy(d) == base
    assert len(base) == 40
    U.clear_cache()
