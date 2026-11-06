"""模型等級:長期模型等級(估值區間 × 品質關卡,或 P2-H10 固定分位)、短期分數等級、資料完整度(原「信心度」)。

研究平台定位:只輸出模型等級與統計描述,不輸出買賣建議、進出場價位或部位建議。level 的數值 0–4 和回測相同。
"""
from __future__ import annotations

import json
import math
import re

from .. import config
from .financials import safe

LT_LEVELS = ["模型等級 E", "模型等級 D", "模型等級 C", "模型等級 B", "模型等級 A"]   # 索引 = level,數值不變
# 舊快照、舊掃描、舊模擬帳戶紀錄裡的字眼 → 現在的顯示名稱(資料檔不改,讀出來時換)
LEGACY_LABELS = {"強力買進": "模型等級 A", "買進": "模型等級 B", "持有": "模型等級 C", "減碼": "模型等級 D", "賣出": "模型等級 E",
                 "偏多": "分數偏高", "偏空": "分數偏低"}
_LEGACY_RE = re.compile("|".join(sorted(map(re.escape, LEGACY_LABELS), key=len, reverse=True)))


def neutral_label(x):
    """舊紀錄的等級字串換成現在的名稱(完全相符才換);其他值原樣回傳。"""
    return LEGACY_LABELS.get(x, x) if isinstance(x, str) else x


def neutral_text(x):
    """舊紀錄的句子(例如模擬帳戶的理由)裡的等級字眼換成現在的名稱。"""
    return _LEGACY_RE.sub(lambda m: LEGACY_LABELS[m.group(0)], x) if isinstance(x, str) else x


CUTPOINTS_FILE = "lt_cutpoints.json"   # P2-H10:回測寫出、網站讀取的股票池長期分數分位切點
POOL_LABEL = {"tw_pit50": "台股 PIT 前 50", "tw_large40": "台股大型 40", "us_large40": "美股大型 40"}


def long_term(val: dict, lt: dict, fund: dict, pillars: dict | None = None, market: str | None = None,
              rules: dict | None = None) -> dict:
    """rules 來自 model_params.json 的 decision_rules(v1.1 依 5 年歷史回測加入,見白皮書 4.1.1)。"""
    rules = rules or {}
    price, fv = val.get("price"), val.get("fair_value")
    reasons = []
    if not fv or not price:
        return {"rating": "不評等(估值不可得)", "level": None, "reasons": [val.get("no_fv_reason") or "公平價資料不足"]}
    z = val["zones"]
    if price <= z["well_below"]:
        lvl = 4
    elif price <= z["below"]:
        lvl = 3
    elif price < z["above"]:
        lvl = 2
    elif price < z["well_above"]:
        lvl = 1
    else:
        lvl = 0
    reasons.append(f"現價相對公平價 {val['upside']:+.1%},不確定性「{val['uncertainty']}」要求安全邊際 {val['margin_of_safety']:.0%}")
    base_lvl = lvl
    lts = lt.get("score")
    if lts is not None and lts < 40 and lvl > 0:
        lvl -= 1
        reasons.append(f"長期體質分數偏低({lts:.0f}),等級下調一級")
    if lts is not None and lts >= 75 and lvl == 2 and val["upside"] > 0.05:
        lvl += 1
        reasons.append(f"長期體質分數較高({lts:.0f}),規則上使用較小的安全邊際,等級由 C 上調為 B")
    red_flags = _red_flags(val, fund)
    if red_flags and lvl > 2:
        lvl = 2
        reasons.append("有財務紅旗,等級上限設為 C")
    q = ((pillars or {}).get("quality") or {}).get("score")
    mo = ((pillars or {}).get("momentum") or {}).get("score")
    if lvl <= 1 and not red_flags and rules.get("qvm_sell_guard", True) and q is not None and mo is not None \
            and q >= 70 and mo >= 60:
        lvl = 2
        reasons.append(f"現價高於模型估值,但品質({q:.0f})與動能({mo:.0f})分數都高,等級維持 C(價值要和品質、動能一起看)")
    if lvl <= 1 and not red_flags and market == "US" and not rules.get("us_valuation_only_sell", False):
        lvl = 2
        reasons.append("美股:5 年回測顯示只因估值偏高而給的低等級命中率不到 5 成,等級維持 C(有財務紅旗才會更低)")
    return {"rating": LT_LEVELS[lvl], "level": lvl, "base_level": base_lvl, "reasons": reasons, "red_flags": red_flags}


