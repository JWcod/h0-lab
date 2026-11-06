"""八大面向評分(0–100)與長短期綜合分數。

指標定義(取值、門檻、權重)在模型檔 lab/models/*.json,打分由 lab/models/engine.py 執行;這裡負責把 ctx 攤平成基礎變數。
每個指標用分段線性函數 lin(x, 差, 好) 轉成 0–100 分;有同業資料時,再跟同業百分位混合(絕對 60% + 相對 40%)。
門檻是 v1 假設(依文獻與常見區間設定),之後用復盤資料校正。
"""
from __future__ import annotations

import numpy as np

from .financials import safe

GRADES = [(90, "A+"), (80, "A"), (70, "B+"), (60, "B"), (50, "C+"), (40, "C"), (30, "D"), (0, "F")]

DEFAULT_MODEL = "jw-mf-2.0"   # 出廠模型(lab/models/jw-mf-2.0.json)

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

    def add(self, key, label, value, score, weight, fmt="pct", note="", peer_pct=None, higher_better=True, abs_weight=0.6):
        if score is None:
            self.items.append({"key": key, "label": label, "value": safe(value), "score": None, "weight": weight,
                               "fmt": fmt, "note": note, "available": False})
            return
        final = score
        if peer_pct is not None:
            rel = peer_pct * 100 if higher_better else (1 - peer_pct) * 100
            final = abs_weight * score + (1 - abs_weight) * rel
        self.items.append({"key": key, "label": label, "value": safe(value), "score": round(final, 1),
                           "abs_score": round(score, 1), "peer_pct": safe(peer_pct), "weight": weight, "fmt": fmt,
                           "note": note, "available": True})

    def result(self) -> dict:
        av = [i for i in self.items if i["available"]]
        tot = sum(i["weight"] for i in self.items)
        w = sum(i["weight"] for i in av)
        if not av or w == 0:   # 可用項目的權重全是 0(使用者把權重調成 0)也視為不可得
            return {"key": self.key, "label": PILLAR_LABEL[self.key], "score": None, "grade": "—", "coverage": 0,
                    "items": self.items}
        s = sum(i["score"] * i["weight"] for i in av) / w
        return {"key": self.key, "label": PILLAR_LABEL[self.key], "score": round(s, 1), "grade": grade(s),
                "coverage": round(w / tot, 2) if tot else 0, "items": self.items}


def peer_percentile(value, peer_values: list) -> float | None:
    v = safe(value)
    xs = [safe(x) for x in peer_values if safe(x) is not None]
    if v is None or len(xs) < 4:
        return None
    return float(np.mean([x < v for x in xs]) + 0.5 * np.mean([x == v for x in xs]))


