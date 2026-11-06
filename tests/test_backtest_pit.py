"""v1.1 在 PIT 新資料上的回測(lab/research/backtest_pit.py):轉接函式的公式測試 + 端到端竄改測試。

端到端竄改測試:用子程序(離線模式)從快取建一份小 store(下市股 2311、多次減資 / 配股的 2327、AAPL,加大盤與總經),
把判斷日 d 之後的資料全部亂改,完全不改的 backtest.judge_at 的輸出(評等、分數、公平價…)必須一模一樣。
沒有快取(px_since、證交所個股月資料)就 skip;測試不會連網。
"""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lab import config
from lab.cache import CacheMiss
from lab.pit import PITStore
from lab.pit.tamper import assert_pit_invariant, tamper
from lab.research import backtest_pit as BP
from lab.analysis.income_risk import KEYS as IR_KEYS

T = pd.Timestamp
ROOT = Path(__file__).resolve().parents[1]
TW_CODES = ["2311", "2327", "2886"]   # 2311:半導體業;2327:電子零組件業;2886:金融保險
US_SYMS = ["AAPL"]            # 2014、2020 分割
CASES = [("TW", "2311.TW", "2016-06-30", "半導體業"), ("TW", "2311.TW", "2017-12-29", "半導體業"),
         ("TW", "2327.TW", "2016-06-30", "電子零組件業"), ("TW", "2327.TW", "2022-09-30", "電子零組件業"),
         ("TW", "2886.TW", "2016-06-30", "金融保險"),
         ("US", "AAPL", "2016-06-30", "consumer-electronics"), ("US", "AAPL", "2020-07-31", "consumer-electronics")]


# ---------------------------------------------------------------- 不需要資料
def _us_store(first_avail):
    """三個年度,前兩個在同一天第一次公布(資料最早那份 10-K 的比較數字)。"""
    rows = []
    for pe, av in zip(["2012-12-31", "2013-12-31", "2014-12-31"], first_avail):
        for f, v in (("revenue", 100.0), ("eps_diluted", 2.0), ("operating_income", 10.0), ("cfo", 12.0), ("capex", -3.0)):
            rows.append({"entity": "XYZ", "field": f, "period_end": T(pe), "period": "FY", "value": v,
                         "available_at": T(av), "source": "t", "filed": T(av) - pd.Timedelta(days=1)})
    # 之後的重編(較晚的 available_at)不應該改變「第一次公布」的日期
    rows.append({"entity": "XYZ", "field": "revenue", "period_end": T("2012-12-31"), "period": "FY", "value": 101.0,
                 "available_at": T("2015-03-02"), "source": "t", "filed": T("2015-03-01")})
    return PITStore(pd.DataFrame(rows))


def test_us_fdat_avail_first_filing_and_tie_break():
    st = _us_store(["2014-03-03", "2014-03-03", "2015-03-02"])
    a = BP.us_fdat_asof(st.as_of("2015-06-30"), "XYZ")["a"]
    av = pd.DatetimeIndex(a["_avail"])
    assert av.is_monotonic_increasing and av.is_unique
    # 同一天公布的兩個年度:較舊的錯開 1 奈秒,午夜判斷日的「_avail ≤ d」結果不變
    assert av[1] == T("2014-03-03") and av[0] == T("2014-03-03") - pd.Timedelta(1, "ns")
    assert av[2] == T("2015-03-02")  # 第一次公布,不是重編日
    assert a.loc[T("2012-12-31"), "revenue"] == 101.0  # 值是 d 當時看得到的最新版本
    assert a["fcf"].iloc[-1] == 9.0 and a["ebit"].iloc[-1] == 10.0
    d = T("2014-03-03")
    assert (a["_avail"] <= d).sum() == 2 and (a["_avail"] <= d - pd.Timedelta(days=1)).sum() == 0


