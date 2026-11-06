"""時點(PIT)股票池與存活者 / 選股偏誤量化(v2 階段 1,單元 1.6,A3)。方法與數字見 docs/存活者偏誤.md。

台股 `tw_universe(d)`:判斷日 d 收盤後,上市普通股(4 碼、1–9 開頭)依「d 以前近 12 個月證交所每週抽樣的平均成交金額」
取前 n 名。只讀 d 以前的每週檔(`twse_hist.daily_all`),含之後下市的公司;只有上市,沒有上櫃(櫃買沒有免費歷史檔)。
- 每週抽樣:每個週三,休市就往後找週四、週五(和 scripts/fetch_finmind_universe.py 相同)。
- 平均成交金額 = 視窗內「這檔有出現的抽樣日成交金額總和 ÷ 視窗內抽樣日數」(沒掛牌的週算 0,新上市股會被保守地壓低)。
- 有資格的股票:d 當時最後一個抽樣日的行情表裡有這個代號(還掛牌;停牌但沒下市也算)。
- 停牌的股票不會出現在證交所當天的行情表,所以判斷日停牌的股票不入選(下個月恢復交易再回來)。
- 代號重用:`code_reuse()` 檢查「消失 ≥ 8 週又出現」的代號;2015 起的資料裡都是同名公司(停牌後復牌),
  沒有不同公司共用代號的案例,所以排名不另外切段。

美股 `us_universe_proxy(d)`:在「現有 S&P 500 成分」裡依 d 以前 12 個月平均成交金額(Close × Volume)取前 n 名。
**這不是 PIT**(缺已被剔除的公司),只用來量化「用今天名單挑出今天的大型股」造成的偏誤。

偏誤量化 `python -m lab.universe_pit` → `$INVEST_LAB_OUT_DIR/research/survivorship_bias.json`。
- 開發集只用「結果視窗結束日 < 2024-10-01」的月份;全期包含 holdout 期間(標註),不單獨計算 2024-10 以後的任何指標。
- 這個模組只產出股票池與偏誤量化,不碰模型、不跑評分。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import sys

import numpy as np
import pandas as pd

from . import config
from .cache import CacheMiss, cached
from .research.splits import HOLDOUT_START
from .sources import finmind
from .sources import twse_hist as T
from .sources import yahoo

SAMPLE_START = pd.Timestamp("2015-01-01")
COMMON_RE = re.compile(r"[1-9]\d{3}")
DELIST_HAIRCUTS = (0.0, -0.30, -1.0)  # 下市後報酬的敏感度:併購 / 轉換(最後價)、折價出場、清算歸零
DEV_START = pd.Timestamp("2021-01-01")
FULL_START = pd.Timestamp("2016-01-01")
SP500_BATCH = 85
SECOND_CLASS = {"GOOG", "FOX", "NWS"}  # 同一家公司的第二種股份,排名時去掉(保留 GOOGL、FOXA、NWSA)

_daily: dict[pd.Timestamp, pd.DataFrame | None] = {}
missing_samples: list[str] = []  # 離線找不到快取的抽樣日


def clear_cache() -> None:
    """清掉程序內記憶的每週檔與美股價格(測試竄改資料時用)。"""
    _daily.clear()
    _sp500.clear()
    _yahoo.clear()
    _divs.clear()


# ---------------------------------------------------------------- 台股:每週抽樣
def _day(day: pd.Timestamp) -> pd.DataFrame | None:
    if day not in _daily:
        try:
            df = T.daily_all(day)
        except CacheMiss:
            missing_samples.append(day.strftime("%Y-%m-%d"))
            df = None
        if df is not None and len(df):
            df = df[df["code"].astype(str).str.fullmatch(COMMON_RE)][["code", "name", "volume", "value", "close"]].copy()
            df["date"] = day
        else:
            df = None
        _daily[day] = df
    return _daily[day]


def sample_dates(end, start=SAMPLE_START) -> list[pd.Timestamp]:
    """start ~ end(含)之間的每週抽樣日。只會讀 end 以前的每週檔。"""
    end = pd.Timestamp(end).normalize()
    out = []
    for w in pd.date_range(pd.Timestamp(start), end, freq="W-WED"):
        for k in range(3):
            day = w + pd.Timedelta(days=k)
            if day > end:
                break
            if _day(day) is not None:
                out.append(day)
                break
    return out


def weekly_panel(end, start=SAMPLE_START) -> pd.DataFrame:
    """start ~ end 的每週抽樣長表(code、name、volume、value、close、date),只含上市普通股。"""
    frames = [_day(x) for x in sample_dates(end, start)]
    frames = [f for f in frames if f is not None]
    if not frames:
        return pd.DataFrame(columns=["code", "name", "volume", "value", "close", "date"])
    return pd.concat(frames, ignore_index=True)


def rank_liquidity(panel: pd.DataFrame, d, n: int = 50, window_months: int = 12) -> pd.DataFrame:
    """純函式:只用 panel 裡 date ≤ d 的列,回傳前 n 名(rank、code、name、avg_value、n_obs)。"""
    d = pd.Timestamp(d)
    p = panel[(panel["date"] <= d) & (panel["date"] > d - pd.DateOffset(months=window_months))]
    if p.empty:
        return pd.DataFrame(columns=["rank", "code", "name", "avg_value", "n_obs"])
    dates = np.sort(p["date"].unique())
    last = dates[-1]
    listed = set(p.loc[p["date"] == last, "code"])
    p = p[p["code"].isin(listed)]
    g = p.sort_values("date").groupby("code")
    r = pd.DataFrame({"avg_value": g["value"].sum(min_count=0).fillna(0) / len(dates), "n_obs": g["value"].count(),
                      "name": g["name"].last()}).reset_index()
    r = r.sort_values(["avg_value", "code"], ascending=[False, True]).head(n).reset_index(drop=True)
    r.insert(0, "rank", np.arange(1, len(r) + 1))
    return r[["rank", "code", "name", "avg_value", "n_obs"]]


def tw_universe_table(d, n: int = 50, window_months: int = 12) -> pd.DataFrame:
    d = pd.Timestamp(d)
    start = max(SAMPLE_START, d - pd.DateOffset(months=window_months) - pd.Timedelta(days=7))
    return rank_liquidity(weekly_panel(d, start), d, n, window_months)


def tw_universe(d, n: int = 50, window_months: int = 12) -> list[str]:
    """判斷日 d 當時的台股 PIT 股票池(代號,不含 .TW),依平均成交金額由大到小。"""
    return tw_universe_table(d, n, window_months)["code"].tolist()


def rebalance_dates(start, end) -> list[pd.Timestamp]:
    """每個月最後一個每週抽樣日(台股股票池的換股日)。"""
    s = pd.Series(sample_dates(end, SAMPLE_START))
    s = s[s >= pd.Timestamp(start)]
    return s.groupby(s.dt.to_period("M")).max().tolist()


def tw_membership(start, end, n: int = 50, window_months: int = 12) -> pd.DataFrame:
    """每月成員表:month、d(換股日 = 當月最後一個抽樣日)、rank、code、name、avg_value、n_obs。"""
    panel = weekly_panel(end)
    rows = []
    for d in rebalance_dates(start, end):
        r = rank_liquidity(panel, d, n, window_months)
        r.insert(0, "d", d)
        r.insert(0, "month", d.to_period("M"))
        rows.append(r)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# ---------------------------------------------------------------- 台股:下市與代號重用
def tw_delistings(panel: pd.DataFrame | None = None) -> pd.DataFrame:
    """FinMind 下市清單(只留 4 碼普通股)+ 證交所每週檔裡的最後抽樣日與最後收盤價(診斷用,不是 PIT)。"""
    dl = finmind.get("TaiwanStockDelisting", None, None, "universe")
    if dl is None or dl.empty:
        return pd.DataFrame(columns=["code", "name", "delist_date", "last_sample", "last_close"])
    dl = dl.rename(columns={"stock_id": "code", "stock_name": "name", "date": "delist_date"})
    dl = dl[dl["code"].astype(str).str.fullmatch(COMMON_RE)].copy()
    dl["delist_date"] = pd.to_datetime(dl["delist_date"])
    if panel is None:
        panel = weekly_panel(config.today())
    px = panel.dropna(subset=["close"]).sort_values("date").groupby("code").agg(last_sample=("date", "last"),
                                                                                  last_close=("close", "last"))
    return dl.merge(px, left_on="code", right_index=True, how="left").sort_values("delist_date").reset_index(drop=True)


def code_reuse(panel: pd.DataFrame, gap_weeks: int = 8) -> pd.DataFrame:
    """同一代號在每週檔裡消失 ≥ gap_weeks 週後又出現(可能是代號重用或重新上市),列出前後名稱。"""
    dates = np.sort(panel["date"].unique())
    pos = {x: i for i, x in enumerate(dates)}
    out = []
    for code, g in panel.groupby("code"):
        idx = np.array(sorted(pos[x] for x in g["date"].unique()))
        gaps = np.where(np.diff(idx) >= gap_weeks)[0]
        for k in gaps:
            before = g[g["date"] == dates[idx[k]]]["name"].iloc[0]
            after = g[g["date"] == dates[idx[k + 1]]]["name"].iloc[0]
            out.append({"code": code, "gone": pd.Timestamp(dates[idx[k]]), "back": pd.Timestamp(dates[idx[k + 1]]),
                        "name_before": before, "name_after": after})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- 台股:報酬(只用來算結果,不影響股票池)
_yahoo: dict[str, pd.Series | None] = {}
_divs: dict[str, pd.DataFrame | None] = {}


def _adj_close(sym: str) -> pd.Series | None:
    if sym not in _yahoo:
        h = yahoo.history_since(sym)
        _yahoo[sym] = None if h is None or h.empty or "Adj Close" not in h else h["Adj Close"].dropna().sort_index()
    return _yahoo[sym]


def _asof(s: pd.Series, t: pd.Timestamp, max_gap_days: int = 7) -> float | None:
    s = s[s.index <= t]
    if s.empty or (t - s.index[-1]).days > max_gap_days:
        return None
    v = float(s.iloc[-1])
    return v if math.isfinite(v) and v > 0 else None


def tw_dividend_events(code: str) -> pd.DataFrame | None:
    """FinMind TaiwanStockDividend → 除息 / 除權事件(date、cash 每股元、stock_ratio 每 1 股變幾股);沒有快取回傳 None。"""
    if code in _divs:
        return _divs[code]
    try:
        df = cached("finmind", f"TaiwanStockDividend|{code}|2010-01-01", 10 ** 9, _no_fetch)
    except CacheMiss:
        df = None
    if df is None:
        _divs[code] = None
        return None
    ev = []
    for _, r in df.iterrows():
        cash = float(r.get("CashEarningsDistribution") or 0) + float(r.get("CashStatutorySurplus") or 0)
        stock = float(r.get("StockEarningsDistribution") or 0) + float(r.get("StockStatutorySurplus") or 0)
        cd, sd = str(r.get("CashExDividendTradingDate") or ""), str(r.get("StockExDividendTradingDate") or "")
        if cash > 0 and cd[:2] in ("19", "20"):
            ev.append({"date": pd.Timestamp(cd[:10]), "cash": cash, "stock_ratio": 1.0})
        if stock > 0 and sd[:2] in ("19", "20"):
            ev.append({"date": pd.Timestamp(sd[:10]), "cash": 0.0, "stock_ratio": 1 + stock / 10.0})
    out = pd.DataFrame(ev, columns=["date", "cash", "stock_ratio"]).sort_values("date") if ev else \
        pd.DataFrame(columns=["date", "cash", "stock_ratio"])
    _divs[code] = out
    return out


def _no_fetch():
    raise CacheMiss("universe_pit 只讀快取")


class TWPrices:
    """證交所每週收盤的查詢表(只拿來算結果報酬,不影響股票池)。"""

    def __init__(self, panel: pd.DataFrame):
        self.by_code = {c: g.set_index("date")["close"].sort_index() for c, g in panel.groupby("code")}
        self.listed = {x: set(g["code"]) for x, g in panel.groupby("date")}
        self.last_seen = panel.groupby("code")["date"].max().to_dict()

    def close(self, code: str, t: pd.Timestamp) -> tuple[float | None, pd.Timestamp | None]:
        g = self.by_code.get(code)
        if g is None:
            return None, None
        g = g[(g.index <= t)].dropna()
        if g.empty or (t - g.index[-1]).days > 60:
            return None, None
        return float(g.iloc[-1]), g.index[-1]

    def corporate_action(self, code: str, t0: pd.Timestamp, t1: pd.Timestamp) -> bool:
        """t0 ~ t1 之間相鄰兩個抽樣的原始收盤比 < 0.5 或 > 2。

        台股漲跌幅限制 ±10%,相鄰抽樣約 5 個交易日,價格最多變成 0.59 ~ 1.61 倍(連假 7 個交易日也只到 0.48 ~ 1.95);
        超出就幾乎一定是面額變更 / 減資這類 FinMind 股利表沒有的公司行動(例:矽力-KY 2022-07 面額變更,
        收盤 2515 → 560、成交股數放大約 5 倍、成交金額不變)。這種月份的報酬無法用原始價格算對,從等權平均排除並計數。
        較小的減資(例如 2 成)抓不到,是殘餘誤差。"""
        g = self.by_code.get(code)
        if g is None:
            return False
        g = g[(g.index >= t0) & (g.index <= t1)].dropna()
        if len(g) < 2:
            return False
        r = g.to_numpy()[1:] / g.to_numpy()[:-1]
        return bool(((r < 0.5) | (r > 2.0)).any())

    def status(self, code: str, t1: pd.Timestamp) -> str:
        """t1 當天:ok(在行情表)/ suspended(不在表上但之後又出現)/ delisted(之後再也沒出現)。"""
        if code in self.listed.get(t1, set()):
            return "ok"
        return "suspended" if self.last_seen.get(code, t1) > t1 else "delisted"


def tw_period_return(code: str, t0: pd.Timestamp, t1: pd.Timestamp, px: TWPrices, haircut: float = 0.0,
                     use_yahoo: bool = True) -> tuple[float | None, str, str]:
    """t0 收盤買、t1 收盤賣的含息報酬。回傳 (報酬, 來源, 狀態 ok / suspended / delisted)。

    - 有 Yahoo 長歷史(且涵蓋 t0、t1)的用 Adj Close;
    - 其他用證交所每週原始收盤 + FinMind 除息 / 除權(現金股利留到期末不再投入;配股依面額 10 元換成股數);
      沒有股利快取就只算價格報酬(來源標 twse_price_only);減資不在 FinMind 股利表裡,報酬會錯,另外標出極端值。
    - t1 停牌:用最後一個有成交的抽樣收盤價計算(不打折)。
    - t1 時已下市(之後再也不在行情表):用最後一個有成交的抽樣收盤價出場,再乘上 (1 + haircut)。
    """
    st = px.status(code, t1)
    if use_yahoo and st == "ok":
        s = _adj_close(f"{code}.TW")
        if s is not None:
            a, b = _asof(s, t0), _asof(s, t1)
            if a and b:
                return b / a - 1, "yahoo", st
    p0, _ = px.close(code, t0)
    if p0 is None:
        return None, "none", st
    p1, t_exit = px.close(code, t1)
    if p1 is None or t_exit is None or t_exit <= t0:
        p1, t_exit = p0, t0
    if px.corporate_action(code, t0, t_exit):
        return None, "corp_action", st
    ev = tw_dividend_events(code)
    src = "twse_price_only" if ev is None else "twse+div"
    shares, cash = 1.0, 0.0
    if ev is not None and len(ev):
        for _, e in ev[(ev["date"] > t0) & (ev["date"] <= t_exit)].iterrows():
            cash += shares * float(e["cash"])
            shares *= float(e["stock_ratio"])
    gross = (p1 * shares + cash) / p0
    if st == "delisted":
        gross *= 1 + haircut
    return gross - 1, src, st


def _tr_bench(t0, t1, tr: pd.Series) -> float | None:
    a, b = _asof(tr, t0), _asof(tr, t1)
    return b / a - 1 if a and b else None


def tw_backtest(start=FULL_START, end=None, n: int = 50, window_months: int = 12, haircut: float = 0.0,
                static: list[str] | None = None) -> pd.DataFrame:
    """每月等權持有一個月。static 給定時用固定名單(例如網站的 TW_LARGE),否則用每月 PIT 前 n 名。

    回傳每月一列:t0、t1、ret(等權)、bench(含息大盤)、n、n_delisted、各來源檔數、members。
    """
    end = pd.Timestamp(end or config.today())
    panel = weekly_panel(end)
    px = TWPrices(panel)
    rb = rebalance_dates(pd.Timestamp(start) - pd.DateOffset(months=1), end)
    tr = T.total_return_index("2014-01-01", end.strftime("%Y-%m-%d"))
    out = []
    for t0, t1 in zip(rb[:-1], rb[1:]):
        members = static if static is not None else rank_liquidity(panel, t0, n, window_months)["code"].tolist()
        rets, srcs, ndl, nsus = [], {}, 0, 0
        for c in members:
            code = c.split(".")[0]
            if c.endswith(".TWO"):  # 上櫃(信驊):只有 Yahoo
                s = _adj_close(c)
                a, b = (_asof(s, t0), _asof(s, t1)) if s is not None else (None, None)
                r, src, st = (b / a - 1, "yahoo", "ok") if a and b else (None, "none", "ok")
            else:
                r, src, st = tw_period_return(code, t0, t1, px, haircut)
            srcs[src] = srcs.get(src, 0) + 1
            if r is None:
                continue
            rets.append(r)
            ndl += int(st == "delisted")
            nsus += int(st == "suspended")
        out.append({"t0": t0, "t1": t1, "ret": float(np.mean(rets)) if rets else np.nan,
                    "bench": _tr_bench(t0, t1, tr), "n": len(rets), "n_delisted": ndl, "n_suspended": nsus, **{f"src_{k}": v for k, v in srcs.items()},
                    "members": [c.split(".")[0] for c in members]})
    df = pd.DataFrame(out)
    return df[df["t1"] >= pd.Timestamp(start)].reset_index(drop=True)


# ---------------------------------------------------------------- 美股:現有 S&P 500 代理
_sp500: dict[str, dict] = {}


def sp500_symbols() -> list[str]:
    tbl = pd.read_csv(config.DATA_DIR / "research" / "sp500_current.csv")
    return [s.replace(".", "-") for s in tbl["Symbol"].astype(str)]


def sp500_prices() -> dict[str, pd.DataFrame]:
    """現有 S&P 500 成分的日價格(Fetcher 的批次快取,key 規則同 scripts/fetch_sp500_prices.py)。"""
    if "px" not in _sp500:
        syms, px = sp500_symbols(), {}
        for i in range(0, len(syms), SP500_BATCH):
            try:
                px.update(cached("sp500_px", "|".join(syms[i:i + SP500_BATCH]), 10 ** 9, _no_fetch))
            except CacheMiss:
                pass
        _sp500["px"] = px
    return _sp500["px"]


def us_rank_from_prices(px: dict[str, pd.DataFrame], d, n: int = 40, window_months: int = 12) -> list[str]:
    """純函式:只用 date ≤ d 的列。平均成交金額 = 視窗內 Close × Volume 總和 ÷ 視窗交易日數(沒交易的日子算 0)。"""
    d = pd.Timestamp(d)
    lo = d - pd.DateOffset(months=window_months)
    dv, days = {}, set()
    for s, df in px.items():
        if s in SECOND_CLASS:
            continue
        w = df[(df.index <= d) & (df.index > lo)]
        if w.empty or (d - w.index[-1]).days > 7:
            continue
        days.update(w.index)
        dv[s] = float((w["Close"] * w["Volume"]).fillna(0).sum())
    nd = max(len(days), 1)
    ranked = sorted(dv.items(), key=lambda kv: (-kv[1] / nd, kv[0]))
    return [s for s, _ in ranked[:n]]


def us_universe_proxy(d, n: int = 40, window_months: int = 12) -> list[str]:
    """判斷日 d:現有 S&P 500 成分裡,d 以前 12 個月平均成交金額前 n 名(不是真正的 PIT,見模組說明)。"""
    return us_rank_from_prices(sp500_prices(), d, n, window_months)


def us_backtest(start=FULL_START, end=None, n: int = 40, static: list[str] | None = None) -> pd.DataFrame:
    """每月最後一個交易日換股、等權持有一個月;報酬用 Adj Close;大盤 ^SP500TR。"""
    end = pd.Timestamp(end or config.today())
    bench = _adj_close("^SP500TR")
    if bench is None:
        raise CacheMiss("缺 ^SP500TR")
    cal = bench.index[(bench.index >= pd.Timestamp(start) - pd.DateOffset(months=2)) & (bench.index <= end)]
    me = pd.Series(cal).groupby(pd.Series(cal).dt.to_period("M")).max().tolist()
    px = sp500_prices()
    out = []
    for t0, t1 in zip(me[:-1], me[1:]):
        members = static if static is not None else us_rank_from_prices(px, t0, n)
        rets = []
        for s in members:
            ser = px[s]["Adj Close"].dropna() if s in px else _adj_close(s)
            if ser is None:
                continue
            a, b = _asof(ser, t0), _asof(ser, t1)
            if a and b:
                rets.append(b / a - 1)
        out.append({"t0": t0, "t1": t1, "ret": float(np.mean(rets)) if rets else np.nan,
                    "bench": float(bench.asof(t1) / bench.asof(t0) - 1), "n": len(rets), "members": list(members)})
    df = pd.DataFrame(out)
    return df[df["t1"] >= pd.Timestamp(start)].reset_index(drop=True)


# ---------------------------------------------------------------- 統計
def ann(r: pd.Series) -> float | None:
    r = pd.Series(r).dropna()
    if r.empty:
        return None
    return float(np.prod(1 + r) ** (12 / len(r)) - 1)


def compare(a: pd.DataFrame, b: pd.DataFrame | None, label_a: str, label_b: str, lo, hi) -> dict:
    """t1 在 [lo, hi) 的月份:a 與 b(或含息大盤)的年化報酬、年化差距、月差 Newey–West(lag 3)。"""
    from .research.evaluate import newey_west
    sel = lambda x: x[(x["t1"] >= pd.Timestamp(lo)) & (x["t1"] < pd.Timestamp(hi))].set_index("t1")  # noqa: E731
    xa = sel(a)
    xb = sel(b)["ret"] if b is not None else xa["bench"]
    j = pd.concat([xa["ret"], xb], axis=1, keys=["a", "b"]).dropna()
    diff = j["a"] - j["b"]
    nw = newey_west(diff.to_numpy(), 3)
    aa, ab = ann(j["a"]), ann(j["b"])
    return {"a": label_a, "b": label_b, "from": str(j.index.min().date()), "to": str(j.index.max().date()), "months": len(j),
            "ann_a": aa, "ann_b": ab, "ann_gap": (aa - ab) if aa is not None and ab is not None else None,
            "mean_monthly_diff_x12": float(diff.mean() * 12),
            "nw_t": nw["t"] if nw else None, "nw_ci_x12": [c * 12 for c in nw["ci"]] if nw else None}


def by_year(a: pd.DataFrame, b: pd.DataFrame | None) -> list[dict]:
    """逐年的年度報酬差距(只到 2024-09;holdout 期間不單獨計算)。"""
    rows = []
    for y in range(FULL_START.year, HOLDOUT_START.year + 1):
        lo, hi = pd.Timestamp(f"{y}-01-01"), min(pd.Timestamp(f"{y + 1}-01-01"), HOLDOUT_START)
        xa = a[(a["t1"] >= lo) & (a["t1"] < hi)].set_index("t1")
        xb = b[(b["t1"] >= lo) & (b["t1"] < hi)].set_index("t1")["ret"] if b is not None else xa["bench"]
        j = pd.concat([xa["ret"], xb], axis=1, keys=["a", "b"]).dropna()
        if j.empty:
            continue
        ra, rb_ = float(np.prod(1 + j["a"]) - 1), float(np.prod(1 + j["b"]) - 1)
        rows.append({"year": f"{y}" + ("(1–9 月)" if hi == HOLDOUT_START else ""), "months": len(j),
                     "ret_a": ra, "ret_b": rb_, "gap": ra - rb_})
    return rows


def turnover(bt: pd.DataFrame, lo, hi) -> float:
    x = bt[(bt["t1"] >= pd.Timestamp(lo)) & (bt["t1"] < pd.Timestamp(hi))]["members"].tolist()
    ch = [len(set(b) - set(a)) / max(len(b), 1) for a, b in zip(x[:-1], x[1:])]
    return float(np.mean(ch)) if ch else float("nan")


def run(out_path=None) -> dict:
    from .universe import TW_LARGE, US_LARGE
    end = pd.Timestamp(config.today())
    res: dict = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "as_of": str(end.date()),
                 "holdout_start": str(HOLDOUT_START.date()), "dev": [str(DEV_START.date()), str(HOLDOUT_START.date())],
                 "full_note": "全期包含樣本外測試期(2024-10 起),只當背景數字,不單獨報告樣本外成績"}
    tw_static = [s for s, _ in TW_LARGE]
    pit = {h: tw_backtest(haircut=h) for h in DELIST_HAIRCUTS}
    site = tw_backtest(static=tw_static)
    base = pit[0.0]
    periods = {"dev": (DEV_START, HOLDOUT_START), "full": (FULL_START, end + pd.Timedelta(days=1))}
    tw = {}
    for k, (lo, hi) in periods.items():
        tw[k] = {
            "site40_vs_pit50": compare(site, base, "網站 TW_LARGE 40", "PIT 前 50", lo, hi),
            "site40_vs_bench": compare(site, None, "網站 TW_LARGE 40", "含息大盤", lo, hi),
            "pit50_vs_bench": compare(base, None, "PIT 前 50", "含息大盤", lo, hi),
            "pit50_haircut": {str(h): compare(pit[h], None, f"PIT 前 50(下市 {h:+.0%})", "含息大盤", lo, hi)
                              for h in DELIST_HAIRCUTS},
            "site40_vs_pit50_haircut": {str(h): compare(site, pit[h], "網站 40", f"PIT 50(下市 {h:+.0%})", lo, hi)
                                        for h in DELIST_HAIRCUTS},
            "pit50_turnover": turnover(base, lo, hi),
        }
    tw["by_year_site40_minus_pit50"] = by_year(site, base)
    tw["by_year_pit50_minus_bench"] = by_year(base, None)
    tw["by_year_site40_minus_bench"] = by_year(site, None)
    # 來源覆蓋
    src_cols = [c for c in base.columns if c.startswith("src_")]
    tw["return_sources_pit50"] = {c[4:]: int(base[c].fillna(0).sum()) for c in src_cols}
    tw["return_sources_site40"] = {c[4:]: int(site[c].fillna(0).sum()) for c in site.columns if c.startswith("src_")}
    tw["delisted_exits_pit50"] = int(base["n_delisted"].sum())
    tw["suspended_months_pit50"] = int(base["n_suspended"].sum())
    # 成員結構
    panel = weekly_panel(end)
    first = rank_liquidity(panel, rebalance_dates(FULL_START, end)[0], 50)
    in2016 = set(first["code"])
    site_listed = [s.split(".")[0] for s in tw_static if s.endswith(".TW")]
    tw["site40_not_in_pit50_at_2016_01"] = sorted(set(site_listed) - in2016)
    tw["site40_otc_excluded"] = [s for s in tw_static if s.endswith(".TWO")]
    mem = pd.DataFrame([(r.t0, c) for r in base.itertuples() for c in r.members], columns=["t0", "code"])
    dl = tw_delistings(panel)
    dlm = mem[mem["code"].isin(set(dl["code"]))]
    dl_info = dl.set_index("code")
    tw["delisted_members"] = [
        {"code": c, "name": str(dl_info.loc[c, "name"]) if c in dl_info.index else "",
         "delist_date": str(pd.Timestamp(dl_info.loc[c, "delist_date"]).date()) if c in dl_info.index else "",
         "last_close": float(dl_info.loc[c, "last_close"]) if c in dl_info.index and pd.notna(dl_info.loc[c, "last_close"]) else None,
         "member_months": int(len(g))}
        for c, g in dlm.groupby("code")]
    tw["union_members"] = sorted(set(mem["code"]))
    try:
        fetched = set((config.DATA_DIR / "research" / "tw_pit50_union.txt").read_text(encoding="utf-8").split())
        tw["members_not_in_fetcher_union"] = sorted(set(mem["code"]) - fetched)
    except OSError:
        tw["members_not_in_fetcher_union"] = None
    reuse = code_reuse(panel)
    tw["code_reuse_in_members"] = reuse[reuse["code"].isin(set(mem["code"]))].astype(str).to_dict("records") if len(reuse) else []
    tw["code_reuse_total"] = int(len(reuse))
    # 極端月報酬(可能是減資、資料錯誤)
    px = TWPrices(panel)
    ext = []
    for r in base[base["t1"] < HOLDOUT_START].itertuples():  # holdout 期間的個股報酬不列出
        for c in r.members:
            v, src, _ = tw_period_return(c, r.t0, r.t1, px)
            if v is not None and src != "yahoo" and (v > 0.6 or v < -0.4):
                ext.append({"code": c, "t0": str(r.t0.date()), "t1": str(r.t1.date()), "ret": v, "src": src})
    tw["extreme_twse_returns"] = ext
    # 方法核對:有 Yahoo 的股票,證交所 + FinMind 股利近似 vs Yahoo Adj Close
    chk = []
    for c in sorted(set(mem["code"])):
        s = _adj_close(f"{c}.TW")
        if s is None:
            continue
        for r in base.itertuples():
            if c not in r.members:
                continue
            y, _, _ = tw_period_return(c, r.t0, r.t1, px)
            t_, src, _ = tw_period_return(c, r.t0, r.t1, px, use_yahoo=False)
            if y is not None and t_ is not None and src == "twse+div":
                chk.append(y - t_)
    chk = np.array(chk)
    tw["method_check_yahoo_minus_twse"] = {"n": int(len(chk)), "mean": float(chk.mean()) if len(chk) else None,
                                           "median_abs": float(np.median(np.abs(chk))) if len(chk) else None,
                                           "share_abs_gt_1pct": float((np.abs(chk) > 0.01).mean()) if len(chk) else None}
    res["tw"] = tw
    res["missing_samples"] = sorted(set(missing_samples))

    # 美股
    us_static = [s for s, _ in US_LARGE]
    proxy = us_backtest()
    usite = us_backtest(static=us_static)
    us = {}
    for k, (lo, hi) in periods.items():
        us[k] = {"site40_vs_proxy40": compare(usite, proxy, "網站 US_LARGE 40", "現有 S&P 500 成交金額前 40", lo, hi),
                 "site40_vs_bench": compare(usite, None, "網站 US_LARGE 40", "^SP500TR", lo, hi),
                 "proxy40_vs_bench": compare(proxy, None, "現有 S&P 500 前 40", "^SP500TR", lo, hi),
                 "proxy40_turnover": turnover(proxy, lo, hi)}
    us["by_year_site40_minus_proxy40"] = by_year(usite, proxy)
    us["by_year_proxy40_minus_bench"] = by_year(proxy, None)
    us["sp500_symbols_with_prices"] = len(sp500_prices())
    us["site40_not_in_proxy40_at_2016_01"] = sorted(set(us_static) - set(proxy.iloc[0]["members"]))
    us["site40_months_with_data_min"] = int(usite["n"].min())
    res["us"] = us
    p = out_path or (config.RESEARCH_DIR / "survivorship_bias.json")
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    # 每月成員表(台股 PIT 前 50)
    pd.DataFrame([(r.t0.date(), i + 1, c) for r in base.itertuples() for i, c in enumerate(r.members)],
                 columns=["d", "rank", "code"]).to_csv(config.RESEARCH_DIR / "tw_pit50_membership.csv", index=False)
    return res


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: r[k] for k in ("tw", "us")}, ensure_ascii=False, indent=1, default=str)[:20000], file=sys.stdout)