# ---------------------------------------------------------------- P2-H10:固定分位評等(網站單檔)
def quantile_thresholds(scores, cuts) -> list[float] | None:
    """由一個判斷日、一個股票池的 lt_score 算出 4 個分數切點,讓「分數 ≥ 第 k 個切點的個數」= 等級。
    等級的算法和回測 `backtest_pit.fixed_quantile_levels` 完全相同(平均排名百分位 + searchsorted left),
    切點取相鄰兩級之間的中點,所以股票池內每一檔用切點換算都會得到和回測相同的等級。
    任何一級沒有股票(股票池太小)時回傳 None。"""
    import numpy as np
    import pandas as pd
    s = pd.Series([float(x) for x in scores if x is not None and not math.isnan(float(x))])
    if s.empty:
        return None
    pct = s.rank(pct=True, method="average").to_numpy()
    lv = np.searchsorted(np.asarray(cuts, dtype=float), pct, side="left")
    out = []
    for k in range(1, len(cuts) + 1):
        lo, hi = s[lv < k], s[lv >= k]
        if lo.empty or hi.empty:
            return None
        out.append((float(lo.max()) + float(hi.min())) / 2)
    return out


def level_from_thresholds(score: float, thresholds: list[float]) -> int:
    return sum(1 for t in thresholds if score >= t)


def pool_percentile(score: float, scores: list[float]) -> float | None:
    """分數在股票池裡的百分位(比它低的比例 + 同分的一半),只用來顯示。"""
    if not scores:
        return None
    lo = sum(1 for x in scores if x < score)
    eq = sum(1 for x in scores if x == score)
    return (lo + eq / 2) / len(scores)


def load_cutpoints(market: str, params: dict) -> tuple[dict | None, str | None]:
    """讀 OUT_DIR(再來 DATA_DIR)的切點檔。回傳 (該市場的切點, 不能用的原因)。
    分位設定或長期權重和目前參數不同時不用(切點是舊模型算的)。"""
    cuts = (params.get("decision_rules") or {}).get("fixed_quantile_levels")
    for d in (config.OUT_DIR, config.DATA_DIR):
        f = d / CUTPOINTS_FILE
        if f.exists():
            break
    else:
        return None, "還沒有股票池分位切點(要先在本機跑一次回測)"
    try:
        cp = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"分位切點讀取失敗({type(e).__name__})"
    if list(cp.get("cuts") or []) != list(cuts or []):
        return None, "分位切點的分位設定和目前模型不同"
    if cp.get("lt_weights") != (params.get("pillar_weights") or {}).get("long_term"):
        return None, "分位切點是用不同的長期分數權重算的"
    if bool(cp.get("cross_sectional_scoring", False)) != bool(params.get("cross_sectional_scoring", False)):
        return None, "分位切點的分數算法(橫斷面百分位)和目前模型不同"   # RedTeam2 問題 4
    m = (cp.get("markets") or {}).get(market)
    if not m or not m.get("thresholds"):
        return None, f"還沒有{'台股' if market == 'TW' else '美股'}的分位切點"
    return m, None


def long_term_quantile(val: dict, lt: dict, fund: dict, cp: dict, cuts) -> dict:
    """P2-H10 網站版:長期分數對照正式股票池最近一個判斷日的分位切點給評等(分位規則和回測相同;切點來自回測的分數分布,回測沒有分析師與同業資料,網站分數不完全可比)。
    沒有公平價 → 不評等(回測裡這些列也沒有等級、不參與排名);公平價與安全邊際照樣顯示,但不決定等級。
    財務紅旗照樣列出,但和回測一樣不改等級。"""
    price, fv = val.get("price"), val.get("fair_value")
    if not fv or not price:
        return {"rating": "不評等(估值不可得)", "level": None, "reasons": [val.get("no_fv_reason") or "公平價資料不足"],
                "method": "fixed_quantile"}
    s = lt.get("score")
    if s is None:
        return {"rating": "不評等(長期分數不可得)", "level": None, "reasons": ["長期分數資料不足"], "method": "fixed_quantile"}
    lvl = level_from_thresholds(s, cp["thresholds"])
    pct = pool_percentile(s, cp.get("scores") or [])
    pool = POOL_LABEL.get(cp.get("universe"), cp.get("universe"))
    c = [round(x * 100) for x in cuts]
    bands = [c[0], c[1] - c[0], c[2] - c[1], c[3] - c[2], 100 - c[3]]
    reasons = [f"長期分數 {s:.0f}" + (f",約在{pool} 第 {pct * 100:.0f} 百分位" if pct is not None else "")
               + f"(股票池 {cp.get('date')} 的 {cp.get('n')} 檔分數分布)",
               f"固定分位等級:E = 同股票池後 {bands[0]}%、D = 次 {bands[1]}%、C = 中間 {bands[2]}%、B = 次 {bands[3]}%、A = 前 {bands[4]}%(切點來自回測分數分布;網站分數另含分析師與同業資料,不完全可比)",
               f"估值參考(不決定等級):現價相對公平價 {val['upside']:+.1%},不確定性「{val['uncertainty']}」要求安全邊際 {val['margin_of_safety']:.0%}"]
    red_flags = _red_flags(val, fund)
    if red_flags:
        reasons.append("有財務紅旗(見下):和回測一致,等級不因此調整,請自行查證")
    return {"rating": LT_LEVELS[lvl], "level": lvl, "reasons": reasons, "red_flags": red_flags, "method": "fixed_quantile",
            "quantile": {"percentile": pct, "pool": cp.get("universe"), "pool_label": pool, "date": cp.get("date"),
                         "n": cp.get("n"), "thresholds": cp["thresholds"]}}


