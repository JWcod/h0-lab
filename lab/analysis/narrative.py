"""把所有結果濃縮成一段白話摘要(規則產生,不用 LLM)。"""
from __future__ import annotations

from .decision import LT_LEVELS


def _p(x, d=0):
    return "—" if x is None else f"{x * 100:.{d}f}%"


def _px(x):
    if x is None:
        return "—"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def build(R: dict) -> list[str]:
    m, q, v, val, P = R["meta"], R["quote"], R["verdict"], R["valuation"], R["pillars"]
    ccy = "元" if m["market"] == "TW" else m["currency"]
    out = []
    # 1. 估值結論
    if v.get("fair_value"):
        up = v["upside"]
        s = (f"{m['name']}目前股價 {_px(q['price'])} {ccy},模型估值 {_px(v['fair_value'])} {ccy}"
             f"(合理區間 {_px(v['fair_low'])}–{_px(v['fair_high'])}),"
             + (f"現價低於模型估值約 {_p(up)}" if up > 0 else f"現價高於模型估值約 {_p(-up)}") + "。")
        lt = v["long_term"]
        qt = lt.get("quantile") if lt.get("method") == "fixed_quantile" else None
        s += f"公司屬於「{val.get('company_type_label')}」,估值不確定性「{v['uncertainty']}」,模型使用 {_p(val.get('margin_of_safety'))} 安全邊際"
        if qt:   # P2-H10:評等由長期分數在股票池的固定分位決定,估值只當參考
            s += (f";長期模型等級「{lt['rating']}」由長期分數在{qt['pool_label']}({qt['date']})的固定分位決定"
                  + (f"(約第 {qt['percentile'] * 100:.0f} 百分位)" if qt.get("percentile") is not None else "") + ",估值只當參考。")
        elif lt.get("method") == "us_info_only":   # Q7
            s += ";美股在模型證實有效之前不給等級,這裡只提供資訊。"
        else:
            s += f",因此長期模型等級為「{lt['rating']}」。"
        if not qt and lt.get("base_level") is not None and lt.get("level") is not None and lt["level"] != lt["base_level"]:
            names = LT_LEVELS
            s += (f"(單看估值是「{names[lt['base_level']]}」,"
                  + ("因長期體質分數較高上調一級。)" if lt["level"] > lt["base_level"]
                     else "因體質或財務紅旗下調。)"))
        sc = (val.get("scenario") or {}).get("12m")
        if sc:
            # H09 機率校準沒有通過:不說「機率」,只說依過去波動推算的情境範圍
            s += (f"統計情境(不是預測):若價值差距以每年約 1/3 的速度收斂,12 個月後的中心值約 {_px(sc['center'])} {ccy},"
                  f"依過去波動推算的範圍約 {_px(sc['low68'])}–{_px(sc['high68'])}(情境範圍,不是校準過的機率)。")
        out.append(s)
    # 2. 體質
    names = {"quality": "品質", "growth": "成長", "health": "財務安全", "valuation": "估值"}
    parts = [f"{n} {P[k]['grade']}" for k, n in names.items() if P.get(k, {}).get("score") is not None]
    unval = set(v.get("unvalidated_pillars") or [])          # Q2:沒有歷史回測的面向,提到時標「參考」
    lab = lambda k: P[k]["label"] + ("(參考)" if k in unval else "")  # noqa: E731
    strong = [lab(k) for k in ("quality", "growth", "health", "revisions") if (P.get(k, {}).get("score") or 0) >= 75]
    weak = [lab(k) for k in ("quality", "growth", "health", "valuation", "flows", "revisions") if P.get(k, {}).get("score") is not None and P[k]["score"] < 40]
    s = "體質面:" + "、".join(parts) + "。"
    if strong:
        s += "強項在" + "、".join(strong) + "。"
    if weak:
        s += "弱項在" + "、".join(weak) + "。"
    # 估值已在第一段講過,這裡挑其他面向的重點
    ins = R.get("insights") or []
    pos = [i["title"] for i in ins if i["tone"] == "pos" and i["category"] != "估值"][:2]
    neg = [i["title"] for i in ins if i["tone"] == "neg" and i["category"] != "估值"][:2]
    if pos:
        s += "正面觀察:" + ";".join(pos) + "。"
    if neg:
        s += "主要風險:" + ";".join(neg) + "。"
    out.append(s)
    # 3. 短期
    t = R["technical"]
    st = v["short_term"]
    s = f"短期(1–3 個月)分數等級「{st['signal']}」:技術趨勢{t.get('trend', {}).get('label', '—')},大盤環境{R['regime'].get('label', '—')}。"
    ref = []   # Q2:預期修正、籌碼不進短期分數等級,列為參考
    et = (R["expectations"].get("eps_trend") or {}).get("0y") or {}
    if et.get("chg_90d") is not None:
        ref.append(f"今年 EPS 預估 90 天{'上修' if et['chg_90d'] >= 0 else '下修'} {_p(abs(et['chg_90d']), 1)}")
    ch = R["chips"]
    if ch.get("market") == "TW":
        fi = (ch.get("institutional") or {}).get("外資") or {}
        if fi.get("streak"):
            ref.append(f"外資連續{'買超' if fi['streak'] > 0 else '賣超'} {abs(fi['streak'])} 日")
    else:
        sh = ch.get("short") or {}
        if sh.get("pct_float") is not None:
            ref.append(f"放空比例 {_p(sh['pct_float'], 1)}")
    if ref:
        s += "參考(沒有歷史回測,不計入分數):" + "、".join(ref) + "。"
    for f in st.get("flags", []):
        s += f + "。"
    out.append(s)
    out.append("以上由固定規則整理模型輸出,不是投資建議;模型等級在回測裡沒有證實的預測力。")
    return out
