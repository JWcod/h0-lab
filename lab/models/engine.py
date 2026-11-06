"""依模型檔打分:基礎變數(scoring.base_vars 算好的原始值)→ 各面向 0–100 分。規格見 docs/模型客製化規格.md §2。

輸出的 items 結構與 scoring.Pillar.add 完全相同;面向內依 metrics 順序加總,出廠模型與 M1 前的程式逐位元相同。
"""
from __future__ import annotations

import functools
import re

from ..analysis.scoring import PILLAR_LABEL, Pillar, lin  # noqa: F401  PILLAR_LABEL 給呼叫端用
from .schema import PILLARS


def _linear(x, sc):
    return lin(x, sc["bad"], sc["good"])


def _peak(x, sc):
    # 和 M1 前的 RSI 寫法相同:不經 safe(),None 才回 None
    return None if x is None else max(0.0, 100 - abs(x - sc["center"]) * sc["slope"])


def _map(x, sc):
    if x is None:
        return None
    for k, v in sc["table"].items():
        try:
            if float(k) == x:
                return v
        except (TypeError, ValueError):
            continue
    return None


def _builtin(x, sc, env: dict):
    """回傳 (分數, 備註覆寫或 None)。"""
    fn = sc["fn"]
    if fn == "mr_record":
        return (90 if env.get("mr_record_high") else 72 if env.get("mr_record_same_month") else 45), None
    if fn == "net_debt_ebitda":
        nd = env.get("net_debt")
        if nd is not None and nd < 0:
            return 100.0, "淨現金部位"
        return lin(x, sc.get("bad", 4), sc.get("good", 0)), None
    if fn == "beneish":
        if env.get("beneish_flag") is None:   # 資料缺 flag:不可得(M1 前會丟 KeyError)
            return None, None
        return (80 if not env.get("beneish_flag") else (45 if env.get("beneish_high_growth_caveat") else 20)), None
    if fn == "insider":
        bc, sv, mcap = x, env.get("insider_sell_value_6m") or 0, env.get("market_cap") or 0
        return (88 if bc >= 3 else 75 if bc >= 1 else (40 if mcap and sv > 0.003 * mcap else 52)), None
    raise ValueError(f"未知內建打分 {fn}")


_LABEL_VAR = re.compile(r"\{(\w+)\}")


def label_of(m: dict, env: dict) -> str:
    """標籤範本只代入純變數名 {name}(不用 str.format,避免 {x.__class__} 這類屬性存取)。"""
    return _LABEL_VAR.sub(lambda mo: str(env.get(mo.group(1))) if mo.group(1) in env else "", m["label"])


def _weight(m: dict, env: dict):
    w = m["weight"]
    if m.get("weight_fn") == "margin_weight":
        # 融資餘額占股本很小時(大型權值股常見),百分比變化雜訊大,降低權重
        return w * (1.0 if (env.get("margin_to_shares") or 0) >= 0.005 else 0.3)
    return w


def _applies(m: dict, env: dict) -> bool:
    a = m.get("applies", "all")
    if a == "financial" and not env.get("is_financial"):
        return False
    if a == "non_financial" and env.get("is_financial"):
        return False
    mk = m.get("market", "all")   # 籌碼分支看的是籌碼資料的市場(M1 前:chips.market == "TW",其餘走美股分支)
    if mk == "TW" and not env.get("chips_tw"):
        return False
    if mk == "US" and env.get("chips_tw"):
        return False
    req = m.get("requires")
    return not (req and not env.get(req))


@functools.lru_cache(maxsize=512)
def _compiled(text: str):
    """運算式以完整的基礎變數清單編譯(不是這一筆 env 的鍵:有些變數只在特定分支出現,缺的時候算成 None)。"""
    from ..analysis.scoring import BASE_VARS
    from .expr import compile_expr   # M3
    return compile_expr(text, set(BASE_VARS))


def value_of(m: dict, env: dict):
    v = m["value"]
    if "var" in v:
        return env.get(v["var"])
    return _compiled(v["expr"]).evaluate(env)


def score_metric(m: dict, x, env: dict):
    """回傳 (分數, 備註覆寫或 None)。gate 不成立時分數為 None(項目照樣加入)。"""
    g = m.get("gate")
    if g and not ((env.get(g["var"]) or 0) >= g["min"]):
        return None, None
    sc = m["score"]
    t = sc["type"]
    if t == "linear":
        return _linear(x, sc), None
    if t == "peak":
        return _peak(x, sc), None
    if t == "map":
        return _map(x, sc), None
    return _builtin(x, sc, env)


def score_pillars(model: dict, env: dict) -> dict:
    """各面向的結果(不含 regime);鍵的順序固定為 PILLARS。"""
    acc = {p: Pillar(p) for p in PILLARS}
    for m in model["metrics"]:
        if not m.get("enabled", True) or not _applies(m, env):
            continue
        x = value_of(m, env)
        score, note = score_metric(m, x, env)
        pb = m.get("peer_blend")
        kw = {}
        if pb:
            kw = {"peer_pct": env.get(pb["peer_var"]), "higher_better": pb.get("higher_better", True),
                  "abs_weight": pb.get("abs_weight", 0.6)}
        acc[m["pillar"]].add(m["key"], label_of(m, env), x, score,
                             _weight(m, env), fmt=m.get("fmt", "pct"), note=note if note is not None else m.get("note", ""),
                             **kw)
    return {p: acc[p].result() for p in PILLARS}
