"""技術面:趨勢、動能、波動、量能、支撐壓力、技術訊號,以及這檔股票自己的歷史訊號回測。

支撐壓力與技術訊號是資料頁的統計描述(2026-11-03 從 54dd23b 還原),不是交易計畫,也不進評分。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .financials import safe


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(~((dn == 0) & (up > 0)), 100.0)  # 完全沒有下跌日 → RSI = 100


def macd(close: pd.Series, f=12, s=26, sig=9):
    ef = close.ewm(span=f, adjust=False).mean()
    es = close.ewm(span=s, adjust=False).mean()
    line = ef - es
    signal = line.ewm(span=sig, adjust=False).mean()
    return line, signal, line - signal


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, lo, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def kd(df: pd.DataFrame, n: int = 9):
    """台灣常用 KD(9,3,3)。"""
    low_n = df["Low"].rolling(n).min()
    high_n = df["High"].rolling(n).max()
    rsv = (df["Close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    k = pd.Series(np.nan, index=df.index)
    d = pd.Series(np.nan, index=df.index)
    kv, dv = 50.0, 50.0
    for i, v in enumerate(rsv.to_numpy()):
        if np.isnan(v):
            continue
        kv = kv * 2 / 3 + v / 3
        dv = dv * 2 / 3 + kv / 3
        k.iloc[i], d.iloc[i] = kv, dv
    return k, d


def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["Close"].diff()).fillna(0)
    return (sign * df["Volume"]).cumsum()


def max_drawdown(s: pd.Series) -> float | None:
    s = s.dropna()
    if s.empty:
        return None
    peak = s.cummax()
    return float((s / peak - 1).min())


def _ret(s: pd.Series, days: int):
    s = s.dropna()
    if len(s) <= days:
        return None
    return float(s.iloc[-1] / s.iloc[-1 - days] - 1)



def swing_levels(df: pd.DataFrame, price: float, window: int = 10, lookback: int = 260) -> dict:
    """找近一年的擺動高低點,價位相近(±2%)的合併成一個區,依觸碰次數排序。"""
    d = df.tail(lookback)
    highs, lows = d["High"].to_numpy(), d["Low"].to_numpy()
    pts = []
    for i in range(window, len(d) - window):
        if highs[i] == highs[i - window:i + window + 1].max():
            pts.append(highs[i])
        if lows[i] == lows[i - window:i + window + 1].min():
            pts.append(lows[i])
    pts.sort()
    clusters: list[list[float]] = []
    for p in pts:
        if clusters and abs(p / np.mean(clusters[-1]) - 1) < 0.02:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    levels = [{"price": float(np.mean(c)), "touches": len(c)} for c in clusters]
    sup = sorted([x for x in levels if x["price"] < price * 0.995], key=lambda x: -x["price"])[:3]
    res = sorted([x for x in levels if x["price"] > price * 1.005], key=lambda x: x["price"])[:3]
    return {"support": sup, "resistance": res}


def analyze(h: pd.DataFrame, bench: pd.DataFrame | None, market: str, light: bool = False) -> dict:
    """light=True:歷史回測用,省略圖表、季節性、回測(只算評分需要的指標)。"""
    if h is None or h.empty or len(h) < 60:
        return {"available": False}
    df = h.copy()
    df = df[df["Close"] > 0]
    close = df["Close"]
    adj = df["Adj Close"] if "Adj Close" in df else close
    price = float(close.iloc[-1])
    out: dict = {"available": True, "price": price, "date": df.index[-1].strftime("%Y-%m-%d")}

    ma = {n: close.rolling(n).mean() for n in (5, 10, 20, 50, 60, 120, 200, 240)}
    out["ma"] = {f"ma{n}": safe(s.iloc[-1]) for n, s in ma.items()}
    out["price_vs_ma"] = {f"ma{n}": safe(price / s.iloc[-1] - 1) for n, s in ma.items() if safe(s.iloc[-1])}
    ma200 = ma[200]
    out["ma200_slope_20d"] = safe(ma200.iloc[-1] / ma200.iloc[-21] - 1) if len(ma200.dropna()) > 21 else None
    ma50 = ma[50]
    out["ma50_slope_20d"] = safe(ma50.iloc[-1] / ma50.iloc[-21] - 1) if len(ma50.dropna()) > 21 else None
    # 黃金/死亡交叉(最近 30 個交易日)
    cross = None
    if len(ma200.dropna()) > 31:
        diff = (ma50 - ma200).dropna().tail(31)
        sgn = np.sign(diff.to_numpy())
        for i in range(1, len(sgn)):
            if sgn[i - 1] < 0 < sgn[i]:
                cross = {"type": "golden", "date": diff.index[i].strftime("%Y-%m-%d")}
            elif sgn[i - 1] > 0 > sgn[i]:
                cross = {"type": "death", "date": diff.index[i].strftime("%Y-%m-%d")}
    out["ma_cross"] = cross

    r = rsi(close)
    line, sig, hist = macd(close)
    k, d = kd(df)
    a = atr(df)
    bb_mid = ma[20]
    bb_std = close.rolling(20).std()
    bb_up, bb_dn = bb_mid + 2 * bb_std, bb_mid - 2 * bb_std
    out["rsi14"] = safe(r.iloc[-1])
    out["macd"] = {"line": safe(line.iloc[-1]), "signal": safe(sig.iloc[-1]), "hist": safe(hist.iloc[-1]),
                   "hist_prev": safe(hist.iloc[-2])}
    out["kd"] = {"k": safe(k.iloc[-1]), "d": safe(d.iloc[-1]), "k_prev": safe(k.iloc[-2]), "d_prev": safe(d.iloc[-2])}
    out["atr14"] = safe(a.iloc[-1])
    out["atr_pct"] = safe(a.iloc[-1] / price)
    out["bollinger"] = {"upper": safe(bb_up.iloc[-1]), "lower": safe(bb_dn.iloc[-1]), "mid": safe(bb_mid.iloc[-1]),
                        "pct_b": safe((price - bb_dn.iloc[-1]) / (bb_up.iloc[-1] - bb_dn.iloc[-1])),
                        "bandwidth": safe((bb_up.iloc[-1] - bb_dn.iloc[-1]) / bb_mid.iloc[-1])}
    bw = ((bb_up - bb_dn) / bb_mid).dropna()
    out["bollinger"]["squeeze"] = bool(len(bw) > 130 and bw.iloc[-1] <= bw.tail(126).quantile(0.1))

    # 報酬
    rets = {}
    for label, n in (("1w", 5), ("1m", 21), ("3m", 63), ("6m", 126), ("1y", 252), ("3y", 756), ("5y", 1260)):
        rets[label] = _ret(adj, n)
    ytd_base = adj[adj.index.year < df.index[-1].year]
    rets["ytd"] = safe(adj.iloc[-1] / ytd_base.iloc[-1] - 1) if len(ytd_base) else None
    out["returns"] = rets
    out["mom_12_1"] = safe(adj.iloc[-22] / adj.iloc[-253] - 1) if len(adj) > 253 else None

    hi52 = df["High"].tail(252).max()
    lo52 = df["Low"].tail(252).min()
    out["high_52w"], out["low_52w"] = safe(hi52), safe(lo52)
    out["pct_from_52w_high"] = safe(price / hi52 - 1)
    out["pos_in_52w_range"] = safe((price - lo52) / (hi52 - lo52)) if hi52 > lo52 else None
    out["all_time_high"] = safe(df["High"].max())
    out["drawdown_from_ath"] = safe(price / df["High"].max() - 1)

    lr = np.log(adj).diff().dropna()
    out["vol_20d"] = safe(lr.tail(20).std() * math.sqrt(252))
    out["vol_60d"] = safe(lr.tail(60).std() * math.sqrt(252))
    out["vol_1y"] = safe(lr.tail(252).std() * math.sqrt(252))
    neg = lr.tail(252)[lr.tail(252) < 0]
    out["downside_vol_1y"] = safe(neg.std() * math.sqrt(252)) if len(neg) > 10 else None
    out["max_dd_1y"] = max_drawdown(adj.tail(252))
    out["max_dd_5y"] = max_drawdown(adj.tail(1260))
    out["var95_1d"] = safe(-np.percentile(lr.tail(500), 5)) if len(lr) > 250 else None
    tail = lr.tail(500)
    out["cvar95_1d"] = safe(-tail[tail <= np.percentile(tail, 5)].mean()) if len(tail) > 250 else None
    out["skew_1y"] = safe(lr.tail(252).skew())
    out["kurt_1y"] = safe(lr.tail(252).kurt())

    v = df["Volume"].replace(0, np.nan)
    out["vol_ratio_20_60"] = safe(v.tail(20).mean() / v.tail(60).mean()) if v.tail(60).mean() else None
    out["vol_ratio_5_20"] = safe(v.tail(5).mean() / v.tail(20).mean()) if v.tail(20).mean() else None
    out["avg_turnover_20d"] = safe((close * df["Volume"]).tail(20).mean())
    ob = obv(df)
    out["obv_slope_20d"] = safe((ob.iloc[-1] - ob.iloc[-21]) / (v.tail(20).mean() * 20)) if v.tail(20).mean() else None

    # 相對大盤
    if bench is not None and not bench.empty:
        b = bench["Close"].reindex(adj.index).ffill()
        rel = {}
        for label, n in (("1m", 21), ("3m", 63), ("6m", 126), ("1y", 252)):
            sr, br = _ret(adj, n), _ret(b, n)
            rel[label] = (sr - br) if sr is not None and br is not None else None
        out["relative"] = rel
        wk = pd.concat([adj, b], axis=1, keys=["s", "b"]).dropna().resample("W-FRI").last().pct_change().dropna().tail(104)
        if len(wk) > 30 and wk["b"].var() > 0:
            beta_raw = wk["s"].cov(wk["b"]) / wk["b"].var()
            out["beta_raw"] = safe(beta_raw)
            out["beta_adj"] = safe(min(2.0, max(0.6, 0.67 * beta_raw + 0.33)))
            out["corr_bench"] = safe(wk["s"].corr(wk["b"]))
    out["trend"] = classify_trend(price, out)
    if light:
        return out
    out["levels"] = swing_levels(df, price)          # 只給技術面頁顯示;評分與回測(light)不用
    out["signals"] = signals(df, out, r, line, sig, k, d)
    out["seasonality"] = seasonality(adj)
    out["backtest"] = backtests(adj, market)
    out["chart"] = chart_data(df, ma, r, line, sig, hist, k, d, bb_up, bb_dn)
    return out


def classify_trend(price: float, t: dict) -> dict:
    ma50, ma200 = t["ma"].get("ma50"), t["ma"].get("ma200")
    slope = t.get("ma200_slope_20d")
    if not ma50 or not ma200:
        return {"label": "資料不足", "code": 0}
    if price > ma50 > ma200 and (slope or 0) > 0:
        return {"label": "強勢多頭", "code": 2}
    if price > ma200 and (slope or 0) >= 0:
        return {"label": "多頭", "code": 1}
    if price < ma50 < ma200 and (slope or 0) < 0:
        return {"label": "強勢空頭", "code": -2}
    if price < ma200 and (slope or 0) <= 0:
        return {"label": "空頭", "code": -1}
    return {"label": "盤整/轉折", "code": 0}



def signals(df, t, r, line, sig, k, d) -> list[dict]:
    s = []

    def add(name, direction, detail):
        s.append({"name": name, "direction": direction, "detail": detail})

    if t["ma_cross"]:
        if t["ma_cross"]["type"] == "golden":
            add("黃金交叉", "多", f"MA50 於 {t['ma_cross']['date']} 上穿 MA200")
        else:
            add("死亡交叉", "空", f"MA50 於 {t['ma_cross']['date']} 下穿 MA200")
    rv = t["rsi14"]
    if rv is not None:
        if rv >= 70:
            add("RSI 超買", "空", f"RSI(14) = {rv:.0f} ≥ 70,短線過熱")
        elif rv <= 30:
            add("RSI 超賣", "多", f"RSI(14) = {rv:.0f} ≤ 30,短線超跌")
    if len(line) > 5:
        h_now, h_prev = line.iloc[-1] - sig.iloc[-1], line.iloc[-4] - sig.iloc[-4]
        if h_prev < 0 < h_now:
            add("MACD 黃金交叉", "多", "MACD 線近 3 日上穿訊號線")
        elif h_prev > 0 > h_now:
            add("MACD 死亡交叉", "空", "MACD 線近 3 日下穿訊號線")
    kv, dv, kp, dp = t["kd"]["k"], t["kd"]["d"], t["kd"]["k_prev"], t["kd"]["d_prev"]
    if None not in (kv, dv, kp, dp):
        if kp < dp and kv > dv and kv < 30:
            add("KD 低檔黃金交叉", "多", f"K={kv:.0f} 在 30 以下上穿 D")
        elif kp > dp and kv < dv and kv > 70:
            add("KD 高檔死亡交叉", "空", f"K={kv:.0f} 在 70 以上下穿 D")
    if t["pct_from_52w_high"] is not None and t["pct_from_52w_high"] > -0.02:
        add("接近 52 週高點", "多", "距 52 週高點 2% 內;George & Hwang (2004):接近 52 週高點的股票後續報酬較好")
    if t["pos_in_52w_range"] is not None and t["pos_in_52w_range"] < 0.1:
        add("接近 52 週低點", "空", "位於 52 週區間底部 10%")
    if t["bollinger"]["squeeze"]:
        add("布林通道壓縮", "中性", "通道寬度在半年最低 10%,常是大波動前兆(方向未定)")
    pb = t["bollinger"]["pct_b"]
    if pb is not None and pb > 1:
        add("突破布林上緣", "多", "收盤在上緣之上:強勢,但也可能短線過熱")
    elif pb is not None and pb < 0:
        add("跌破布林下緣", "空", "收盤在下緣之下:弱勢,或短線超跌")
    vr = t.get("vol_ratio_5_20")
    r1w = t["returns"].get("1w")
    if vr and vr > 1.8 and r1w is not None:
        add("爆量" + ("上漲" if r1w > 0 else "下跌"), "多" if r1w > 0 else "空", f"近 5 日均量是 20 日均量的 {vr:.1f} 倍")
    p200 = t["price_vs_ma"].get("ma200")
    if p200 is not None and len(df) > 205:
        c = df["Close"]
        ma200 = c.rolling(200).mean()
        prev_above = c.iloc[-6] > ma200.iloc[-6]
        now_above = c.iloc[-1] > ma200.iloc[-1]
        if not prev_above and now_above:
            add("站上 200 日線", "多", "近一週由下往上突破長期均線")
        elif prev_above and not now_above:
            add("跌破 200 日線", "空", "近一週由上往下跌破長期均線")
    return s


def seasonality(adj: pd.Series) -> list[dict]:
    m = adj.resample("ME").last().pct_change().dropna()
    if len(m) < 24:
        return []
    out = []
    for month in range(1, 13):
        x = m[m.index.month == month]
        if len(x):
            out.append({"month": month, "avg": safe(x.mean()), "win_rate": safe((x > 0).mean()), "n": int(len(x))})
    return out


def backtests(adj: pd.Series, market: str) -> dict:
    """這檔股票自己的歷史:簡單規則 vs 持有不動(樣本內、僅供參考)。
    成本:台股來回約 0.6%(手續費 0.1425%×2 + 證交稅 0.3%),美股 0.1%。"""
    adj = adj.dropna()
    if len(adj) < 600:
        return {}
    cost = 0.006 if market == "TW" else 0.001
    ret = adj.pct_change().fillna(0)
    ma200 = adj.rolling(200).mean()
    rules = {
        "站上 200 日線才持有": (adj > ma200).astype(float),
        "12 個月動能為正才持有": (adj / adj.shift(252) - 1 > 0).astype(float),
        "MA50 > MA200 才持有": (adj.rolling(50).mean() > ma200).astype(float),
    }
    start = adj.index[252]
    res = {}
    curves = {"date": [d.strftime("%Y-%m-%d") for d in adj.loc[start:].index[::5]]}
    bh = (1 + ret.loc[start:]).cumprod()
    curves["持有不動"] = [safe(v) for v in bh.iloc[::5]]
    res["持有不動"] = _stats(ret.loc[start:], bh)
    for name, pos in rules.items():
        p = pos.shift(1).fillna(0).loc[start:]  # 用前一天的訊號,避免偷看未來
        trades = p.diff().abs().fillna(0)
        r = p * ret.loc[start:] - trades * cost / 2
        eq = (1 + r).cumprod()
        st = _stats(r, eq)
        st["time_in_market"] = safe(p.mean())
        st["trades"] = int(trades.sum())
        res[name] = st
        curves[name] = [safe(v) for v in eq.iloc[::5]]
    # 前瞻報酬:依目前趨勢狀態分組(歷史上同樣狀態下,之後 60 日的報酬分布)
    fwd60 = adj.shift(-60) / adj - 1
    state = pd.Series(np.where(adj > ma200, "在 200 日線上", "在 200 日線下"), index=adj.index).where(ma200.notna())
    st = pd.DataFrame({"fwd": fwd60, "state": state}).dropna()  # 前 200 天沒有均線,不列入
    cond = {}
    for s_name, g in st.groupby("state"):
        cond[s_name] = {"avg_fwd60": safe(g["fwd"].mean()), "win_rate": safe((g["fwd"] > 0).mean()), "n": int(len(g))}
    return {"stats": res, "curves": curves, "conditional": cond, "cost_roundtrip": cost,
            "note": "樣本內歷史回測,單一股票樣本小,只能當參考,不代表未來"}


def _stats(r: pd.Series, eq: pd.Series) -> dict:
    yrs = len(r) / 252
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 and eq.iloc[-1] > 0 else None
    vol = r.std() * math.sqrt(252)
    sharpe = (r.mean() * 252) / vol if vol > 0 else None
    return {"cagr": safe(cagr), "vol": safe(vol), "sharpe": safe(sharpe), "max_dd": max_drawdown(eq),
            "total": safe(eq.iloc[-1] - 1)}


def chart_data(df, ma, r, line, sig, hist, k, d, bb_up, bb_dn, days: int = 750) -> dict:
    x = df.tail(days)
    idx = x.index

    def ser(s):
        return [safe(v) for v in s.reindex(idx).round(4)]

    return {
        "dates": [i.strftime("%Y-%m-%d") for i in idx],
        "ohlc": [[safe(o), safe(c), safe(lo), safe(h)] for o, c, lo, h in zip(x["Open"], x["Close"], x["Low"], x["High"])],
        "volume": [safe(v) for v in x["Volume"]],
        "ma20": ser(ma[20]), "ma50": ser(ma[50]), "ma200": ser(ma[200]), "ma60": ser(ma[60]), "ma240": ser(ma[240]),
        "bb_up": ser(bb_up), "bb_dn": ser(bb_dn),
        "rsi": ser(r), "macd": ser(line), "macd_signal": ser(sig), "macd_hist": ser(hist), "k": ser(k), "d": ser(d),
    }
