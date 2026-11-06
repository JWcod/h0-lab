"""S4(上手精靈 §6):dividend_yield_ttm、div_paid_years_5y、vol_1y、beta_2y 的公式、股數基準、防洩漏與網站 / 回測一致性。"""
import math

import numpy as np
import pandas as pd
import pytest

from lab.analysis import income_risk as IR
from lab.analysis import scoring as SC
from lab.pit import PITStore
from lab.pit.build_tw import price_asof
from lab.pit.tamper import assert_pit_invariant

T = pd.Timestamp


def _bdays(a, b):
    return pd.bdate_range(a, b)


# ---------------------------------------------------------------- 公式
def test_dividend_yield_only_past_12m_and_ex_date_le_d():
    d = T("2022-06-30")
    divs = pd.Series({T("2021-06-30"): 9.0,      # 剛好 1 年前:不在 (d − 1 年, d]
                      T("2021-07-01"): 1.0, T("2022-03-15"): 2.0, T("2022-06-30"): 0.5,  # 算
                      T("2022-07-01"): 100.0})   # d 之後除息(已公告也不算)
    y = IR.dividend_yield_ttm(divs, 50.0, d, T("2015-01-01"))
    assert y == pytest.approx(3.5 / 50.0)
    assert IR.dividend_yield_ttm(pd.Series(dtype=float), 50.0, d, T("2015-01-01")) == 0.0   # 沒配息 = 0,不是缺值
    assert IR.dividend_yield_ttm(divs, 50.0, d, T("2021-12-01")) is None                     # 歷史不滿 1 年
    assert IR.dividend_yield_ttm(divs, None, d, T("2015-01-01")) is None
    assert IR.dividend_yield_ttm(divs, float("nan"), d, T("2015-01-01")) is None


def test_div_paid_years_rolling_windows():
    d = T("2022-06-30")
    # 區間 k=0 (2021-06-30, 2022-06-30]、k=1、k=2 沒有、k=3、k=4 邊界
    divs = pd.Series({T("2022-01-10"): 1.0, T("2022-02-10"): 1.0, T("2020-08-01"): 1.0,
                      T("2018-12-01"): 1.0, T("2017-07-01"): 1.0, T("2017-06-30"): 1.0, T("2023-01-01"): 1.0})
    assert IR.div_paid_years_5y(divs, d, T("2010-01-01")) == 4   # k=0,1,3,4;2017-06-30 = 5 年前,不算;d 之後不算
    assert IR.div_paid_years_5y(divs, d, T("2018-01-01")) is None   # 歷史不滿 5 年
    assert IR.div_paid_years_5y(pd.Series(dtype=float), d, T("2010-01-01")) == 0


def test_vol_1y_matches_definition():
    rng = np.random.default_rng(0)
    idx = _bdays("2020-01-01", "2021-12-31")
    px = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))), index=idx)
    d = idx[-1]
    lr = np.log(px).diff().dropna().tail(252)
    assert IR.vol_1y(px, d) == pytest.approx(float(lr.std() * math.sqrt(252)))
    assert IR.vol_1y(px.iloc[:252], d) is None        # 只有 251 個日報酬
    # d 之後的資料不影響
    assert IR.vol_1y(px, idx[400]) == pytest.approx(IR.vol_1y(px.iloc[:401], idx[400]))


def test_beta_2y_blume_and_min_weeks():
    rng = np.random.default_rng(1)
    idx = _bdays("2019-01-01", "2021-12-31")
    br = rng.normal(0, 0.01, len(idx))
    bench = pd.Series(100 * np.exp(np.cumsum(br)), index=idx)
    # 股票的累積對數報酬 = 1.5 × 大盤 → 週報酬近似 1.5 倍(對數空間精確,簡單報酬近似)
    stock = pd.Series(50 * np.exp(1.5 * np.cumsum(br)), index=idx)
    b = IR.beta_2y(stock, bench, idx[-1])
    assert b == pytest.approx(0.67 * 1.5 + 0.33, abs=0.03)
    # 照公式重算(週五週末、取最後收盤、最後 104 週)
    wk = pd.concat([stock, bench], axis=1, keys=["s", "b"]).resample("W-FRI").last().pct_change().dropna().tail(104)
    assert b == pytest.approx(0.67 * wk["s"].cov(wk["b"]) / wk["b"].var() + 0.33, rel=1e-12)
    assert IR.beta_2y(stock.iloc[-300:], bench, idx[-1]) is None   # 約 60 週 < 78
    assert IR.beta_2y(stock, None, idx[-1]) is None
    flat = pd.Series(100.0, index=idx)
    assert IR.beta_2y(stock, flat, idx[-1]) is None                 # 大盤沒有變異


