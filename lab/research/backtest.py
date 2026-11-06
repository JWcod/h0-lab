"""歷史回測(模型復盤):用「現在這套模型」回到過去,每個月底只用當時拿得到的資料重新判斷,再對答案。

重點:沒有偷看未來(point-in-time)
- 台股季財報:季末 + 45 天才算公布(Q4 / 年報:+90 天);月營收:次月 10 日公布
- 美股年報(SEC 10-K):會計年度結束 + 75 天
- 本益比歷史、股價、大盤、VIX、利率:只用到當天為止
- 評分、估值、評等的程式碼就是網站上用的同一套(scoring / valuation / decision)

回測時拿不到的部分(會說清楚):分析師預估與目標價、新聞情緒、法人籌碼、同業倍數 → 這些面向缺席,權重自動重新分配。

對答案的方式:
- 長期評等(買進以上 / 減碼以下)→ 看之後 12 個月,相對「同股票池平均」的超額報酬(這是選股能力的公平量尺:隨便選的期望值剛好 50%)
- 短期訊號(偏多 / 偏空)→ 看之後 3 個月的相對報酬

holdout 保護(v2 起,任務 1.9b):彙總輸出(命中率、評等分組、逐年、面向 IC、模擬投組)只算開發集
(lab/research/splits.py:每個預測期的結果視窗必須在 2024-10-01 前結束;投組只用 2024-09-30 前的月份)。
逐筆判斷檔 backtest_*_rows.csv 不變(v1.1 基準回歸靠它的 SHA-256)。正式評估改用 backtest_pit + evaluate。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
import traceback

import numpy as np
import pandas as pd
import yfinance as yf

from .. import resolver
from ..analysis import decision as DE
from ..analysis import financials as FN
from ..analysis import fundamentals as FU
from ..analysis import income_risk as IR
from ..analysis import regime as RG
from ..analysis import scoring as SC
from ..analysis import technical as TE
from ..analysis.financials import safe
from ..cache import cached
from ..config import RESEARCH_DIR, load_params
from ..sources import finmind, sec, yahoo
from ..universe import TW_LARGE, US_LARGE
from ..valuation import model as VM
from . import splits

OUT_DIR = RESEARCH_DIR
H = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
LT_NAMES = ["賣出", "減碼", "持有", "買進", "強力買進"]


# ---------------- 資料 ----------------

def _download(symbols: list[str]) -> dict[str, pd.DataFrame]:
    def fetch():
        df = yf.download(symbols, period="10y", auto_adjust=False, progress=False, group_by="ticker", threads=True)
        out = {}
        for s in symbols:
            try:
                d = df[s].dropna(how="all")
            except KeyError:
                continue
            if d.empty:
                continue
            d.index = pd.to_datetime(d.index).tz_localize(None)
            out[s] = d
        return out

    return cached("bt_px", "|".join(sorted(symbols)), 60 * 60 * 12, fetch)


def _q_avail(qend: pd.Timestamp) -> pd.Timestamp:
    return qend + pd.Timedelta(days=90 if qend.month == 12 else 45)


def tw_data(code: str) -> dict | None:
    fm = {
        "income": finmind.get("TaiwanStockFinancialStatements", code, "2013-01-01", "statements_slow"),
        "balance": finmind.get("TaiwanStockBalanceSheet", code, "2013-01-01", "statements_slow"),
        "cashflow": finmind.get("TaiwanStockCashFlowsStatement", code, "2013-01-01", "statements_slow"),
    }
    q = FN.tw_quarterly(fm)
    if q.empty:
        return None
    q = q.copy()
    q["_avail"] = [_q_avail(i) for i in q.index]
    mr = finmind.get("TaiwanStockMonthRevenue", code, "2013-01-01", "statements")
    if mr is not None and not mr.empty:
        mr = mr.copy()
        ym = pd.to_datetime(dict(year=mr["revenue_year"], month=mr["revenue_month"], day=1))
        mr["_avail"] = ym + pd.offsets.MonthBegin(1) + pd.Timedelta(days=9)
    per = finmind.get("TaiwanStockPER", code, finmind._ago(365 * 10 + 40), "statements")
    pe = None
    if per is not None and not per.empty:
        pe = per.set_index(pd.to_datetime(per["date"]))["PER"].astype(float).replace(0, np.nan).sort_index()
    return {"q": q, "mr": mr, "pe": pe}


def us_data(sym: str, splits) -> dict | None:
    cik = sec.cik_for(sym)
    if not cik:
        return None
    a = sec.annual_table(cik)
    if a.empty:
        return None
    a = sec.split_adjust(a, splits)
    if "cfo" in a and "capex" in a:
        a["fcf"] = a["cfo"] - a["capex"].abs()
    if "total_debt" not in a:
        a["total_debt"] = a.get("long_term_debt")
    a["ebit"] = a.get("operating_income")
    a["_avail"] = a.index + pd.Timedelta(days=75)
    return {"a": a}


# ---------------- 單一時點的完整判斷 ----------------

def _multiples(price, mcap, t, eps_ttm, net_debt):
    ev = mcap + net_debt if mcap else None
    rev = (t or {}).get("revenue")
    return {
        "pe_ttm": price / eps_ttm if eps_ttm and eps_ttm > 0 else None,
        "forward_pe": None,
        "pb": mcap / t["equity"] if t and t.get("equity") and mcap and t["equity"] > 0 else None,
        "ps": mcap / rev if rev and mcap else None, "ev": ev,
        "ev_ebitda": ev / t["ebitda"] if ev and t and t.get("ebitda") and t["ebitda"] > 0 else None,
        "ev_ebit": ev / t["ebit"] if ev and t and t.get("ebit") and t["ebit"] > 0 else None,
        "fcf_yield": t["fcf"] / mcap if t and t.get("fcf") is not None and mcap else None,
        "earnings_yield": eps_ttm / price if eps_ttm else None, "dividend_yield": None, "peg": None,
    }


def _us_ttm_row(ttm: dict, base: dict) -> dict:
    """把 ttm_asof 的結果蓋在年報列上(TTM 缺的欄位沿用年報),並重算衍生欄位(fcf、total_debt、ebit)。"""
    row = dict(base)
    row.update({k: safe(v) for k, v in ttm.items() if k not in ("quarters",) and v is not None})
    if row.get("cfo") is not None and row.get("capex") is not None:
        row["fcf"] = row["cfo"] - abs(row["capex"])
    if ttm.get("long_term_debt") is not None:
        row["total_debt"] = ttm["long_term_debt"]
    row["ebit"] = row.get("operating_income")
    row["period_end"] = ttm["period_end"]
    return row


def judge_at(d: pd.Timestamp, market: str, sym: str, px: pd.DataFrame, fdat: dict, macro: dict, params: dict,
             industry_tw: str | None, industry_key: str | None, chips_fm: dict | None = None,
             income_risk: dict | None = None) -> dict | None:
    """income_risk:股利與風險基礎變數(lab/analysis/income_risk.py);backtest_pit 透過 view 算好傳進來,
    沒給就從 px(Dividends、Adj Close)與 macro["bench_tr"](沒有就是 None)算。只進基礎變數,不影響出廠分數。"""
    from ..report import detect_cyclical  # 避免循環匯入
    h = px[px.index <= d].tail(760)
    if len(h) < 300:
        return None
    price = float(h["Close"].iloc[-1])
    # --- 財報(只用當時已公布的) ---
    if market == "TW":
        q = fdat["q"]
        q = q[q["_avail"] <= d].drop(columns="_avail")
        if len(q) < 8:
            return None
        annual = FN.annualize_quarters(q)
        t, t_prev = FN.ttm(q), FN.ttm(q, 4)
        if t is None:
            return None
        shares = t.get("shares_out")
        mr_df = fdat.get("mr")
        mr = FU.month_revenue(mr_df[mr_df["_avail"] <= d].drop(columns="_avail")) if mr_df is not None else None
        pe_hist = fdat["pe"][fdat["pe"].index <= d] if fdat.get("pe") is not None else None
        eps_ttm = t.get("eps_diluted")
        quarterly = q
    else:
        a = fdat["a"]
        a = a[a["_avail"] <= d].drop(columns="_avail")
        if len(a) < 4:
            return None
        annual = a
        t = {k: safe(v) for k, v in a.iloc[-1].items()}
        t["period_end"] = a.index[-1].date().isoformat()
        t_prev = {k: safe(v) for k, v in a.iloc[-2].items()}
        ttm = fdat.get("ttm")
        if params.get("us_quarterly_ttm") and ttm and ttm["period_end"] > t["period_end"]:
            # P2-H08:最新季報比最新年報新 → 改用近四季 TTM(時點科目取最新一季季底);成長率比較用一年前的 TTM,
            # 沒有就用最新年報(落後 1–3 季,近似)
            t = _us_ttm_row(ttm, t)
            t_prev = _us_ttm_row(fdat["ttm_prev"], t_prev) if fdat.get("ttm_prev") else {k: safe(v) for k, v in a.iloc[-1].items()}
        shares = t.get("shares_diluted")
        mr = None
        eps_ttm = t.get("eps_diluted")
        # 本益比歷史:每天的股價 ÷ 當時已公布的最新年度 EPS
        full = fdat["a"]
        eps_step = pd.Series(full["eps_diluted"].values, index=full["_avail"]).sort_index()
        eps_step = eps_step[eps_step.index <= d]  # 只用當時已公布的年度 EPS
        idx_all = px.index[px.index <= d]
        e = eps_step.reindex(idx_all.union(eps_step.index)).ffill().reindex(idx_all)
        pe_hist = (px["Close"][px.index <= d] / e).where(e > 0)
        quarterly = None
    mcap = price * shares if shares else None
    debt = (t or {}).get("total_debt") or 0.0
    cash = (t or {}).get("cash_sti") or (t or {}).get("cash") or 0.0
    net_debt = debt - cash
    is_fin = any(k in (industry_key or "") for k in ("bank", "insurance")) or "金融" in (industry_tw or "")
    fund = {
        "profitability": FU.profitability(annual, t, quarterly),
        "growth": FU.growth(annual, annual, quarterly, t, t_prev, {}),
        "stability": FU.stability(annual, quarterly),
        "piotroski": FU.piotroski(annual),
        "beneish": FU.beneish(annual),
        "efficiency": FU.efficiency(annual, t, quarterly),
        "shareholder": FU.shareholder(annual, t, mcap, {}),
        "health": FU.health(annual, t, mcap, is_fin),
    }
    # --- 技術與大盤(截到當天) ---
    bench = macro["bench"][macro["bench"].index <= d]
    tech = TE.analyze(h, bench.tail(760), market, light=True)
    reg = RG.compute(market, bench, macro["vix"][macro["vix"].index <= d] if macro.get("vix") is not None else None,
                     macro["tnx"][macro["tnx"].index <= d] if macro.get("tnx") is not None else None,
                     macro["fx"][macro["fx"].index <= d] if macro.get("fx") is not None else None)
    # --- 估值(與網站同一套;沒有分析師與同業) ---
    rev_ttm = t.get("revenue")
    tax = fund["profitability"].get("tax_rate") or 0.2
    fcf_m = (annual["fcf"] / annual["revenue"]).dropna().tail(3).tolist() if "fcf" in annual and "revenue" in annual else []
    ttm_m = (t["fcf"] / t["revenue"]) if t.get("fcf") is not None and t.get("revenue") else None
    margin = None
    if fcf_m or ttm_m is not None:
        base_m = float(np.median(fcf_m)) if fcf_m else ttm_m
        margin = 0.5 * base_m + 0.5 * ttm_m if ttm_m is not None and fcf_m else base_m
        ie = t.get("interest_expense")
        if ie and rev_ttm:
            margin += abs(ie) * (1 - tax) / rev_ttm
    cyc = detect_cyclical(annual, industry_tw, industry_key)
    if cyc.get("cyclical") and margin is not None and cyc.get("fcf_margin_long") is not None:
        margin = 0.4 * margin + 0.6 * cyc["fcf_margin_long"]
    g0 = fund["growth"].get("revenue_cagr_3y")
    if g0 is None:
        g0 = fund["growth"].get("revenue_yoy")
    om = (annual["operating_income"] / annual["revenue"]).dropna().tail(12 if cyc.get("cyclical") else 5) \
        if "operating_income" in annual else pd.Series(dtype=float)
    ebit_norm = float(om.median()) * rev_ttm if len(om) and rev_ttm else None
    roe_all = (annual["net_income"] / annual["equity"]).dropna() if "equity" in annual else pd.Series(dtype=float)
    roe_norm = min(roe_all.tail(3).mean(), roe_all.tail(5).median()) if len(roe_all) else None
    vctx = {
        "price": price, "market": market, "currency": "TWD" if market == "TW" else "USD", "shares": shares,
        "net_debt": net_debt, "revenue_ttm": rev_ttm, "tax_rate": tax, "industry_key": industry_key,
        "industry_tw": industry_tw, "ni_ttm": t.get("net_income"), "eps_ttm": eps_ttm, "growth_ref": g0,
        "beta": tech.get("beta_adj"), "rf_us": (reg.get("us10y") or {}).get("value"), "market_cap": mcap,
        "total_debt": debt, "interest_expense": t.get("interest_expense"), "fcff_margin": margin,
        "margin_basis": "", "g_start": g0, "g_basis": "過去 3 年營收 CAGR(回測沒有分析師預估)", "pe_hist": pe_hist,
        "ntm_eps": None, "close_s": None, "eps_ttm_s": None, "peers": [], "ebitda_ttm": t.get("ebitda"), "targets": {},
        "ebit_norm": ebit_norm, "roe_norm": roe_norm,
        "bvps": (t.get("equity") / shares) if t.get("equity") and shares else None, "vol_1y": tech.get("vol_1y"),
        "financial_currency": "TWD" if market == "TW" else "USD", "eps_cagr_3y": fund["growth"].get("eps_cagr_3y"),
        "cyclical": cyc, "dividend_yield": 0.0, "rates_asof": macro.get("rates") or {},
    }
    params_bt = json.loads(json.dumps(params))
    params_bt["valuation"]["mc_draws"] = 1500  # 回測時減少模擬次數(只影響不確定性分數的精細度)
    val = VM.run(vctx, params_bt)
    mult = _multiples(price, mcap, t, eps_ttm, net_debt)
    chips = {"market": market}
    if market == "TW" and chips_fm:   # P2-H07:d 當時拿得到的籌碼歷史(PIT,見 lab/pit/build_chips.py)
        from ..analysis import chips as CH
        chips = CH.tw(chips_fm, shares, h["Close"], None)
    sctx = {"fund": fund, "valuation": val, "technical": tech, "chips": chips, "expectations": {},
            "news_summary": {}, "regime": reg, "peers": [], "month_revenue": mr, "multiples": mult, "market_cap": mcap,
            "income_risk": income_risk if income_risk is not None else IR.from_yf(px, macro.get("bench_tr"), d)}
    pillars = SC.build(sctx)
    pw = params["pillar_weights"]
    lt = SC.composite(pillars, pw["long_term"])
    st = SC.composite(pillars, SC.short_term_weights(params, market))
    lt_dec = DE.long_term(val, lt, fund, pillars, market, params.get("decision_rules"))
    st_dec = DE.short_term(st, {}, params)
    out = {
        "date": d.strftime("%Y-%m-%d"), "symbol": sym, "price": price,
        "fv": val.get("fair_value"), "upside": val.get("upside"), "uncertainty": val.get("uncertainty"),
        "lt_level": lt_dec.get("level"), "lt_score": lt.get("score"), "st_code": st_dec.get("code"), "st_score": st.get("score"),
        "pillars": {k: p.get("score") for k, p in pillars.items()}, "cyclical": bool(cyc.get("cyclical")),
        # M4:基礎變數快照(backtest_pit 存進 raw_{tw,us}.sqlite 給自訂模型重算;不寫進逐筆檔)
        "_env": SC.base_vars(sctx),
    }
    if params.get("cross_sectional_scoring") or params.get("tw_industry_neutral_valuation"):
        # P2-H05:帶出長期分數用到的指標(key、原始值、絕對分數、權重),由 backtest_pit 依當日股票池改算百分位;不寫進逐筆檔
        out["_items"] = {k: [(i["key"], i["value"], i.get("abs_score"), i["weight"]) for i in pillars[k]["items"] if i["available"]]
                         for k in pw["long_term"] if k in pillars}
    return out


# ---------------- 主流程 ----------------

def run(market: str, start: str = "2021-01-01", progress=None) -> dict:
    t0 = time.time()
    params = load_params()
    lst = TW_LARGE if market == "TW" else US_LARGE
    syms = [s for s, _ in lst]
    bench_sym = "^TWII" if market == "TW" else "^GSPC"
    extra = [bench_sym, "^VIX", "^TNX"] + (["TWD=X"] if market == "TW" else [])
    px = _download(syms + extra)
    macro = {"bench": px.get(bench_sym), "vix": px.get("^VIX"), "tnx": px.get("^TNX"), "fx": px.get("TWD=X")}
    last = macro["bench"].index[-1]
    dates = [d for d in pd.date_range(start, last, freq="BME") if d <= last]
    # 對齊到實際交易日
    idx = macro["bench"].index
    dates = sorted({idx[idx.searchsorted(d, side="right") - 1] for d in dates})
    rows, skipped = [], []
    for i, s in enumerate(syms):
        if progress:
            progress(i, len(syms), s)
        p = px.get(s)
        if p is None:
            skipped.append({"symbol": s, "reason": "沒有股價"})
            continue
        try:
            if market == "TW":
                code = s.split(".")[0]
                fdat = tw_data(code)
                meta = resolver.tw_lookup(code) or {}
                ind_tw, ind_key = meta.get("industry"), None
            else:
                yb_splits = yahoo._get(s, "splits", "info", yahoo.Status(), False)
                fdat = us_data(s, yb_splits)
                ind_tw, ind_key = None, (yahoo.quick_info(s) or {}).get("industryKey")
            if fdat is None:
                skipped.append({"symbol": s, "reason": "沒有歷史財報"})
                continue
            for d in dates:
                try:
                    r = judge_at(d, market, s, p, fdat, macro, params, ind_tw, ind_key)
                    if r:
                        rows.append(r)
                except Exception as e:  # noqa: BLE001
                    skipped.append({"symbol": s, "date": str(d.date()), "reason": f"{type(e).__name__}: {e}"[:160]})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            skipped.append({"symbol": s, "reason": f"{type(e).__name__}: {e}"[:160]})
    df = pd.DataFrame(rows)
    if "_env" in df:
        df = df.drop(columns="_env")
    res = evaluate(df, px, syms, bench_sym, market)
    res.update({"market": market, "universe": "台股權值 40" if market == "TW" else "美股大型 40", "start": start,
                "n_judgments": int(len(df)), "n_stocks": int(df["symbol"].nunique()) if len(df) else 0,
                "skipped": skipped[:60], "n_skipped": len(skipped), "elapsed_sec": round(time.time() - t0),
                "computed": dt.datetime.now().isoformat(timespec="seconds"),
                "model_version": params["version"]})
    txt = json.dumps(res, ensure_ascii=False, default=float)
    (OUT_DIR / f"backtest_{market}.json").write_text(txt, encoding="utf-8")
    (OUT_DIR / f"backtest_{market}_{params['version'].split()[-1]}.json").write_text(txt, encoding="utf-8")
    df.to_csv(OUT_DIR / f"backtest_{market}_rows.csv", index=False)
    return res


def _fwd(adj: pd.Series, d: pd.Timestamp, n: int) -> float | None:
    i = adj.index.searchsorted(d, side="right") - 1
    if i < 0 or i + n >= len(adj):
        return None
    a, b = adj.iloc[i], adj.iloc[i + n]
    return float(b / a - 1) if a and b and not math.isnan(a) and not math.isnan(b) else None


SPLIT_NOTE = ("只含開發集:每個預測期的結果視窗都在 2024-10-01 前結束(結果視窗跨進 holdout 的判斷剔除),"
              "模擬投組只到 2024-09。2024-10-01 以後的判斷不在任何彙總裡。")


def evaluate(df: pd.DataFrame, px: dict, syms: list[str], bench_sym: str, market: str) -> dict:
    if df.empty:
        return {"error": "沒有任何回測結果"}
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    adj = {s: (px[s]["Adj Close"] if "Adj Close" in px[s] else px[s]["Close"]).dropna() for s in syms if s in px}
    bench = px[bench_sym]["Close"].dropna()
    # holdout 保護:判斷日在 holdout 起點以後的列整個不進彙總
    df = df[df["date"] < splits.HOLDOUT_START].reset_index(drop=True)
    if df.empty:
        return {"error": "開發集沒有任何回測結果"}
    for k, n in H.items():
        df[f"r_{k}"] = [_fwd(adj[s], d, n) if s in adj else None for s, d in zip(df["symbol"], df["date"])]
        df[f"b_{k}"] = [_fwd(bench, d, n) for d in df["date"]]
        # 相對「同一天、同股票池」的中位數:隨便猜的命中率剛好 50%(用平均數會被少數飆股拉高,對買進不公平)
        df[f"x_{k}"] = df[f"r_{k}"] - df.groupby("date")[f"r_{k}"].transform("median")
        df[f"xb_{k}"] = df[f"r_{k}"] - df[f"b_{k}"]
        # 結果視窗跨進 holdout 的(purged)與沒有結果的,這個預測期的報酬一律當作沒有
        not_dev = (splits.assign(df["date"], k, bench.index) != splits.DEV).to_numpy()
        for c in ("r", "b", "x", "xb"):
            df.loc[not_dev, f"{c}_{k}"] = np.nan
    out: dict = {"split": splits.DEV, "holdout_start": str(splits.HOLDOUT_START.date()), "split_note": SPLIT_NOTE}

    def hit_block(mask_good, mask_bad, col):
        g = df[mask_good & df[col].notna()]
        b = df[mask_bad & df[col].notna()]
        right = int((g[col] > 0).sum() + (b[col] < 0).sum())
        wrong = int((g[col] <= 0).sum() + (b[col] >= 0).sum())
        return {"right": right, "wrong": wrong, "hit": right / (right + wrong) if right + wrong else None,
                "n_bull": int(len(g)), "n_bear": int(len(b)),
                "bull_hit": float((g[col] > 0).mean()) if len(g) else None,
                "bear_hit": float((b[col] < 0).mean()) if len(b) else None,
                "bull_avg": float(g[col].mean()) if len(g) else None, "bear_avg": float(b[col].mean()) if len(b) else None}

    lt_buy, lt_sell = df["lt_level"] >= 3, df["lt_level"] <= 1
    st_bull, st_bear = df["st_code"] > 0, df["st_code"] < 0
    out["long_term"] = {h: hit_block(lt_buy, lt_sell, f"x_{h}") for h in ("6m", "12m")}
    out["long_term_vs_index"] = {h: hit_block(lt_buy, lt_sell, f"xb_{h}") for h in ("6m", "12m")}
    out["short_term"] = {h: hit_block(st_bull, st_bear, f"x_{h}") for h in ("1m", "3m")}
    out["short_term_vs_index"] = {h: hit_block(st_bull, st_bear, f"xb_{h}") for h in ("1m", "3m")}
    # 依評等分組的平均超額報酬(理想:評等越高越好,單調)
    out["by_rating"] = []
    for lvl in (4, 3, 2, 1, 0):
        g = df[(df["lt_level"] == lvl) & df["x_12m"].notna()]
        if len(g):
            out["by_rating"].append({"rating": LT_NAMES[lvl], "n": int(len(g)), "avg_x12": float(g["x_12m"].mean()),
                                     "win": float((g["x_12m"] > 0).mean()), "avg_r12": float(g["r_12m"].mean())})
    out["by_signal"] = []
    for c, name in ((1, "偏多"), (0, "中性"), (-1, "偏空")):
        g = df[(df["st_code"] == c) & df["x_3m"].notna()]
        if len(g):
            out["by_signal"].append({"signal": name, "n": int(len(g)), "avg_x3": float(g["x_3m"].mean()),
                                     "win": float((g["x_3m"] > 0).mean())})
    # 逐年
    out["by_year"] = []
    for y, g in df.groupby(df["date"].dt.year):
        gl = g[g["x_12m"].notna()]
        right = int(((gl["lt_level"] >= 3) & (gl["x_12m"] > 0)).sum() + ((gl["lt_level"] <= 1) & (gl["x_12m"] < 0)).sum())
        wrong = int(((gl["lt_level"] >= 3) & (gl["x_12m"] <= 0)).sum() + ((gl["lt_level"] <= 1) & (gl["x_12m"] >= 0)).sum())
        gs = g[g["x_3m"].notna()]
        sr = int(((gs["st_code"] > 0) & (gs["x_3m"] > 0)).sum() + ((gs["st_code"] < 0) & (gs["x_3m"] < 0)).sum())
        sw = int(((gs["st_code"] > 0) & (gs["x_3m"] <= 0)).sum() + ((gs["st_code"] < 0) & (gs["x_3m"] >= 0)).sum())
        out["by_year"].append({"year": int(y), "lt_right": right, "lt_wrong": wrong, "lt_hit": right / (right + wrong) if right + wrong else None,
                               "st_right": sr, "st_wrong": sw, "st_hit": sr / (sr + sw) if sr + sw else None})
    # 各面向的預測力(每月橫斷面 Spearman IC 的平均)
    out["pillar_ic"] = []
    pil = pd.DataFrame(list(df["pillars"])).add_prefix("p_")
    d2 = pd.concat([df.reset_index(drop=True), pil], axis=1)
    for col in [c for c in d2.columns if c.startswith("p_")] + ["lt_score", "st_score", "upside"]:
        for hz in ("3m", "12m"):
            ics = []
            for _, g in d2.groupby("date"):
                g = g[[col, f"x_{hz}"]].dropna()
                if len(g) >= 10 and g[col].nunique() > 1:
                    ics.append(g[col].rank().corr(g[f"x_{hz}"].rank()))
            if len(ics) >= 6:
                s = pd.Series(ics)
                out["pillar_ic"].append({"key": col.replace("p_", ""), "horizon": hz, "ic": float(s.mean()),
                                         "t": float(s.mean() / s.std() * math.sqrt(len(s))) if s.std() > 0 else None,
                                         "n_months": len(s), "hit": float((s > 0).mean())})
    # 模擬投資組合:每月等權持有「長期買進以上」的股票,對照「全部等權」與大盤
    out["portfolio"] = portfolio(df, adj, bench, market)
    return out


def portfolio(df: pd.DataFrame, adj: dict, bench: pd.Series, market: str) -> dict:
    cost = 0.006 if market == "TW" else 0.001
    # 只用開發集月份:持有期的終點也必須在 holdout 起點之前
    dates = sorted(d for d in pd.to_datetime(df["date"]).unique() if pd.Timestamp(d) < splits.HOLDOUT_START)
    curve = {"date": [], "model": [], "universe": [], "bench": [], "model_st": [], "ex_sell": []}
    v_m = v_u = v_b = v_s = v_x = 1.0
    prev_x = set()
    prev = set()
    turns = []
    for i in range(len(dates) - 1):
        d, d1 = dates[i], dates[i + 1]
        g = df[df["date"] == d]
        rets = {}
        for s in g["symbol"]:
            a = adj.get(s)
            if a is None:
                continue
            i0, i1 = a.index.searchsorted(d, side="right") - 1, a.index.searchsorted(d1, side="right") - 1
            if i0 >= 0 and i1 > i0:
                rets[s] = float(a.iloc[i1] / a.iloc[i0] - 1)
        if not rets:
            continue
        picks = [s for s in g[g["lt_level"] >= 3]["symbol"] if s in rets]
        picks_st = [s for s in g[(g["lt_level"] >= 3) & (g["st_code"] >= 0)]["symbol"] if s in rets]
        picks_x = [s for s in g[g["lt_level"] >= 2]["symbol"] if s in rets]  # 只避開「減碼 / 賣出」
        uni = float(np.mean(list(rets.values())))
        pm = float(np.mean([rets[s] for s in picks])) if picks else uni
        ps = float(np.mean([rets[s] for s in picks_st])) if picks_st else uni
        px_ = float(np.mean([rets[s] for s in picks_x])) if picks_x else uni
        cur_x = set(picks_x)
        turn_x = len(cur_x ^ prev_x) / max(1, len(cur_x | prev_x)) if prev_x or cur_x else 0
        prev_x = cur_x
        cur = set(picks)
        turnover = len(cur ^ prev) / max(1, len(cur | prev)) if prev or cur else 0
        turns.append(turnover)
        prev = cur
        b0, b1 = bench.index.searchsorted(d, side="right") - 1, bench.index.searchsorted(d1, side="right") - 1
        br = float(bench.iloc[b1] / bench.iloc[b0] - 1)
        v_m *= 1 + pm - turnover * cost
        v_s *= 1 + ps - turnover * cost
        v_x *= 1 + px_ - turn_x * cost
        v_u *= 1 + uni
        v_b *= 1 + br
        curve["date"].append(pd.Timestamp(d1).strftime("%Y-%m"))
        curve["model"].append(round(v_m, 4))
        curve["model_st"].append(round(v_s, 4))
        curve["ex_sell"].append(round(v_x, 4))
        curve["universe"].append(round(v_u, 4))
        curve["bench"].append(round(v_b, 4))
    yrs = len(curve["date"]) / 12 if curve["date"] else 1

    def stats(series):
        s = pd.Series(series)
        r = s.pct_change().fillna(s.iloc[0] - 1)
        cagr = s.iloc[-1] ** (1 / yrs) - 1
        mdd = float((s / s.cummax() - 1).min())
        sharpe = float(r.mean() / r.std() * math.sqrt(12)) if r.std() > 0 else None
        return {"total": float(s.iloc[-1] - 1), "cagr": float(cagr), "max_dd": mdd, "sharpe": sharpe}

    return {"curve": curve, "cost": cost, "avg_turnover": float(np.mean(turns)) if turns else None,
            "stats": {k: stats(curve[k]) for k in ("model", "model_st", "ex_sell", "universe", "bench")} if curve["date"] else {}}


def latest(market: str) -> dict | None:
    p = OUT_DIR / f"backtest_{market}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
