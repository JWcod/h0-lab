"""Agent 團隊:把分析流程拆成 6 個角色,邊做邊回報(給前端即時顯示),最後各自給一句結論。

這些「agent」是規則與程式組成的分工(不是付費 LLM),每一句發現都來自實際算出來的數字。
"""
from __future__ import annotations

AGENTS = [
    {"id": "data", "name": "資料蒐集", "role": "抓取股價、財報、籌碼、新聞與官方公告", "icon": "◎", "color": "#5ab0ff"},
    {"id": "fund", "name": "財報分析", "role": "整理三大報表,評估獲利、成長與財務安全", "icon": "▤", "color": "#3ee0c5"},
    {"id": "market", "name": "技術與籌碼", "role": "判讀趨勢、動能、法人與大盤環境", "icon": "◭", "color": "#ffd166"},
    {"id": "news", "name": "消息面", "role": "分類新聞情緒與重大訊息", "icon": "✦", "color": "#f472b6"},
    {"id": "val", "name": "估值", "role": "DCF 蒙地卡羅、本益比河流、同業與分析師", "icon": "◆", "color": "#9b8cff"},
    {"id": "dec", "name": "資料整理", "role": "整理資料完整度、財務紅旗與研究檢查清單", "icon": "★", "color": "#ff8a5c"},
]


def _p(x, d=1):
    return "—" if x is None else f"{x * 100:.{d}f}%"


def _px(x):
    if x is None:
        return "—"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def fund_findings(fund: dict, mr: dict | None) -> list[str]:
    p, g, h = fund.get("profitability") or {}, fund.get("growth") or {}, fund.get("health") or {}
    out = []
    if p.get("roic") is not None:
        out.append(f"ROIC {_p(p['roic'])}、ROE {_p(p.get('roe'))}")
    if p.get("gross_margin") is not None:
        out.append(f"毛利率 {_p(p['gross_margin'])}、營益率 {_p(p.get('operating_margin'))}")
    if g.get("revenue_yoy") is not None:
        out.append(f"營收年增 {_p(g['revenue_yoy'])},3 年 CAGR {_p(g.get('revenue_cagr_3y'))}")
    pf = fund.get("piotroski")
    if pf:
        out.append(f"Piotroski F-score {pf['score']}/9")
    if h.get("altman_z") is not None:
        out.append(f"Altman Z {h['altman_z']:.2f}({'安全' if h['altman_z'] > 2.99 else '灰色' if h['altman_z'] >= 1.81 else '危險'}區)")
    if mr and mr.get("yoy") is not None:
        out.append(f"{mr['latest_month']} 月營收年增 {_p(mr['yoy'])}" + ("(創歷史新高)" if mr.get("record_high") else ""))
    return out


def market_findings(tech: dict, ch: dict, regime: dict) -> list[str]:
    out = []
    if tech.get("available"):
        out.append(f"趨勢:{(tech.get('trend') or {}).get('label', '—')},RSI {tech.get('rsi14') or 0:.0f}")
        if tech.get("mom_12_1") is not None:
            out.append(f"12-1 個月動能 {_p(tech['mom_12_1'])},距 52 週高點 {_p(tech.get('pct_from_52w_high'))}")
    if ch.get("market") == "TW":
        fi = (ch.get("institutional") or {}).get("外資") or {}
        if fi.get("streak"):
            out.append(f"外資連續{'買超' if fi['streak'] > 0 else '賣超'} {abs(fi['streak'])} 日,20 日合計 {fi.get('d20') or 0:+,.0f} 張")
    else:
        sh = ch.get("short") or {}
        if sh.get("pct_float") is not None:
            out.append(f"放空比例 {_p(sh['pct_float'])}")
    if regime.get("label"):
        out.append(f"大盤環境:{regime['label']}({regime.get('score', 0):.0f})")
    return out


