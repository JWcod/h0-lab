"""基本面指標:獲利能力、成長、財務安全、效率、股東回饋、法務鑑識分數(Altman / Piotroski / Beneish)。

每個指標的公式寫在 lab/metric_dictionary.py,前端「方法論」頁面直接讀那份定義。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .financials import safe


def _div(a, b):
    a, b = safe(a), safe(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _avg(a, b):
    a, b = safe(a), safe(b)
    if a is None:
        return b
    if b is None:
        return a
    return (a + b) / 2


def _g(df: pd.DataFrame, col: str, i: int = -1):
    if df is None or df.empty or col not in df.columns or len(df) < abs(i):
        return None
    return safe(df[col].iloc[i])


def cagr(first, last, years):
    first, last = safe(first), safe(last)
    if first is None or last is None or years <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def series_cagr(s: pd.Series, years: int):
    s = s.dropna()
    if len(s) < years + 1:
        return None
    return cagr(s.iloc[-1 - years], s.iloc[-1], years)


def ratio_history(df: pd.DataFrame, num: str, den: str) -> list[dict]:
    if df is None or df.empty or num not in df or den not in df:
        return []
    out = []
    for idx, r in df.iterrows():
        v = _div(r[num], r[den])
        out.append({"period": idx.strftime("%Y-%m-%d"), "value": v})
    return out


def year_ago(q: pd.DataFrame | None, a: pd.DataFrame | None, col: str, period_end: str | None):
    """找「一年前」的存量數字(算平均股東權益/資產用):先找季資料,再找年資料。"""
    if not period_end:
        return _g(a, col, -2) if a is not None and len(a) >= 2 else None
    target = pd.Timestamp(period_end) - pd.Timedelta(days=365)
    for df, tol in ((q, 40), (a, 100)):
        if df is None or df.empty or col not in df.columns:
            continue
        d = (df.index - target).days
        i = int(np.argmin(np.abs(d)))
        if abs(d[i]) <= tol and pd.notna(df[col].iloc[i]):
            return safe(df[col].iloc[i])
    return None


def profitability(a: pd.DataFrame, t: dict | None, q: pd.DataFrame | None) -> dict:
    """a=年度表(升冪),t=TTM dict。優先用 TTM,沒有就用最新年度。"""
    m: dict = {}
    src = t or ({c: _g(a, c) for c in a.columns} if a is not None and not a.empty else {})
    rev = safe(src.get("revenue"))
    m["gross_margin"] = _div(src.get("gross_profit"), rev)
    m["operating_margin"] = _div(src.get("operating_income"), rev)
    m["net_margin"] = _div(src.get("net_income"), rev)
    m["fcf_margin"] = _div(src.get("fcf"), rev)
    m["ebitda_margin"] = _div(src.get("ebitda"), rev)
    pe = (t or {}).get("period_end")
    eq_now = safe(src.get("equity"))
    eq_prev = year_ago(q, a, "equity", pe)
    ta_now = safe(src.get("total_assets"))
    ta_prev = year_ago(q, a, "total_assets", pe)
    m["roe"] = _div(src.get("net_income"), _avg(eq_now, eq_prev)) if eq_now and eq_now > 0 else None
    m["roa"] = _div(src.get("net_income"), _avg(ta_now, ta_prev))
    tax_rate = _g(a, "tax_rate")
    if tax_rate is None or not (0 <= tax_rate <= 0.5):
        tax_rate = _div(src.get("tax"), src.get("pretax_income"))
        if tax_rate is None or not (0 <= tax_rate <= 0.5):
            tax_rate = 0.21
    m["tax_rate"] = tax_rate
    ebit = safe(src.get("ebit")) or safe(src.get("operating_income"))
    debt = safe(src.get("total_debt")) or 0.0
    cash = safe(src.get("cash_sti")) or safe(src.get("cash")) or 0.0
    ic = (eq_now or 0) + debt - cash
    if ebit is not None and ic > 0 and eq_now:
        m["roic"] = ebit * (1 - tax_rate) / ic
    else:
        m["roic"] = None
    m["gp_assets"] = _div(src.get("gross_profit"), ta_now)
    m["cfo_to_ni"] = _div(src.get("cfo"), src.get("net_income")) if (safe(src.get("net_income")) or 0) > 0 else None
    ni, cfo = safe(src.get("net_income")), safe(src.get("cfo"))
    m["accruals"] = _div((ni - cfo) if ni is not None and cfo is not None else None, _avg(ta_now, ta_prev))
    # 歷史(年度)
    m["history"] = {
        "gross_margin": ratio_history(a, "gross_profit", "revenue"),
        "operating_margin": ratio_history(a, "operating_income", "revenue"),
        "net_margin": ratio_history(a, "net_income", "revenue"),
        "fcf_margin": ratio_history(a, "fcf", "revenue"),
        "roe": ratio_history(a, "net_income", "equity"),
    }
    # 季毛利率(台股長序列 / 美股 5–6 季)
    if q is not None and not q.empty and "gross_profit" in q and "revenue" in q:
        gm_q = (q["gross_profit"] / q["revenue"]).dropna()
        m["gm_quarterly"] = [{"period": i.strftime("%Y-%m-%d"), "value": safe(v)} for i, v in gm_q.items()]
        om_q = (q["operating_income"] / q["revenue"]).dropna() if "operating_income" in q else pd.Series(dtype=float)
        m["om_quarterly"] = [{"period": i.strftime("%Y-%m-%d"), "value": safe(v)} for i, v in om_q.items()]
    return m


def stability(long: pd.DataFrame | None, q: pd.DataFrame | None) -> dict:
    out = {}
    if long is not None and not long.empty and "revenue" in long:
        if "gross_profit" in long:
            gm = (long["gross_profit"] / long["revenue"]).dropna().tail(10)
            if len(gm) >= 4:
                out["gm_std_10y"] = safe(gm.std())
                out["gm_mean_10y"] = safe(gm.mean())
        if "net_income" in long:
            ni = long["net_income"].dropna().tail(10)
            if len(ni) >= 4:
                out["loss_years_10y"] = int((ni < 0).sum())
                out["years_covered"] = int(len(ni))
    return out


def growth(a: pd.DataFrame, long: pd.DataFrame | None, q: pd.DataFrame | None, t: dict | None,
           t_prev: dict | None, est: dict | None) -> dict:
    m: dict = {}
    if t and t_prev:
        r1, r0 = safe(t.get("revenue")), safe(t_prev.get("revenue"))
        m["revenue_yoy"] = (r1 / r0 - 1) if r1 and r0 and r0 > 0 else None
        e1, e0 = safe(t.get("eps_diluted")), safe(t_prev.get("eps_diluted"))
        m["eps_yoy"] = (e1 - e0) / abs(e0) if e1 is not None and e0 not in (None, 0) else None
        f1, f0 = safe(t.get("fcf")), safe(t_prev.get("fcf"))
        m["fcf_yoy"] = (f1 - f0) / abs(f0) if f1 is not None and f0 not in (None, 0) else None
        m["yoy_basis"] = "TTM"
    elif a is not None and len(a) >= 2:
        r1, r0 = _g(a, "revenue"), _g(a, "revenue", -2)
        m["revenue_yoy"] = (r1 / r0 - 1) if r1 and r0 else None
        e1, e0 = _g(a, "eps_diluted"), _g(a, "eps_diluted", -2)
        m["eps_yoy"] = (e1 - e0) / abs(e0) if e1 is not None and e0 not in (None, 0) else None
        f1, f0 = _g(a, "fcf"), _g(a, "fcf", -2)
        m["fcf_yoy"] = (f1 - f0) / abs(f0) if f1 is not None and f0 not in (None, 0) else None
        m["yoy_basis"] = "年度"
    base = long if long is not None and not long.empty and "revenue" in long and len(long) >= 4 else a
    if base is not None and not base.empty:
        for col, key in (("revenue", "revenue"), ("net_income", "ni"), ("eps_diluted", "eps"), ("fcf", "fcf")):
            if col in base:
                for yrs in (3, 5, 10):
                    m[f"{key}_cagr_{yrs}y"] = series_cagr(base[col], yrs)
    # 最近一季 YoY(需要同季去年)
    if q is not None and len(q) >= 5 and "revenue" in q:
        last, prev = q.iloc[-1], q.iloc[-5]
        if (q.index[-1] - q.index[-5]).days in range(350, 380):
            m["q_revenue_yoy"] = _div(last["revenue"] - prev["revenue"], abs(prev["revenue"]))
            if "eps_diluted" in q and safe(prev.get("eps_diluted")):
                m["q_eps_yoy"] = _div(last["eps_diluted"] - prev["eps_diluted"], abs(prev["eps_diluted"]))
            if "gross_profit" in q:
                gm1 = _div(last["gross_profit"], last["revenue"])
                gm0 = _div(prev["gross_profit"], prev["revenue"])
                m["q_gm_change_yoy"] = (gm1 - gm0) if gm1 is not None and gm0 is not None else None
        # 毛利率連續變化
        if "gross_profit" in q:
            gm = (q["gross_profit"] / q["revenue"]).dropna()
            d = gm.diff().dropna()
            streak = 0
            for v in reversed(d.tolist()):
                if v > 0 and streak >= 0:
                    streak += 1
                elif v < 0 and streak <= 0:
                    streak -= 1
                else:
                    break
            m["gm_streak_q"] = streak
    if est:
        m.update({k: v for k, v in est.items() if k.startswith("fwd_")})
    return m


def month_revenue(df: pd.DataFrame | None) -> dict | None:
    """台股月營收動能(台灣實證:營收動能 / 營收創新高有 1–12 個月正報酬)。"""
    if df is None or df.empty:
        return None
    d = df.copy()
    d["ym"] = pd.to_datetime(dict(year=d["revenue_year"], month=d["revenue_month"], day=1))
    d = d.drop_duplicates("ym", keep="last").set_index("ym").sort_index()
    s = d["revenue"].astype(float)
    yoy = s / s.shift(12) - 1
    mom = s / s.shift(1) - 1
    r3 = s.rolling(3).sum()
    r12 = s.rolling(12).sum()
    yoy3 = r3 / r3.shift(12) - 1
    yoy12 = r12 / r12.shift(12) - 1
    latest = s.index[-1]
    prior_max = s.iloc[:-1].max() if len(s) > 1 else None
    record = bool(prior_max is not None and s.iloc[-1] >= prior_max)
    # 同月份歷史新高
    same_month = s[s.index.month == latest.month]
    record_same_month = bool(len(same_month) > 1 and s.iloc[-1] >= same_month.iloc[:-1].max())
    streak = 0
    for v in reversed(yoy.dropna().tolist()):
        if v > 0 and streak >= 0:
            streak += 1
        elif v < 0 and streak <= 0:
            streak -= 1
        else:
            break
    # 標準化未預期營收(SUR,類似 SUE):(本月 YoY 成長 − 過去 12 個月平均) / 標準差
    g = yoy.dropna()
    sur = None
    if len(g) >= 13:
        hist = g.iloc[-13:-1]
        if hist.std() > 0:
            sur = (g.iloc[-1] - hist.mean()) / hist.std()
    hist = [{"month": i.strftime("%Y-%m"), "revenue": safe(v), "yoy": safe(yoy.get(i)), "mom": safe(mom.get(i))}
            for i, v in s.tail(60).items()]
    return {
        "latest_month": latest.strftime("%Y-%m"), "latest": safe(s.iloc[-1]), "yoy": safe(yoy.iloc[-1]),
        "mom": safe(mom.iloc[-1]), "yoy_3m": safe(yoy3.iloc[-1]), "yoy_12m": safe(yoy12.iloc[-1]),
        "momentum_3_12": safe(r3.iloc[-1] / 3 / (r12.iloc[-1] / 12)) if r12.iloc[-1] else None,
        "record_high": record, "record_same_month": record_same_month, "yoy_streak": streak,
        "sur": safe(sur), "history": hist,
        "release_note": "台股月營收於次月 10 日前公布",
    }


def health(a: pd.DataFrame, t: dict | None, market_cap: float | None, is_financial: bool) -> dict:
    m: dict = {}
    src = {c: _g(a, c) for c in a.columns} if a is not None and not a.empty else {}
    if t:  # 存量用最新一季
        for k in ("current_assets", "current_liabilities", "total_debt", "cash", "cash_sti", "equity", "total_assets",
                  "total_liabilities", "inventory", "retained_earnings"):
            if t.get(k) is not None:
                src[k] = t[k]
        for k in ("ebit", "ebitda", "interest_expense", "revenue", "operating_income"):
            if t.get(k) is not None:
                src[k] = t[k]
    m["current_ratio"] = _div(src.get("current_assets"), src.get("current_liabilities"))
    ca, inv = safe(src.get("current_assets")), safe(src.get("inventory")) or 0.0
    m["quick_ratio"] = _div(ca - inv if ca is not None else None, src.get("current_liabilities"))
    debt = safe(src.get("total_debt")) or 0.0
    cash = safe(src.get("cash_sti")) or safe(src.get("cash")) or 0.0
    m["debt_to_equity"] = _div(debt, src.get("equity")) if (safe(src.get("equity")) or 0) > 0 else None
    m["net_debt"] = debt - cash
    ebitda = safe(src.get("ebitda")) or (safe(src.get("operating_income")) or 0)
    m["net_debt_ebitda"] = _div(debt - cash, ebitda) if ebitda and ebitda > 0 else None
    ie = safe(src.get("interest_expense"))
    ebit = safe(src.get("ebit")) or safe(src.get("operating_income"))
    m["interest_coverage"] = (ebit / abs(ie)) if ie and ebit is not None and abs(ie) > 0 else (999.0 if ebit and ebit > 0 and debt < 1 else None)
    m["liabilities_to_assets"] = _div(src.get("total_liabilities"), src.get("total_assets"))
    m["net_cash_to_mcap"] = _div(cash - debt, market_cap) if market_cap else None
    if not is_financial:
        m["altman_z"], m["altman_parts"] = altman_z(src, market_cap)
    else:
        m["altman_z"], m["altman_parts"] = None, None
    return m


def altman_z(src: dict, market_cap: float | None):
    ta = safe(src.get("total_assets"))
    tl = safe(src.get("total_liabilities"))
    if not ta or not tl or market_cap is None:
        return None, None
    wc = (safe(src.get("current_assets")) or 0) - (safe(src.get("current_liabilities")) or 0)
    re_ = safe(src.get("retained_earnings")) or 0
    ebit = safe(src.get("ebit")) or safe(src.get("operating_income")) or 0
    sales = safe(src.get("revenue")) or 0
    parts = {"X1 營運資金/總資產": wc / ta, "X2 保留盈餘/總資產": re_ / ta, "X3 EBIT/總資產": ebit / ta,
             "X4 市值/總負債": market_cap / tl, "X5 營收/總資產": sales / ta}
    z = 1.2 * parts["X1 營運資金/總資產"] + 1.4 * parts["X2 保留盈餘/總資產"] + 3.3 * parts["X3 EBIT/總資產"] \
        + 0.6 * parts["X4 市值/總負債"] + 1.0 * parts["X5 營收/總資產"]
    return z, {k: round(v, 4) for k, v in parts.items()}


def piotroski(a: pd.DataFrame) -> dict | None:
    """Piotroski F-score(9 分),用最近兩個年度。"""
    if a is None or len(a) < 3:
        return None
    c, p, pp = a.iloc[-1], a.iloc[-2], a.iloc[-3]

    def roa(r, prev):
        return _div(r.get("net_income"), _avg(r.get("total_assets"), prev.get("total_assets")))

    checks = []
    roa_c, roa_p = roa(c, p), roa(p, pp)
    cfo_c = safe(c.get("cfo"))
    ta_avg = _avg(c.get("total_assets"), p.get("total_assets"))
    checks.append(("ROA > 0", roa_c is not None and roa_c > 0, roa_c))
    checks.append(("營業現金流 > 0", cfo_c is not None and cfo_c > 0, cfo_c))
    checks.append(("ROA 較去年提升", roa_c is not None and roa_p is not None and roa_c > roa_p, (roa_c or 0) - (roa_p or 0)))
    cfo_ta = _div(cfo_c, ta_avg)
    checks.append(("現金流 > 淨利(應計品質)", cfo_ta is not None and roa_c is not None and cfo_ta > roa_c, cfo_ta))
    lev_c = _div(c.get("long_term_debt") or 0, ta_avg)
    lev_p = _div(p.get("long_term_debt") or 0, _avg(p.get("total_assets"), pp.get("total_assets")))
    checks.append(("長期負債比下降", lev_c is not None and lev_p is not None and lev_c <= lev_p, (lev_c or 0) - (lev_p or 0)))
    cr_c, cr_p = _div(c.get("current_assets"), c.get("current_liabilities")), _div(p.get("current_assets"), p.get("current_liabilities"))
    checks.append(("流動比上升", cr_c is not None and cr_p is not None and cr_c > cr_p, (cr_c or 0) - (cr_p or 0)))
    sh_c, sh_p = safe(c.get("shares_diluted")) or safe(c.get("shares_out")), safe(p.get("shares_diluted")) or safe(p.get("shares_out"))
    checks.append(("沒有增發股票", sh_c is not None and sh_p is not None and sh_c <= sh_p * 1.005, _div(sh_c, sh_p)))
    gm_c, gm_p = _div(c.get("gross_profit"), c.get("revenue")), _div(p.get("gross_profit"), p.get("revenue"))
    checks.append(("毛利率上升", gm_c is not None and gm_p is not None and gm_c > gm_p, (gm_c or 0) - (gm_p or 0)))
    at_c = _div(c.get("revenue"), ta_avg)
    at_p = _div(p.get("revenue"), _avg(p.get("total_assets"), pp.get("total_assets")))
    checks.append(("資產周轉率上升", at_c is not None and at_p is not None and at_c > at_p, (at_c or 0) - (at_p or 0)))
    score = sum(1 for _, ok, _ in checks if ok)
    return {"score": score, "checks": [{"name": n, "pass": bool(ok), "value": safe(v)} for n, ok, v in checks],
            "period": a.index[-1].strftime("%Y")}


def beneish(a: pd.DataFrame) -> dict | None:
    """Beneish M-score(8 變數)。> -1.78 代表財報操縱機率偏高(高成長公司容易誤判)。"""
    if a is None or len(a) < 2:
        return None
    c, p = a.iloc[-1], a.iloc[-2]
    need = ["revenue", "receivables", "gross_profit", "current_assets", "ppe", "total_assets", "net_income", "cfo"]
    if any(safe(c.get(k)) is None or safe(p.get(k)) is None for k in need):
        return None
    try:
        dsri = (c["receivables"] / c["revenue"]) / (p["receivables"] / p["revenue"])
        gmi = (p["gross_profit"] / p["revenue"]) / (c["gross_profit"] / c["revenue"])
        aqi = (1 - (c["current_assets"] + c["ppe"]) / c["total_assets"]) / (1 - (p["current_assets"] + p["ppe"]) / p["total_assets"])
        sgi = c["revenue"] / p["revenue"]
        dep_c, dep_p = safe(c.get("da")), safe(p.get("da"))
        depi = ((dep_p / (dep_p + p["ppe"])) / (dep_c / (dep_c + c["ppe"]))) if dep_c and dep_p else 1.0
        sga_c, sga_p = safe(c.get("sga")), safe(p.get("sga"))
        sgai = ((sga_c / c["revenue"]) / (sga_p / p["revenue"])) if sga_c and sga_p else 1.0
        ltd_c, ltd_p = safe(c.get("long_term_debt")) or 0, safe(p.get("long_term_debt")) or 0
        lvgi = ((c["current_liabilities"] + ltd_c) / c["total_assets"]) / ((p["current_liabilities"] + ltd_p) / p["total_assets"])
        tata = (c["net_income"] - c["cfo"]) / c["total_assets"]
    except (ZeroDivisionError, TypeError, KeyError):
        return None
    m = -4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi
    vals = {"DSRI 應收帳款指數": dsri, "GMI 毛利率指數": gmi, "AQI 資產品質指數": aqi, "SGI 營收成長指數": sgi,
            "DEPI 折舊指數": depi, "SGAI 管銷費用指數": sgai, "LVGI 槓桿指數": lvgi, "TATA 應計/總資產": tata}
    if not np.isfinite(m):
        return None
    return {"m": float(m), "flag": bool(m > -1.78), "vars": {k: round(float(v), 4) for k, v in vals.items()},
            "period": a.index[-1].strftime("%Y"), "high_growth_caveat": bool(sgi > 1.3)}


def efficiency(a: pd.DataFrame, t: dict | None, q: pd.DataFrame | None = None) -> dict:
    m: dict = {}
    if a is None or a.empty:
        return m
    c = {k: _g(a, k) for k in a.columns}
    if t:
        c.update({k: v for k, v in t.items() if v is not None})
    ta_prev = year_ago(q, a, "total_assets", (t or {}).get("period_end"))
    m["asset_turnover"] = _div(c.get("revenue"), _avg(c.get("total_assets"), ta_prev))
    m["dso"] = _div(c.get("receivables"), c.get("revenue"))
    m["dso"] = m["dso"] * 365 if m["dso"] is not None else None
    cogs = safe(c.get("cogs")) or ((safe(c.get("revenue")) or 0) - (safe(c.get("gross_profit")) or 0)) or None
    m["dio"] = _div(c.get("inventory"), cogs)
    m["dio"] = m["dio"] * 365 if m["dio"] is not None else None
    m["dpo"] = _div(c.get("payables"), cogs)
    m["dpo"] = m["dpo"] * 365 if m["dpo"] is not None else None
    if m["dso"] is not None and m["dio"] is not None and m["dpo"] is not None:
        m["ccc"] = m["dso"] + m["dio"] - m["dpo"]
    capex = safe(c.get("capex"))
    m["capex_to_revenue"] = _div(abs(capex) if capex is not None else None, c.get("revenue"))
    m["rnd_to_revenue"] = _div(c.get("rnd"), c.get("revenue"))
    m["sbc_to_revenue"] = _div(c.get("sbc"), c.get("revenue"))
    # 應收、存貨成長 vs 營收成長(年度)
    if len(a) >= 2:
        r1, r0 = _g(a, "revenue"), _g(a, "revenue", -2)
        ar1, ar0 = _g(a, "receivables"), _g(a, "receivables", -2)
        in1, in0 = _g(a, "inventory"), _g(a, "inventory", -2)
        rg = (r1 / r0 - 1) if r1 and r0 else None
        m["receivables_growth_minus_revenue"] = ((ar1 / ar0 - 1) - rg) if ar1 and ar0 and rg is not None else None
        m["inventory_growth_minus_revenue"] = ((in1 / in0 - 1) - rg) if in1 and in0 and rg is not None else None
    return m


def shareholder(a: pd.DataFrame, t: dict | None, market_cap: float | None, info: dict) -> dict:
    m: dict = {}
    src = t or ({k: _g(a, k) for k in a.columns} if a is not None and not a.empty else {})
    div = abs(safe(src.get("dividends_paid")) or 0)
    bb = abs(safe(src.get("buybacks")) or 0)
    ni, fcf = safe(src.get("net_income")), safe(src.get("fcf"))
    m["payout_ratio"] = div / ni if ni and ni > 0 else None
    m["fcf_payout"] = div / fcf if fcf and fcf > 0 else None
    m["dividend_yield"] = div / market_cap if market_cap else None
    m["buyback_yield"] = bb / market_cap if market_cap else None
    m["shareholder_yield"] = (div + bb) / market_cap if market_cap else None
    if a is not None and len(a) >= 2:
        s1 = _g(a, "shares_diluted") or _g(a, "shares_out")
        s0 = _g(a, "shares_diluted", -2) or _g(a, "shares_out", -2)
        m["share_change_yoy"] = (s1 / s0 - 1) if s1 and s0 else None
        if len(a) >= 4:
            s3 = _g(a, "shares_diluted", -4) or _g(a, "shares_out", -4)
            m["share_change_3y"] = (s1 / s3) ** (1 / 3) - 1 if s1 and s3 else None
    return m
