"""API 對外輸出的過濾:開源版不提供內建模型的個股評等、綜合公平價與交易計畫(2026-11-03「自建模型優先」)。

- 只在 app/server.py 回應前呼叫;`report.build`、快照、模擬帳戶、市場掃描檔、`scripts/scan_cli.py` 都照舊用完整報告,
  前瞻紀錄與研究重現不受影響。`lab/analysis/` 的評分計算一行都不改。
- 資料頁(財報、品質、技術面、籌碼、預期、新聞、公司)與估值工具(DCF、蒙地卡羅、反向 DCF、本益比河流、EPV、同業倍數)照常提供。
- 使用者自己的模型分數由 /api/models/validate 用記憶體裡的報告重算,不經過這裡。
"""
from __future__ import annotations

import math

# 估值:綜合公平價與由它衍生的區間、安全邊際、統計情境、觸及機率、加權方式
VALUATION_DROP = ("fair_value", "fair_low", "fair_high", "upside", "zones", "margin_of_safety", "touch_prob_12m", "scenario",
                  "uncertainty", "uncertainty_score", "uncertainty_parts", "no_fv_reason", "weights", "composite",
                  "single_method")
METHOD_DROP = ("weight", "included", "excluded_reason")
# 報告頂層:內建八面向分數、依內建模型整理的摘要、與上次比較(含等級變化)
# model_env 也不回傳:裡面的 upside 是綜合公平價的差距;使用者模型的分數在伺服器用記憶體裡的報告重算,前端用不到
REPORT_DROP = ("pillars", "narrative", "since_last", "model_env")
# 依內建模型結果產生的見解(標題開頭)與檢查清單項目(文字)
INSIGHT_DROP_PREFIX = ("模型估值", "12 個月內觸及公平價")
CHECK_DROP = ("品質分數 ≥ 60", "籌碼分數 ≥ 40", "看過模型估值與它的假設", "估值不確定性高:看區間,不看單點")
NOTICE = "開源版不提供內建模型的個股評等與綜合公平價;分數只來自你自己建立的模型。"


def _valuation(val: dict | None) -> dict:
    if not isinstance(val, dict):
        return {}
    out = {k: v for k, v in val.items() if k not in VALUATION_DROP}
    out["methods"] = [{k: v for k, v in m.items() if k not in METHOD_DROP} for m in val.get("methods") or [] if isinstance(m, dict)]
    pe = out.get("pe_band")
    if isinstance(pe, dict):
        out["pe_band"] = {k: v for k, v in pe.items() if k != "weight"}
    return out


def _insights(items) -> list[dict]:
    return [i for i in items or [] if isinstance(i, dict) and not str(i.get("title", "")).startswith(INSIGHT_DROP_PREFIX)]


def _agents(items) -> list[dict]:
    """分析過程的重播:只留資料步驟;內建模型的結論那一步不回傳。"""
    return [a for a in items or [] if isinstance(a, dict) and a.get("id") != "dec"]


def report(r: dict) -> dict:
    """完整報告 → 對外版本(淺拷貝,不改記憶體快取裡的報告)。"""
    if not isinstance(r, dict):
        return r
    out = {k: v for k, v in r.items() if k not in REPORT_DROP}
    v = r.get("verdict") or {}
    lt = v.get("long_term_model") or v.get("long_term") or {}
    out["verdict"] = {"confidence": v.get("confidence"), "red_flags": lt.get("red_flags") or [], "notice": NOTICE}
    out["valuation"] = _valuation(r.get("valuation"))
    ins = _insights(r.get("insights"))
    out["insights"] = ins
    out["checklist"] = [c for c in r.get("checklist") or [] if isinstance(c, dict) and c.get("text") not in CHECK_DROP]
    out["agents"] = _agents(r.get("agents"))
    meta = dict(r.get("meta") or {})
    meta.pop("model_version", None)
    out["meta"] = meta
    return out


# ---------- 串流:分析過程 ----------
def stream_event(ev: dict) -> dict | None:
    """SSE 事件:內建模型結論那一步只留開始與完成(文字換成中性說明);估值步驟不播綜合公平價。"""
    if not isinstance(ev, dict):
        return ev
    a = ev.get("agent")
    if a == "dec":
        if ev.get("kind") == "start":
            return {**ev, "msg": "整理資料完整度、財務紅旗與研究檢查清單"}
        if ev.get("kind") == "done":
            return {**ev, "msg": "整理完成"}
        return None
    if a == "val" and ev.get("kind") == "finding" and str(ev.get("msg", "")).startswith("模型估值"):
        return None
    return ev


# ---------- 首頁、掃描 ----------
SNAP_KEEP = ("symbol", "name", "market", "date", "time", "price", "currency")


def snapshot(s: dict | None) -> dict | None:
    return {k: s.get(k) for k in SNAP_KEEP} if isinstance(s, dict) else None


# 掃描列:只留資料欄(名稱、股價、市值、倍數、動能、波動、資料完整度、財務紅旗)
SCAN_KEEP = ("symbol", "name", "market", "industry", "currency", "price", "change_pct", "market_cap", "pe", "forward_pe",
             "dividend_yield", "company_type", "cyclical", "red_flags", "mom_12_1", "vol_1y", "data_fails", "confidence",
             "missing_core")


def scan_row(r: dict) -> dict:
    return {k: r.get(k) for k in SCAN_KEEP}


def scan_status(st: dict) -> dict:
    out = dict(st)
    out["recent"] = [{k: x.get(k) for k in ("symbol", "name", "price")} for x in st.get("recent") or []]
    return out


def finite(x):
    """遞迴把 NaN / 無限大換成 None(JSON 不能放 NaN)。"""
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else x
    if isinstance(x, dict):
        return {k: finite(v) for k, v in x.items()}
    if isinstance(x, list):
        return [finite(v) for v in x]
    return x
