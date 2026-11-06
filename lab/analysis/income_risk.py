"""股利與風險基礎變數(上手精靈規格 §6,單元 S4):給「存股高股息」「穩健低波動」範本用,出廠模型不使用。

| 變數 | 定義 |
|---|---|
| dividend_yield_ttm | 判斷日 d 前 12 個月(d − 1 年, d]**已除息**的現金股利合計 ÷ d 的收盤價(同一股數基準) |
| div_paid_years_5y | 近 5 個「滾動 12 個月」區間((d−k−1 年, d−k 年],k = 0..4)裡,有除息現金股利的區間數(0–5) |
| vol_1y | d 以前最後 252 個交易日的日對數報酬(含息還原價)標準差 × √252 |
| beta_2y | 最近 104 週的週報酬(含息還原價)對含息大盤週報酬迴歸的斜率,Blume 調整:0.67 × 原始 + 0.33 |

防洩漏:輸入只用 ≤ d 的資料(除息日 ≤ d、價格 ≤ d);每股股利一律換到 d 的股數基準
(回測:`view.split_factor(entity, 除息日)`;網站:Yahoo 已回溯調整的價格與股利,或 FinMind 股利 ÷ 之後的分割 / 配股倍數)。
資料不足時是 None(不是 0):價格歷史不滿 1 年 → 殖利率 None;不滿 5 年 → 配息年數 None;
日報酬不滿 252 個 → vol_1y None;重疊週數不滿 78 週 → beta_2y None。

網站(lab/report.py)與回測(backtest.judge_at / backtest_pit.judge_pit)都只呼叫 `compute`,定義一致;
差別只在怎麼取得輸入(`from_view` / `from_yf` / `tw_dividends_web`)。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252
BETA_WEEKS = 104
BETA_MIN_WEEKS = 78
DIV_YEARS = 5
BLUME = (0.67, 0.33)
BENCH_TR = {"TW": "TWSE_TR", "US": "^SP500TR"}   # 含息大盤(證交所發行量加權股價報酬指數 / S&P 500 Total Return)
KEYS = ("dividend_yield_ttm", "div_paid_years_5y", "vol_1y", "beta_2y")


def _clean(s: pd.Series | None) -> pd.Series:
    if s is None or len(s) == 0:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s = pd.to_numeric(pd.Series(s), errors="coerce")
    s.index = pd.DatetimeIndex(s.index)
    return s[np.isfinite(s)].sort_index()


def dividend_yield_ttm(divs: pd.Series, price, d, history_start) -> float | None:
    """divs:index = 除息日、值 = 每股現金股利(d 的股數基準);price:d 收盤價(同基準)。"""
    d = pd.Timestamp(d)
    if price is None or not np.isfinite(price) or price <= 0 or history_start is None \
            or pd.Timestamp(history_start) > d - pd.DateOffset(years=1):
        return None
    s = _clean(divs)
    s = s[(s.index <= d) & (s.index > d - pd.DateOffset(years=1)) & (s > 0)]
    return float(s.sum() / price)


def div_paid_years_5y(divs: pd.Series, d, history_start) -> int | None:
    d = pd.Timestamp(d)
    if history_start is None or pd.Timestamp(history_start) > d - pd.DateOffset(years=DIV_YEARS):
        return None
    s = _clean(divs)
    s = s[(s.index <= d) & (s > 0)]
    n = 0
    for k in range(DIV_YEARS):
        hi, lo = d - pd.DateOffset(years=k), d - pd.DateOffset(years=k + 1)
        n += bool(((s.index > lo) & (s.index <= hi)).any())
    return n


def vol_1y(total: pd.Series, d) -> float | None:
    """total:含息還原收盤價(≤ d)。"""
    s = _clean(total)
    s = s[(s.index <= pd.Timestamp(d)) & (s > 0)]
    lr = np.log(s).diff().dropna().tail(TRADING_DAYS)
    if len(lr) < TRADING_DAYS:
        return None
    v = float(lr.std() * math.sqrt(TRADING_DAYS))
    return v if np.isfinite(v) else None


def beta_2y(total: pd.Series, bench_tr: pd.Series | None, d) -> float | None:
    """週報酬(週五為週末,取每週最後一個收盤)對含息大盤的迴歸斜率,Blume 調整。"""
    d = pd.Timestamp(d)
    s = _clean(total)
    s = s[(s.index <= d) & (s > 0)]
    b = _clean(bench_tr)
    b = b[(b.index <= d) & (b > 0)]
    if s.empty or b.empty:
        return None
    b = b.reindex(s.index.union(b.index)).ffill().reindex(s.index)   # 大盤缺值的日子用前一個大盤收盤
    px = pd.concat([s, b], axis=1, keys=["s", "b"]).dropna()
    wk = px.resample("W-FRI").last().dropna().pct_change().dropna().tail(BETA_WEEKS)
    if len(wk) < BETA_MIN_WEEKS or not wk["b"].var() > 0:
        return None
    raw = float(wk["s"].cov(wk["b"]) / wk["b"].var())
    if not np.isfinite(raw):
        return None
    return BLUME[0] * raw + BLUME[1]


def compute(price, total: pd.Series, divs: pd.Series, bench_tr: pd.Series | None, d, history_start) -> dict:
    """四個基礎變數。所有輸入都必須只含 ≤ d 的資料、每股數字已換到 d 的股數基準(函式內也會再截一次 ≤ d)。"""
    return {"dividend_yield_ttm": dividend_yield_ttm(divs, price, d, history_start),
            "div_paid_years_5y": div_paid_years_5y(divs, d, history_start),
            "vol_1y": vol_1y(total, d),
            "beta_2y": beta_2y(total, bench_tr, d)}


# ---------------------------------------------------------------- 輸入轉接

def pit_dividends(view, entity: str) -> pd.Series:
    """回測:d 以前每次除息的現金股利,換到 d 的股數基準(÷ view.split_factor(entity, 除息日))。

    store 的 cash_div 是「除息當天的股數基準」;台股另外把現金減資退還的股款也記成 cash_div(含息報酬才正確),
    那不是股利,所以減資生效日起第一個交易日的 cash_div 不算(同一天剛好也除息時會一起漏算,很少見)。"""
    raw = view.prices(entity, adjust="none")
    if raw is None or raw.empty or "cash_div" not in raw:
        return pd.Series(dtype=float)
    cd = raw["cash_div"].fillna(0.0)
    cd = cd[cd > 0]
    ev = view.events(entity)
    # 退款記在減資生效日起的第一個交易日(減資換股常停牌,可能比事件日晚好幾天,見 build_tw.build_actions)
    red = set()
    for r in (pd.DatetimeIndex(ev[ev["kind"] == "reduction"]["date"]) if len(ev) else []):
        after = raw.index[raw.index >= r]
        if len(after):
            red.add(after[0])
    vals = {t: float(v) / view.split_factor(entity, t) for t, v in cd.items() if t not in red}
    return pd.Series(vals, dtype=float).sort_index()


def from_view(view, entity: str, market: str, macro_view=None, px: pd.DataFrame | None = None) -> dict:
    """回測(backtest_pit.judge_pit):全部透過 view。px = build_tw.price_asof(view, entity)(有的話省掉重算含息價)。"""
    mv = macro_view or view
    if px is None:
        from ..pit.build_tw import price_asof
        px = price_asof(view, entity)
    if px is None or px.empty:
        return dict.fromkeys(KEYS)
    close = px["Close"].dropna()
    price = float(px["Close"].iloc[-1]) if len(px) else None
    bp = mv.prices(BENCH_TR[market], adjust="none")
    bench = bp["close"] if len(bp) else None
    return compute(price, px["Adj Close"], pit_dividends(view, entity), bench, view.d,
                   close.index[0] if len(close) else None)


def from_yf(h: pd.DataFrame, bench_tr: pd.Series | None, d, divs: pd.Series | None = None) -> dict:
    """yfinance 欄名的價格表(Close = 分割還原、Adj Close = 含息還原、Dividends = 同基準每股股利)。
    網站(Yahoo 價格)與 v1.1 回測(backtest.run)用。divs 給了就取代 Dividends 欄(台股網站用 FinMind)。"""
    if h is None or h.empty:
        return dict.fromkeys(KEYS)
    d = pd.Timestamp(d)
    h = h[h.index <= d]
    if h.empty:
        return dict.fromkeys(KEYS)
    close = h["Close"].dropna()
    total = h["Adj Close"] if "Adj Close" in h else h["Close"]
    if divs is None:
        divs = h["Dividends"] if "Dividends" in h else pd.Series(dtype=float)
    price = float(h["Close"].iloc[-1])
    return compute(price, total, divs, bench_tr, d, close.index[0] if len(close) else None)


def _num0(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if np.isfinite(v) else 0.0


def tw_dividends_web(fm_div: pd.DataFrame | None, hist: pd.DataFrame) -> pd.Series | None:
    """網站台股:FinMind 股利政策表的現金股利(盈餘 + 公積),除息日 = CashExDividendTradingDate。
    FinMind 金額是除息當時的每股金額;換到 Yahoo 價格的基準(= 今天)要除以除息日之後所有 Yahoo 分割 / 配股 / 減資比率的乘積
    (和回測的 split_factor 同義)。沒有 FinMind 資料回傳 None(呼叫端改用 Yahoo 股利,減資退款那天不算)。"""
    if fm_div is None or len(fm_div) == 0 or "CashExDividendTradingDate" not in fm_div:
        return None
    sp = hist["Stock Splits"] if "Stock Splits" in hist else pd.Series(dtype=float)
    sp = sp[(sp > 0) & (sp != 1)].astype(float)
    sp.index = pd.DatetimeIndex(sp.index)
    vals: dict = {}
    for _, r in fm_div.iterrows():
        ex = str(r.get("CashExDividendTradingDate") or "").strip()
        amt = _num0(r.get("CashEarningsDistribution")) + _num0(r.get("CashStatutorySurplus"))
        if not ex or not amt > 0:
            continue
        try:
            t = pd.Timestamp(ex)
        except ValueError:
            continue
        f = float(sp[sp.index > t].prod()) if len(sp) else 1.0
        vals[t] = vals.get(t, 0.0) + amt / f
    return pd.Series(vals, dtype=float).sort_index()


def yahoo_dividends_ex_refunds(hist: pd.DataFrame) -> pd.Series:
    """Yahoo 股利,去掉減資(Stock Splits < 1)前後 7 天內的「股利」(Yahoo 有時把減資退款記成股利)。"""
    dv = hist["Dividends"] if "Dividends" in hist else pd.Series(dtype=float)
    dv = dv[dv > 0]
    sp = hist["Stock Splits"] if "Stock Splits" in hist else pd.Series(dtype=float)
    red = pd.DatetimeIndex(sp[(sp > 0) & (sp < 1)].index)
    if len(red):
        keep = [not any(abs((t - r).days) <= 7 for r in red) for t in pd.DatetimeIndex(dv.index)]
        dv = dv[keep]
    return dv
