"""用某個模型重算一份報告的分數(不重抓資料):報告輸出的 model_env(基礎變數快照)→ 各面向與長短期分數。

我的模型只輸出分數、等級(A+ ~ F);不輸出評等、買賣字眼,也不走決策切點與模擬帳戶(規格 §4.4)。
"""
from __future__ import annotations

import math

from ..analysis.scoring import PILLAR_LABEL, composite, grade
from . import engine
from .schema import score_hash

DISCLAIMER = "自訂模型只顯示分數與等級,不是買賣建議;沒有經過回測驗證前一律標「未驗證」。研究用途,非投資建議。"


def json_safe(env: dict) -> dict:
    """NaN / 無限大 → None,其餘原樣(JSON 不能放 NaN)。"""
    out = {}
    for k, v in env.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            v = None
        out[k] = v
    return out


def rescore(env: dict, model: dict, market: str, validated_pillars: list[str] | None = None) -> dict:
    pillars = engine.score_pillars(model, env)
    rs = env.get("regime")
    pillars["regime"] = {"key": "regime", "label": PILLAR_LABEL["regime"], "score": rs, "grade": grade(rs),
                         "coverage": 1, "items": []}
    pw = model["pillar_weights"]
    st_w = pw["short_term_tw"] if market == "TW" else pw["short_term_us"]
    only = (lambda w: {k: v for k, v in w.items() if k in validated_pillars}) if validated_pillars else (lambda w: w)
    used = {k for k, w in (*pw["long_term"].items(), *st_w.items()) if w}
    return {
        "model": {"id": model["id"], "name": model["name"], "family_id": model["family_id"],
                  "score_hash": score_hash(model)},
        "pillars": pillars,
        "long_term": composite(pillars, pw["long_term"]),
        "short_term": composite(pillars, st_w),
        # 只用已驗證面向的分數(和網站出廠模型的長短期分數同一個口徑);上面兩個含參考面向
        "long_term_validated": composite(pillars, only(pw["long_term"])),
        "short_term_validated": composite(pillars, only(st_w)),
        "unvalidated_pillars": sorted(k for k in used if validated_pillars and k not in validated_pillars),
        "status": "未驗證",
        "disclaimer": DISCLAIMER,
    }
