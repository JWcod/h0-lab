"""大盤環境(市場狀態):大盤趨勢、波動(VIX)、利率、匯率。

市場狀態影響短期判斷:逆風環境下個股多頭訊號的勝率通常較低。
compute() 只吃歷史序列,所以回測時可以把序列截到某一天,重算「當時」的大盤環境。
"""
from __future__ import annotations

import numpy as np

from ..sources import yahoo
from .financials import safe


def _trend(h):
    if h is None or len(h) < 210:
        return None
    c = h["Close"]
    ma50, ma200 = c.rolling(50).mean(), c.rolling(200).mean()
    p = float(c.iloc[-1])
    return {
        "price": p, "vs_ma200": safe(p / ma200.iloc[-1] - 1), "vs_ma50": safe(p / ma50.iloc[-1] - 1),
        "ma200_slope": safe(ma200.iloc[-1] / ma200.iloc[-21] - 1),
        "ret_1m": safe(c.iloc[-1] / c.iloc[-22] - 1), "ret_3m": safe(c.iloc[-1] / c.iloc[-64] - 1),
        "dd_from_high_1y": safe(p / c.tail(252).max() - 1),
        "vol_20d": safe(np.log(c).diff().tail(20).std() * np.sqrt(252)),
    }


def compute(market: str, bench, vix=None, tnx=None, fx=None, market_pe: float | None = None) -> dict:
    out: dict = {"market": market}
    bench_sym = "^TWII" if market == "TW" else "^GSPC"
    out["benchmark"] = {"symbol": bench_sym, "name": "加權指數" if market == "TW" else "S&P 500", **(_trend(bench) or {})}
    out["benchmark_history"] = bench
    if vix is not None and len(vix):
        v = float(vix["Close"].iloc[-1])
        out["vix"] = {"value": v, "pct_1y": safe((vix["Close"].tail(252) < v).mean()),
                      "label": "平靜" if v < 15 else "正常" if v < 20 else "緊張" if v < 28 else "恐慌"}
    if tnx is not None and len(tnx) > 70:
        y = tnx["Close"] / 100
        out["us10y"] = {"value": safe(y.iloc[-1]), "chg_3m": safe(y.iloc[-1] - y.iloc[-64])}
    if market_pe:
        norm = 16.0 if market == "TW" else 19.0
        out["market_pe"] = {"etf": "0050.TW" if market == "TW" else "SPY", "pe": market_pe, "norm": norm,
                            "premium": market_pe / norm - 1, "earnings_yield": 1 / market_pe,
                            "label": "偏高" if market_pe > norm * 1.3 else "偏低" if market_pe < norm * 0.8 else "正常"}
    if market == "TW" and fx is not None and len(fx) > 70:
        out["usdtwd"] = {"value": safe(fx["Close"].iloc[-1]), "chg_3m": safe(fx["Close"].iloc[-1] / fx["Close"].iloc[-64] - 1)}
    # 分數:趨勢 50% + 波動 30% + 利率/匯率 20%
    b = out["benchmark"]
    if b.get("vs_ma200") is not None:
        t = 50 + 250 * b["vs_ma200"] + 600 * (b.get("ma200_slope") or 0)
        score = 0.5 * min(100, max(0, t))
    else:
        score = 25
    if "vix" in out:
        vv = out["vix"]["value"]
        score += 0.3 * min(100, max(0, 100 - (vv - 12) * 4))
    else:
        score += 15
    macro = 50.0
    if "us10y" in out and out["us10y"]["chg_3m"] is not None:
        macro -= out["us10y"]["chg_3m"] * 100 * 40  # 3 個月升息 0.5% → -20
    if "usdtwd" in out and out["usdtwd"]["chg_3m"] is not None:
        macro -= out["usdtwd"]["chg_3m"] * 100 * 5  # 台幣升值(USD/TWD 下跌)→ 外資流入,加分
    score += 0.2 * min(100, max(0, macro))
    out["score"] = round(score, 1)
    out["label"] = "順風" if score >= 62 else "逆風" if score < 42 else "中性"
    return out


def analyze(market: str) -> dict:
    bench = yahoo.index_history("^TWII" if market == "TW" else "^GSPC")
    vix = yahoo.index_history("^VIX", "2y")
    tnx = yahoo.index_history("^TNX", "2y")
    fx = yahoo.index_history("TWD=X", "2y") if market == "TW" else None
    # 大盤估值:用 ETF 的本益比當代理(台股 0050、美股 SPY)。長期常態約 台股 15–17 倍、美股 18–20 倍(粗略參考)
    ei = yahoo.quick_info("0050.TW" if market == "TW" else "SPY")
    return compute(market, bench, vix, tnx, fx, safe(ei.get("trailingPE")))
