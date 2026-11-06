"""【凍結對照組,不得修改】M1(2026-11-02)前的 lab/analysis/scoring.py 原樣,用來驗證模型引擎逐位元相同。

八大面向評分(0–100)與長短期綜合分數。

每個指標用分段線性函數 lin(x, 差, 好) 轉成 0–100 分;有同業資料時,再跟同業百分位混合(絕對 60% + 相對 40%)。
門檻是 v1 假設(依文獻與常見區間設定),之後用復盤資料校正。
"""
from __future__ import annotations

import numpy as np

from lab.analysis.financials import safe

GRADES = [(90, "A+"), (80, "A"), (70, "B+"), (60, "B"), (50, "C+"), (40, "C"), (30, "D"), (0, "F")]

PILLAR_LABEL = {"quality": "品質", "growth": "成長", "valuation": "估值", "health": "財務安全",
                "momentum": "技術動能", "flows": "籌碼", "revisions": "預期修正", "sentiment": "消息情緒",
                "regime": "大盤環境"}


def grade(score: float | None) -> str:
    if score is None:
        return "—"
    for th, g in GRADES:
        if score >= th:
            return g
    return "F"


def lin(x, bad, good) -> float | None:
    x = safe(x)
    if x is None:
        return None
    if good == bad:
        return 50.0
    t = (x - bad) / (good - bad)
    return float(min(100.0, max(0.0, t * 100)))


class Pillar:
    def __init__(self, key: str):
        self.key = key
        self.items: list[dict] = []

    def add(self, key, label, value, score, weight, fmt="pct", note="", peer_pct=None, higher_better=True):
        if score is None:
            self.items.append({"key": key, "label": label, "value": safe(value), "score": None, "weight": weight,
                               "fmt": fmt, "note": note, "available": False})
            return
        final = score
        if peer_pct is not None:
            rel = peer_pct * 100 if higher_better else (1 - peer_pct) * 100
            final = 0.6 * score + 0.4 * rel
        self.items.append({"key": key, "label": label, "value": safe(value), "score": round(final, 1),
                           "abs_score": round(score, 1), "peer_pct": safe(peer_pct), "weight": weight, "fmt": fmt,
                           "note": note, "available": True})

    def result(self) -> dict:
        av = [i for i in self.items if i["available"]]
        tot = sum(i["weight"] for i in self.items)
        if not av:
            return {"key": self.key, "label": PILLAR_LABEL[self.key], "score": None, "grade": "—", "coverage": 0,
                    "items": self.items}
        w = sum(i["weight"] for i in av)
        s = sum(i["score"] * i["weight"] for i in av) / w
        return {"key": self.key, "label": PILLAR_LABEL[self.key], "score": round(s, 1), "grade": grade(s),
                "coverage": round(w / tot, 2) if tot else 0, "items": self.items}


def peer_percentile(value, peer_values: list) -> float | None:
    v = safe(value)
    xs = [safe(x) for x in peer_values if safe(x) is not None]
    if v is None or len(xs) < 4:
        return None
    return float(np.mean([x < v for x in xs]) + 0.5 * np.mean([x == v for x in xs]))


