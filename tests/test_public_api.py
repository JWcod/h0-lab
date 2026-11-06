"""開源版對外輸出(lab/public.py):不提供內建評等、綜合公平價與交易計畫;技術面的支撐壓力、技術訊號是資料頁內容。

2026-11-03「自建模型優先」(docs/技術決策紀錄.md)。report.build、快照、模擬帳戶照舊用完整報告,這裡只測對外過濾。
"""
from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd

from lab import public
from lab.analysis import technical as TE

FULL = {
    "meta": {"symbol": "2330.TW", "name": "台積電", "market": "TW", "model_version": "JW-MF v2.0-dev", "generated_at": "2026-11-03T10:00:00"},
    "quote": {"price": 100.0},
    "verdict": {
        "long_term": {"rating": "模型等級 B", "level": 3, "reasons": ["x"], "red_flags": []},
        "short_term": {"signal": "分數偏高", "code": 1},
        "long_term_model": {"rating": "模型等級 B", "level": 3, "red_flags": ["Altman Z 1.2"]},
        "short_term_model": {"signal": "分數偏高", "code": 1},
        "fair_value": 120.0, "fair_low": 100.0, "fair_high": 140.0, "upside": 0.2, "uncertainty": "中",
        "confidence": {"score": 80, "label": "高", "missing_core": []}, "sim_max_position_pct": 0.05,
        "lt_score": {"score": 70, "grade": "B"}, "st_score": {"score": 60, "grade": "C"},
        "positives": [], "negatives": [],
    },
    "pillars": {"quality": {"score": 80, "grade": "A"}},
    "valuation": {"fair_value": 120.0, "fair_low": 100.0, "fair_high": 140.0, "upside": 0.2, "margin_of_safety": 0.25,
                  "zones": {"well_below": 90.0, "below": 105.0, "above": 135.0, "well_above": 150.0},
                  "uncertainty": "中", "uncertainty_score": 0.3, "touch_prob_12m": 0.4, "scenario": {"3m": {}},
                  "methods": [{"key": "dcf", "label": "DCF", "value": 110.0, "low": 90.0, "high": 130.0, "upside": 0.1,
                               "weight": 40, "included": True}],
                  "dcf": {"base": 110.0, "implied_growth": 0.08, "inputs": {"g_start": 0.1}},
                  "pe_band": {"value": 105.0, "current_pct_rank": 0.5}, "company_type_label": "成熟"},
    "insights": [{"title": "模型估值 120.00 TWD(+20.0%)", "tone": "pos"}, {"title": "ROIC 25%:資本效率很高", "tone": "pos"},
                 {"title": "12 個月內觸及公平價的機率約 40%", "tone": "watch"}],
    "checklist": [{"text": "品質分數 ≥ 60"}, {"text": "看過模型估值與它的假設"}, {"text": "我能用一句話說出這家公司怎麼賺錢"}],
    "narrative": ["長期模型等級「模型等級 B」"],
    "since_last": {"rating_change": {"from": "模型等級 C", "to": "模型等級 B"}},
    "agents": [{"id": "val", "findings": []}, {"id": "dec", "findings": ["長期模型等級「模型等級 B」"]}],
    "technical": {"levels": {"support": [{"price": 95.0, "touches": 3}], "resistance": []}, "signals": [{"name": "RSI 超買"}]},
    "model_env": {"roe": 0.2},
}
BANNED_KEYS = {"rating", "fair_value", "fair_low", "fair_high", "zones", "upside", "lt_score", "st_score", "pillars", "narrative",
               "since_last", "sim_max_position_pct", "long_term", "short_term", "margin_of_safety", "scenario", "touch_prob_12m",
               "weight", "included", "model_version"}


def _keys(x, out=None):
    out = set() if out is None else out
    if isinstance(x, dict):
        for k, v in x.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(x, list):
        for v in x:
            _keys(v, out)
    return out


