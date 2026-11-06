"""公平價模型:把 DCF、本益比河流、同業、分析師、EPV、P/B-ROE 綜合成一個公平價區間與不確定性等級。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..analysis.financials import safe
from . import dcf as D


def cost_of_capital(market: str, beta: float | None, rf_us: float | None, params: dict, market_cap: float | None,
                    total_debt: float, interest_expense: float | None, tax: float, rates: dict | None = None) -> dict:
    v = params["valuation"]
    # P2-H01(參數開關 rates_asof):判斷日當時的台灣 10 年期公債年平均、Damodaran 年初的台灣總 ERP / 成熟市場 ERP;
    # 沒有資料的項目維持參數值。v1.1 參數沒有這個鍵 → 一律用參數值(基準可逐位元重現)
    r = (rates or {}) if v.get("rates_asof") else {}
    mature = r.get("erp_mature", v["erp_mature"])
    if market == "TW":
        rf = r.get("tw_rf", v["tw_rf"])
        erp = r.get("tw_erp", v["tw_erp"])
        floor_coe = v["coe_floor_tw"]
    else:
        rf = rf_us if rf_us else v["us_rf_fallback"]
        erp = mature
        floor_coe = v["coe_floor_us"]
    crp = erp - mature
    b = beta if beta else 1.0
    coe = max(rf + b * erp, floor_coe)
    kd = None
    if interest_expense and total_debt and total_debt > 0:
        kd = abs(interest_expense) / total_debt
    if kd is None or not (rf + 0.005 <= kd <= rf + 0.06):
        kd = min(max(kd or (rf + 0.015), rf + 0.005), rf + 0.06)
    e = market_cap or 0
    d = max(total_debt or 0, 0)
    we = e / (e + d) if e + d > 0 else 1.0
    wacc = we * coe + (1 - we) * kd * (1 - tax)
    floor = v["wacc_floor_tw"] if market == "TW" else v["wacc_floor"]
    wacc_c = min(max(wacc, floor), v["wacc_cap"])
    return {"rf": rf, "beta": b, "erp": mature, "crp": crp, "rates_source": "as_of" if r else "params", "coe_floor": floor_coe, "cost_of_equity": coe, "cost_of_debt": kd,
            "weight_equity": we, "wacc_raw": wacc, "wacc": wacc_c, "tax": tax}


def pe_band(pe_hist: pd.Series | None, eps_fwd: float | None, eps_ttm: float | None, price: float,
            coe: float = 0.08) -> dict | None:
    """本益比河流:用近 5 年本益比(近四季 EPS 基礎)分位數 × EPS。

    用未來 12 個月 EPS 時:一年後的近四季 EPS ≈ 現在的 NTM EPS,
    所以「一年後的合理價 = 歷史本益比 × NTM EPS」,再用股權成本折回今天。
    """
    if pe_hist is None or len(pe_hist.dropna()) < 100:
        return None
    s = pe_hist.dropna()
    s = s[(s > 0) & (s < 300)]
    if len(s) < 100:  # 過去多半虧損(本益比無意義)時不適用
        return None
    s5 = s[s.index >= s.index[-1] - pd.Timedelta(days=365 * 5)]
    if len(s5) < 100:
        return None
    q = {p: float(np.percentile(s5, p)) for p in (10, 25, 50, 75, 90)}
    eps = eps_fwd if eps_fwd and eps_fwd > 0 else eps_ttm
    if not eps or eps <= 0:
        return {"percentiles": q, "current_pe": safe(s.iloc[-1]), "value": None}
    cur = s.iloc[-1]
    rank = float((s5 < cur).mean())
    fwd = bool(eps_fwd and eps_fwd > 0)
    disc = 1 / (1 + coe) if fwd else 1.0
    return {"percentiles": q, "current_pe": safe(cur), "current_pct_rank": rank, "eps_used": eps,
            "eps_basis": "未來 12 個月預估(一年後價格以股權成本折現)" if fwd else "近四季",
            "discount": disc, "low": q[25] * eps * disc, "value": q[50] * eps * disc, "high": q[75] * eps * disc,
            "years": round(len(s5) / 250, 1)}


def river(price_s: pd.Series, eps_ttm_s: pd.Series, pcts: dict, days: int = 1250) -> dict | None:
    """畫河流圖:每天的 TTM EPS × 歷史本益比分位數。"""
    if price_s is None or eps_ttm_s is None or eps_ttm_s.dropna().empty:
        return None
    eps = eps_ttm_s.reindex(price_s.index, method="ffill")
    df = pd.DataFrame({"price": price_s, "eps": eps}).dropna().tail(days)
    df = df[df["eps"] > 0]
    if len(df) < 60:
        return None
    step = max(1, len(df) // 400)
    df = df.iloc[::step]
    out = {"dates": [d.strftime("%Y-%m-%d") for d in df.index], "price": [safe(v) for v in df["price"]]}
    for p, pe in pcts.items():
        out[f"pe{p}"] = [safe(v * pe) for v in df["eps"]]
    out["pe_levels"] = {str(k): v for k, v in pcts.items()}
    return out


def peers_value(peer_rows: list[dict], eps_fwd, ebitda, net_debt, shares, revenue, unprofitable: bool) -> dict | None:
    if not peer_rows:
        return None
    def med(key):
        xs = [r[key] for r in peer_rows if r.get(key) is not None and 0 < r[key] < 200]
        return (float(np.median(xs)), float(np.percentile(xs, 25)), float(np.percentile(xs, 75)), len(xs)) if len(xs) >= 3 else None

    vals = []
    detail = {}
    fpe = med("forward_pe") or med("pe")
    if fpe and eps_fwd and eps_fwd > 0 and not unprofitable:
        vals.append((fpe[0] * eps_fwd, fpe[1] * eps_fwd, fpe[2] * eps_fwd))
        detail["pe"] = {"median": fpe[0], "p25": fpe[1], "p75": fpe[2], "n": fpe[3]}
    eve = med("ev_ebitda")
    if eve and ebitda and ebitda > 0 and shares:
        f = lambda m: (m * ebitda - net_debt) / shares  # noqa: E731
        vals.append((f(eve[0]), f(eve[1]), f(eve[2])))
        detail["ev_ebitda"] = {"median": eve[0], "p25": eve[1], "p75": eve[2], "n": eve[3]}
    if unprofitable:
        evs = med("ev_sales")
        if evs and revenue and shares:
            f = lambda m: (m * revenue - net_debt) / shares  # noqa: E731
            vals.append((f(evs[0]), f(evs[1]), f(evs[2])))
            detail["ev_sales"] = {"median": evs[0], "p25": evs[1], "p75": evs[2], "n": evs[3]}
    vals = [v for v in vals if v[0] and v[0] > 0]
    if not vals:
        return {"value": None, "detail": detail}
    return {"value": float(np.mean([v[0] for v in vals])), "low": float(np.mean([v[1] for v in vals])),
            "high": float(np.mean([v[2] for v in vals])), "detail": detail}


def classify(industry_key: str, industry_tw: str, ni_ttm, eps_ttm, growth_ref) -> str:
    """金融股只算銀行、保險(Visa 這類支付公司歸一般公司)。"""
    ik = (industry_key or "").lower()
    if any(k in ik for k in ("bank", "insurance", "mortgage")) or any(k in (industry_tw or "") for k in ("金融", "保險", "銀行")):
        return "financial"
    if (ni_ttm is not None and ni_ttm <= 0) or (eps_ttm is not None and eps_ttm <= 0):
        return "unprofitable"
    if growth_ref is not None and growth_ref > 0.15:
        return "growth"
    return "mature"


PRICE_ANCHORED = ("analyst",)   # 以現價推算的方法(P2-H04)
TYPE_LABEL = {"mature": "成熟獲利型", "growth": "高成長型", "unprofitable": "尚未獲利型", "financial": "金融股"}
METHOD_LABEL = {"dcf": "DCF 現金流折現(蒙地卡羅)", "pe_band": "歷史本益比河流", "peers": "同業相對估值",
                "analyst": "分析師目標價(偏誤校正)", "epv": "EPV 零成長獲利價值", "pb_roe": "P/B–ROE(金融股)"}


def uncertainty_level(mc_spread: float | None, methods: list[dict], vol_1y: float | None, ctype: str) -> tuple[str, float]:
    """不確定性 = 蒙地卡羅分布寬度 40% + 各方法分歧度 30% + 股價波動 30%(再依公司類型加碼)。"""
    vals = [m["value"] for m in methods if m.get("value") and m.get("included")]
    cv = float(np.std(vals) / np.mean(vals)) if len(vals) >= 2 and np.mean(vals) > 0 else 0.3
    mc = mc_spread if mc_spread is not None else 0.9
    vol = vol_1y if vol_1y is not None else 0.35
    score = 0.4 * mc + 0.3 * (1.5 * cv) + 0.3 * (1.5 * vol)
    if ctype == "unprofitable":
        score += 0.25
    level = "低" if score < 0.35 else "中" if score < 0.6 else "高" if score < 0.9 else "很高"
    return level, score


def run(ctx: dict, params: dict) -> dict:
    """ctx:報告組裝時準備好的所有輸入(見 report.py)。"""
    price = ctx["price"]
    market = ctx["market"]
    v = params["valuation"]
    out: dict = {"price": price, "currency": ctx.get("currency")}
    shares = ctx.get("shares")
    net_debt = ctx.get("net_debt") or 0.0
    rev0 = ctx.get("revenue_ttm")
    tax = ctx.get("tax_rate") or 0.2
    ctype = classify(ctx.get("industry_key"), ctx.get("industry_tw"), ctx.get("ni_ttm"), ctx.get("eps_ttm"), ctx.get("growth_ref"))
    out["company_type"], out["company_type_label"] = ctype, TYPE_LABEL[ctype]
    # 折現率依「財報幣別」決定:台積電 ADR 的現金流是台幣,就用台幣利率(避免美元利率折現台幣現金流)
    rate_mkt = "TW" if (market == "TW" or ctx.get("financial_currency") == "TWD") else "US"
    coc = cost_of_capital(rate_mkt, ctx.get("beta"), ctx.get("rf_us"), params, ctx.get("market_cap"),
                          ctx.get("total_debt") or 0, ctx.get("interest_expense"), tax, ctx.get("rates_asof"))
    coc["rate_basis"] = "台幣利率" if rate_mkt == "TW" else "美元利率"
    out["wacc"] = coc
    wacc = coc["wacc"]
    g_term = v["terminal_growth_tw"] if rate_mkt == "TW" else v["terminal_growth_us"]
    if v.get("rates_asof"):
        g_term = min(g_term, coc["rf"])   # P2-H01:永續成長不高於判斷日當時的無風險利率(Damodaran 一致性原則)
    years = v["forecast_years"]
    methods: list[dict] = []

    # --- DCF ---
    margin = ctx.get("fcff_margin")
    g0 = ctx.get("g_start")
    dcf_out = None
    if ctype != "financial" and rev0 and shares and margin is not None and g0 is not None:
        g0c = min(max(g0, v["start_growth_floor"]), v["start_growth_cap"])
        base = D.dcf_value(rev0, g0c, margin, wacc, g_term, years, net_debt, shares)
        if base and margin > 0:
            mc = D.monte_carlo(rev0, g0c, margin, wacc, g_term, years, net_debt, shares, v["mc_draws"])
            implied = D.reverse_dcf(price, rev0, margin, wacc, g_term, years, net_debt, shares)
            dcf_out = {"base": base["per_share"], "ev": base["ev"], "tv_share": base["tv_share"], "path": base["path"],
                       "inputs": {"revenue_ttm": rev0, "g_start": g0c, "g_start_raw": g0, "g_start_basis": ctx.get("g_basis"),
                                  "fcff_margin": margin, "margin_basis": ctx.get("margin_basis"), "wacc": wacc,
                                  "g_terminal": g_term, "years": years, "net_debt": net_debt, "shares": shares},
                       "mc": mc, "implied_growth": implied,
                       "sensitivity": D.sensitivity(rev0, g0c, margin, wacc, g_term, years, net_debt, shares)}
            lo = mc["p25"] if mc else base["per_share"] * 0.8
            hi = mc["p75"] if mc else base["per_share"] * 1.2
            methods.append({"key": "dcf", "value": base["per_share"], "low": lo, "high": hi})
        elif margin is not None and margin <= 0:
            dcf_out = {"base": None, "note": "自由現金流為負,DCF 不適用(改看營收倍數與分析師)"}
    out["dcf"] = dcf_out

    # --- 本益比河流 ---
    pb = pe_band(ctx.get("pe_hist"), ctx.get("ntm_eps"), ctx.get("eps_ttm"), price, coc["cost_of_equity"])
    out["pe_band"] = pb
    if pb and pb.get("value"):
        mth = {"key": "pe_band", "value": pb["value"], "low": pb["low"], "high": pb["high"]}
        eg = ctx.get("eps_cagr_3y")
        if (ctx.get("cyclical") or {}).get("cyclical"):
            mth["force_exclude"] = "景氣循環股:獲利高峰時本益比低、谷底時本益比高,河流法容易誤判"
        elif eg is not None and eg > 0.35:
            # 獲利在短時間內暴增時,過去的高本益比是「低獲利時期」的倍數,套到現在會嚴重高估
            mth["force_exclude"] = f"近 3 年 EPS 年化成長 {eg:.0%},獲利結構改變太大,歷史本益比不適用"
        methods.append(mth)
    out["river"] = river(ctx.get("close_s"), ctx.get("eps_ttm_s"), pb["percentiles"]) if pb else None

    # --- 同業 ---
    pv = peers_value(ctx.get("peers") or [], ctx.get("ntm_eps") or ctx.get("eps_ttm"), ctx.get("ebitda_ttm"), net_debt,
                     shares, rev0, ctype == "unprofitable")
    out["peers_valuation"] = pv
    if pv and pv.get("value"):
        methods.append({"key": "peers", "value": pv["value"], "low": pv["low"], "high": pv["high"]})

    # --- 分析師(偏誤校正) ---
    tg = ctx.get("targets") or {}
    med = tg.get("median") or tg.get("mean")
    if med and price and (tg.get("n") or 0) >= 3:
        implied = med / price - 1
        adj = price * (1 + implied - v["analyst_optimism_haircut"])
        adj = min(adj, med)
        methods.append({"key": "analyst", "value": adj, "low": adj * 0.9, "high": adj * 1.1, "raw": med,
                        "n": tg.get("n")})

    # --- EPV ---
    ebit_norm = ctx.get("ebit_norm")
    if ctype in ("mature", "growth") and ebit_norm and shares:
        e = D.epv(ebit_norm, tax, wacc, net_debt, shares)
        if e and e > 0:
            methods.append({"key": "epv", "value": e, "low": e * 0.9, "high": e * 1.1})
            out["epv"] = {"value": e, "ebit_norm": ebit_norm, "growth_premium": safe(price / e - 1)}

    # --- P/B-ROE ---
    if ctype == "financial":
        roe = ctx.get("roe_norm")
        bvps = ctx.get("bvps")
        coe = coc["cost_of_equity"]
        val = D.pb_roe(roe, coe, g_term, bvps)
        if val:
            lo = D.pb_roe(roe, coe + 0.01, g_term, bvps)
            hi = D.pb_roe(roe, coe - 0.01, g_term, bvps)
            methods.append({"key": "pb_roe", "value": val, "low": lo or val * 0.85, "high": hi or val * 1.15})
            out["pb_roe"] = {"roe": roe, "coe": coe, "fair_pb": val / bvps, "bvps": bvps}

    # --- 綜合 ---
    weights = dict(params["method_weights"][ctype])
    if (ctx.get("cyclical") or {}).get("cyclical"):
        # 循環股:同業倍數在景氣高峰會高估 → 減半;EPV 用整個循環的利潤率 → 加重
        weights["peers"] = weights.get("peers", 0) * 0.5
        if ctype in ("mature", "growth"):
            weights["epv"] = 15
    vals = [m["value"] for m in methods if m.get("value") and m["value"] > 0 and not m.get("force_exclude")
            and weights.get(m["key"], 0) > 0]
    medv = float(np.median(vals)) if vals else None
    for m in methods:
        m["label"] = METHOD_LABEL[m["key"]]
        m["weight"] = weights.get(m["key"], 0)
        m["included"] = bool(m.get("value") and m["value"] > 0 and m["weight"] > 0)
        if m.get("force_exclude"):
            m["included"] = False
            m["excluded_reason"] = m.pop("force_exclude")
        elif m["included"] and medv and abs(math.log(m["value"] / medv)) > math.log(4):
            m["included"] = False
            m["excluded_reason"] = "與其他方法中位數差距超過 4 倍,視為離群值"
        m["upside"] = safe(m["value"] / price - 1) if m.get("value") else None
    inc = [m for m in methods if m["included"]]
    out["methods"] = methods
    if not inc:
        out["fair_value"] = None
        out["uncertainty"] = "很高"
        out["no_fv_reason"] = "沒有可用的估值方法"
        return out
    # P2-H04(Red-team M3):剩下的方法全都以現價為基礎(分析師調整價 = 現價 × (1 + 隱含報酬 − 折扣))時,
    # 公平價等於拿現價估現價,不給公平價與評等
    if all(m["key"] in PRICE_ANCHORED for m in inc):
        for m in inc:
            m["included"] = False
            m["excluded_reason"] = "只剩以現價推算的方法,會循環引用現價,不採用"
        out["fair_value"] = None
        out["uncertainty"] = "很高"
        out["no_fv_reason"] = "排除離群值後只剩以現價推算的方法(分析師調整價),估值不可得"
        return out
    # 加權「幾何」平均:估值誤差是倍數型的(高估 2 倍和低估一半一樣嚴重),幾何平均對極端值比較穩健
    wsum = sum(m["weight"] for m in inc)

    def gmean(key):
        return math.exp(sum(math.log(max(m[key], 1e-9)) * m["weight"] for m in inc) / wsum)

    fv = gmean("value")
    lo = gmean("low")
    hi = gmean("high")
    level, uscore = uncertainty_level((dcf_out or {}).get("mc", {}).get("spread") if dcf_out and dcf_out.get("mc") else None,
                                      methods, ctx.get("vol_1y"), ctype)
    cyc = ctx.get("cyclical") or {}
    out["cyclical"] = cyc
    if cyc.get("cyclical"):
        uscore += 0.15
        level = "低" if uscore < 0.35 else "中" if uscore < 0.6 else "高" if uscore < 0.9 else "很高"
    if len(inc) == 1 and v.get("single_method_very_high"):
        # P2-H03:只剩一種有效估值方法時無法交叉驗證(回測裡常只剩 DCF 或本益比河流),不確定性一律「很高」
        # (參數開關:v1.1 的參數沒有這個鍵 → 行為不變,基準可逐位元重現)
        level, uscore = "很高", max(uscore, 0.9)
        out["single_method"] = inc[0]["key"]
    mos = v["margin_of_safety"][level]
    out.update({"fair_value": fv, "fair_low": min(lo, fv), "fair_high": max(hi, fv), "upside": fv / price - 1,
                "uncertainty": level, "uncertainty_score": uscore, "margin_of_safety": mos,
                "zones": {"well_below": fv * (1 - mos), "below": fv * (1 - mos / 2), "above": fv * (1 + mos / 2),
                          "well_above": fv * (1 + mos)}})
    # 12 個月內觸及公平價的機率(對數常態、零漂移、反射原理)
    sig = ctx.get("vol_1y")
    if sig and sig > 0:
        out["touch_prob_12m"] = touch_probability(price, fv, sig, 1.0)
        out["scenario"] = scenarios(price, fv, coc["cost_of_equity"], ctx.get("dividend_yield") or 0.0, sig)
    return out


def scenarios(price: float, fv: float, coe: float, dy: float, sigma: float, k: float = 1 / 3) -> dict:
    """情境價格:假設價格每年往公平價收斂 1/3(價值差距平均約 3 年消化),再加上股權成本扣掉股利的正常漂移;
    區間用年化波動率的對數常態近似。這是「統計情境」,不是預測。"""
    out = {"assumption": "每年收斂 1/3 的價值差距 + (股權成本 − 殖利率) 漂移;區間用歷史波動率", "k": k}
    for label, t in (("3m", 0.25), ("12m", 1.0)):
        center = price * (fv / price) ** (k * t) * (1 + coe - dy) ** t
        s = sigma * math.sqrt(t)
        out[label] = {"center": center, "exp_return": center / price - 1,
                      "low68": center * math.exp(-s), "high68": center * math.exp(s),
                      "low90": center * math.exp(-1.645 * s), "high90": center * math.exp(1.645 * s)}
    return out


def touch_probability(s0: float, k: float, sigma: float, t: float, mu: float = 0.0) -> float | None:
    from scipy.stats import norm
    if s0 <= 0 or k <= 0 or sigma <= 0:
        return None
    b = math.log(k / s0)
    st = sigma * math.sqrt(t)
    nu = mu - 0.5 * sigma ** 2
    if b >= 0:
        p = norm.cdf((-b + nu * t) / st) + math.exp(2 * nu * b / sigma ** 2) * norm.cdf((-b - nu * t) / st)
    else:
        bb = -b
        p = norm.cdf((-bb - nu * t) / st) + math.exp(-2 * nu * bb / sigma ** 2) * norm.cdf((-bb + nu * t) / st)
    return float(min(1.0, max(0.0, p)))
