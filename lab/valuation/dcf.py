"""DCF(兩階段 FCFF)、蒙地卡羅、反向 DCF、敏感度表、EPV、P/B-ROE。"""
from __future__ import annotations

import numpy as np

from ..analysis.financials import safe


def growth_path(g0: float, g_term: float, years: int) -> np.ndarray:
    """第 1 年 g0,線性收斂到第 years 年的 g_term。"""
    if years == 1:
        return np.array([g0])
    return np.array([g0 + (g_term - g0) * i / (years - 1) for i in range(years)])


def dcf_value(rev0: float, g0: float, margin: float, wacc: float, g_term: float, years: int,
              net_debt: float, shares: float) -> dict | None:
    if wacc - g_term < 0.015 or shares <= 0:
        return None
    gs = growth_path(g0, g_term, years)
    revs = rev0 * np.cumprod(1 + gs)
    fcff = revs * margin
    disc = (1 + wacc) ** np.arange(1, years + 1)
    pv = fcff / disc
    tv = fcff[-1] * (1 + g_term) / (wacc - g_term)
    pv_tv = tv / disc[-1]
    ev = pv.sum() + pv_tv
    eq = ev - net_debt
    return {"per_share": eq / shares, "ev": ev, "equity": eq, "pv_explicit": pv.sum(), "pv_terminal": pv_tv,
            "tv_share": pv_tv / ev if ev else None,
            "path": [{"year": i + 1, "growth": float(gs[i]), "revenue": float(revs[i]), "fcff": float(fcff[i]),
                      "pv": float(pv[i])} for i in range(years)]}


def monte_carlo(rev0, g0, margin, wacc, g_term, years, net_debt, shares, draws=5000, seed=7) -> dict | None:
    rng = np.random.default_rng(seed)
    # 波動以「相對」為主,絕對下限設小一點:低利潤率公司(如 Walmart 2–3%)不會被誇大成極高不確定
    g = rng.normal(g0, max(0.02, 0.3 * abs(g0)), draws).clip(-0.25, 0.8)
    m = rng.normal(margin, max(0.005, 0.2 * abs(margin)), draws).clip(-0.2, 0.7)
    # 蒙地卡羅主要反映「營運」不確定(成長、利潤率);利率的影響另外看敏感度表,這裡只給小幅擾動
    w = rng.normal(wacc, 0.005, draws).clip(0.04, 0.2)
    gt = rng.normal(g_term, 0.003, draws).clip(0.0, 0.04)
    ok = (w - gt) >= 0.015
    g, m, w, gt = g[ok], m[ok], w[ok], gt[ok]
    if len(g) < 100:
        return None
    t = np.arange(years)
    gs = g[:, None] + (gt - g)[:, None] * (t[None, :] / max(1, years - 1))
    revs = rev0 * np.cumprod(1 + gs, axis=1)
    fcff = revs * m[:, None]
    disc = (1 + w)[:, None] ** (t[None, :] + 1)
    pv = (fcff / disc).sum(axis=1)
    tv = fcff[:, -1] * (1 + gt) / (w - gt)
    val = (pv + tv / disc[:, -1] - net_debt) / shares
    val = val[np.isfinite(val)]
    if len(val) < 100:
        return None
    pct = {f"p{p}": float(np.percentile(val, p)) for p in (5, 10, 25, 50, 75, 90, 95)}
    hist, edges = np.histogram(np.clip(val, pct["p5"] * (0.7 if pct["p5"] > 0 else 1.3), pct["p95"] * 1.3), bins=40)
    return {**pct, "mean": float(val.mean()), "n": int(len(val)),
            "hist": {"counts": hist.tolist(), "edges": [float(e) for e in edges]},
            "spread": float((pct["p90"] - pct["p10"]) / abs(pct["p50"])) if pct["p50"] else None}


def reverse_dcf(price, rev0, margin, wacc, g_term, years, net_debt, shares) -> float | None:
    """解出讓 DCF 價值 = 現價 的起始成長率(市場隱含成長)。"""
    def f(g):
        v = dcf_value(rev0, g, margin, wacc, g_term, years, net_debt, shares)
        return None if v is None else v["per_share"] - price

    lo, hi = -0.5, 1.5
    flo, fhi = f(lo), f(hi)
    if flo is None or fhi is None or flo * fhi > 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        fm = f(mid)
        if fm is None:
            return None
        if flo * fm <= 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def sensitivity(rev0, g0, margin, wacc, g_term, years, net_debt, shares) -> dict:
    waccs = [wacc + d for d in (-0.02, -0.01, 0, 0.01, 0.02)]
    gts = [g_term + d for d in (-0.01, -0.005, 0, 0.005, 0.01)]
    grid = []
    for w in waccs:
        row = []
        for gt in gts:
            v = dcf_value(rev0, g0, margin, w, gt, years, net_debt, shares)
            row.append(safe(v["per_share"]) if v else None)
        grid.append(row)
    margins = [margin + d for d in (-0.04, -0.02, 0, 0.02, 0.04)]
    g0s = [g0 + d for d in (-0.06, -0.03, 0, 0.03, 0.06)]
    grid2 = []
    for mg in margins:
        row = []
        for g in g0s:
            v = dcf_value(rev0, g, mg, wacc, g_term, years, net_debt, shares)
            row.append(safe(v["per_share"]) if v else None)
        grid2.append(row)
    return {"wacc": waccs, "g_term": gts, "grid": grid, "margins": margins, "g0s": g0s, "grid_margin_growth": grid2}


def epv(ebit_norm: float, tax: float, wacc: float, net_debt: float, shares: float) -> float | None:
    if ebit_norm is None or ebit_norm <= 0 or wacc <= 0 or shares <= 0:
        return None
    return (ebit_norm * (1 - tax) / wacc - net_debt) / shares


def pb_roe(roe: float, coe: float, g: float, bvps: float) -> float | None:
    if roe is None or bvps is None or bvps <= 0 or coe - g < 0.02:
        return None
    pb = max(0.2, (roe - g) / (coe - g))
    return pb * bvps
