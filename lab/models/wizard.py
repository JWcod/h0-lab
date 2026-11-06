"""上手精靈:範本 + 使用者的選擇 → 模型;用原始值快取最新判斷日的資料算今天的股票池排名。

規格 docs/上手精靈規格.md §3、§4.2、§8。換算規則只在這裡(前端只送選擇),精靈與之後的 AI 助手共用。
只輸出分數、等級、排名,不輸出任何買賣字眼(模型客製化規格 §4.4)。研究用途,非投資建議。
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re

from ..analysis import scoring as SC
from . import catalog
from .schema import MODELS_DIR, PILLARS, ModelError, score_hash, validate

TEMPLATES_DIR = MODELS_DIR / "templates"
MULT = {"off": 0, "normal": 1, "high": 2}
_LEVEL_ALIAS = {"不看": "off", "普通": "normal", "很重要": "high"}
TEMPLATE_NOTE = "因子範本只是起點,不預設、不推薦,沒有證實有效"
DISCLAIMER = "只顯示分數、等級與排名,不是買賣建議。研究用途,非投資建議。"
GROUPS = ("long_term", "short_term_tw", "short_term_us")
DEFAULT_UNIVERSE = {"TW": "tw_pit50", "US": "us_large40"}
TOP_N = 10
# 不在精靈列出的範本(2026-11-03「自建模型優先」):sample.json 是內建研究基準模型(JW-MF v2.0)的範本版,
# 檔案留著給 tests/test_wizard.py 驗證「範本 = 出廠模型」與既有存檔的 wizard.template 追溯;精靈只列 7 個因子範本。
HIDDEN_TEMPLATES = frozenset({"sample"})


# ---------------- 範本 ----------------
_raw_cache: dict = {}


def raw_templates() -> list[dict]:
    """範本檔原樣(依 order 排序;同一組檔案只讀一次)。呼叫端不得修改,要改請用 load_template。"""
    files = sorted(TEMPLATES_DIR.glob("*.json"))
    key = tuple((p.name, p.stat().st_mtime_ns) for p in files)
    if key not in _raw_cache:
        out = []
        for p in files:
            t = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(t.get("template"), dict) or t["template"].get("id") != p.stem:
                raise ModelError(f"範本 {p.name} 的 template.id 必須和檔名相同")
            validate(t)
            out.append(t)
        _raw_cache.clear()
        _raw_cache[key] = sorted(out, key=lambda t: (t["template"].get("order", 99), t["template"]["id"]))
    return _raw_cache[key]


def missing_vars(t: dict) -> list[str]:
    return [v for v in t["template"].get("requires_vars") or [] if v not in SC.BASE_VARS]


def is_ready(t: dict) -> bool:
    return not missing_vars(t)


def load_template(template_id: str) -> dict:
    """深拷貝。基礎變數都齊了(S4 完成)時,把事先寫好的 pending_metrics 併進 metrics(權重在範本檔裡已經固定)。"""
    for t in raw_templates():
        if t["template"]["id"] == template_id:
            t = copy.deepcopy(t)
            if is_ready(t) and t["template"].get("pending_metrics"):
                t["metrics"] = t["metrics"] + t["template"].pop("pending_metrics")
                validate(t)
            return t
    raise ModelError(f"找不到範本 {template_id}")


def _share(ws: dict, p: str) -> float:
    tot = sum(v for k, v in ws.items() if k != "regime")
    return round(ws.get(p, 0) / tot, 4) if tot else 0.0


def template_summary(t: dict) -> dict:
    meta = t["template"]
    miss = missing_vars(t)
    lt = t["pillar_weights"]["long_term"]
    return {
        "id": meta["id"], "order": meta.get("order"), "markets": meta.get("markets") or ["TW", "US"],
        **{k: meta.get(k) for k in ("title", "for_whom", "focus", "ignores", "rationale", "sources")},
        "status": "preparing" if miss else "ready",
        "status_label": catalog.tr("準備中" if miss else "可以選"),
        "missing_vars": miss, "notice": catalog.tr(TEMPLATE_NOTE),
        "score_hash": score_hash(load_template(meta["id"])),
        "long_term_share": {p: _share(lt, p) for p in PILLARS if p in lt},
        "default_levels": {
            "pillars": {p: "normal" for p in PILLARS},
            "signals": {m["key"]: "normal" if m["weight"] > 0 else "off" for m in t["metrics"]},
        },
    }


def list_templates(market: str | None = None) -> list[dict]:
    return [template_summary(t) for t in raw_templates() if t["template"]["id"] not in HIDDEN_TEMPLATES
            and (not market or market in (t["template"].get("markets") or ["TW", "US"]))]


_PAREN_TAIL = re.compile(r"\s*[((][^()()]*[))]\s*$")


def default_name(template_id: str) -> str:
    """精靈存檔沒填名稱時用的名稱:「我的 <範本名稱>」(去掉結尾括號說明,例如「(只有台股)」);找不到範本就用「我的模型」。"""
    for t in raw_templates():
        if t["template"]["id"] == template_id:
            title = (t["template"].get("title") or {}).get(catalog.LANG) or template_id
            return f"我的{_PAREN_TAIL.sub('', title) or title}"
    return "我的模型"


# ---------------- 選擇 → 模型(§4.2) ----------------
def _level(v, where: str) -> str:
    if not isinstance(v, str):
        raise ModelError(f"{where} 的重要度只能是「不看」「普通」「很重要」")
    v = _LEVEL_ALIAS.get(v, v)
    if v not in MULT:
        raise ModelError(f"{where} 的重要度只能是「不看」「普通」「很重要」")
    return v


def _norm_choices(t: dict, choices) -> dict:
    if choices is None:
        choices = {}
    if not isinstance(choices, dict):
        raise ModelError("choices 必須是物件")
    ps, ss = choices.get("pillars") or {}, choices.get("signals") or {}
    if not isinstance(ps, dict) or not isinstance(ss, dict):
        raise ModelError("choices.pillars、choices.signals 必須是物件")
    keys = {m["key"] for m in t["metrics"]}
    out = {"pillars": {}, "signals": {}}
    for p, v in ps.items():
        if p not in PILLARS:
            raise ModelError(f"未知面向 {p}")
        out["pillars"][p] = _level(v, f"面向「{SC.PILLAR_LABEL[p]}」")
    for k, v in ss.items():
        if k not in keys:
            raise ModelError(f"這個範本沒有訊號 {k}")
        out["signals"][k] = _level(v, f"訊號 {k}")
    return out


def _signal_weight(w, level: str):
    if level == "off":
        return 0
    if not w:   # 範本原權重為 0:普通給 1、很重要給 2
        return MULT[level]
    return w * MULT[level]


def apply_choices(template: dict, choices: dict | None) -> dict:
    """範本 + 選擇 → 模型(驗證過;不存檔)。

    - 訊號:不看 = 權重 0(保留在清單,enabled 不動);普通 = 範本原權重;很重要 = 原權重 × 2;
      範本原權重為 0 的訊號,普通給 1、很重要給 2。沒有送的訊號 = 照範本。
    - 面向:不看 / 普通 / 很重要 = 該面向在 pillar_weights 三組(長期、台股短期、美股短期)內乘 0 / 1 / 2,
      再把每一組重新正規化成總和 1(大盤環境 regime 不是面向,不乘,但一起正規化)。沒有送的面向 = 普通。
    - 全部面向都不看 → ModelError「至少要看一個面向」;長期分數用到的面向都被設成不看 → 也是錯誤(排名看長期分數)。
    回傳的模型沒有 template 欄位,多一個 wizard 欄位記範本與選擇(不影響評分雜湊)。"""
    meta = template.get("template") or {}
    if not is_ready(template):
        raise ModelError(f"「{meta.get('title', {}).get(catalog.LANG, meta.get('id'))}」範本還在準備中"
                         f"(缺少基礎變數:{'、'.join(missing_vars(template))})")
    ch = _norm_choices(template, choices)
    m = copy.deepcopy(template)
    m.pop("template", None)
    for x in m["metrics"]:
        lv = ch["signals"].get(x["key"])
        if lv is not None:
            x["weight"] = _signal_weight(x["weight"], lv)
    ps = ch["pillars"]
    if ps and all(ps.get(p) == "off" for p in PILLARS):
        raise ModelError("至少要看一個面向")
    pw = m["pillar_weights"]
    for g in GROUPS:
        mult = {k: MULT[ps.get(k, "normal")] for k, v in pw[g].items() if k != "regime" and v}
        ws = {k: (v if k == "regime" else v * MULT[ps.get(k, "normal")]) for k, v in pw[g].items()}
        if g == "long_term" and sum(v for k, v in ws.items() if k != "regime") <= 0:
            raise ModelError("至少要看一個面向:這個範本長期分數用到的面向都被設成「不看」")
        if len(set(mult.values())) <= 1 and (not mult or next(iter(mult.values())) == 1 or "regime" not in pw[g]):
            # 比例沒變(全部普通、或長期組全部很重要):保留範本原數字。正規化只改刻度不改分數,但會改評分雜湊,
            # 照預設存檔的「範例模型」就會被當成和出廠模型不同,繞過模型工作台的出廠模型防線(S1 Verifier)
            continue
        tot = sum(ws.values())   # 短期組只剩大盤環境時照樣正規化(短期分數 = 大盤環境)
        pw[g] = {k: (v / tot if tot else 0) for k, v in ws.items()}
    tid = meta.get("id", "custom")
    title = (meta.get("title") or {}).get(catalog.LANG, tid)
    m.update({"id": f"wizard-{tid.replace('_', '-')}", "family_id": f"wizard-{tid.replace('_', '-')}",
              "name": f"{title}(精靈)", "based_on": f"template:{tid}@{score_hash(template)}",
              "created": dt.date.today().isoformat(), "notes": f"由上手精靈從範本「{title}」建立。{TEMPLATE_NOTE}。",
              "wizard": {"template": tid, "choices": ch}})
    validate(m)
    return m


def warnings(template: dict, model: dict, market: str | None = None, params: dict | None = None) -> list[str]:
    """不會讓換算失敗、但使用者可能想不到的情況(白話,直接顯示)。給 market 時,另外提醒今天的排名
    (讀回測原始值快取)算不到的面向:分析師、新聞、籌碼等回測沒有的資料,在排名裡也算不到。"""
    out = []
    lt0 = template["pillar_weights"]["long_term"]
    ch = (model.get("wizard") or {}).get("choices") or {}
    for p, lv in (ch.get("pillars") or {}).items():
        if lv == "high" and not lt0.get(p):
            out.append(f"這個範本的長期分數沒有用到「{SC.PILLAR_LABEL[p]}」,選「很重要」只影響短期分數,不影響排名")
    lt = model["pillar_weights"]["long_term"]
    for p in PILLARS:
        if not lt.get(p):
            continue
        rows = [x for x in model["metrics"] if x["pillar"] == p and x.get("enabled", True)]
        if rows and all(not x["weight"] for x in rows):
            out.append(f"「{SC.PILLAR_LABEL[p]}」的訊號都設成「不看」,這個面向算不出分數,長期分數只看其他面向")
            continue
        if market and rows:
            bt = {s["key"]: s["backtestable"] for s in catalog.signals(market, params)}
            used = [x for x in rows if x["weight"] and x.get("market", "all") in ("all", market)]
            if used and not any(bt.get(x["key"]) for x in used):
                out.append(f"「{SC.PILLAR_LABEL[p]}」的訊號在回測資料裡都沒有,今天的排名和過去表現都算不到這個面向,"
                           "長期分數實際上只看其他面向")
    return out


# ---------------- 今天的排名 ----------------
def _num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


def score_rows(model: dict, rows, market: str, params: dict) -> list[dict]:
    """rows(某股票池 rows_*.csv 的子集)逐筆用模型打分。打分和 model_eval.rescore_rows 同一條路
    (同一份原始值快取、同一個 engine、同樣的權重與 P3-H01 開關處理),但不做 holdout 遮罩:
    這裡只回傳判斷日當天的分數,不碰任何判斷日以後的股價或報酬,所以不是看 holdout 成績。"""
    from ..research import model_eval as ME
    if params.get("cross_sectional_scoring") or params.get("tw_industry_neutral_valuation"):
        raise ME.ModelEvalError("參數開了橫斷面打分(cross_sectional_scoring / tw_industry_neutral_valuation),快取重算不等價")
    env, meta = ME.load_env(market)
    if meta.get("cross_sectional_scoring") == "True" or meta.get("tw_industry_neutral_valuation") == "True":
        raise ME.ModelEvalError("這份快取是在橫斷面打分開著時產生的,重算不等價;請用目前的參數重跑 judge")
    from .. import config
    if meta.get("data_dir") and meta["data_dir"] != config.DATA_DIR.name:
        raise ME.ModelEvalError(f"快取是用資料夾 {meta['data_dir']} 產生的,現在用的是 {config.DATA_DIR.name};請重跑 judge")
    from . import store
    shipped = score_hash(model, params) in {score_hash(store.load_shipped(i, readonly=True), params)
                                             for i in store.shipped_ids()}
    p = ME._params_for(model, params, shipped)
    lt_w = p["pillar_weights"]["long_term"]
    st_w = SC.short_term_weights(p, market)
    out, bad = [], 0
    for d, s, st0 in zip(rows["date"].astype(str), rows["symbol"], rows["st_score"]):
        hit = env.get((d[:10], s))
        if hit is None or not ME._same(hit[1], st0):
            bad += 1
            continue
        e = hit[0]
        pil = engine_pillars(model, e)
        lt = SC.composite(pil, lt_w)
        st = SC.composite(pil, st_w)
        out.append({"date": d[:10], "symbol": s, "lt_score": _num(lt.get("score")), "grade": lt.get("grade", "—"),
                    "st_score": _num(st.get("score")),
                    "pillars": {k: _num(v.get("score")) for k, v in pil.items()}})
    if bad:
        raise ME.ModelEvalError(f"快取有 {bad} 筆判斷對不上逐筆檔:快取和逐筆檔不是同一次 judge 產生的,請重跑 judge")
    return out


def engine_pillars(model: dict, env: dict) -> dict:
    from . import engine
    pil = engine.score_pillars(model, env)
    pil["regime"] = {"score": env.get("regime")}
    return pil


_tw_names_memo: dict = {}


def _tw_daily_names(as_of: str) -> dict[str, str]:
    """證交所每日行情快取裡的「代號 → 簡稱」(判斷日當天,沒有就往前找 14 天)。只讀磁碟快取、絕不連網:
    快取沒有就回空的(名稱缺值只影響顯示)。"""
    from .. import config
    from ..cache import CacheMiss, cached
    key = (str(config.CACHE_DIR), as_of)
    hit = _tw_names_memo.get(key)   # 只讀一次 dict:別的執行緒可能同時 clear()(Verifier)
    if hit is None:
        def no_fetch():
            raise CacheMiss("只讀快取")
        out: dict[str, str] = {}
        d0 = dt.date.fromisoformat(as_of)
        for k in range(15):
            try:
                raw = cached("twse_daily_all", f"{d0 - dt.timedelta(days=k):%Y%m%d}", 10 ** 12, no_fetch)
            except Exception:  # noqa: BLE001  快取沒有或壞檔都當作沒有
                continue
            try:
                f = raw["fields"]
                ic, iname = f.index("證券代號"), f.index("證券名稱")
                out = {str(r[ic]).strip(): str(r[iname]).strip() for r in raw["data"] if str(r[iname]).strip()}
            except (TypeError, KeyError, ValueError, IndexError):
                continue
            if out:
                break
        if not out:   # 沒找到不記住:之後補了快取就讀得到
            return {}
        _tw_names_memo.clear()   # 只留最近一個判斷日
        _tw_names_memo[key] = hit = out
    return hit


def stock_names(symbols, market: str, as_of: str) -> dict[str, str | None]:
    """代號 → 公司名稱(台股中文簡稱、美股公司名)。來源只用專案裡現成的:台股先找證交所每日行情快取(判斷日當時的簡稱),
    再找 lab/universe.py 的權值股清單;美股用 lab/universe.py 的大型股清單。都找不到給 None(前端只顯示代號)。不連網。"""
    from .. import universe as UV
    static = dict(UV.TW_LARGE if market == "TW" else UV.US_LARGE)
    daily = _tw_daily_names(as_of) if market == "TW" else {}
    return {s: daily.get(s.split(".")[0]) or static.get(s) for s in symbols}


def today_ranking(model: dict, universe: str, params: dict | None = None, top: int = TOP_N) -> dict:
    """股票池在原始值快取最新判斷日的排名(依長期分數;同分依代號)。前 top 名與後 top 名,附分數與等級。"""
    from ..config import load_params
    from ..research import backtest_pit as BP
    from ..research import model_eval as ME
    params = params if params is not None else load_params()
    rows = ME._load_rows(universe)
    market = BP.UNIVERSES[universe]
    if not len(rows):
        raise ME.ModelEvalError(f"{universe} 沒有任何判斷")
    d = str(rows["date"].astype(str).str[:10].max())
    sub = rows[rows["date"].astype(str).str[:10] == d]
    scored = [r for r in score_rows(model, sub, market, params) if r["lt_score"] is not None]
    scored.sort(key=lambda r: (-r["lt_score"], r["symbol"]))
    for i, r in enumerate(scored, 1):
        r["rank"] = i
    top = max(1, min(int(top), 50))
    shown = scored[:top] + (scored[-top:] if len(scored) > top else [])
    names = stock_names([r["symbol"] for r in shown], market, d)
    for r in shown:
        r["name"] = names.get(r["symbol"])
    return {"universe": universe, "market": market, "as_of": d, "n": len(scored), "n_universe": int(len(sub)),
            "top": scored[:top], "bottom": scored[-top:] if len(scored) > top else [],
            "note": f"資料日期 {d}(回測原始值快取的最新判斷日),依長期分數排名;和回測同一條打分路徑",
            "disclaimer": DISCLAIMER}


def default_universe(market: str) -> str:
    """精靈的預設股票池:正式股票池有回測資料就用它;沒有(新使用者用 scripts/setup_research.py 建的精簡版)
    就用同市場第一個有資料的股票池;都沒有回正式股票池(讓錯誤訊息與引導卡照常出現)。"""
    from ..research import backtest_pit as BP
    d = DEFAULT_UNIVERSE[market]
    if (BP.OUT / f"rows_{d}.csv").exists():
        return d
    alt = [u for u, m in BP.UNIVERSES.items() if m == market and u != d and (BP.OUT / f"rows_{u}.csv").exists()]
    return alt[0] if alt else d


def preview(template_id: str, choices: dict | None, market: str, universe: str | None = None,
            params: dict | None = None, top: int = TOP_N) -> dict:
    """精靈第 ③ 步:換算、驗證、今天的排名。不存檔、不計入「試了幾次」。
    換算錯誤回 ok = False(和 /api/models/validate 一樣);排名算不出來時回 ranking_error,不影響換算結果。"""
    from ..research import backtest_pit as BP
    from ..research import model_eval as ME
    if template_id in HIDDEN_TEMPLATES:
        raise ModelError(f"找不到範本 {template_id}")
    t = load_template(template_id)
    mk = t["template"].get("markets") or ["TW", "US"]
    if market not in mk:
        raise ValueError(f"「{t['template']['title'][catalog.LANG]}」範本只能用在{'、'.join('台股' if x == 'TW' else '美股' for x in mk)}")
    universe = universe or default_universe(market)
    if BP.UNIVERSES.get(universe) != market:
        raise ValueError(f"股票池 {universe} 不是{'台股' if market == 'TW' else '美股'}的股票池")
    out = {"template": template_id, "market": market, "universe": universe, "disclaimer": DISCLAIMER,
           "notice": TEMPLATE_NOTE}
    try:
        m = apply_choices(t, choices)
    except ModelError as e:
        return out | {"ok": False, "error": str(e)}
    out |= {"ok": True, "model": m, "score_hash": score_hash(m), "warnings": warnings(t, m, market, params)}
    try:
        out["ranking"] = today_ranking(m, universe, params, top)
        if not out["ranking"].get("n"):
            out["ranking_error"] = "長期分數算不出來:看的訊號在這個股票池都沒有資料,或長期面向的訊號全部設成不看"
    except ME.ModelEvalError as e:
        out["ranking_error"] = str(e)
    return out
