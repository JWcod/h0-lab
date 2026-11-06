"""報告組裝:輸入代號 → 抓全部資料 → 計算所有指標 / 估值 / 評分 / 決策 / 見解 → 一份 JSON。"""
from __future__ import annotations

import datetime as dt
import math
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import agents as AG
from . import notes, resolver, snapshots
from .analysis import chips as CH
from .analysis import decision as DE
from .analysis import expectations as EX
from .analysis import financials as FN
from .analysis import fundamentals as FU
from .analysis import income_risk as IR
from .analysis import insights as IN
from .analysis import regime as RG
from .analysis import scoring as SC
from .analysis import technical as TE
from .analysis.financials import safe
from .config import load_params
from .sources import bench_tr, finmind, mops, news, sec, tdcc, twse, yahoo
from .sources.yahoo import Status


def clean(o):
    """遞迴轉成可 JSON 序列化的型別(NaN/Inf → None)。"""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, dt.datetime, dt.date)):
        return o.isoformat()[:19]
    if isinstance(o, (pd.DataFrame, pd.Series)):
        return None
    return o


def _fx(from_ccy: str | None, to_ccy: str | None) -> float:
    if not from_ccy or not to_ccy or from_ccy == to_ccy:
        return 1.0
    h = yahoo.index_history(f"{from_ccy}{to_ccy}=X", "1mo")
    if h is not None and len(h):
        return float(h["Close"].iloc[-1])
    return 1.0


def _peer_row(sym: str) -> dict | None:
    i = yahoo.quick_info(sym)
    if not i or not i.get("marketCap"):
        return None
    return {
        "symbol": sym, "name": i.get("shortName") or i.get("longName"), "price": safe(i.get("currentPrice") or i.get("regularMarketPrice")),
        "market_cap": safe(i.get("marketCap")), "currency": i.get("currency"),
        "pe": safe(i.get("trailingPE")), "forward_pe": safe(i.get("forwardPE")), "ev_ebitda": safe(i.get("enterpriseToEbitda")),
        "ev_sales": safe(i.get("enterpriseToRevenue")), "ps": safe(i.get("priceToSalesTrailing12Months")),
        "pb": safe(i.get("priceToBook")), "gross_margin": safe(i.get("grossMargins")), "operating_margin": safe(i.get("operatingMargins")),
        "roe": safe(i.get("returnOnEquity")), "revenue_growth": safe(i.get("revenueGrowth")),
        "earnings_growth": safe(i.get("earningsGrowth")), "dividend_yield": safe(i.get("dividendYield")),
    }


def peers_for(symbol: str, market: str, info: dict, tw_meta: dict | None) -> tuple[list[dict], dict]:
    cands: list[str] = []
    extra: dict = {}
    if market == "US":
        for p in yahoo.industry_peers(info.get("industryKey"), 12):
            if p["symbol"] != symbol and "." not in p["symbol"]:
                cands.append(p["symbol"])
        cands = cands[:9]
        # 產業內同業太小(例如 Apple 的產業只有 Sonos 這類小公司)→ 改用同類股(sector)前幾大
        rows0 = [r for r in (yahoo.quick_info(c) for c in cands[:6]) if r]
        mcap = safe(info.get("marketCap")) or 0
        big = [r for r in rows0 if (safe(r.get("marketCap")) or 0) >= 0.02 * mcap]
        if len(big) < 3 and info.get("sectorKey"):
            extra["peer_source"] = "同類股前幾大(產業內同業規模差太多)"
            cands = [p["symbol"] for p in yahoo.sector_top(info.get("sectorKey"), 12)
                     if p["symbol"] != symbol and "." not in p["symbol"]][:9]
        else:
            extra["peer_source"] = "同產業"
    elif tw_meta:
        tw, _ = resolver.universe()
        same = [x for x in tw if x["industry"] == tw_meta.get("industry") and x["code"] != tw_meta["code"]]
        try:
            vt = twse.valuation_table()
            codes = {x["code"] for x in same} | {tw_meta["code"]}
            sub = vt[vt["code"].isin(codes)]
            def med(c):
                s = sub[c].dropna()
                s = s[(s > 0) & (s < 200)] if c == "pe" else s
                return safe(s.median()) if len(s) >= 3 else None
            extra = {"industry": tw_meta.get("industry"), "n": int(len(sub)), "pe_median": med("pe"),
                     "pb_median": med("pb"), "dy_median": med("dy")}
            me = vt[vt["code"] == tw_meta["code"]]
            if not me.empty:
                extra["self"] = {"pe": safe(me.iloc[0]["pe"]), "pb": safe(me.iloc[0]["pb"]), "dy": safe(me.iloc[0]["dy"])}
            extra["table"] = [{"code": r.code, "name": r.name, "pe": safe(r.pe), "pb": safe(r.pb), "dy": safe(r.dy)}
                              for r in sub.itertuples()][:200]
        except Exception:  # noqa: BLE001
            pass
        # 候選:同產業依成交金額(規模代理)取前 12 檔,再用 Yahoo 市值排序取前 8
        tv = twse.trade_values()
        same.sort(key=lambda x: -(tv.get(x["code"]) or 0))
        cands = [x["symbol"] for x in same][:12]
    rows = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(_peer_row, cands):
            if r:
                if r["symbol"].endswith((".TW", ".TWO")):  # 台股同業用中文名稱
                    x = resolver.tw_lookup(r["symbol"].split(".")[0])
                    if x:
                        r["name"] = x["name"]
                rows.append(r)
    rows.sort(key=lambda r: -(r.get("market_cap") or 0))
    return rows[:8], extra