def base_vars(ctx: dict) -> dict:
    """把報告 / 回測的 ctx 攤平成模型用的基礎變數(原始值,不打分)。

    取值寫法刻意和 M1 前的 build() 一模一樣(例如 eps_cagr 用 `or`、fwd 用 `is not None`),出廠模型才能逐位元相同。
    變數清單即運算式語言可用的名稱(規格 §3.4)。
    """
    f = ctx["fund"]
    prof, grw, hl = f["profitability"], f["growth"], f["health"]
    stab = f.get("stability", {})
    val = ctx["valuation"]
    tech = ctx["technical"]
    chips = ctx["chips"]
    exp = ctx["expectations"]
    news = ctx["news_summary"]
    peers = ctx.get("peers") or []
    pv = lambda k: [p.get(k) for p in peers]  # noqa: E731
    e: dict = {"is_financial": val.get("company_type") == "financial", "market_cap": ctx.get("market_cap"),
               "regime": (ctx["regime"] or {}).get("score")}
    # 品質
    for k in ("roic", "roe", "gp_assets", "gross_margin", "operating_margin", "cfo_to_ni", "accruals", "roa"):
        e[k] = prof.get(k)
    e["gm_std_10y"] = stab.get("gm_std_10y")
    e["loss_years_10y"] = stab.get("loss_years_10y")
    e["peer_pct_roe"] = peer_percentile(prof.get("roe"), pv("roe"))
    e["peer_pct_gross_margin"] = peer_percentile(prof.get("gross_margin"), pv("gross_margin"))
    e["peer_pct_operating_margin"] = peer_percentile(prof.get("operating_margin"), pv("operating_margin"))
    # 成長
    e["yoy_basis"] = grw.get("yoy_basis", "")
    for k in ("revenue_yoy", "revenue_cagr_3y", "eps_yoy", "eps_cagr_3y", "ni_cagr_3y"):
        e[k] = grw.get(k)
    e["eps_cagr_eff"] = grw.get("eps_cagr_3y") or grw.get("ni_cagr_3y")
    e["peer_pct_revenue_yoy"] = peer_percentile(grw.get("revenue_yoy"), pv("revenue_growth"))
    e["fwd_rev"] = exp.get("fwd_rev_growth_fy1") if exp.get("fwd_rev_growth_fy1") is not None else exp.get("fwd_rev_growth_fy0")
    e["fwd_eps"] = exp.get("fwd_eps_growth_fy1") if exp.get("fwd_eps_growth_fy1") is not None else exp.get("fwd_eps_growth_fy0")
    e["q_gm_change_yoy"] = grw.get("q_gm_change_yoy")
    mr = ctx.get("month_revenue")
    e["mr_present"] = bool(mr)
    mr = mr or {}
    e["mr_yoy_3m"], e["mr_sur"] = mr.get("yoy_3m"), mr.get("sur")
    e["mr_record_high"], e["mr_record_same_month"] = mr.get("record_high"), mr.get("record_same_month")
    e["mr_record"] = 1 if mr.get("record_high") else 0
    # 估值
    e["upside"] = val.get("upside")
    e["pe_rank"] = (val.get("pe_band") or {}).get("current_pct_rank")
    mult = ctx.get("multiples", {})
    fpe = mult.get("forward_pe")
    peer_fpe = [x for x in pv("forward_pe") if x and 0 < x < 200]
    e["forward_pe"] = fpe
    e["fpe_rel_peers"] = fpe / float(np.median(peer_fpe)) if fpe and peer_fpe and len(peer_fpe) >= 3 else None
    for k in ("fcf_yield", "ev_ebitda", "peg", "pb"):
        e[k] = mult.get(k)
    e["peer_pct_ev_ebitda"] = peer_percentile(mult.get("ev_ebitda"), pv("ev_ebitda"))
    # 財務安全
    for k in ("altman_z", "net_debt_ebitda", "net_debt", "interest_coverage", "current_ratio", "debt_to_equity"):
        e[k] = hl.get(k)
    e["piotroski"] = (f.get("piotroski") or {}).get("score")
    bm = f.get("beneish")
    e["beneish_present"] = bool(bm)
    e["beneish_m"] = bm.get("m") if bm else None
    e["beneish_flag"] = bm.get("flag") if bm else None   # 缺 flag 鍵:M1 前會丟 KeyError,現在視為不可得(engine)
    e["beneish_high_growth_caveat"] = bm.get("high_growth_caveat") if bm else None
    la = hl.get("liabilities_to_assets")
    e["equity_assets"] = (1 - la) if la is not None else None
    # 技術動能(只有 available 時才有 price_vs_ma 等欄位)
    e["tech_available"] = bool(tech.get("available"))
    if e["tech_available"]:
        e["mom_12_1"] = tech.get("mom_12_1")
        e["pct_from_52w_high"] = tech.get("pct_from_52w_high")
        e["vs_ma200"] = tech["price_vs_ma"].get("ma200")
        e["ma200_slope_20d"] = tech.get("ma200_slope_20d")
        e["rel_6m"] = (tech.get("relative") or {}).get("6m")
        e["rsi14"] = tech.get("rsi14")
        e["trend_code"] = tech.get("trend", {}).get("code")
    # 籌碼
    e["chips_tw"] = chips.get("market") == "TW"
    if e["chips_tw"]:
        inst = chips.get("institutional") or {}
        fi, it = inst.get("外資", {}), inst.get("投信", {})
        e["foreign_d20_pct"], e["trust_d20_pct"], e["foreign_streak"] = fi.get("d20_pct"), it.get("d20_pct"), fi.get("streak")
        e["foreign_hold_chg_60d"] = (chips.get("foreign_holding") or {}).get("chg_60d")
        mg = chips.get("margin") or {}
        e["margin_chg_20d"], e["margin_to_shares"] = mg.get("margin_chg_20d"), mg.get("margin_to_shares")
        td = chips.get("tdcc") or {}
        e["big1000_chg"], e["holders_chg"] = td.get("big1000_chg"), td.get("holders_chg")
    else:
        ins = chips.get("insiders") or {}
        e["insider_present"] = bool(ins)
        e["insider_buy_count_6m"] = ins.get("buy_count_6m", 0) if ins else None
        e["insider_sell_value_6m"] = ins.get("sell_value_6m") if ins else None
        sh = chips.get("short") or {}
        e["short_pct_float"], e["short_chg_vs_prior"] = sh.get("pct_float"), sh.get("chg_vs_prior")
        e["inst_net_adding"] = chips.get("inst_net_adding")
    # 預期修正
    et = exp.get("eps_trend") or {}
    e["eps0y_chg_90d"] = (et.get("0y") or {}).get("chg_90d")
    e["eps1y_chg_90d"] = (et.get("+1y") or {}).get("chg_90d")
    e["eps0y_chg_30d"] = (et.get("0y") or {}).get("chg_30d")
    e["rev_breadth"] = (exp.get("eps_revisions") or {}).get("0y", {}).get("breadth")
    e["surprise_avg_4q"], e["beats_4q"] = exp.get("surprise_avg_4q"), exp.get("beats_4q")
    e["rec_score_chg_3m"] = exp.get("rec_score_chg_3m")
    rc = exp.get("rating_changes_90d")
    e["rating_changes_present"] = bool(rc)
    e["rating_net"] = rc["up"] - rc["down"] + 0.5 * (rc["pt_raises"] - rc["pt_lowers"]) if rc else None
    # 消息情緒
    d7, d30 = news.get("d7", {}), news.get("d30", {})
    e["news_d7_avg"], e["news_d7_n"] = d7.get("avg"), d7.get("n")
    e["news_d30_avg"], e["news_d30_n"] = d30.get("avg"), d30.get("n")
    # 股利與風險(上手精靈 §6;lab/analysis/income_risk.py 算好放在 ctx["income_risk"],出廠模型不使用)
    ir = ctx.get("income_risk") or {}
    for k in ("dividend_yield_ttm", "div_paid_years_5y", "vol_1y", "beta_2y"):
        e[k] = ir.get(k)
    return e