def news_findings(news_sum: dict, n_items: int, ann: list | None) -> list[str]:
    out = [f"讀取 {n_items} 則新聞"]
    d7 = (news_sum or {}).get("d7") or {}
    if d7.get("n"):
        out.append(f"近 7 日 {d7['n']} 則,情緒 {d7.get('avg') or 0:+.2f}(正 {d7.get('pos', 0)} / 負 {d7.get('neg', 0)})")
    ev = (news_sum or {}).get("events") or {}
    if ev:
        top = sorted(ev.items(), key=lambda t: -t[1])[:3]
        out.append("焦點:" + "、".join(k for k, _ in top))
    if ann:
        out.append(f"官方重大訊息 {len(ann)} 則,最新:{ann[0].get('subject', '')[:28]}")
    return out


def val_findings(val: dict) -> list[str]:
    out = []
    for m in val.get("methods") or []:
        if m.get("value"):
            out.append(f"{m['label']}:{_px(m['value'])}")    # 各工具在預設假設下的結果;開源版不播綜合公平價
    d = val.get("dcf") or {}
    if d.get("implied_growth") is not None:
        out.append(f"反向 DCF:市場隱含成長 {_p(d['implied_growth'])}")
    return out


def dec_findings(pillars: dict, lt: dict, st: dict) -> list[str]:
    keys = ["quality", "growth", "valuation", "health", "momentum", "flows", "revisions", "sentiment"]
    sc = [f"{pillars[k]['label']} {pillars[k]['grade']}" for k in keys if pillars.get(k, {}).get("score") is not None]
    out = ["八大面向:" + " · ".join(sc)]
    out.append(f"長期模型等級「{lt.get('rating')}」,短期分數等級「{st.get('signal')}」")
    if lt.get("red_flags"):
        out.append("⚠ " + ";".join(lt["red_flags"]))
    return out


def headline(agent_id: str, R: dict) -> str:
    """每個 agent 的一句話結論(報告頂端用)。"""
    v, val = R["verdict"], R["valuation"]
    f, t = R["fundamentals"], R["technical"]
    if agent_id == "data":
        ok = sum(1 for s in R["data_status"] if s["ok"])
        return f"{ok}/{len(R['data_status'])} 個資料源成功"
    if agent_id == "fund":
        p = f.get("profitability") or {}
        return f"ROIC {_p(p.get('roic'))};毛利率 {_p(p.get('gross_margin'))};營收年增 {_p((f.get('growth') or {}).get('revenue_yoy'))}"
    if agent_id == "market":
        return f"{(t.get('trend') or {}).get('label', '—')};12-1 個月動能 {_p(t.get('mom_12_1'))}"
    if agent_id == "news":
        d7 = (R["news"]["summary"] or {}).get("d7") or {}
        return f"近 7 日 {d7.get('n', 0)} 則,情緒 {(d7.get('avg') or 0):+.2f}"
    if agent_id == "val":
        d = val.get("dcf") or {}
        return f"DCF 每股 {_px(d.get('base'))};反向 DCF 隱含成長 {_p(d.get('implied_growth'))}"
    if agent_id == "dec":
        return f"長期{v['long_term']['rating']} · 短期{v['short_term']['signal']}"
    return ""


def summary(R: dict) -> list[dict]:
    """完整報告 → 每個 agent 的結論與發現(快取重播、報告頂端都用這個)。"""
    out = []
    for a in AGENTS:
        if a["id"] == "data":
            fl = [f"{s['source']}:{s['item']}" + ("" if s["ok"] else "(失敗)") for s in R["data_status"]][:12]
        elif a["id"] == "fund":
            fl = fund_findings(R["fundamentals"], R["fundamentals"].get("month_revenue"))
        elif a["id"] == "market":
            fl = market_findings(R["technical"], R["chips"], R["regime"])
        elif a["id"] == "news":
            fl = news_findings(R["news"]["summary"], len(R["news"]["items"]), (R.get("extra") or {}).get("announcements"))
        elif a["id"] == "val":
            fl = val_findings(R["valuation"])
        else:
            fl = dec_findings(R["pillars"], R["verdict"]["long_term"], R["verdict"]["short_term"])
        out.append({**a, "headline": headline(a["id"], R), "findings": fl})
    return out