CYC_TW = ("航運", "鋼鐵", "塑膠", "化學", "水泥", "造紙", "玻璃", "油電燃氣", "橡膠", "汽車")
CYC_US = ("marine-shipping", "steel", "chemicals", "oil-gas", "copper", "aluminum", "coal", "airlines", "auto-manufacturers",
          "building-materials", "lumber", "agricultural", "metal", "trucking", "semiconductor-equipment")


def detect_cyclical(tbl: pd.DataFrame, industry_tw: str | None, industry_key: str | None) -> dict:
    """景氣循環判斷:長期營業利益率波動大且曾大幅回落,或屬於典型循環產業。"""
    out = {"cyclical": False}
    if tbl is None or tbl.empty or "operating_income" not in tbl or "revenue" not in tbl:
        return out
    om = (tbl["operating_income"] / tbl["revenue"]).dropna().tail(12)
    if len(om) < 5:
        return out
    std = float(om.std())
    peak, dd = -9.0, 0.0
    for v in om:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    hint = any(k in (industry_tw or "") for k in CYC_TW) or any(k in (industry_key or "") for k in CYC_US)
    # 真正的景氣循環:利潤率大跌的那一年,營收也停滯或衰退(需求下滑);
    # 營收大增但利潤率下降多半是併購攤銷(例如 Broadcom 併 VMware),不算循環
    rev = tbl["revenue"].reindex(om.index)
    rg = rev.pct_change()
    down_years = int(((om.diff() <= -0.08) & (rg <= 0.05)).sum())
    stat = std >= 0.07 and dd >= 0.15 and down_years >= 1
    out.update({"om_mean": float(om.mean()), "om_median": float(om.median()), "om_std": std, "om_max_drop": dd,
                "om_now": float(om.iloc[-1]), "years": int(len(om)), "industry_hint": hint})
    if "fcf" in tbl:
        fm = (tbl["fcf"] / tbl["revenue"]).dropna().tail(12)
        if len(fm) >= 5:
            out["fcf_margin_long"] = float(fm.median())  # 用中位數:不被景氣高峰年拉高
    if stat or (hint and std >= 0.04):
        out["cyclical"] = True
        out["reason"] = (f"近 {len(om)} 年營業利益率標準差 {std:.1%}、最大回落 {dd:.1%}" + (",且屬於典型循環產業" if hint else ""))
    return out


def _us_eps_ttm_series(ed: pd.DataFrame | None) -> pd.Series | None:
    if ed is None or ed.empty or "Reported EPS" not in ed:
        return None
    s = ed["Reported EPS"].dropna()
    if len(s) < 5:
        return None
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    s = s.sort_index()
    s = s[~s.index.duplicated(keep="last")]
    ttm = s.rolling(4).sum().dropna()
    return ttm


def _long_term_decision(val: dict, lt: dict, fund: dict, pillars: dict, market: str, params: dict) -> dict:
    """P2-H10(參數 decision_rules.fixed_quantile_levels):有切點檔就用股票池固定分位(分位規則和回測相同,切點來自回測分數分布,網站分數不完全可比);
    沒有切點檔就退回舊規則並標示。v1.1 參數沒有這個鍵 = 舊行為(輸出完全不變)。"""
    rules = params.get("decision_rules") or {}
    cuts = rules.get("fixed_quantile_levels")
    if not cuts:
        return DE.long_term(val, lt, fund, pillars, market, rules)
    cp, why = DE.load_cutpoints(market, params)
    if cp is not None:
        return DE.long_term_quantile(val, lt, fund, cp, cuts)
    out = DE.long_term(val, lt, fund, pillars, market, rules)
    out["method"] = "legacy_fallback"
    if out.get("level") is not None:
        out["reasons"].append(f"⚠ {why}:暫時改用估值區間規則等級,和回測用的固定分位等級不一致")
    return out


