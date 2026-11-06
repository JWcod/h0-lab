"""籌碼面。

台股:三大法人買賣超、融資融券、外資持股比例、借券、集保大戶比例(FinMind / TDCC)。
美股:內部人交易、放空比例、機構持股(Yahoo)。
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .financials import safe

INST_GROUP = {
    "Foreign_Investor": "外資", "Foreign_Dealer_Self": "外資", "Investment_Trust": "投信",
    "Dealer_self": "自營商", "Dealer_Hedging": "自營商",
}


def _streak(s: pd.Series) -> int:
    n = 0
    for v in reversed(s.tolist()):
        if v > 0 and n >= 0:
            n += 1
        elif v < 0 and n <= 0:
            n -= 1
        else:
            break
    return n


def tw(fm: dict, shares_out: float | None, close: pd.Series | None, tdcc: dict | None) -> dict:
    out: dict = {"market": "TW"}
    inst = fm.get("institutional")
    if inst is not None and not inst.empty:
        d = inst.copy()
        d["group"] = d["name"].map(INST_GROUP)
        d = d.dropna(subset=["group"])
        d["net"] = (d["buy"] - d["sell"]) / 1000.0  # 張
        p = d.pivot_table(index="date", columns="group", values="net", aggfunc="sum").fillna(0)
        p.index = pd.to_datetime(p.index)
        p = p.sort_index()
        for g in ("外資", "投信", "自營商"):
            if g not in p:
                p[g] = 0.0
        p["合計"] = p[["外資", "投信", "自營商"]].sum(axis=1)
        summary = {}
        lots_out = shares_out / 1000 if shares_out else None
        for g in ("外資", "投信", "自營商", "合計"):
            s = p[g]
            row = {f"d{n}": safe(s.tail(n).sum()) for n in (1, 5, 20, 60)}
            row["streak"] = _streak(s)
            if lots_out:
                row["d5_pct"] = safe(s.tail(5).sum() / lots_out)
                row["d20_pct"] = safe(s.tail(20).sum() / lots_out)
                row["d60_pct"] = safe(s.tail(60).sum() / lots_out)
            summary[g] = row
        out["institutional"] = summary
        tail = p.tail(120)
        out["institutional_series"] = {
            "dates": [i.strftime("%Y-%m-%d") for i in tail.index],
            **{g: [safe(v) for v in tail[g]] for g in ("外資", "投信", "自營商")},
            "close": [safe(close.get(i)) if close is not None else None for i in tail.index],
        }
    m = fm.get("margin")
    if m is not None and not m.empty:
        m = m.copy()
        m["date"] = pd.to_datetime(m["date"])
        m = m.sort_values("date")
        mb, sb = m["MarginPurchaseTodayBalance"].astype(float), m["ShortSaleTodayBalance"].astype(float)
        lim = m["MarginPurchaseLimit"].astype(float).replace(0, np.nan)
        out["margin"] = {
            "margin_balance": safe(mb.iloc[-1]), "short_balance": safe(sb.iloc[-1]),
            "margin_chg_5d": safe(mb.iloc[-1] / mb.iloc[-6] - 1) if len(mb) > 6 and mb.iloc[-6] else None,
            "margin_chg_20d": safe(mb.iloc[-1] / mb.iloc[-21] - 1) if len(mb) > 21 and mb.iloc[-21] else None,
            "margin_usage": safe(mb.iloc[-1] / lim.iloc[-1]) if lim.iloc[-1] else None,
            "short_margin_ratio": safe(sb.iloc[-1] / mb.iloc[-1]) if mb.iloc[-1] else None,
            "margin_to_shares": safe(mb.iloc[-1] * 1000 / shares_out) if shares_out else None,
        }
        t = m.tail(120)
        out["margin_series"] = {"dates": [d.strftime("%Y-%m-%d") for d in t["date"]],
                                "margin": [safe(v) for v in t["MarginPurchaseTodayBalance"]],
                                "short": [safe(v) for v in t["ShortSaleTodayBalance"]]}
    sh = fm.get("shareholding")
    if sh is not None and not sh.empty:
        sh = sh.copy()
        sh["date"] = pd.to_datetime(sh["date"])
        sh = sh.sort_values("date")
        r = sh["ForeignInvestmentSharesRatio"].astype(float)
        out["foreign_holding"] = {
            "ratio": safe(r.iloc[-1]),
            "chg_20d": safe(r.iloc[-1] - r.iloc[-21]) if len(r) > 21 else None,
            "chg_60d": safe(r.iloc[-1] - r.iloc[-61]) if len(r) > 61 else None,
            "chg_250d": safe(r.iloc[-1] - r.iloc[-250]) if len(r) > 250 else None,
            "series": {"dates": [d.strftime("%Y-%m-%d") for d in sh["date"].tail(250)],
                       "ratio": [safe(v) for v in r.tail(250)]},
        }
    sbl = fm.get("short_balance")
    if sbl is not None and not sbl.empty:
        sbl = sbl.copy()
        sbl["date"] = pd.to_datetime(sbl["date"])
        sbl = sbl.sort_values("date")
        col = next((c for c in sbl.columns if "SBLShortSales" in c and "CurrentDayBalance" in c), None)
        if col:
            s = sbl[col].astype(float)
            out["sbl"] = {"balance": safe(s.iloc[-1]),
                          "chg_20d": safe(s.iloc[-1] / s.iloc[-21] - 1) if len(s) > 21 and s.iloc[-21] else None,
                          "to_shares": safe(s.iloc[-1] / shares_out) if shares_out else None}
    if tdcc:
        h = tdcc["history"]
        out["tdcc"] = {"latest": tdcc["latest"], "history": h,
                       "big400_pct": h[-1]["big400_pct"], "big1000_pct": h[-1]["big1000_pct"],
                       "small50_pct": h[-1]["small50_pct"], "holders": h[-1]["holders"],
                       "big1000_chg": (h[-1]["big1000_pct"] - h[-2]["big1000_pct"]) if len(h) >= 2 else None,
                       "holders_chg": (h[-1]["holders"] / h[-2]["holders"] - 1) if len(h) >= 2 and h[-2]["holders"] else None,
                       "weeks": len(h)}
    return out


def us(yb: dict) -> dict:
    info = yb.get("info") or {}
    out: dict = {"market": "US"}
    out["short"] = {
        "pct_float": safe(info.get("shortPercentOfFloat")),
        "days_to_cover": safe(info.get("shortRatio")),
        "shares_short": safe(info.get("sharesShort")),
        "shares_short_prior": safe(info.get("sharesShortPriorMonth")),
        "chg_vs_prior": safe(info["sharesShort"] / info["sharesShortPriorMonth"] - 1)
        if info.get("sharesShort") and info.get("sharesShortPriorMonth") else None,
    }
    out["ownership"] = {"institutions": safe(info.get("heldPercentInstitutions")),
                        "insiders": safe(info.get("heldPercentInsiders"))}
    it = yb.get("insider_transactions")
    if it is not None and not it.empty:
        d = it.copy()
        d["date"] = pd.to_datetime(d.get("Start Date"), errors="coerce")
        cutoff = pd.Timestamp(dt.date.today() - dt.timedelta(days=182))
        d6 = d[d["date"] >= cutoff]
        text = d6["Text"].fillna("").str.lower()
        buys = d6[text.str.contains("purchase")]
        sells = d6[text.str.contains("sale")]
        out["insiders"] = {
            "buy_count_6m": int(len(buys)), "sell_count_6m": int(len(sells)),
            "buy_value_6m": safe(buys["Value"].fillna(0).sum()), "sell_value_6m": safe(sells["Value"].fillna(0).sum()),
            "buyers": sorted(set(buys["Insider"].dropna().tolist()))[:8],
            "recent": [{"date": r.date.strftime("%Y-%m-%d") if pd.notna(r.date) else None, "insider": r.Insider,
                        "position": r.Position, "text": r.Text, "shares": safe(r.Shares), "value": safe(r.Value)}
                       for r in d.sort_values("date", ascending=False).head(15).itertuples()],
        }
    ih = yb.get("institutional_holders")
    if ih is not None and not ih.empty:
        out["top_institutions"] = [{"holder": r.Holder, "pct": safe(r.pctHeld), "shares": safe(r.Shares),
                                    "value": safe(r.Value), "chg": safe(r.pctChange),
                                    "date": str(r._1)[:10] if hasattr(r, "_1") else None}
                                   for r in ih.head(10).itertuples()]
        chg = ih["pctChange"].dropna()
        out["inst_net_adding"] = safe((chg > 0).mean()) if len(chg) else None
    return out
