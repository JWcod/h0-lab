"""研究檢查清單:把模型結果轉成「一條一條要查證的事」,幫助整理研究筆記;不是買賣條件。

status:pass(符合)/ fail(不符合)/ warn(要注意)/ manual(要你自己回答)
"""
from __future__ import annotations


def build(R: dict) -> list[dict]:
    v, val, P = R["verdict"], R["valuation"], R["pillars"]
    f, t, exp = R["fundamentals"], R["technical"], R["expectations"]
    q = R["quote"]
    items = []

    def add(group, text, status, detail=""):
        items.append({"group": group, "text": text, "status": status, "detail": detail})

    flags = v["long_term"].get("red_flags") or []
    add("體質", "沒有財務紅旗(破產風險、財報操縱模型)", "fail" if flags else "pass", ";".join(flags) or "Altman / Beneish 都在一般範圍")
    c = f["profitability"].get("cfo_to_ni")
    if c is not None:
        add("體質", "獲利有變成現金(營業現金流 ÷ 淨利 ≥ 0.8)", "pass" if c >= 0.8 else "fail", f"目前 {c:.2f}")
    qs = P.get("quality", {}).get("score")
    if qs is not None:
        add("體質", "品質分數 ≥ 60", "pass" if qs >= 60 else "warn", f"目前 {qs:.0f}")
    g = f["growth"].get("revenue_yoy")
    rev = ((exp.get("eps_trend") or {}).get("0y") or {}).get("chg_90d")
    if g is not None:
        ok = g > 0 and (rev is None or rev >= -0.01)
        add("成長", "營收在成長、分析師預估沒有下修", "pass" if ok else "warn",
            f"營收年增 {g:.1%}" + (f";EPS 預估 90 天 {rev:+.1%}" if rev is not None else ""))
    fv = val.get("fair_value")
    if fv and q.get("price"):
        add("估值", "看過模型估值與它的假設", "manual", f"現價 {q['price']:,.2f};模型估值 {fv:,.2f}")
    if val.get("uncertainty") in ("高", "很高"):
        add("估值", "估值不確定性高:看區間,不看單點", "warn", f"不確定性「{val['uncertainty']}」")
    tc = (t.get("trend") or {}).get("code")
    if tc is not None:
        add("市場資料", "股價在 200 日線附近或之上", "pass" if tc >= 0 else "warn", (t.get("trend") or {}).get("label", ""))
    fl = P.get("flows", {}).get("score")
    if fl is not None:
        add("市場資料", "籌碼分數 ≥ 40", "pass" if fl >= 40 else "warn", f"目前 {fl:.0f}")
    dte = exp.get("days_to_earnings")
    if dte is not None:
        add("市場資料", "近 7 天沒有財報(波動可能放大)", "warn" if 0 <= dte <= 7 else "pass", f"下次財報 {exp.get('next_earnings') or '—'}")
    add("自己", "我能用一句話說出這家公司怎麼賺錢", "manual")
    add("自己", "我寫下了研究假設,以及什麼情況代表假設不成立", "manual")
    add("自己", "我查過原始資料,知道這些數字的來源與限制", "manual")
    return items