def build(symbol: str, refresh: bool = False, progress=None) -> dict:
    """progress(event):即時回報給前端的 agent 畫面(可省略)。"""
    t0 = time.time()
    params = load_params()

    def emit(agent: str, kind: str, msg: str = "", **kw):
        if progress:
            try:
                progress({"agent": agent, "kind": kind, "msg": msg, "t": round(time.time() - t0, 2), **kw})
            except Exception:  # noqa: BLE001
                pass

    def on_source(rec):
        mark = "✓" if rec["ok"] else "✗"
        extra = f"({rec['n']} 筆)" if rec.get("n") not in (None, True) and rec["ok"] else (f":{rec.get('error', '')[:60]}" if not rec["ok"] else "")
        emit("data", "log", f"{mark} {rec['source']} · {rec['item']}{extra}", ok=rec["ok"])

    status = Status(on_source)
    market = "TW" if symbol.endswith((".TW", ".TWO")) else "US"
    code = symbol.split(".")[0] if market == "TW" else symbol
    tw_meta = resolver.tw_lookup(code) if market == "TW" else None

    # ---------- 抓資料(平行) ----------
    emit("data", "start", f"開始蒐集 {symbol} 的資料(Yahoo、{'FinMind、集保、證交所' if market == 'TW' else 'SEC EDGAR'}、新聞)")
    emit("market", "log", "等待股價與大盤資料…")
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_y = ex.submit(yahoo.bundle, symbol, status, refresh)
        f_fm = ex.submit(finmind.bundle, code, status, refresh) if market == "TW" else None
        f_rg = ex.submit(RG.analyze, market)
        f_td = ex.submit(tdcc.distribution, code) if market == "TW" else None
        f_btr = ex.submit(bench_tr.recent, market)   # 含息大盤(beta_2y 用;失敗是 None)
        yb = f_y.result()
        fm = f_fm.result() if f_fm else {}
        regime = f_rg.result()
        td = None
        if f_td:
            try:
                td = f_td.result()
                status.ok("TDCC", "集保股權分散", td and td.get("history") and len(td["history"]))
            except Exception as e:  # noqa: BLE001
                status.fail("TDCC", "集保股權分散", str(e))
    info = yb.get("info") or {}
    hist = yb.get("history")
    if hist is None or hist.empty:
        emit("data", "error", f"找不到 {symbol} 的股價資料")
        raise ValueError(f"找不到 {symbol} 的股價資料")
    emit("data", "finding", f"股價歷史 {len(hist)} 個交易日、公司:{info.get('longName') or info.get('shortName') or symbol}")
    qt = (info.get("quoteType") or "").upper()
    if qt in ("ETF", "MUTUALFUND", "INDEX", "CRYPTOCURRENCY", "CURRENCY", "FUTURE"):
        raise ValueError(f"{symbol} 是 {qt},目前只支援個股分析")
    price = safe(info.get("currentPrice") or info.get("regularMarketPrice")) or float(hist["Close"].iloc[-1])
    currency = info.get("currency") or ("TWD" if market == "TW" else "USD")
    fin_ccy = info.get("financialCurrency") or currency
    fx = _fx(fin_ccy, currency)
    adr_like = fin_ccy != currency

    # ---------- 財報 ----------
    annual = FN.from_yahoo(yb.get("income_stmt"), yb.get("balance_sheet"), yb.get("cashflow"))
    yq = FN.from_yahoo(yb.get("quarterly_income_stmt"), yb.get("quarterly_balance_sheet"), yb.get("quarterly_cashflow"))
    if market == "TW":
        twq = FN.tw_quarterly(fm)
        quarterly = twq if not twq.empty else yq
        long = FN.annualize_quarters(twq) if not twq.empty else annual
        q_source = "FinMind(公開資訊觀測站)" if not twq.empty else "Yahoo(FinMind 暫時無法取得)"
        if not twq.empty:
            status.ok("FinMind", "季財報整理(2014 起)", len(twq))
    else:
        q_source = "Yahoo"
        quarterly = yq
        long = pd.DataFrame()
        cik = sec.cik_for(symbol)
        if not cik:
            status.fail("SEC", "CIK 對照(美國公司編號)", "找不到或 SEC 暫時拒絕連線(非美國公司 ADR 也可能沒有)")
        if cik:
            try:
                long = sec.annual_table(cik)
                if not long.empty:
                    long = sec.split_adjust(long, yb.get("splits"))
                    long["fcf"] = long["cfo"] - long["capex"].abs() if "cfo" in long and "capex" in long else np.nan
                    status.ok("SEC", "10 年以上年報(XBRL,已做股票分割調整)", len(long))
            except Exception as e:  # noqa: BLE001
                status.fail("SEC", "10 年以上年報(XBRL)", str(e))
    # 資料交叉驗證:台股 Yahoo 年營收 vs FinMind(公開資訊觀測站)加總,差太多就在資料狀態提醒
    if market == "TW" and not long.empty and not annual.empty and "revenue" in annual:
        y = annual.index[-1].year
        a_rev = safe(annual["revenue"].iloc[-1])
        f_row = long[long.index.year == y]
        f_rev = safe(f_row["revenue"].iloc[-1]) if not f_row.empty else None
        if a_rev and f_rev:
            diff = a_rev / f_rev - 1
            if abs(diff) > 0.03:
                status.fail("交叉驗證", f"{y} 年營收 Yahoo vs 公開資訊觀測站", f"差異 {diff:+.1%},以 FinMind 季資料為主")
            else:
                status.ok("交叉驗證", f"{y} 年營收 Yahoo vs 公開資訊觀測站(差 {diff:+.1%})")
    t = FN.ttm(quarterly)
    t_prev = FN.ttm(quarterly, 4)
    if t is None and not annual.empty:
        t = {c: safe(annual[c].iloc[-1]) for c in annual.columns}
        t["period_end"] = annual.index[-1].date().isoformat()
        t["basis"] = "最新年度(季資料不足)"
    elif t:
        t["basis"] = "近四季合計"
    # 存量項目用最新季資料(美股 Yahoo 季表)
    if t and market == "US" and not yq.empty:
        for k in ("total_debt", "cash_sti", "cash", "equity", "total_assets", "current_assets", "current_liabilities",
                  "total_liabilities", "inventory", "receivables", "retained_earnings", "long_term_debt", "payables"):
            if k in yq and pd.notna(yq[k].iloc[-1]):
                t[k] = float(yq[k].iloc[-1])
    if t and market == "TW":
        # 台股:現金(含短期投資)與有息負債優先用 Yahoo 同一季的資產負債表(欄位定義較完整),沒有才用年報
        same_q = (not yq.empty and t.get("period_end") and yq.index[-1] == pd.Timestamp(t["period_end"]))
        for k in ("cash_sti", "long_term_debt", "total_debt"):
            v = safe(yq[k].iloc[-1]) if same_q and k in yq else None
            if v is None and not annual.empty and k in annual and (t.get(k) is None or (k == "total_debt" and not t.get(k))):
                v = safe(annual[k].iloc[-1])
            if v is not None:
                t[k] = v
    # 台股現金流量表沒有股利欄位:用股利政策資料(近 12 個月除息)× 股數 推算
    if t and market == "TW" and not t.get("dividends_paid"):
        dv = fm.get("dividend")
        if dv is not None and not dv.empty and "CashExDividendTradingDate" in dv:
            d = dv.copy()
            d["ex"] = pd.to_datetime(d["CashExDividendTradingDate"], errors="coerce")
            recent = d[d["ex"] >= pd.Timestamp(dt.date.today() - dt.timedelta(days=365))]
            dps = (recent["CashEarningsDistribution"].fillna(0) + recent["CashStatutorySurplus"].fillna(0)).sum()
            if dps > 0 and t.get("shares_out"):
                t["dividends_paid"] = -float(dps) * t["shares_out"]
                t["dps_ttm"] = float(dps)
    # 外幣財報(ADR):金額換算成報價幣別
    if adr_like and fx != 1.0:
        for d in (t, t_prev):
            if d:
                for k, v in list(d.items()):
                    if isinstance(v, (int, float)) and k not in ("eps_diluted", "tax_rate"):
                        d[k] = v * fx

    market_cap = safe(info.get("marketCap"))
    shares = safe(info.get("sharesOutstanding")) or (t.get("shares_out") if t else None)
    if market_cap and price:
        shares = market_cap / price  # ADR 比例也自動處理
    elif shares and price:
        market_cap = shares * price

    emit("data", "done", f"資料蒐集完成:{sum(1 for x in status.items if x['ok'])} 項成功、{sum(1 for x in status.items if not x['ok'])} 項缺漏")
    emit("fund", "start", f"整理財報:年度 {len(annual)} 年、季度 {len(quarterly)} 季" + (f"、長期 {len(long)} 年" if not long.empty else ""))
    # ---------- 各項分析 ----------
    expv = EX.analyze(yb, price)
    est_fwd = {"fwd_rev_fy1": expv.get("fwd_rev_growth_fy1"), "fwd_eps_fy1": expv.get("fwd_eps_growth_fy1")}
    fund = {
        "profitability": FU.profitability(annual, t, quarterly),
        "growth": FU.growth(annual, long if not long.empty else None, quarterly, t, t_prev, est_fwd),
        "stability": FU.stability(long if not long.empty else annual, quarterly),
        "piotroski": FU.piotroski(annual),
        "beneish": FU.beneish(annual),
        "efficiency": FU.efficiency(annual, t, quarterly),
        "shareholder": FU.shareholder(annual, t, market_cap, info),
    }
    is_fin_hint = any(k in (info.get("industryKey") or "") for k in ("bank", "insurance")) or "金融" in ((tw_meta or {}).get("industry") or "")
    fund["health"] = FU.health(annual, t, market_cap, is_fin_hint)
    emit("fund", "log", "計算獲利能力、成長、Piotroski / Altman / Beneish…")
    mr = FU.month_revenue(fm.get("month_revenue")) if market == "TW" else None

    for msg in AG.fund_findings(fund, FU.month_revenue(fm.get("month_revenue")) if market == "TW" else None):
        emit("fund", "finding", msg)
    emit("fund", "done", "財報分析完成")
    emit("market", "start", "計算均線、RSI、MACD、KD 與籌碼")
    bench = regime.get("benchmark_history")
    tech = TE.analyze(hist, bench, market)
    # 股利與風險基礎變數(上手精靈 §6;和回測同一組公式 lab/analysis/income_risk.py)
    ir_divs = None
    if market == "TW":
        ir_divs = IR.tw_dividends_web(fm.get("dividend"), hist)
        if ir_divs is None:
            ir_divs = IR.yahoo_dividends_ex_refunds(hist)
    income_risk = IR.from_yf(hist, f_btr.result(), hist.index[-1], ir_divs)
    ch = CH.tw(fm, (t or {}).get("shares_out") or shares, hist["Close"], td) if market == "TW" else CH.us(yb)

    for msg in AG.market_findings(tech, ch, regime):
        emit("market", "finding", msg)
    emit("market", "done", "技術與籌碼判讀完成")
    emit("news", "start", "搜尋新聞與官方公告,做情緒分類")
    # 新聞
    name = (tw_meta or {}).get("name") or info.get("shortName") or symbol
    items = []
    if market == "TW":
        items += news.google_news(name, "zh", 14)
        en = info.get("shortName") or info.get("longName")
        if en:
            items += news.google_news(f"{en.split(' ')[0]} {en.split(' ')[1] if len(en.split(' ')) > 1 else ''} stock".strip(), "en", 14)[:30]
    else:
        short = (info.get("shortName") or symbol).replace(", Inc.", "").replace(" Inc.", "").replace(" Corporation", "")
        items += news.google_news(f"{short} stock", "en", 14)
        items += [dict(x, origin="Yahoo") for x in yahoo.news(symbol)]
    news_items = news.enrich(items)
    status.ok("News", "新聞(Google/Yahoo)", len(news_items)) if news_items else status.fail("News", "新聞", "無資料")
    news_sum = news.summarize(news_items)
    for msg in AG.news_findings(news_sum, len(news_items), None):
        emit("news", "finding", msg)
    emit("news", "done", "消息面整理完成")
    emit("val", "start", "尋找同業、計算 WACC、跑 DCF 蒙地卡羅(數千次)…")

    peers, tw_industry = peers_for(symbol, market, info, tw_meta)
    if peers:
        status.ok("Yahoo", "同業比較", len(peers))

    # ---------- 估值輸入 ----------
    rev_ttm = (t or {}).get("revenue")
    tax = fund["profitability"].get("tax_rate") or 0.2
    debt = (t or {}).get("total_debt") or 0.0
    cash = (t or {}).get("cash_sti") or (t or {}).get("cash") or 0.0
    net_debt = debt - cash
    # FCFF 利潤率:年度 FCF 利潤率中位數(近 3 年)與 TTM 平均,再加回稅後利息
    fcf_m = []
    if not annual.empty and "fcf" in annual and "revenue" in annual:
        fcf_m = (annual["fcf"] / annual["revenue"]).dropna().tail(3).tolist()
    ttm_m = (t["fcf"] / t["revenue"]) if t and t.get("fcf") is not None and t.get("revenue") else None
    margin = None
    if fcf_m or ttm_m is not None:
        base_m = float(np.median(fcf_m)) if fcf_m else ttm_m
        margin = 0.5 * base_m + 0.5 * ttm_m if ttm_m is not None and fcf_m else base_m
        ie = (t or {}).get("interest_expense")
        if ie and rev_ttm:
            margin += abs(ie) * (1 - tax) / rev_ttm
    margin_basis = "近 3 年 FCF 利潤率中位數與近四季平均,加回稅後利息"
    cyc = detect_cyclical(long if not long.empty else annual, (tw_meta or {}).get("industry"), info.get("industryKey"))
    if cyc.get("cyclical") and margin is not None and cyc.get("fcf_margin_long") is not None:
        margin = 0.4 * margin + 0.6 * cyc["fcf_margin_long"]
        margin_basis += f";景氣循環股,再與 {cyc['years']} 年 FCF 利潤率中位數 {cyc['fcf_margin_long']:.1%} 混合(目前 40% / 循環 60%)"
    g_hist = fund["growth"].get("revenue_cagr_3y")
    g_fwd = expv.get("fwd_rev_growth_fy1") if expv.get("fwd_rev_growth_fy1") is not None else expv.get("fwd_rev_growth_fy0")
    if g_fwd is not None and g_hist is not None:
        g0, g_basis = 0.6 * g_fwd + 0.4 * g_hist, "分析師預估 60% + 過去 3 年 CAGR 40%"
    elif g_fwd is not None:
        g0, g_basis = g_fwd, "分析師預估營收成長"
    elif g_hist is not None:
        g0, g_basis = g_hist, "過去 3 年營收 CAGR"
    else:
        g0, g_basis = fund["growth"].get("revenue_yoy"), "最近一年營收成長"
    # 正規化 EBIT:近 5 年營業利益率中位數 × 近四季營收
    base_tbl = long if not long.empty and "operating_income" in long else annual
    om = (base_tbl["operating_income"] / base_tbl["revenue"]).dropna().tail(12 if cyc.get("cyclical") else 5) \
        if not base_tbl.empty and "operating_income" in base_tbl else pd.Series(dtype=float)
    ebit_norm = float(om.median()) * rev_ttm if len(om) and rev_ttm else None
    eps_ttm = None
    if adr_like:
        eps_ttm = safe(info.get("trailingEps"))
    else:
        eps_ttm = (t or {}).get("eps_diluted") or safe(info.get("trailingEps"))
    # 本益比歷史
    pe_hist, eps_ttm_s = None, None
    close_s = hist["Close"]
    if market == "TW" and fm.get("per") is not None and not fm["per"].empty:
        per = fm["per"].copy()
        per.index = pd.to_datetime(per["date"])
        pe_hist = per["PER"].astype(float).replace(0, np.nan)
        eps_ttm_s = (close_s.reindex(pe_hist.index, method="ffill") / pe_hist).dropna()
    else:
        es = _us_eps_ttm_series(yb.get("earnings_dates"))
        if es is not None and not adr_like:
            eps_ttm_s = es
            e = es.reindex(close_s.index, method="ffill")
            pe_hist = (close_s / e).where(e > 0)
    # 金融股正規化 ROE:近 3 年平均與近 5 年中位數取較低者(避免用到景氣高峰年)
    roe_src = long if not long.empty and "equity" in long and "net_income" in long else annual
    roe_all = (roe_src["net_income"] / roe_src["equity"]).dropna() if not roe_src.empty and "equity" in roe_src else pd.Series(dtype=float)
    roe_hist = pd.Series([min(roe_all.tail(3).mean(), roe_all.tail(5).median())]) if len(roe_all) else pd.Series(dtype=float)
    vctx = {
        "price": price, "market": market, "currency": currency, "shares": shares, "net_debt": net_debt,
        "revenue_ttm": rev_ttm, "tax_rate": tax, "industry_key": info.get("industryKey"),
        "industry_tw": (tw_meta or {}).get("industry"), "ni_ttm": (t or {}).get("net_income"), "eps_ttm": eps_ttm,
        "growth_ref": g0, "beta": tech.get("beta_adj") or safe(info.get("beta")),
        "rf_us": (regime.get("us10y") or {}).get("value"), "market_cap": market_cap, "total_debt": debt,
        "interest_expense": (t or {}).get("interest_expense"), "fcff_margin": margin, "margin_basis": margin_basis,
        "g_start": g0, "g_basis": g_basis, "pe_hist": pe_hist, "ntm_eps": expv.get("ntm_eps"), "close_s": close_s,
        "eps_ttm_s": eps_ttm_s, "peers": peers, "ebitda_ttm": (t or {}).get("ebitda"), "targets": expv.get("targets"),
        "ebit_norm": ebit_norm, "roe_norm": float(roe_hist.mean()) if len(roe_hist) else None,
        "bvps": ((t or {}).get("equity") / shares) if (t or {}).get("equity") and shares else None,
        "vol_1y": tech.get("vol_1y"), "financial_currency": fin_ccy,
        "eps_cagr_3y": fund["growth"].get("eps_cagr_3y"), "cyclical": cyc,
        "dividend_yield": (safe(info.get("dividendRate")) / price) if safe(info.get("dividendRate")) and price else
        ((safe(info.get("dividendYield")) or 0) / 100),
    }
    from .valuation import model as VM
    val = VM.run(vctx, params)
    for msg in AG.val_findings(val):
        emit("val", "finding", msg)
    emit("val", "done", "估值完成")
    emit("dec", "start", "綜合八大面向,套用等級規則")

    # 倍數
    ev = (market_cap or 0) + net_debt if market_cap else None
    ntm = expv.get("ntm_eps")
    fwd_g = expv.get("fwd_eps_growth_fy1") or fund["growth"].get("eps_cagr_3y")
    ge5 = (expv.get("growth_estimates") or {}).get("+5y", {}).get("stockTrend")
    peg_g = ge5 if ge5 else fwd_g
    mult = {
        "pe_ttm": price / eps_ttm if eps_ttm and eps_ttm > 0 else None,
        "forward_pe": price / ntm if ntm and ntm > 0 else None,
        "pb": market_cap / t["equity"] if t and t.get("equity") and market_cap and t["equity"] > 0 else None,
        "ps": market_cap / rev_ttm if rev_ttm and market_cap else None,
        "ev": ev, "ev_ebitda": ev / t["ebitda"] if ev and t and t.get("ebitda") and t["ebitda"] > 0 else None,
        "ev_ebit": ev / t["ebit"] if ev and t and t.get("ebit") and t["ebit"] > 0 else None,
        "ev_sales": ev / rev_ttm if ev and rev_ttm else None,
        "fcf_yield": t["fcf"] / market_cap if t and t.get("fcf") is not None and market_cap else None,
        "earnings_yield": eps_ttm / price if eps_ttm else None,
        # yfinance 的 dividendYield 單位是「%」;優先用 年股利 ÷ 股價
        "dividend_yield": (safe(info.get("dividendRate")) / price) if safe(info.get("dividendRate")) and price
        else (safe(info.get("dividendYield")) / 100 if safe(info.get("dividendYield")) is not None else None),
    }
    if mult["forward_pe"] and peg_g and peg_g > 0:
        mult["peg"] = mult["forward_pe"] / (peg_g * 100)
    mult["peg_growth_used"] = peg_g

    exd = None
    cal = yb.get("calendar") or {}
    if isinstance(cal, dict) and cal.get("Ex-Dividend Date"):
        try:
            d0 = cal["Ex-Dividend Date"]
            d0 = d0 if isinstance(d0, dt.date) else dt.date.fromisoformat(str(d0)[:10])
            if d0 >= dt.date.today():
                exd = d0.isoformat()
        except (TypeError, ValueError):
            exd = None
    ann = []
    if market == "TW":
        try:
            ann = mops.for_code(code)
        except Exception:  # noqa: BLE001
            ann = []
    recent_cut = (dt.date.today() - dt.timedelta(days=14)).isoformat()
    sctx = {"fund": fund, "valuation": val, "technical": tech, "chips": ch, "expectations": expv,
            "news_summary": news_sum, "regime": regime, "peers": peers, "month_revenue": mr, "multiples": mult,
            "market_cap": market_cap, "currency": currency, "ex_dividend_date": exd, "income_risk": income_risk,
            "announcements": [a for a in ann if a.get("date", "") >= recent_cut]}
    pillars = SC.build(sctx)
    pw = params["pillar_weights"]
    st_w = SC.short_term_weights(params, market)          # 含 P3-H01 開關(預設關閉)
    # Q2(專案決定 2026-10-16):評等只用有歷史回測的面向。其他面向(預期修正、籌碼、新聞)照樣計算與顯示,標「參考」,不進評等。
    validated = params.get("validated_pillars")
    only = (lambda w: {k: v for k, v in w.items() if k in validated}) if validated else (lambda w: w)
    lt = SC.composite(pillars, only(pw["long_term"]))
    st = SC.composite(pillars, only(st_w))
    st_full = SC.composite(pillars, st_w)                      # 含未驗證面向的參考分數(細節層顯示,不進等級)
    unvalidated = sorted({k for k in (*pw["long_term"], *st_w) if validated and k not in validated and k in pillars})
    ins = IN.generate(sctx)
    lt_dec = _long_term_decision(val, lt, fund, pillars, market, params)
    st_dec = DE.short_term(st, expv, params)
    lt_model, st_model = lt_dec, st_dec                        # 模型內部的等級:掃描、模擬帳戶、前瞻快照用(行為不變)
    if market == "US" and params.get("us_ratings") is False:
        # Q7:美股長期分數沒有證據(恢復門檻:重疊世代月 IC 的 HAC 95% 下界 > 0 且 holdout 同向)→ 網站只顯示資訊、不給評等
        why = "美股在模型證實有效之前不給等級(恢復條件:長期分數月 IC 的 95% 區間下界 > 0,且樣本外測試期方向一致)"
        lt_dec = {"rating": "不評等", "level": None, "method": "us_info_only", "reasons": [why],
                  "red_flags": lt_model.get("red_flags") or []}
        st_dec = {"signal": "不評等", "code": 0, "score": st.get("score"), "flags": st_model.get("flags") or []}
    sim_cap = DE.sim_position_cap(price, tech)                 # 只給模擬帳戶的規則用,不顯示
    conf = DE.confidence(pillars, val, status.items)
    for msg in AG.dec_findings(pillars, lt_dec, st_dec):
        emit("dec", "finding", msg)
    emit("dec", "log", "整理一段話摘要與研究檢查清單…")

    # ---------- 輸出 ----------
    prev_close = safe(info.get("regularMarketPreviousClose") or info.get("previousClose"))
    if prev_close is None and len(hist) > 1:
        prev_close = float(hist["Close"].iloc[-2])
    quote = {
        "price": price, "prev_close": prev_close, "change": price - prev_close if prev_close else None,
        "change_pct": price / prev_close - 1 if prev_close else None, "open": safe(info.get("open")),
        "day_high": safe(info.get("dayHigh")), "day_low": safe(info.get("dayLow")), "volume": safe(info.get("volume")),
        "avg_volume": safe(info.get("averageVolume")), "market_cap": market_cap, "shares": shares,
        "float_shares": safe(info.get("floatShares")), "high_52w": tech.get("high_52w"), "low_52w": tech.get("low_52w"),
        "beta": safe(info.get("beta")), "last_trade": hist.index[-1].strftime("%Y-%m-%d"),
    }
    meta = {
        "symbol": symbol, "code": code, "market": market, "name": name,
        "name_en": info.get("longName") or info.get("shortName"), "exchange": (tw_meta or {}).get("exchange") or info.get("fullExchangeName"),
        "currency": currency, "financial_currency": fin_ccy, "fx_to_quote": fx if adr_like else None,
        "sector": info.get("sector"), "industry": info.get("industry"), "industry_tw": (tw_meta or {}).get("industry"),
        "website": info.get("website") if str(info.get("website", "")).startswith("http") else None,
        "summary": info.get("longBusinessSummary"), "employees": info.get("fullTimeEmployees"),
        "country": info.get("country"), "city": info.get("city"),
        "officers": [{"name": o.get("name"), "title": o.get("title"), "age": o.get("age"), "pay": o.get("totalPay")}
                     for o in (info.get("companyOfficers") or [])[:8]],
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"), "model_version": params["version"],
        "elapsed_sec": None,
    }
    # 年報 / 季報表格(給前端)
    fin_cols = ["revenue", "gross_profit", "operating_income", "net_income", "eps_diluted", "ebitda", "cfo", "capex", "fcf",
                "total_assets", "total_liabilities", "equity", "cash_sti", "total_debt", "current_assets",
                "current_liabilities", "inventory", "receivables", "rnd", "sga", "dividends_paid", "buybacks", "shares_out", "da",
                "interest_expense", "tax", "pretax_income", "cash", "ppe", "goodwill_intangibles", "retained_earnings", "payables",
                "sbc", "operating_expenses"]
    financials = {
        "annual": FN.table_to_records(annual, [c for c in fin_cols if c in annual.columns]),
        "quarterly": FN.table_to_records(quarterly.tail(16), [c for c in fin_cols if c in quarterly.columns]),
        "long": FN.table_to_records(long, [c for c in fin_cols if c in long.columns]) if not long.empty else [],
        "ttm": t, "currency": fin_ccy, "fx_to_quote": fx if adr_like else None,
        "quarterly_source": q_source,
        "long_source": "FinMind 季資料加總" if market == "TW" else "SEC 10-K(XBRL)",
    }
    extra = {}
    if market == "US":
        cik = sec.cik_for(symbol)
        if cik:
            extra["filings"] = sec.recent_filings(cik)
            extra["cik"] = cik
    else:
        dv = fm.get("dividend")
        if dv is not None and not dv.empty:
            extra["dividends"] = [{"year": r.year, "cash": safe(r.CashEarningsDistribution) + (safe(r.CashStatutorySurplus) or 0)
                                   if safe(r.CashEarningsDistribution) is not None else None,
                                   "stock": safe(r.StockEarningsDistribution), "ex_date": getattr(r, "CashExDividendTradingDate", ""),
                                   "pay_date": getattr(r, "CashDividendPaymentDate", "")}
                                  for r in dv.tail(24).itertuples()][::-1]
        extra["tw_industry"] = tw_industry
        extra["announcements"] = ann
        extra["announcements_days"] = mops.days_collected()
    divs = yb.get("dividends")
    if divs is not None and len(divs):
        d = divs.copy()
        d.index = pd.to_datetime(d.index).tz_localize(None)
        extra["dividend_history"] = [{"date": i.strftime("%Y-%m-%d"), "amount": safe(v)} for i, v in d.tail(40).items()]

    positives = [i for i in ins if i["tone"] == "pos"][:4]
    negatives = [i for i in ins if i["tone"] == "neg"][:4]
    report = {
        "meta": meta, "quote": quote,
        "verdict": {"long_term": lt_dec, "short_term": st_dec, "fair_value": val.get("fair_value"),
                    "long_term_model": lt_model, "short_term_model": st_model, "st_score_full": st_full,
                    "unvalidated_pillars": unvalidated,
                    "fair_low": val.get("fair_low"), "fair_high": val.get("fair_high"), "upside": val.get("upside"),
                    "uncertainty": val.get("uncertainty"), "confidence": conf, "sim_max_position_pct": sim_cap,
                    "positives": positives, "negatives": negatives, "lt_score": lt, "st_score": st},
        "pillars": pillars, "valuation": {**val, "multiples": mult, "peers": peers},
        "fundamentals": {**fund, "month_revenue": mr}, "financials": financials, "technical": tech, "chips": ch,
        "expectations": expv, "news": {"items": news_items[:80], "summary": news_sum},
        "regime": {k: v for k, v in regime.items() if k != "benchmark_history"}, "insights": ins, "extra": extra,
        "data_status": status.items,
        # 模型客製化(M2):基礎變數快照;換模型時用它重算分數,不用重抓資料(lab/models/apply.py)
        "model_env": SC.base_vars(sctx),
    }
    meta["elapsed_sec"] = round(time.time() - t0, 1)
    out = clean(report)
    try:
        from .analysis import narrative
        out["narrative"] = narrative.build(out)
    except Exception as e:  # noqa: BLE001
        out["narrative"] = [f"(摘要產生失敗:{e})"]
    try:
        from .analysis import checklist
        out["checklist"] = checklist.build(out)
    except Exception as e:  # noqa: BLE001
        out["checklist"] = [{"group": "錯誤", "text": f"檢查清單產生失敗:{e}", "status": "warn", "detail": ""}]
    try:
        prev = snapshots.previous(symbol, dt.date.today().isoformat())
        if prev:
            out["since_last"] = snapshots.diff(prev, snapshots.make(out))
        snapshots.save(out)
    except Exception:  # noqa: BLE001
        pass
    try:
        out["notes"] = notes.load(symbol)
    except Exception:  # noqa: BLE001
        out["notes"] = None
    try:
        out["agents"] = AG.summary(out)
    except Exception:  # noqa: BLE001
        out["agents"] = []
    emit("dec", "done", f"完成:長期「{lt_dec.get('rating')}」、短期「{st_dec.get('signal')}」,共 {round(time.time() - t0, 1)} 秒")
    return out