def test_total_return_index_pads_delisted_flat():
    idx = pd.bdate_range("2018-01-01", "2018-04-17")
    px = pd.DataFrame({"open": 10.0, "high": 10.0, "low": 10.0, "close": np.linspace(10, 12, len(idx)), "volume": 1.0,
                       "cash_div": 0.0}, index=idx)
    st = PITStore(prices={"2311": px, "2330": px.copy()})
    cal = pd.bdate_range("2018-01-01", "2018-12-31")
    tr = BP.total_return_index(st, "TW", ["2311.TW", "2330.TW"], cal)
    assert tr["2311.TW"].index[-1] == cal[-1] and tr["2311.TW"].iloc[-1] == pytest.approx(12.0)
    assert tr["2330.TW"].index[-1] == idx[-1]  # 不是下市股:不延伸


def test_price_source_default_unchanged(monkeypatch):
    from lab.pit import build_tw as BTW
    from lab.sources import finmind, yahoo
    called = []
    monkeypatch.setattr(yahoo, "history", lambda sym, st, *a, **k: called.append(("10y", sym)) or None)
    monkeypatch.setattr(yahoo, "history_since", lambda sym, *a, **k: called.append(("since", sym)) or None)
    monkeypatch.setattr(BTW, "twse_daily_hist", lambda code, *a, **k: called.append(("twse", code)) or None)
    monkeypatch.setattr(finmind, "get", lambda *a, **k: None)
    assert BTW.load_raw("2330", "2330.TW")["price_source"] == "yahoo_10y"
    assert called == [("10y", "2330.TW")]
    called.clear()
    BTW.load_raw("2311", "2311.TW", "since")
    assert called == [("since", "2311.TW"), ("twse", "2311")]
    with pytest.raises(ValueError):
        BTW.load_raw("2330", "2330.TW", "bogus")