# base_vars 可能產生的全部變數(有些只在特定分支出現);模型的 value.var、gate、requires、label 只能用這些
BASE_VARS = (
    "is_financial", "market_cap", "regime", "roic", "roe", "gp_assets", "gross_margin", "operating_margin",
    "cfo_to_ni", "accruals", "roa", "gm_std_10y", "loss_years_10y", "peer_pct_roe", "peer_pct_gross_margin",
    "peer_pct_operating_margin", "yoy_basis", "revenue_yoy", "revenue_cagr_3y", "eps_yoy", "eps_cagr_3y",
    "ni_cagr_3y", "eps_cagr_eff", "peer_pct_revenue_yoy", "fwd_rev", "fwd_eps", "q_gm_change_yoy", "mr_present",
    "mr_yoy_3m", "mr_sur", "mr_record_high", "mr_record_same_month", "mr_record", "upside", "pe_rank", "forward_pe",
    "fpe_rel_peers", "fcf_yield", "ev_ebitda", "peg", "pb", "peer_pct_ev_ebitda", "altman_z", "net_debt_ebitda",
    "net_debt", "interest_coverage", "current_ratio", "debt_to_equity", "piotroski", "beneish_present", "beneish_m",
    "beneish_flag", "beneish_high_growth_caveat", "equity_assets", "tech_available", "mom_12_1",
    "pct_from_52w_high", "vs_ma200", "ma200_slope_20d", "rel_6m", "rsi14", "trend_code", "chips_tw",
    "foreign_d20_pct", "trust_d20_pct", "foreign_streak", "foreign_hold_chg_60d", "margin_chg_20d",
    "margin_to_shares", "big1000_chg", "holders_chg", "insider_present", "insider_buy_count_6m",
    "insider_sell_value_6m", "short_pct_float", "short_chg_vs_prior", "inst_net_adding", "eps0y_chg_90d",
    "eps1y_chg_90d", "eps0y_chg_30d", "rev_breadth", "surprise_avg_4q", "beats_4q", "rec_score_chg_3m",
    "rating_changes_present", "rating_net", "news_d7_avg", "news_d7_n", "news_d30_avg", "news_d30_n",
    "dividend_yield_ttm", "div_paid_years_5y", "vol_1y", "beta_2y",
)


def build(ctx: dict, model: dict | None = None) -> dict:
    """八大面向 + 大盤環境。model 不給就用出廠模型(lab/models/<default_model>.json)。"""
    from ..models import engine
    from ..models.schema import load_shipped
    if model is None:
        model = load_shipped(DEFAULT_MODEL, readonly=True)
    pillars = engine.score_pillars(model, base_vars(ctx))
    regime = ctx["regime"]
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