def _red_flags(val: dict, fund: dict) -> list[str]:
    red_flags = []
    z_score = (fund.get("health") or {}).get("altman_z")
    if z_score is not None and z_score < 1.81 and val.get("company_type") != "financial":
        red_flags.append(f"Altman Z = {z_score:.2f} 落在危險區")
    bm = fund.get("beneish")
    if bm and bm.get("flag") and not bm.get("high_growth_caveat"):
        red_flags.append(f"Beneish M = {bm['m']:.2f} 高於 −1.78,財報品質需查證")
    return red_flags


def short_term(st: dict, exp: dict, params: dict) -> dict:
    th = params["short_term_thresholds"]
    s = st.get("score")
    if s is None:
        return {"signal": "無法判斷", "code": 0}
    code = 1 if s >= th["bullish"] else -1 if s <= th["bearish"] else 0
    label = {1: "分數偏高", 0: "中性", -1: "分數偏低"}[code]
    flags = []
    dte = exp.get("days_to_earnings")
    if dte is not None and 0 <= dte <= 10:
        when = "今天" if dte == 0 else "明天" if dte == 1 else f"{dte} 天後"
        flags.append(f"{when}公布財報:事件風險,價格可能大幅跳動")
    return {"signal": label, "code": code, "score": s, "flags": flags}


def sim_position_cap(price: float, tech: dict) -> float | None:
    """只給模擬帳戶用的單檔上限(模擬規則的一部分,不顯示):以 2.5 倍 ATR 的跌幅最多影響模擬資金 1% 換算。"""
    atr = tech.get("atr14")
    if not atr or not price or atr <= 0:
        return None
    return safe(min(0.25, 0.01 / (2.5 * atr / price)))


def confidence(pillars: dict, val: dict, status_items: list[dict]) -> dict:
    cov = [p.get("coverage") or 0 for k, p in pillars.items() if k != "regime"]
    data = sum(cov) / len(cov) if cov else 0
    ok = sum(1 for s in status_items if s["ok"])
    src = ok / len(status_items) if status_items else 0
    unc = {"低": 1.0, "中": 0.8, "高": 0.6, "很高": 0.4}.get(val.get("uncertainty"), 0.5)
    inc = [m for m in val.get("methods", []) if m.get("included")]
    meth = min(1.0, len(inc) / 4)
    score = 100 * (0.35 * data + 0.15 * src + 0.3 * unc + 0.2 * meth)
    # 核心資料缺漏(財報、法人籌碼、月營收)直接扣分,並把資料完整度上限壓在「中」
    core = ("綜合損益表", "三大法人", "月營收", "股價歷史")
    missing = [s["item"] for s in status_items if not s["ok"] and any(c in s["item"] for c in core)]
    score -= 12 * len(missing)
    label = "高" if score >= 72 and not missing else "中" if score >= 52 else "低"
    if val.get("uncertainty") == "很高" or not val.get("fair_value"):
        label = "低"   # P2-H04(Red-team M3):估值不確定性「很高」或沒有公平價時,資料完整度最高「低」
    # 名稱是「資料完整度」:分數只反映資料覆蓋、資料源成功率、估值不確定性與方法數,不是「判斷對的機率」(階段 3 D0 A3)
    return {"title": "資料完整度", "note": "反映資料覆蓋、資料源成功率、估值不確定性與估值方法數;不是判斷正確的機率",
            "score": max(0, round(score)), "label": label, "missing_core": missing,
            "parts": {"指標覆蓋率": round(data, 2), "資料源成功率": round(src, 2), "估值不確定性": unc, "估值方法數": len(inc),
                      "缺少核心資料": "、".join(missing) or "無"}}
