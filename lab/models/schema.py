"""模型檔(h0-model/1)的載入、驗證與評分雜湊。規格見 docs/模型客製化規格.md §2。"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import re
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent
SCHEMA = "h0-model/1"
PILLARS = ("quality", "growth", "valuation", "health", "momentum", "flows", "revisions", "sentiment")
SCORE_TYPES = ("linear", "peak", "map", "builtin")
BUILTINS = ("mr_record", "net_debt_ebitda", "beneish", "insider")
WEIGHT_FNS = ("margin_weight",)
APPLIES = ("all", "non_financial", "financial")
MARKETS = ("all", "TW", "US")

# 影響分數的欄位(評分雜湊只看這些;名稱、說明不影響)
SCORE_FIELDS = ("pillar_weights", "short_term_thresholds")
METRIC_SCORE_FIELDS = ("key", "pillar", "applies", "market", "value", "score", "weight", "weight_fn", "peer_blend",
                       "enabled", "requires", "gate")
# model_params.json 中也會影響評分的欄位(§1 評分雜湊)
PARAM_SCORE_FIELDS = ("valuation", "method_weights", "validated_pillars", "cross_sectional_scoring",
                      "tw_industry_neutral_valuation", "tw_chips_history", "tw_st_drop_momentum", "us_quarterly_ttm",
                      "decision_rules")


class ModelError(ValueError):
    """模型檔不合法(訊息是繁體中文,可以直接顯示給使用者)。"""


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _base_vars() -> tuple[str, ...]:
    from ..analysis.scoring import BASE_VARS
    return BASE_VARS


def expr_available() -> bool:
    """運算式語言(M3,lab/models/expr.py)是否已經合併。"""
    return importlib.util.find_spec(f"{__package__}.expr") is not None


_LABEL_FIELD = re.compile(r"\{([^{}]*)\}")


def validate(model: dict) -> None:
    if model.get("schema") != SCHEMA:
        raise ModelError(f"不支援的模型格式 {model.get('schema')!r},應為 {SCHEMA}")
    for k in ("id", "family_id", "name"):
        if not isinstance(model.get(k), str) or not model[k]:
            raise ModelError(f"缺少 {k}")
    pw = model.get("pillar_weights") or {}
    for grp in ("long_term", "short_term_tw", "short_term_us"):
        ws = pw.get(grp)
        if not isinstance(ws, dict) or not ws:
            raise ModelError(f"pillar_weights.{grp} 缺少或是空的")
        for p, w in ws.items():
            if p not in PILLARS + ("regime",):
                raise ModelError(f"pillar_weights.{grp} 有未知面向 {p}")
            if not _num(w) or w < 0:
                raise ModelError(f"pillar_weights.{grp}.{p} 必須是 ≥ 0 的數字")
    th = model.get("short_term_thresholds") or {}
    if not (_num(th.get("bullish")) and _num(th.get("bearish")) and th["bearish"] < th["bullish"]):
        raise ModelError("short_term_thresholds 需要 bullish、bearish 兩個數字,且 bearish < bullish")
    seen = set()
    known = set(_base_vars())
    metrics = model.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ModelError("metrics 必須是非空的清單")
    for i, m in enumerate(metrics):
        if not isinstance(m, dict):
            raise ModelError(f"metrics[{i}] 必須是物件")
        where = f"metrics[{i}]({m.get('key')})"
        if not isinstance(m.get("key"), str) or not re.match(r"^[a-z0-9_]{1,40}$", m["key"]):
            raise ModelError(f"{where}:key 只能用小寫英文、數字、底線")
        lab = m.get("label")
        if not isinstance(lab, str) or not lab or len(lab) > 80:
            raise ModelError(f"{where}:label 必須是 1–80 字的文字")
        if lab.count("{") != lab.count("}") or any(f not in known for f in _LABEL_FIELD.findall(lab)):
            raise ModelError(f"{where}:label 只能用 {{變數名}} 代入已知的基礎變數")
        for k in ("fmt", "note"):
            if m.get(k) is not None and not isinstance(m[k], str):
                raise ModelError(f"{where}:{k} 必須是文字")
        if m.get("pillar") not in PILLARS:
            raise ModelError(f"{where}:未知面向 {m.get('pillar')}")
        if m.get("applies", "all") not in APPLIES or m.get("market", "all") not in MARKETS:
            raise ModelError(f"{where}:applies / market 不合法")
        ident = (m.get("key"), m.get("applies", "all"), m.get("market", "all"))
        if ident in seen:
            raise ModelError(f"{where}:同一個 key 在相同 applies / market 重複")
        seen.add(ident)
        v = m.get("value") or {}
        if not (isinstance(v.get("var"), str) or isinstance(v.get("expr"), str)) or ("var" in v and "expr" in v):
            raise ModelError(f"{where}:value 必須是 {{\"var\": ...}} 或 {{\"expr\": ...}} 其中一個")
        if "var" in v and v["var"] not in known:   # 上面已確認 var 是字串
            raise ModelError(f"{where}:未知的基礎變數 {v['var']}")
        if "expr" in v:
            if not expr_available():
                raise ModelError(f"{where}:自訂運算式還沒開放(運算式語言尚未合併)")
            from .expr import ExprError, compile_expr
            try:
                compile_expr(v["expr"], known)
            except ExprError as e:
                raise ModelError(f"{where}:運算式錯誤:{e}") from e
        sc = m.get("score") or {}
        t = sc.get("type")
        if t not in SCORE_TYPES:
            raise ModelError(f"{where}:未知打分類型 {t}")
        if t == "linear" and not (_num(sc.get("bad")) and _num(sc.get("good"))):
            raise ModelError(f"{where}:linear 需要 bad、good 兩個數字")
        if t == "peak" and not (_num(sc.get("center")) and _num(sc.get("slope"))):
            raise ModelError(f"{where}:peak 需要 center、slope 兩個數字")
        if t == "map" and not (isinstance(sc.get("table"), dict) and sc["table"]
                               and all(_num(x) for x in sc["table"].values()) and all(_is_num_str(k) for k in sc["table"])):
            raise ModelError(f"{where}:map 需要 table(值 → 分數)")
        if t == "builtin":
            if sc.get("fn") not in BUILTINS:
                raise ModelError(f"{where}:未知內建打分 {sc.get('fn')}")
            if "expr" in v:
                raise ModelError(f"{where}:自訂指標(expr)不能用 builtin 打分")
            if sc["fn"] == "net_debt_ebitda" and not (_num(sc.get("bad", 4)) and _num(sc.get("good", 0))):
                raise ModelError(f"{where}:net_debt_ebitda 的 bad、good 必須是數字")
        if m.get("weight_fn") is not None and m["weight_fn"] not in WEIGHT_FNS:
            raise ModelError(f"{where}:未知 weight_fn {m['weight_fn']}")
        if not _num(m.get("weight")) or m["weight"] < 0:
            raise ModelError(f"{where}:weight 必須是 ≥ 0 的數字")
        g = m.get("gate")
        if g is not None and not (isinstance(g, dict) and isinstance(g.get("var"), str) and g["var"] in known
                                  and _num(g.get("min"))):
            raise ModelError(f"{where}:gate 必須是 {{\"var\": 已知變數, \"min\": 數字}}")
        req = m.get("requires")
        if req is not None and not (isinstance(req, str) and req in known):
            raise ModelError(f"{where}:requires 必須是已知的基礎變數名")
        pb = m.get("peer_blend")
        if pb is not None and not (isinstance(pb, dict) and isinstance(pb.get("peer_var"), str) and pb["peer_var"] in known
                                   and _num(pb.get("abs_weight", 0.6)) and 0 <= pb.get("abs_weight", 0.6) <= 1
                                   and isinstance(pb.get("higher_better", True), bool)):
            raise ModelError(f"{where}:peer_blend 需要已知的 peer_var、0–1 的 abs_weight")
        if not isinstance(m.get("enabled", True), bool):
            raise ModelError(f"{where}:enabled 必須是 true / false")


def _is_num_str(k) -> bool:
    try:
        return math.isfinite(float(k))
    except (TypeError, ValueError):
        return False


def load(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        model = json.load(f)
    validate(model)
    return model


_default_cache: dict[str, dict] = {}


def load_shipped(model_id: str, readonly: bool = False) -> dict:
    """出廠模型(lab/models/<id>.json);預設回傳深拷貝,呼叫端改了也不影響快取。
    readonly=True 直接回傳快取(評分熱路徑用,呼叫端不得修改;要改請用預設的深拷貝)。"""
    if model_id not in _default_cache:
        p = MODELS_DIR / f"{model_id}.json"
        if not p.is_file():
            raise ModelError(f"找不到出廠模型 {model_id}")
        _default_cache[model_id] = load(p)
    return _default_cache[model_id] if readonly else copy.deepcopy(_default_cache[model_id])


def _norm_num(x):
    """整數值的 float 轉成 int:瀏覽器的 JSON 會把 0.0 變成 0,分數定義沒變時雜湊不能跟著變(M6 發現)。"""
    if isinstance(x, bool):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, dict):
        return {k: _norm_num(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_norm_num(v) for v in x]
    return x


def score_hash(model: dict, params: dict | None = None) -> str:
    """評分雜湊:只看影響分數的欄位(含參數檔的評分開關),數字正規化後的 JSON 的 SHA-256 前 16 碼。"""
    body = {k: model.get(k) for k in SCORE_FIELDS}
    body["metrics"] = [{k: m.get(k) for k in METRIC_SCORE_FIELDS} for m in model.get("metrics") or []]
    if params is not None:
        body["params"] = {k: params.get(k) for k in PARAM_SCORE_FIELDS}
    s = json.dumps(_norm_num(body), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