def test_public_report_drops_builtin_ratings_and_fair_value():
    src = copy.deepcopy(FULL)
    out = public.report(src)
    assert src == FULL, "不得改到記憶體快取裡的完整報告"
    keys = _keys({k: v for k, v in out.items() if k != "technical"})
    # methods[].upside 是單一估值工具相對現價的差距(資料),不是綜合公平價;其他禁用欄位都不得出現
    assert not (keys & BANNED_KEYS) - {"upside"}, keys & BANNED_KEYS
    assert "upside" not in out["valuation"]
    text = json.dumps(out, ensure_ascii=False)
    assert "模型等級" not in text and "公平價" not in text.replace("綜合公平價", "")
    assert out["verdict"]["confidence"]["score"] == 80 and out["verdict"]["red_flags"] == ["Altman Z 1.2"]
    assert [i["title"] for i in out["insights"]] == ["ROIC 25%:資本效率很高"]
    assert [c["text"] for c in out["checklist"]] == ["我能用一句話說出這家公司怎麼賺錢"]
    assert [a["id"] for a in out["agents"]] == ["val"]
    assert out["valuation"]["dcf"]["base"] == 110.0 and out["valuation"]["methods"][0]["value"] == 110.0
    assert out["technical"]["levels"]["support"][0]["price"] == 95.0, "支撐壓力是技術面資料頁內容,要保留"
    assert "model_env" not in out, "model_env.upside 是綜合公平價的差距,不得對外"


def test_stream_events_hide_builtin_conclusion():
    assert public.stream_event({"agent": "dec", "kind": "finding", "msg": "長期模型等級「模型等級 B」"}) is None
    assert "等級" not in public.stream_event({"agent": "dec", "kind": "done", "msg": "完成:長期「模型等級 B」"})["msg"]
    assert public.stream_event({"agent": "val", "kind": "finding", "msg": "模型估值 120"}) is None
    ev = {"agent": "fund", "kind": "finding", "msg": "ROIC 25%"}
    assert public.stream_event(ev) == ev


def test_scan_row_and_status_have_no_builtin_fields():
    row = {"symbol": "2330.TW", "name": "台積電", "price": 100, "lt_rating": "模型等級 B", "lt_level": 3, "fair_value": 120,
           "upside": 0.2, "lt_score": 70, "st_signal": "分數偏高", "pillars": {"quality": 80}, "model_env": {"roe": 0.2},
           "forward_pe": 18.0, "red_flags": []}
    out = public.scan_row(row)
    assert set(out) <= set(public.SCAN_KEEP) and out["forward_pe"] == 18.0
    assert not {"lt_rating", "lt_level", "fair_value", "upside", "lt_score", "st_signal", "pillars", "model_env"} & set(out)
    st = public.scan_status({"running": True, "recent": [row]})
    assert st["recent"] == [{"symbol": "2330.TW", "name": "台積電", "price": 100}]
    assert public.snapshot({"symbol": "X", "date": "2026-11-01", "price": 1, "lt_rating": "模型等級 A", "fair_value": 2}) == \
        {"symbol": "X", "name": None, "market": None, "date": "2026-11-01", "time": None, "price": 1, "currency": None}


def _ohlc(n=520, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Adj Close": close,
                         "Volume": rng.integers(1_000, 5_000, n).astype(float)}, index=idx)


def test_technical_levels_and_signals_restored_but_not_in_light_path():
    """從 54dd23b 還原的支撐壓力與技術訊號:完整報告有,回測用的 light 路徑不算(評分與回測不受影響)。"""
    h = _ohlc()
    full = TE.analyze(h, None, "TW")
    assert set(full["levels"]) == {"support", "resistance"} and isinstance(full["signals"], list)
    px = full["price"]
    assert all(x["price"] < px for x in full["levels"]["support"]) and all(x["price"] > px for x in full["levels"]["resistance"])
    assert all({"name", "direction", "detail"} <= set(s) for s in full["signals"])
    light = TE.analyze(h, None, "TW", light=True)
    assert "levels" not in light and "signals" not in light
    shared = set(light) & set(full)
    assert {k: light[k] for k in shared if k != "chart"} == {k: full[k] for k in shared if k != "chart"}, "評分用到的指標不得改變"