def build(ctx: dict) -> dict:
    f = ctx["fund"]
    prof, grw, hl = f["profitability"], f["growth"], f["health"]
    stab = f.get("stability", {})
    val = ctx["valuation"]
    tech = ctx["technical"]
    chips = ctx["chips"]
    exp = ctx["expectations"]
    news = ctx["news_summary"]
    regime = ctx["regime"]
    peers = ctx.get("peers") or []
    is_fin = val.get("company_type") == "financial"
    pv = lambda k: [p.get(k) for p in peers]  # noqa: E731

    # ---------- 品質 ----------
    q = Pillar("quality")
    if not is_fin:
        q.add("roic", "ROIC 投入資本報酬率", prof.get("roic"), lin(prof.get("roic"), 0.0, 0.25), 3,
              note="NOPAT ÷ (股東權益 + 負債 − 現金)。持續 > 15% 通常代表有護城河")
        q.add("roe", "ROE 股東權益報酬率", prof.get("roe"), lin(prof.get("roe"), 0.0, 0.25), 2,
              peer_pct=peer_percentile(prof.get("roe"), pv("roe")))
        q.add("gp_assets", "毛利 ÷ 總資產", prof.get("gp_assets"), lin(prof.get("gp_assets"), 0.05, 0.45), 2,
              note="Novy-Marx (2013):預測力與淨值市價比相當")
        q.add("gross_margin", "毛利率", prof.get("gross_margin"), lin(prof.get("gross_margin"), 0.15, 0.6), 1.5,
              peer_pct=peer_percentile(prof.get("gross_margin"), pv("gross_margin")))
        q.add("operating_margin", "營業利益率", prof.get("operating_margin"), lin(prof.get("operating_margin"), 0.0, 0.3), 1.5,
              peer_pct=peer_percentile(prof.get("operating_margin"), pv("operating_margin")))
        q.add("gm_std_10y", "毛利率穩定度(10 年標準差)", stab.get("gm_std_10y"), lin(stab.get("gm_std_10y"), 0.08, 0.01), 1,
              note="越低越穩定;QMJ 的 safety 元素")
        q.add("cfo_to_ni", "營業現金流 ÷ 淨利", prof.get("cfo_to_ni"), lin(prof.get("cfo_to_ni"), 0.6, 1.2), 1.5, fmt="x",
              note="< 0.8 代表獲利沒有變成現金,要注意")
        q.add("accruals", "應計比率", prof.get("accruals"), lin(prof.get("accruals"), 0.10, -0.05), 1,
              note="Sloan (1996):應計高的公司後續報酬較差")
        q.add("loss_years", "近 10 年虧損年數", stab.get("loss_years_10y"), lin(stab.get("loss_years_10y"), 3, 0), 1, fmt="int")
    else:
        q.add("roe", "ROE 股東權益報酬率", prof.get("roe"), lin(prof.get("roe"), 0.04, 0.15), 3)
        q.add("roa", "ROA 資產報酬率", prof.get("roa"), lin(prof.get("roa"), 0.003, 0.015), 2)
        q.add("loss_years", "近 10 年虧損年數", stab.get("loss_years_10y"), lin(stab.get("loss_years_10y"), 3, 0), 1, fmt="int")

    # ---------- 成長 ----------
    g = Pillar("growth")
    g.add("revenue_yoy", f"營收年增率({grw.get('yoy_basis', '')})", grw.get("revenue_yoy"), lin(grw.get("revenue_yoy"), -0.05, 0.25), 2,
          peer_pct=peer_percentile(grw.get("revenue_yoy"), pv("revenue_growth")))
    g.add("revenue_cagr_3y", "營收 3 年 CAGR", grw.get("revenue_cagr_3y"), lin(grw.get("revenue_cagr_3y"), -0.02, 0.2), 2)
    g.add("eps_yoy", "EPS 年增率", grw.get("eps_yoy"), lin(grw.get("eps_yoy"), -0.1, 0.3), 1.5)
    g.add("eps_cagr_3y", "EPS 3 年 CAGR", grw.get("eps_cagr_3y") or grw.get("ni_cagr_3y"),
          lin(grw.get("eps_cagr_3y") or grw.get("ni_cagr_3y"), -0.05, 0.25), 1.5)
    fr = exp.get("fwd_rev_growth_fy1") if exp.get("fwd_rev_growth_fy1") is not None else exp.get("fwd_rev_growth_fy0")
    fe = exp.get("fwd_eps_growth_fy1") if exp.get("fwd_eps_growth_fy1") is not None else exp.get("fwd_eps_growth_fy0")
    g.add("fwd_rev", "分析師預估營收成長", fr, lin(fr, 0.0, 0.2), 1.5)
    g.add("fwd_eps", "分析師預估 EPS 成長", fe, lin(fe, 0.0, 0.25), 1.5)
    g.add("q_gm_yoy", "單季毛利率年變化", grw.get("q_gm_change_yoy"), lin(grw.get("q_gm_change_yoy"), -0.03, 0.03), 1, fmt="pp")
    mr = ctx.get("month_revenue")
    if mr:
        g.add("mr_yoy3", "月營收近 3 月年增", mr.get("yoy_3m"), lin(mr.get("yoy_3m"), -0.05, 0.25), 2,
              note="台灣實證:營收動能 1–12 個月有正報酬")
        rh = 90 if mr.get("record_high") else 72 if mr.get("record_same_month") else 45
        g.add("mr_record", "月營收創新高", 1 if mr.get("record_high") else 0, rh, 1, fmt="bool",
              note="營收創歷史新高動能(台灣實證有效)")
        g.add("mr_sur", "未預期營收(SUR)", mr.get("sur"), lin(mr.get("sur"), -1.5, 1.5), 1, fmt="num")

    # ---------- 估值 ----------
    v = Pillar("valuation")
    v.add("fv_upside", "相對公平價空間", val.get("upside"), lin(val.get("upside"), -0.3, 0.4), 3,
          note="綜合公平價 ÷ 現價 − 1")
    pb = val.get("pe_band") or {}
    v.add("pe_rank", "本益比位於自身 5 年分位", pb.get("current_pct_rank"), lin(pb.get("current_pct_rank"), 0.9, 0.1), 2,
          note="越低代表比自己過去便宜")
    mult = ctx.get("multiples", {})
    fpe = mult.get("forward_pe")
    peer_fpe = [x for x in pv("forward_pe") if x and 0 < x < 200]
    if fpe and peer_fpe and len(peer_fpe) >= 3:
        rel = fpe / float(np.median(peer_fpe))
        v.add("fpe_vs_peers", "預估本益比 ÷ 同業中位數", rel, lin(rel, 1.5, 0.7), 1.5, fmt="x")
    else:
        v.add("fpe_vs_peers", "預估本益比 ÷ 同業中位數", None, None, 1.5, fmt="x")
    v.add("fcf_yield", "自由現金流殖利率", mult.get("fcf_yield"), lin(mult.get("fcf_yield"), 0.0, 0.07), 1.5)
    if not is_fin:
        v.add("ev_ebitda", "EV/EBITDA", mult.get("ev_ebitda"), lin(mult.get("ev_ebitda"), 25, 6), 1, fmt="x",
              peer_pct=peer_percentile(mult.get("ev_ebitda"), pv("ev_ebitda")), higher_better=False)
    v.add("peg", "PEG(預估本益比 ÷ 成長)", mult.get("peg"), lin(mult.get("peg"), 3, 0.8), 1, fmt="x")
    if is_fin:
        v.add("pb", "股價淨值比", mult.get("pb"), lin(mult.get("pb"), 2.5, 0.8), 1.5, fmt="x")

    # ---------- 財務安全 ----------
    h = Pillar("health")
    if not is_fin:
        h.add("altman_z", "Altman Z-score", hl.get("altman_z"), lin(hl.get("altman_z"), 1.8, 3.0), 2, fmt="num",
              note="> 2.99 安全、1.81–2.99 灰色、< 1.81 危險")
    pf = f.get("piotroski") or {}
    if not is_fin:  # 銀行保險的營業現金流、流動比沒有意義,Piotroski 不適用
        h.add("piotroski", "Piotroski F-score", pf.get("score"), lin(pf.get("score"), 2, 8), 2, fmt="int",
              note="9 項財務改善檢查;≥ 7 分強、≤ 3 分弱")
    if not is_fin:
        nde = hl.get("net_debt_ebitda")
        if hl.get("net_debt") is not None and hl["net_debt"] < 0:
            h.add("net_debt_ebitda", "淨負債 ÷ EBITDA", nde, 100.0, 1.5, fmt="x", note="淨現金部位")
        else:
            h.add("net_debt_ebitda", "淨負債 ÷ EBITDA", nde, lin(nde, 4, 0), 1.5, fmt="x")
        ic = hl.get("interest_coverage")
        h.add("interest_coverage", "利息保障倍數", ic, lin(ic, 2, 15), 1.5, fmt="x")
        h.add("current_ratio", "流動比率", hl.get("current_ratio"), lin(hl.get("current_ratio"), 0.8, 2.0), 1, fmt="x")
        h.add("de", "負債 ÷ 股東權益", hl.get("debt_to_equity"), lin(hl.get("debt_to_equity"), 2.0, 0.2), 1, fmt="x")
        bm = f.get("beneish")
        if bm:
            sc = 80 if not bm["flag"] else (45 if bm.get("high_growth_caveat") else 20)
            h.add("beneish", "Beneish M-score", bm["m"], sc, 1, fmt="num",
                  note="> −1.78 代表財報操縱風險偏高;高成長公司容易誤判")
    else:
        la = hl.get("liabilities_to_assets")
        h.add("equity_assets", "股東權益 ÷ 總資產(資本厚度)", (1 - la) if la is not None else None,
              lin((1 - la) if la is not None else None, 0.04, 0.12), 2, note="金融業的槓桿天生高;權益占資產 > 10% 算厚實")
        h.add("loss_years_fin", "近 10 年虧損年數", stab.get("loss_years_10y"), lin(stab.get("loss_years_10y"), 2, 0), 1, fmt="int")

    # ---------- 技術動能 ----------
    m = Pillar("momentum")
    if tech.get("available"):
        m.add("mom_12_1", "12-1 個月動能", tech.get("mom_12_1"), lin(tech.get("mom_12_1"), -0.2, 0.4), 2,
              note="過去 12 個月扣掉最近 1 個月的報酬(Jegadeesh & Titman)")
        m.add("from_high", "距 52 週高點", tech.get("pct_from_52w_high"), lin(tech.get("pct_from_52w_high"), -0.35, 0), 1.5,
              note="George & Hwang (2004)")
        m.add("vs_ma200", "股價相對 200 日線", tech["price_vs_ma"].get("ma200"), lin(tech["price_vs_ma"].get("ma200"), -0.15, 0.15), 1.5)
        m.add("ma200_slope", "200 日線斜率(20 日)", tech.get("ma200_slope_20d"), lin(tech.get("ma200_slope_20d"), -0.03, 0.03), 1)
        rel6 = (tech.get("relative") or {}).get("6m")
        m.add("rel_6m", "6 個月相對大盤", rel6, lin(rel6, -0.2, 0.2), 1.5)
        rv = tech.get("rsi14")
        m.add("rsi", "RSI(14)", rv, None if rv is None else max(0.0, 100 - abs(rv - 58) * 2.5), 0.5, fmt="num",
              note="50–65 最健康;過高過熱、過低弱勢")
        tc = tech.get("trend", {}).get("code")
        m.add("trend", "趨勢狀態", tc, {-2: 10, -1: 30, 0: 50, 1: 75, 2: 90}.get(tc), 1.5, fmt="trend")

    # ---------- 籌碼 ----------
    fl = Pillar("flows")
    if chips.get("market") == "TW":
        inst = chips.get("institutional") or {}
        fi, it = inst.get("外資", {}), inst.get("投信", {})
        fl.add("foreign_20d", "外資 20 日買賣超(占股本)", fi.get("d20_pct"), lin(fi.get("d20_pct"), -0.003, 0.003), 2, fmt="pct2")
        fl.add("trust_20d", "投信 20 日買賣超(占股本)", it.get("d20_pct"), lin(it.get("d20_pct"), -0.002, 0.002), 1.5, fmt="pct2")
        fl.add("foreign_streak", "外資連續買賣超天數", fi.get("streak"), lin(fi.get("streak"), -5, 5), 0.5, fmt="int")
        fh = chips.get("foreign_holding") or {}
        fl.add("foreign_hold_60d", "外資持股比 60 日變化", fh.get("chg_60d"), lin(fh.get("chg_60d"), -2, 2), 1.5, fmt="pp_raw")
        mg = chips.get("margin") or {}
        # 融資餘額占股本很小時(大型權值股常見),百分比變化雜訊大,降低權重
        mw = 1.0 if (mg.get("margin_to_shares") or 0) >= 0.005 else 0.3
        fl.add("margin_20d", "融資餘額 20 日變化", mg.get("margin_chg_20d"), lin(mg.get("margin_chg_20d"), 0.15, -0.10), mw,
               note="融資大增通常代表散戶追價(反向指標);融資占股本 < 0.5% 時權重降低")
        td = chips.get("tdcc") or {}
        fl.add("big1000_chg", "千張大戶持股比週變化", td.get("big1000_chg"), lin(td.get("big1000_chg"), -0.5, 0.5), 1, fmt="pp_raw")
        fl.add("holders_chg", "股東人數週變化", td.get("holders_chg"), lin(td.get("holders_chg"), 0.03, -0.03), 0.5,
               note="散戶人數減少 = 籌碼集中")
    else:
        ins = chips.get("insiders") or {}
        if ins:
            bc, sv = ins.get("buy_count_6m", 0), ins.get("sell_value_6m") or 0
            mcap = ctx.get("market_cap") or 0
            sc = 88 if bc >= 3 else 75 if bc >= 1 else (40 if mcap and sv > 0.003 * mcap else 52)
            fl.add("insider", "內部人 6 個月買進", bc, sc, 2, fmt="int",
                   note="Cohen, Malloy & Pomorski:投機性買進才有資訊量;例行性賣出多半是稅務/分散")
        sh = chips.get("short") or {}
        fl.add("short_float", "放空占流通股比", sh.get("pct_float"), lin(sh.get("pct_float"), 0.15, 0.01), 2,
               note="放空高的股票後續報酬偏弱(但有軋空風險)")
        fl.add("short_chg", "放空股數月變化", sh.get("chg_vs_prior"), lin(sh.get("chg_vs_prior"), 0.2, -0.2), 1)
        fl.add("inst_adding", "前十大機構加碼比例", chips.get("inst_net_adding"), lin(chips.get("inst_net_adding"), 0.3, 0.7), 1)

    # ---------- 預期修正 ----------
    r = Pillar("revisions")
    et = exp.get("eps_trend") or {}
    r.add("eps0y_90d", "今年 EPS 預估 90 日修正", (et.get("0y") or {}).get("chg_90d"), lin((et.get("0y") or {}).get("chg_90d"), -0.05, 0.05), 2)
    r.add("eps1y_90d", "明年 EPS 預估 90 日修正", (et.get("+1y") or {}).get("chg_90d"), lin((et.get("+1y") or {}).get("chg_90d"), -0.05, 0.05), 1.5)
    r.add("eps0y_30d", "今年 EPS 預估 30 日修正", (et.get("0y") or {}).get("chg_30d"), lin((et.get("0y") or {}).get("chg_30d"), -0.02, 0.02), 1)
    br = (exp.get("eps_revisions") or {}).get("0y", {}).get("breadth")
    r.add("breadth", "上修家數 − 下修家數(占比)", br, lin(br, -0.5, 0.5), 1.5)
    r.add("surprise", "近 4 季平均財報驚喜", exp.get("surprise_avg_4q"), lin(exp.get("surprise_avg_4q"), -0.05, 0.08), 1.5)
    r.add("beats", "近 4 季打敗預期次數", exp.get("beats_4q"), lin(exp.get("beats_4q"), 1, 4), 0.5, fmt="int")
    r.add("rec_chg", "評等分數 3 個月變化", exp.get("rec_score_chg_3m"), lin(exp.get("rec_score_chg_3m"), -0.3, 0.3), 0.5, fmt="num")
    rc = exp.get("rating_changes_90d")
    if rc:
        net = rc["up"] - rc["down"] + 0.5 * (rc["pt_raises"] - rc["pt_lowers"])
        r.add("rating_net", "90 日升降評與目標價調整(淨值)", net, lin(net, -3, 3), 1, fmt="num")

    # ---------- 消息 ----------
    s = Pillar("sentiment")
    d7, d30 = news.get("d7", {}), news.get("d30", {})
    s.add("news7", "近 7 日新聞情緒", d7.get("avg"), lin(d7.get("avg"), -0.4, 0.4) if (d7.get("n") or 0) >= 3 else None, 2, fmt="num")
    s.add("news30", "近 30 日新聞情緒", d30.get("avg"), lin(d30.get("avg"), -0.3, 0.3) if (d30.get("n") or 0) >= 5 else None, 1, fmt="num")

    pillars = {p.key: p.result() for p in (q, g, v, h, m, fl, r, s)}
    pillars["regime"] = {"key": "regime", "label": PILLAR_LABEL["regime"], "score": regime.get("score"),
                         "grade": grade(regime.get("score")), "coverage": 1, "items": []}
    return pillars


def short_term_weights(params: dict, market: str) -> dict:
    """短期分數的面向權重。P3-H01(參數 tw_st_drop_momentum,預設關閉):台股拿掉技術動能;
    composite 依可用面向的權重加權平均,所以其他面向等於依原比例放大。"""
    pw = params["pillar_weights"]
    w = dict(pw["short_term_tw"] if market == "TW" else pw["short_term_us"])
    if market == "TW" and params.get("tw_st_drop_momentum"):
        w.pop("momentum", None)
    return w


def composite(pillars: dict, weights: dict) -> dict:
    used, tot, s = [], 0.0, 0.0
    for k, w in weights.items():
        sc = (pillars.get(k) or {}).get("score")
        if sc is None:
            continue
        s += sc * w
        tot += w
        used.append(k)
    if tot == 0:
        return {"score": None, "grade": "—", "used": [], "coverage": 0}
    val = s / tot
    return {"score": round(val, 1), "grade": grade(val), "used": used, "coverage": round(tot / sum(weights.values()), 2),
            "weights": weights}