# ---------------------------------------------------------------- 時效檢查(1.9d,合成 store)
def _px(idx):
    return pd.DataFrame({"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1.0, "cash_div": 0.0},
                        index=pd.DatetimeIndex(idx))


def _fact(ent, pe, period, av):
    return {"entity": ent, "field": "revenue", "period_end": T(pe), "period": period, "value": 1.0,
            "available_at": T(av), "source": "t"}


def _stale_store(tw_px_end="2019-12-31", tw_last_q="2019-09-30", us_last_fy="2018-12-31", us_last_q="2019-09-30"):
    """台股 9999、美股 XYZ;大盤日曆 = 2017–2019 平日。財報期末到 *_last_*,可用日 = 法定期限(台股)/ 期末 + 60 天(美股)。"""
    from lab.pit import build_tw as BTW
    cal = pd.bdate_range("2017-01-02", "2019-12-31")
    rows = []
    for pe in pd.date_range("2016-03-31", tw_last_q, freq="QE"):
        rows.append(_fact("9999", pe, f"Q{(pe.month - 1) // 3 + 1}", BTW.quarter_available_at(pe, False, cal)))
    for pe in pd.date_range("2016-03-31", us_last_q, freq="QE"):
        rows.append(_fact("XYZ", pe, f"Q{(pe.month - 1) // 3 + 1}", pe + pd.Timedelta(days=45)))
    for pe in pd.date_range("2014-12-31", us_last_fy, freq="YE"):
        rows.append(_fact("XYZ", pe, "FY", pe + pd.Timedelta(days=60)))
    prices = {"^TWII": _px(cal), "^GSPC": _px(cal), "9999": _px(cal[cal <= T(tw_px_end)]), "XYZ": _px(cal)}
    return PITStore(pd.DataFrame(rows), prices)


@pytest.fixture
def no_finmind(monkeypatch):
    monkeypatch.setattr(BP, "_tw_kind", lambda code: (False, False))


D_STALE = "2019-06-28"  # 週五;Q1 期限 5/15 已過 → 應已公布 2019Q1,容許晚一季 → 至少 2018Q4


def test_staleness_normal_not_skipped(no_finmind):
    st = _stale_store()
    v = st.as_of(D_STALE)
    assert BP.staleness_reason(v, "TW", "9999.TW") is None
    assert BP.staleness_reason(v, "US", "XYZ") is None
    # 邊界:最後收盤剛好是第 5 個交易日(6/24)、財報剛好晚一季(2018Q4)→ 仍不跳過
    st = _stale_store(tw_px_end="2019-06-24", tw_last_q="2018-12-31")
    assert BP.staleness_reason(st.as_of(D_STALE), "TW", "9999.TW") is None
    assert BP.expected_tw_quarter(T(D_STALE), False, False, pd.bdate_range("2017-01-02", D_STALE)) == T("2019-03-31")
    # 5/15 期限 → 5/16 可用:5/15 當天還不算已公布
    cal = pd.bdate_range("2017-01-02", "2019-12-31")
    assert BP.expected_tw_quarter(T("2019-05-15"), False, False, cal) == T("2018-12-31")
    assert BP.expected_tw_quarter(T("2019-05-16"), False, False, cal) == T("2019-03-31")


def test_staleness_price_gap_skipped(no_finmind):
    st = _stale_store(tw_px_end="2019-06-21")  # 6/24–6/28 五個交易日都沒有收盤價
    r = BP.staleness_reason(st.as_of(D_STALE), "TW", "9999.TW")
    assert r and r.startswith("時效:價格中斷")
    # 收盤價是 NaN 也算沒有
    st = _stale_store()
    st.prices["9999"].loc[st.prices["9999"].index > T("2019-06-21"), "close"] = np.nan
    assert BP.staleness_reason(st.as_of(D_STALE), "TW", "9999.TW").startswith("時效:價格中斷")


def test_staleness_financials_gap_skipped(no_finmind):
    st = _stale_store(tw_last_q="2018-09-30", us_last_fy="2017-12-31", us_last_q="2018-09-30")
    v = st.as_of(D_STALE)
    assert BP.staleness_reason(v, "TW", "9999.TW").startswith("時效:財報中斷")
    assert BP.staleness_reason(v, "US", "XYZ").startswith("時效:年報中斷")
    st = _stale_store(us_last_q="2018-09-30")  # 年報正常、季報停在 2018Q3(< d − 7 個月)
    assert BP.staleness_reason(st.as_of(D_STALE), "US", "XYZ").startswith("時效:季報中斷")


@pytest.mark.parametrize("kw", [{}, {"tw_px_end": "2019-06-21"}, {"tw_last_q": "2018-09-30", "us_last_fy": "2017-12-31"},
                                {"tw_px_end": "2019-06-24", "tw_last_q": "2018-12-31"}])
def test_staleness_tamper_invariant(no_finmind, kw):
    """時效檢查本身只用 d 以前的資料:d 之後的價格、財報、事件亂改 / 補上,結果不變。"""
    st = _stale_store(**kw)

    def fn(view):
        return [BP.staleness_reason(view, "TW", "9999.TW"), BP.staleness_reason(view, "US", "XYZ")]
    assert_pit_invariant(fn, st, D_STALE, seeds=(1, 2, 3))
    # 對照組:只在 d 之後「補上缺口」(之後復牌、補申報);若時效檢查偷看未來,這裡會變成不跳過
    d = T(D_STALE)
    full = _stale_store()
    fp = full.prices["9999"]
    prices = dict(st.prices, **{"9999": pd.concat([st.prices["9999"], fp[fp.index > d]])})
    facts = pd.concat([st.facts, full.facts[full.facts["available_at"] > d]], ignore_index=True)
    assert fn(PITStore(facts, prices).as_of(d)) == fn(st.as_of(d))


# ---------------------------------------------------------------- 真實快取的端到端竄改測試
def _data_dir() -> Path | None:
    env = os.environ.get("INVEST_LAB_DATA_DIR")
    for p in ([Path(env)] if env else []) + [config.MAIN_ROOT / "data"]:
        c = p / "cache"
        if (c / "px_since").is_dir() and (c / "twse_stock_month").is_dir():
            return p
    return None


@pytest.fixture(scope="module")
def combined(tmp_path_factory):
    data = _data_dir()
    if data is None:
        pytest.skip("沒有長歷史快取(px_since / 證交所個股月資料;data/ 不進 git)")
    tmp = tmp_path_factory.mktemp("bt_pit")
    env = dict(os.environ, INVEST_LAB_OFFLINE="1", INVEST_LAB_DATA_DIR=str(data), INVEST_LAB_OUT_DIR=str(tmp / "out"),
               INVEST_LAB_PRIVATE_DIR=str(tmp / "private_fake"), INVEST_LAB_RECORD="0", INVEST_LAB_AGENT="test")
    out = tmp / "combined.sqlite"
    r = subprocess.run([sys.executable, "-m", "lab.research.backtest_pit", "sample", "--tw", ",".join(TW_CODES),
                        "--us", ",".join(US_SYMS), "--out", str(out)], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=900)
    if r.returncode != 0:
        pytest.skip(f"建不出測試 store(快取不完整?):{r.stderr[-300:]}")
    st = PITStore.load(out)
    need = {"2311", "2327", "2886", "AAPL", "^TWII", "^GSPC"}
    if not need <= set(st.prices):
        pytest.skip(f"快取不完整,缺 {sorted(need - set(st.prices))}")
    return st


@pytest.mark.parametrize("market,sym,d,expected_industry", CASES)
def test_judge_pit_tamper_invariant(combined, market, sym, d, expected_industry):
    from lab.config import load_params
    params = load_params()
    ind_tw, ind_key = BP._industry(market, sym)
    assert (ind_tw or ind_key) == expected_industry
    base = BP.judge_pit(combined.as_of(d), market, sym, params, ind_tw, ind_key)
    assert base is not None, "這個判斷日應該要有判斷(資料不足就是快取有問題)"
    assert base["lt_level"] is not None and base["fv"] is not None

    def fn(view):
        r = BP.judge_pit(view, market, sym, params, ind_tw, ind_key)
        return {k: r[k] for k in ("lt_level", "lt_score", "st_code", "st_score", "fv", "upside", "uncertainty", "price",
                                  "pillars")} | {k: r["_env"][k] for k in IR_KEYS}   # S4 股利與風險基礎變數
    assert all(base["_env"][k] is not None for k in ("vol_1y", "dividend_yield_ttm")), base["_env"]
    assert_pit_invariant(fn, combined, d, seeds=(1, 2))


def test_tamper_actually_changes_future(combined):
    """竄改真的有動到 d 之後的資料(避免測試因為竄改沒生效而假通過)。"""
    d = T("2016-06-30")
    t = tamper(combined, d, 1)
    a, b = combined.prices["2327"], t.prices["2327"]
    assert not a.index[a.index > d].equals(b.index[b.index > d])
    assert not a.loc[a.index > d, "close"].equals(b.loc[b.index > d, "close"])
    assert np.allclose(a.loc[a.index <= d, "close"], b.loc[b.index <= d, "close"], equal_nan=True)


def _tamper_daily(orig, d, seed):
    rng = np.random.default_rng(seed)

    def fake(day):
        df = orig(day)
        if df is None or T(day) <= d:
            return df
        df = df.copy()
        df["value"] = df["value"] * rng.uniform(0.01, 100, len(df))
        df["close"] = df["close"] * rng.uniform(0.2, 5, len(df))
        extra = df.iloc[:1].copy()
        extra["code"], extra["value"] = "9998", 1e15
        return pd.concat([df, extra], ignore_index=True)
    return fake


@pytest.mark.parametrize("d", ["2016-06-30", "2017-12-29"])
def test_tw_universe_members_tamper(monkeypatch, d):
    """判斷日用的 PIT 股票池:d 之後的每週行情亂改,成員不變(A3 的作法,換成這裡用的月底判斷日)。"""
    from lab import universe_pit as U
    if not (config.CACHE_DIR / "twse_daily_all").is_dir():
        pytest.skip("沒有證交所每週快取")
    U.clear_cache()
    try:
        base = U.tw_universe(d, 50)
    except CacheMiss:
        pytest.skip("沒有證交所每週快取")
    if len(base) < 50:
        pytest.skip("每週快取不完整")
    orig = U.T.daily_all
    for seed in (1, 2):
        U.clear_cache()
        monkeypatch.setattr(U.T, "daily_all", _tamper_daily(orig, T(d), seed))
        assert U.tw_universe(d, 50) == base
        panel = U.weekly_panel(T(d) + pd.DateOffset(months=6))
        assert U.rank_liquidity(panel, d, 50)["code"].tolist() == base
    U.clear_cache()


def test_judge_pit_tamper_invariant_us_quarterly_ttm(combined):
    """P2-H08 開關打開時(目前關閉,待維護者決定),美股 TTM 路徑也不得偷看未來。"""
    from lab.config import load_params
    params = load_params()
    params["us_quarterly_ttm"] = True
    ind_tw, ind_key = BP._industry("US", "AAPL")
    for d in ("2019-05-31", "2022-08-31"):
        d = pd.Timestamp(d)
        base = BP.judge_pit(combined.as_of(d), "US", "AAPL", params, ind_tw, ind_key)
        assert base is not None

        def fn(view):
            r = BP.judge_pit(view, "US", "AAPL", params, ind_tw, ind_key)
            return {k: r[k] for k in ("lt_level", "lt_score", "fv", "upside", "pillars")}
        assert_pit_invariant(fn, combined, d, seeds=(1, 2))


def test_fixed_quantile_levels():
    """P2-H10:每日固定分位評等;沒有評等的列維持 None、不參與排名;只看同一天。"""
    import numpy as np
    scores = list(np.linspace(1, 100, 50))
    r = pd.DataFrame({"date": ["2020-01-31"] * 50 + ["2020-02-28"] * 3, "lt_score": scores + [10.0, 20.0, None],
                      "lt_level": [2.0] * 49 + [None] + [2.0, 2.0, 2.0]})
    lv = BP.fixed_quantile_levels(r, [0.1, 0.3, 0.7, 0.9])
    day1 = lv[:49]
    counts = day1.value_counts().sort_index().to_dict()
    assert counts == {0.0: 4, 1.0: 10, 2.0: 20, 3.0: 10, 4.0: 5}      # 49 檔:≤ 10% / 30% / 70% / 90% 的分位
    assert day1.is_monotonic_increasing and np.isnan(lv[49]) and np.isnan(lv[52])
    assert lv[50:52].tolist() == [2.0, 4.0]                          # 第二天自己排名,不受第一天影響


def _xs_rows():
    # 兩天、四檔;品質面向兩個指標(q1 越高越好、q2 原始值越低越好),估值一個指標
    def it(q1, q1s, q2, q2s, v, vs):
        return {"quality": [("q1", q1, q1s, 2.0), ("q2", q2, q2s, 1.0)], "valuation": [("v", v, vs, 1.0)]}
    rows = [
        ("2020-01-31", "A", it(0.30, 100.0, 0.5, 80.0, 0.1, 60.0)),
        ("2020-01-31", "B", it(0.25, 100.0, 0.9, 40.0, 0.2, 70.0)),    # q1 和 A 同被截在 100,原始值較低 → 排在 A 後
        ("2020-01-31", "C", it(0.10, 50.0, 1.2, 10.0, None, None)),
        ("2020-01-31", "D", it(0.05, 25.0, 0.7, 60.0, 0.3, 80.0)),
        ("2020-02-28", "A", it(0.30, 100.0, 0.5, 80.0, 0.1, 60.0)),
    ]
    r = pd.DataFrame([{"date": pd.Timestamp(d), "symbol": s, "_items": i, "lt_score": 0.0, "lt_level": 2.0,
                       "pillars": {"quality": 0.0, "valuation": 0.0, "momentum": 55.0}} for d, s, i in rows])
    return r


def test_cross_sectional_rescore():
    """P2-H05:指標換成當日股票池百分位;截斷同分用原始值(方向由同日未截斷樣本決定)再排;只用同一天。"""
    w = {"quality": 25, "valuation": 25, "growth": 25, "health": 25}
    out = BP.cross_sectional_rescore(_xs_rows(), w)
    day = out[out["date"] == pd.Timestamp("2020-01-31")].set_index("symbol")
    q = {s: day.at[s, "pillars"]["quality"] for s in "ABCD"}
    # q1 排名:A(100,原始 0.30) > B(100,原始 0.25) > C(50) > D(25) → 100 / 75 / 50 / 25;q2:A 80 > D 60 > B 40 > C 10
    assert q["A"] == pytest.approx((2 * 100 + 1 * 100) / 3, abs=0.1)
    assert q["B"] == pytest.approx((2 * 75 + 1 * 50) / 3, abs=0.1)
    assert q["D"] == pytest.approx((2 * 25 + 1 * 75) / 3, abs=0.1)
    assert day.at["C", "pillars"]["valuation"] == 0.0                     # 沒有估值指標 → 面向維持原值(這裡是 0)
    assert day.at["A", "pillars"]["momentum"] == 55.0                     # 長期沒用到的面向不動
    assert day.at["A", "lt_score"] == pytest.approx((q["A"] + day.at["A", "pillars"]["valuation"]) / 2, abs=0.1)
    feb = out[out["date"] == pd.Timestamp("2020-02-28")].iloc[0]
    assert feb["pillars"]["quality"] == 100.0                             # 第二天只有一檔:自己排第一,不受第一天影響
    # 只用同一天:刪掉第二天,第一天的結果不變
    only = BP.cross_sectional_rescore(_xs_rows().iloc[:4], w)
    pd.testing.assert_series_equal(only["lt_score"], out["lt_score"].iloc[:4])


def test_judge_pit_tamper_invariant_cross_sectional_items(combined):
    """P2-H05:開關打開時 judge_pit 帶出的指標(_items)不得偷看未來。"""
    from lab.config import load_params
    params = load_params()
    params["cross_sectional_scoring"] = True
    for market, sym, d in (("TW", "2327.TW", "2022-09-30"), ("US", "AAPL", "2020-07-31")):
        d = pd.Timestamp(d)
        ind_tw, ind_key = BP._industry(market, sym)
        base = BP.judge_pit(combined.as_of(d), market, sym, params, ind_tw, ind_key)
        assert base is not None and base["_items"]

        def fn(view, market=market, sym=sym, ind_tw=ind_tw, ind_key=ind_key):
            return BP.judge_pit(view, market, sym, params, ind_tw, ind_key)["_items"]
        assert_pit_invariant(fn, combined, d, seeds=(1, 2))


def test_industry_neutral_valuation():
    """P2-H11:同產業 ≥ 8 檔用產業內百分位、其他用整個股票池;其他估值指標維持絕對分數;只用同一天。"""
    rows = []
    for i in range(12):   # 8 檔半導體 + 4 檔其他
        ind = "半導體業" if i < 8 else f"其他{i}"
        rows.append({"date": pd.Timestamp("2020-01-31"), "symbol": f"S{i}", "ind": ind,
                     "_items": {"valuation": [("fcf_yield", None, float(10 * i), 1.0), ("fv_upside", None, 50.0, 1.0)]},
                     "pillars": {"valuation": 0.0, "quality": 60.0}, "lt_score": 0.0, "lt_level": 2.0})
    r = pd.DataFrame(rows)
    out = BP.industry_neutral_valuation(r, {"quality": 25, "valuation": 25}, dict(zip(r.symbol, r.ind)))
    v = dict(zip(out.symbol, [p["valuation"] for p in out["pillars"]]))
    assert v["S7"] == pytest.approx((100 + 50) / 2, abs=0.06)              # 半導體裡最高 → 100
    assert v["S0"] == pytest.approx((12.5 + 50) / 2, abs=0.06)             # 半導體 8 檔裡最低 → 1/8
    assert v["S11"] == pytest.approx((100 + 50) / 2, abs=0.06)             # 其他產業不滿 8 檔 → 4 檔一起排,最高 → 100
    assert v["S8"] == pytest.approx((25 + 50) / 2, abs=0.06)
    assert out.loc[out.symbol == "S7", "lt_score"].iloc[0] == pytest.approx((60 + 75) / 2, abs=0.06)