# ---------------------------------------------------------------- PIT(合成 store)
def _store():
    rng = np.random.default_rng(7)
    idx = _bdays("2014-01-01", "2022-12-30")
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, len(idx))))
    p = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                      "volume": 1e6, "cash_div": 0.0}, index=idx)
    # 每年 7 月第一個交易日配 3 元(當天股數基準)
    for y in range(2014, 2023):
        p.loc[p.index[p.index >= T(f"{y}-07-01")][0], "cash_div"] = 3.0
    # 2020-09-01 配股 1.1(每 1 股變 1.1 股):價格掉 1/1.1
    sd = T("2020-09-01")
    p.loc[p.index >= sd, ["open", "high", "low", "close"]] /= 1.1
    # 2021-03-01 減資 0.8,退還每新股 2.5 元(記成 cash_div,但不是股利)
    rd = T("2021-03-01")
    p.loc[p.index >= rd, ["open", "high", "low", "close"]] /= 0.8
    # 換股停牌:2021-03-01 ~ 03-05 沒有價格,退款記在恢復交易第一天 03-08(和 build_tw 一樣)
    p = p.drop(index=p.index[(p.index >= rd) & (p.index < T("2021-03-08"))])
    p.loc[T("2021-03-08"), "cash_div"] = 2.5
    bench = pd.DataFrame({"open": np.nan, "high": np.nan, "low": np.nan,
                          "close": 1e4 * np.exp(np.cumsum(rng.normal(0.0003, 0.009, len(idx)))),
                          "volume": np.nan, "cash_div": 0.0}, index=idx)
    ev = pd.DataFrame({"entity": ["X", "X"], "date": [sd, rd], "kind": ["stock_div", "reduction"], "ratio": [1.1, 0.8]})
    return PITStore(prices={"X": p, "TWSE_TR": bench, "^SP500TR": bench}, events=ev, vintage="synthetic")


def test_pit_dividends_split_basis_and_reduction_excluded():
    st = _store()
    v = st.as_of(T("2021-12-31"))
    dv = IR.pit_dividends(v, "X")
    assert T("2021-03-08") not in dv.index                          # 減資退款(停牌後第一天)不算股利
    assert dv[T("2019-07-01")] == pytest.approx(3.0 / (1.1 * 0.8))  # 之後有配股與減資 → 換到 d 的股數基準
    assert dv[T("2021-07-01")] == pytest.approx(3.0)                # 之後沒有公司行動
    assert dv.index.max() <= v.d
    # 配股前一天看:還沒發生的配股不能套用
    v0 = st.as_of(T("2020-08-31"))
    assert IR.pit_dividends(v0, "X")[T("2019-07-01")] == pytest.approx(3.0)


@pytest.mark.parametrize("key", IR.KEYS)
@pytest.mark.parametrize("d", ["2019-06-28", "2020-12-31", "2021-06-30"])
def test_each_variable_tamper_invariant(key, d):
    st = _store()
    assert_pit_invariant(lambda v: IR.from_view(v, "X", "TW")[key], st, d)


def test_from_view_values_and_web_path_agree():
    """回測(from_view)與網站(from_yf,吃 yfinance 欄名的價格表)同一份資料算出一樣的值。"""
    st = _store()
    d = T("2021-12-31")
    v = st.as_of(d)
    a = IR.from_view(v, "X", "TW")
    assert all(a[k] is not None for k in IR.KEYS)
    assert a["div_paid_years_5y"] == 5
    price = float(v.prices("X", "split")["close"].iloc[-1])
    assert a["dividend_yield_ttm"] == pytest.approx(3.0 / price)
    px = price_asof(v, "X")
    b = IR.from_yf(px, v.prices("TWSE_TR", "none")["close"], d, divs=IR.pit_dividends(v, "X"))
    for k in IR.KEYS:
        assert a[k] == pytest.approx(b[k], rel=1e-12), k


def test_tw_dividends_web_split_basis():
    hist = pd.DataFrame({"Close": 1.0, "Stock Splits": 0.0, "Dividends": 0.0}, index=_bdays("2019-01-01", "2021-12-31"))
    hist.loc[T("2020-09-01"), "Stock Splits"] = 1.1
    fm = pd.DataFrame({"CashExDividendTradingDate": ["2019-07-01", "2021-07-01", "", "2022-07-01"],
                       "CashEarningsDistribution": [2.5, 3.0, 1.0, 4.0], "CashStatutorySurplus": [0.5, 0.0, 0.0, None]})
    s = IR.tw_dividends_web(fm, hist)
    assert s[T("2019-07-01")] == pytest.approx(3.0 / 1.1)
    assert s[T("2021-07-01")] == pytest.approx(3.0)
    assert len(s) == 3                     # 空的除息日略過;未來除息日留著,由 compute 截到 ≤ d
    assert IR.tw_dividends_web(None, hist) is None


def test_yahoo_dividends_drop_refunds():
    idx = _bdays("2021-01-01", "2021-12-31")
    h = pd.DataFrame({"Dividends": 0.0, "Stock Splits": 0.0}, index=idx)
    h.loc[T("2021-03-01"), "Stock Splits"] = 0.8
    h.loc[T("2021-03-03"), "Dividends"] = 2.0     # 減資退款被 Yahoo 記成股利
    h.loc[T("2021-07-01"), "Dividends"] = 3.0
    s = IR.yahoo_dividends_ex_refunds(h)
    assert list(s.index) == [T("2021-07-01")]


def test_base_vars_exposes_new_vars_without_touching_shipped_model():
    """新變數只進基礎變數;沒有 income_risk 的 ctx 是 None。出廠模型不引用它們。"""
    from lab.models.schema import load_shipped
    m = load_shipped(SC.DEFAULT_MODEL, readonly=True)
    text = repr(m)
    for k in IR.KEYS:
        assert k in SC.BASE_VARS
        assert k not in text
