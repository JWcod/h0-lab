"""市場預期:分析師目標價、評等分布、EPS / 營收預估、預估修正、財報驚喜、評等變動。"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .financials import safe

REC_SCORE = {"strongBuy": 5, "buy": 4, "hold": 3, "sell": 2, "strongSell": 1}


def _row(df: pd.DataFrame | None, period: str) -> dict:
    if df is None or df.empty or period not in df.index:
        return {}
    return {k: (safe(v) if not isinstance(v, str) else v) for k, v in df.loc[period].to_dict().items()}


def ntm_eps(info: dict, est: pd.DataFrame | None) -> tuple[float | None, str]:
    """未來 12 個月 EPS:依本會計年度剩餘比例,混合今年度與明年度預估。"""
    fy0, fy1 = _row(est, "0y").get("avg"), _row(est, "+1y").get("avg")
    if fy0 is None and fy1 is None:
        fwd = safe(info.get("forwardEps"))
        return fwd, "Yahoo forwardEps"
    if fy1 is None:
        return fy0, "今年度預估"
    if fy0 is None:
        return fy1, "明年度預估"
    nfy = info.get("nextFiscalYearEnd") or info.get("lastFiscalYearEnd")
    w = 0.5
    if nfy:
        try:
            end = dt.datetime.fromtimestamp(int(nfy))
            today = dt.datetime.now()
            if info.get("lastFiscalYearEnd") and int(info["lastFiscalYearEnd"]) > today.timestamp():
                end = dt.datetime.fromtimestamp(int(info["lastFiscalYearEnd"]))
            remain = (end - today).days / 365
            while remain > 1:
                remain -= 1
            w = min(1.0, max(0.0, remain))
        except (ValueError, OSError):
            pass
    return w * fy0 + (1 - w) * fy1, f"今年度 {w:.0%} + 明年度 {1 - w:.0%}"


def earnings_moves(hist: pd.DataFrame | None, ed: pd.DataFrame | None, n: int = 8) -> dict | None:
    """過去財報公布前後的股價反應:前一日收盤 → 隔一日收盤(涵蓋盤前或盤後公布)。"""
    if hist is None or hist.empty or ed is None or len(ed) == 0:
        return None
    close = hist["Close"]
    idx = close.index
    moves = []
    for d in sorted(pd.to_datetime(ed.index).tz_localize(None).normalize(), reverse=True):
        if d >= idx[-1] - pd.Timedelta(days=1):
            continue
        pos = idx.searchsorted(d)
        if pos < 1 or pos + 1 >= len(idx):
            continue
        r = float(close.iloc[pos + 1] / close.iloc[pos - 1] - 1)
        moves.append({"date": d.strftime("%Y-%m-%d"), "move": r})
        if len(moves) >= n:
            break
    if not moves:
        return None
    absm = [abs(m["move"]) for m in moves]
    return {"moves": moves, "avg_abs": sum(absm) / len(absm), "max_abs": max(absm),
            "ups": sum(1 for m in moves if m["move"] > 0), "n": len(moves)}


def analyze(yb: dict, price: float | None) -> dict:
    info = yb.get("info") or {}
    out: dict = {}
    tgt = yb.get("analyst_price_targets") or {}
    n = safe(info.get("numberOfAnalystOpinions"))
    out["targets"] = {"mean": safe(tgt.get("mean") or info.get("targetMeanPrice")),
                      "median": safe(tgt.get("median") or info.get("targetMedianPrice")),
                      "high": safe(tgt.get("high") or info.get("targetHighPrice")),
                      "low": safe(tgt.get("low") or info.get("targetLowPrice")), "n": n}
    med = out["targets"]["median"] or out["targets"]["mean"]
    out["targets"]["implied_upside"] = safe(med / price - 1) if med and price else None
    rec = yb.get("recommendations")
    if rec is not None and not rec.empty:
        hist = []
        for r in rec.itertuples():
            tot = sum(getattr(r, k, 0) or 0 for k in REC_SCORE)
            score = sum((getattr(r, k, 0) or 0) * v for k, v in REC_SCORE.items()) / tot if tot else None
            hist.append({"period": r.period, "strongBuy": int(r.strongBuy), "buy": int(r.buy), "hold": int(r.hold),
                         "sell": int(r.sell), "strongSell": int(r.strongSell), "score": safe(score), "total": int(tot)})
        out["recommendations"] = hist
        out["rec_score"] = hist[0]["score"] if hist else None
        out["rec_score_chg_3m"] = (hist[0]["score"] - hist[-1]["score"]) if len(hist) > 1 and hist[0]["score"] and hist[-1]["score"] else None
    out["rec_key"] = info.get("recommendationKey")

    ee, re_ = yb.get("earnings_estimate"), yb.get("revenue_estimate")
    out["eps_estimates"] = {p: _row(ee, p) for p in ("0q", "+1q", "0y", "+1y")}
    out["rev_estimates"] = {p: _row(re_, p) for p in ("0q", "+1q", "0y", "+1y")}
    et = yb.get("eps_trend")
    trend = {}
    for p in ("0q", "+1q", "0y", "+1y"):
        r = _row(et, p)
        cur = r.get("current")
        if cur is None:
            continue
        trend[p] = {"current": cur,
                    "chg_7d": safe(cur / r["7daysAgo"] - 1) if r.get("7daysAgo") else None,
                    "chg_30d": safe(cur / r["30daysAgo"] - 1) if r.get("30daysAgo") else None,
                    "chg_90d": safe(cur / r["90daysAgo"] - 1) if r.get("90daysAgo") else None}
    out["eps_trend"] = trend
    er = yb.get("eps_revisions")
    rev = {}
    for p in ("0q", "+1q", "0y", "+1y"):
        r = _row(er, p)
        if not r:
            continue
        na = _row(ee, p).get("numberOfAnalysts") or None
        up, dn = r.get("upLast30days") or 0, r.get("downLast30days") or 0
        rev[p] = {"up30": up, "down30": dn, "up7": r.get("upLast7days") or 0, "down7": r.get("downLast7Days") or 0,
                  "breadth": safe((up - dn) / na) if na else None}
    out["eps_revisions"] = rev

    # 財報驚喜(較長歷史用 earnings_dates)
    surprises = []
    ed = yb.get("earnings_dates")
    if ed is not None and not ed.empty:
        d = ed.copy()
        d = d[pd.notna(d.get("Reported EPS"))] if "Reported EPS" in d else d.iloc[0:0]
        for idx, r in d.head(12).iterrows():
            surprises.append({"date": pd.Timestamp(idx).strftime("%Y-%m-%d"), "estimate": safe(r.get("EPS Estimate")),
                              "actual": safe(r.get("Reported EPS")), "surprise": safe((r.get("Surprise(%)") or 0) / 100)
                              if r.get("Surprise(%)") is not None and pd.notna(r.get("Surprise(%)")) else None})
    if not surprises:
        eh = yb.get("earnings_history")
        if eh is not None and not eh.empty:
            for idx, r in eh.iterrows():
                surprises.append({"date": str(idx)[:10], "estimate": safe(r.get("epsEstimate")),
                                  "actual": safe(r.get("epsActual")), "surprise": safe(r.get("surprisePercent"))})
            surprises.reverse()
    out["surprises"] = surprises
    last4 = [s["surprise"] for s in surprises[:4] if s["surprise"] is not None]
    out["surprise_avg_4q"] = safe(sum(last4) / len(last4)) if last4 else None
    out["beats_4q"] = sum(1 for s in last4 if s > 0) if last4 else None

    ud = yb.get("upgrades_downgrades")
    if ud is not None and not ud.empty:
        d = ud.copy()
        d.index = pd.to_datetime(d.index)
        d = d[d.index >= pd.Timestamp(dt.date.today() - dt.timedelta(days=90))]
        acts = d["Action"].value_counts().to_dict() if "Action" in d else {}
        pta = d["priceTargetAction"].value_counts().to_dict() if "priceTargetAction" in d else {}
        out["rating_changes_90d"] = {"up": int(acts.get("up", 0)), "down": int(acts.get("down", 0)),
                                     "init": int(acts.get("init", 0)), "main": int(acts.get("main", 0)),
                                     "pt_raises": int(pta.get("Raises", 0)), "pt_lowers": int(pta.get("Lowers", 0))}
        out["rating_changes_list"] = [
            {"date": i.strftime("%Y-%m-%d"), "firm": r.get("Firm"), "to": r.get("ToGrade"), "from": r.get("FromGrade"),
             "action": r.get("Action"), "pt": safe(r.get("currentPriceTarget")), "pt_prev": safe(r.get("priorPriceTarget")),
             "pt_action": r.get("priceTargetAction")}
            for i, r in ud.head(25).iterrows()]
    cal = yb.get("calendar") or {}
    nxt = None
    if isinstance(cal, dict) and cal.get("Earnings Date"):
        try:
            nxt = cal["Earnings Date"][0]
            nxt = nxt.isoformat() if hasattr(nxt, "isoformat") else str(nxt)
        except (IndexError, TypeError):
            nxt = None
    out["next_earnings"] = nxt
    out["days_to_earnings"] = (dt.date.fromisoformat(nxt[:10]) - dt.date.today()).days if nxt else None
    ge = yb.get("growth_estimates")
    out["growth_estimates"] = {p: _row(ge, p) for p in ("0q", "+1q", "0y", "+1y", "+5y")} if ge is not None else {}
    eps, basis = ntm_eps(info, ee)
    out["ntm_eps"], out["ntm_basis"] = eps, basis
    out["earnings_moves"] = earnings_moves(yb.get("history"), yb.get("earnings_dates"))
    fy0, fy1 = _row(ee, "0y"), _row(ee, "+1y")
    out["fwd_eps_growth_fy1"] = safe(fy1["avg"] / fy0["avg"] - 1) if fy1.get("avg") and fy0.get("avg") and fy0["avg"] > 0 else None
    out["fwd_eps_growth_fy0"] = fy0.get("growth")
    r0, r1 = _row(re_, "0y"), _row(re_, "+1y")
    out["fwd_rev_growth_fy0"] = r0.get("growth")
    out["fwd_rev_growth_fy1"] = safe(r1["avg"] / r0["avg"] - 1) if r1.get("avg") and r0.get("avg") else None
    return out
