"""S1:風格範本、訊號目錄、選擇 → 模型的換算、今天的排名(docs/上手精靈規格.md §3、§4、§8)。
全部用範本檔與合成資料;需要真實原始值快取的檢查沒有快取時 skip。"""
from __future__ import annotations

import copy
import json
import random
import re

import pytest

from lab import config
from lab.analysis import scoring as SC
from lab.models import catalog, engine, schema, wizard
from lab.research import backtest_pit as BP
from lab.research import model_eval as ME
from tests.test_model_engine import make_ctx

TEMPLATE_IDS = ["sample", "value", "quality", "growth", "momentum", "flows_tw", "dividend", "lowvol"]
# 出廠模型 JW-MF v2.0 的評分雜湊(只能經由 scripts/gate.py / holdout.py 改;改了要同步更新這裡)
SHIPPED_HASH = "84220996991ad8b6"
BUY_SELL = re.compile(r"買進|賣出|強力買進|減碼|加碼|\bbuy\b|\bsell\b", re.I)
# 訊號目錄要描述「法人買賣超」「內部人買進」這類資料本身,所以只擋建議用語
ADVICE = re.compile(r"強力買進|建議(買|賣)|(買進|賣出)(訊號|建議|評等)|應該(買|賣)|\bbuy\b|\bsell\b", re.I)


def shipped():
    return schema.load_shipped(SC.DEFAULT_MODEL)


# ---------------- 範本 ----------------
def test_eight_templates_validate_and_ordered():
    ts = wizard.raw_templates()
    assert [t["template"]["id"] for t in ts] == TEMPLATE_IDS
    for t in ts:
        schema.validate(t)
        meta = t["template"]
        for k in ("title", "for_whom", "focus", "ignores", "rationale"):
            assert isinstance(meta[k], dict) and meta[k]["zh-TW"], (meta["id"], k)
        assert meta["sources"] and all(isinstance(s, str) and s for s in meta["sources"])
        assert set(meta["markets"]) <= {"TW", "US"} and meta["markets"]
        assert "沒有證實有效" in t["notes"]
        assert not BUY_SELL.search(json.dumps(meta, ensure_ascii=False)), meta["id"]
        lt = t["pillar_weights"]["long_term"]
        assert sum(lt.values()) == 100, meta["id"]
    assert wizard.load_template("flows_tw")["template"]["markets"] == ["TW"]


def test_templates_not_listed_as_shipped_models():
    from lab.models import store
    assert store.shipped_ids() == ["jw-mf-2.0"]


def test_sample_is_shipped_model():
    s, b = wizard.load_template("sample"), shipped()
    assert schema.score_hash(b) == SHIPPED_HASH
    assert schema.score_hash(s) == SHIPPED_HASH
    assert s["metrics"] == b["metrics"] and s["pillar_weights"] == b["pillar_weights"]
    assert s["short_term_thresholds"] == b["short_term_thresholds"]


def test_template_field_does_not_affect_score_hash():
    t = wizard.load_template("value")
    h = schema.score_hash(t)
    t2 = copy.deepcopy(t)
    t2["template"]["title"] = {"zh-TW": "別的名字"}
    t2["template"]["requires_vars"] = ["x"]
    t2["template"]["pending_metrics"] = [{"key": "zz"}]
    assert schema.score_hash(t2) == h
    t2.pop("template")
    assert schema.score_hash(t2) == h
    s = copy.deepcopy(shipped())
    s["template"] = wizard.load_template("sample")["template"]
    assert schema.score_hash(s) == SHIPPED_HASH


def test_s4_templates_ready_after_merge():
    """S4 已合併:存股、低波動範本需要的變數都在 BASE_VARS,pending_metrics 併入後可選。"""
    need = {"dividend": {"dividend_yield_ttm", "div_paid_years_5y"}, "lowvol": {"vol_1y", "beta_2y"}}
    for tid, vs in need.items():
        t = wizard.load_template(tid)
        assert set(t["template"]["requires_vars"]) == vs
        assert not wizard.missing_vars(t) and wizard.is_ready(t)
        m = wizard.apply_choices(t, {})
        used = {x["value"].get("var") for x in m["metrics"] if x.get("enabled", True)}
        assert vs <= used
    summ = {s["id"]: s for s in wizard.list_templates()}
    assert summ["dividend"]["status"] == "ready" and summ["lowvol"]["status"] == "ready"
    assert summ["value"]["status"] == "ready" and not summ["value"]["missing_vars"]
    shown = [i for i in TEMPLATE_IDS if i not in wizard.HIDDEN_TEMPLATES]
    assert [s["id"] for s in wizard.list_templates("US")] == [i for i in shown if i != "flows_tw"]


def test_listed_templates_leave_builtin_fair_value_signal_off():
    """開源版不提供綜合公平價:範本預設不用它,但使用者在精靈選「普通」或「很重要」還是能打開。"""
    for tid in TEMPLATE_IDS:
        if tid in wizard.HIDDEN_TEMPLATES:
            continue
        t = wizard.load_template(tid)
        rows = [m for m in t["metrics"] if m["key"] == "fv_upside"]
        assert rows and all(m["weight"] == 0 and m.get("enabled", True) for m in rows), tid
        on = wizard.apply_choices(t, {"signals": {"fv_upside": "normal"}})
        assert all(m["weight"] == 1 for m in on["metrics"] if m["key"] == "fv_upside"), tid


def test_sample_template_hidden_from_wizard():
    """2026-11-03「自建模型優先」:綜合範本 sample(= 出廠模型)不在精靈列出、不能預覽;檔案保留給上面的對照測試。"""
    ids = [s["id"] for s in wizard.list_templates()]
    assert "sample" not in ids and len(ids) == 7
    assert wizard.load_template("sample")["template"]["id"] == "sample"
    with pytest.raises(schema.ModelError):
        wizard.preview("sample", {}, "TW", None, config.load_params())


def test_pending_metrics_merge_when_vars_exist(monkeypatch):
    """S4 加進基礎變數之後,事先寫好的 pending_metrics 自動併入(權重不變),範本變成可選。"""
    new = ("dividend_yield_ttm", "div_paid_years_5y", "vol_1y", "beta_2y")
    monkeypatch.setattr(SC, "BASE_VARS", SC.BASE_VARS + new)
    for tid in ("dividend", "lowvol"):
        raw = next(t for t in wizard.raw_templates() if t["template"]["id"] == tid)
        t = wizard.load_template(tid)
        assert wizard.is_ready(t) and "pending_metrics" not in t["template"]
        assert t["metrics"][-len(raw["template"]["pending_metrics"]):] == raw["template"]["pending_metrics"]
        m = wizard.apply_choices(t, {})
        schema.validate(m)
    assert "pending_metrics" in next(t for t in wizard.raw_templates() if t["template"]["id"] == "dividend")["template"]


def test_preparing_when_base_vars_missing(monkeypatch):
    """缺基礎變數時(例如舊版資料):範本標準備中、pending_metrics 不併入、apply_choices 報準備中(S1 Verifier 建議補回)。"""
    monkeypatch.setattr(SC, "BASE_VARS", tuple(v for v in SC.BASE_VARS if v != "vol_1y"))
    t = wizard.load_template("lowvol")
    assert wizard.missing_vars(t) == ["vol_1y"] and not wizard.is_ready(t)
    with pytest.raises(schema.ModelError, match="準備中"):
        wizard.apply_choices(t, {})
    summ = {x["id"]: x for x in wizard.list_templates()}
    assert summ["lowvol"]["status"] == "preparing" and summ["dividend"]["status"] == "ready"


# ---------------- 出廠模型不受影響 ----------------
def test_shipped_scores_unchanged_by_wizard():
    """載入範本、換算、列目錄都不得改到出廠模型(快取或檔案);sample 範本與出廠模型分數逐位元相同。"""
    before = json.dumps(schema.load_shipped(SC.DEFAULT_MODEL, readonly=True), sort_keys=True)
    wizard.list_templates()
    catalog.catalog("TW", config.load_params())
    for tid in TEMPLATE_IDS:
        t = wizard.load_template(tid)
        if wizard.is_ready(t):
            wizard.apply_choices(t, {"pillars": {"quality": "high"}, "signals": {"roic": "off"}})
    assert json.dumps(schema.load_shipped(SC.DEFAULT_MODEL, readonly=True), sort_keys=True) == before
    assert schema.score_hash(shipped()) == SHIPPED_HASH
    sample = wizard.load_template("sample")
    r = random.Random(7)
    for _ in range(25):
        ctx = make_ctx(r)
        a = SC.build(copy.deepcopy(ctx))
        b = engine.score_pillars(sample, SC.base_vars(copy.deepcopy(ctx)))
        assert {k: v.get("score") for k, v in a.items() if k != "regime"} == {k: v.get("score") for k, v in b.items()}


# ---------------- 換算規則(§4.2) ----------------
def _w(m, key):
    return [x["weight"] for x in m["metrics"] if x["key"] == key]


def test_signal_levels():
    t = wizard.load_template("value")
    m = wizard.apply_choices(t, {"signals": {"roic": "off", "fcf_yield": "normal", "ev_ebitda": "high", "roe": "很重要"}})
    assert _w(m, "roic") == [0] and _w(t, "roic") == [3]
    assert _w(m, "fcf_yield") == _w(t, "fcf_yield") == [2.5]
    assert _w(m, "ev_ebitda") == [4]
    assert _w(m, "roe") == [w * 2 for w in _w(t, "roe")] and len(_w(m, "roe")) == 2   # 金融、非金融兩個分支都改
    assert all(x.get("enabled", True) for x in m["metrics"])                          # 不看 ≠ 停用
    assert len(m["metrics"]) == len(t["metrics"])
    # 沒送的訊號照範本
    assert _w(m, "piotroski") == _w(t, "piotroski")


def test_zero_weight_signal_gets_one_or_two():
    t = wizard.load_template("value")
    t["metrics"][0]["weight"] = 0
    k = t["metrics"][0]["key"]
    assert wizard.apply_choices(t, {"signals": {k: "normal"}})["metrics"][0]["weight"] == 1
    assert wizard.apply_choices(t, {"signals": {k: "high"}})["metrics"][0]["weight"] == 2
    assert wizard.apply_choices(t, {"signals": {k: "off"}})["metrics"][0]["weight"] == 0
    assert wizard.apply_choices(t, {})["metrics"][0]["weight"] == 0                  # 沒送 = 照範本
    assert wizard.template_summary(t)["default_levels"]["signals"][k] == "off"


def test_pillar_levels_multiply_and_normalize():
    t = wizard.load_template("value")
    m = wizard.apply_choices(t, {"pillars": {"valuation": "high", "growth": "off"}})
    lt0 = t["pillar_weights"]["long_term"]
    lt = m["pillar_weights"]["long_term"]
    raw = {k: v * {"valuation": 2, "growth": 0}.get(k, 1) for k, v in lt0.items()}
    tot = sum(raw.values())
    for k in lt0:
        assert lt[k] == pytest.approx(raw[k] / tot)
    for g in wizard.GROUPS:
        assert sum(m["pillar_weights"][g].values()) == pytest.approx(1.0)
    st = m["pillar_weights"]["short_term_tw"]
    st0 = t["pillar_weights"]["short_term_tw"]
    assert st["regime"] == pytest.approx(st0["regime"] / (sum(st0.values()) + st0["valuation"]))   # regime 不乘
    # 沒送任何選擇、或全部普通:保留範本原數字(不正規化,評分雜湊不變)
    for ch in (None, {"pillars": {p: "normal" for p in schema.PILLARS}}):
        assert wizard.apply_choices(t, ch)["pillar_weights"] == t["pillar_weights"]
    # 長期全部很重要:比例不變 → 長期組保留原數字;短期組有 regime(不乘),比例改變 → 正規化
    m2 = wizard.apply_choices(t, {"pillars": {p: "high" for p in schema.PILLARS}})
    assert m2["pillar_weights"]["long_term"] == t["pillar_weights"]["long_term"]
    assert sum(m2["pillar_weights"]["short_term_tw"].values()) == pytest.approx(1.0)


def test_default_sample_keeps_shipped_hash():
    """照預設存檔的範例模型 = 出廠模型(評分雜湊相同),模型工作台的出廠模型防線才擋得住(S1 Verifier)。"""
    m = wizard.apply_choices(wizard.load_template("sample"), {})
    assert schema.score_hash(m) == SHIPPED_HASH
    params = config.load_params()
    with pytest.raises(ME.ModelEvalError, match="出廠模型"):
        ME._check_user_model(m, params)


def test_normalization_keeps_composite_scores():
    """正規化成總和 1 只改刻度,不改分數(composite 是加權平均)。"""
    t = wizard.load_template("growth")
    m = wizard.apply_choices(t, {})
    r = random.Random(3)
    for _ in range(10):
        env = SC.base_vars(make_ctx(r))
        p1, p2 = engine.score_pillars(t, env), engine.score_pillars(m, env)
        a = SC.composite(p1, t["pillar_weights"]["long_term"])["score"]
        b = SC.composite(p2, m["pillar_weights"]["long_term"])["score"]
        assert a == pytest.approx(b, abs=0.11)   # composite 四捨五入到 0.1,浮點誤差可能剛好跨過進位點


def test_all_pillars_off_is_error():
    t = wizard.load_template("value")
    with pytest.raises(schema.ModelError, match="至少要看一個面向"):
        wizard.apply_choices(t, {"pillars": {p: "off" for p in schema.PILLARS}})
    with pytest.raises(schema.ModelError, match="至少要看一個面向"):
        wizard.apply_choices(t, {"pillars": {p: "不看" for p in ("quality", "valuation", "growth", "health")}})
    # 只關掉一部分可以
    wizard.apply_choices(t, {"pillars": {"quality": "off", "growth": "off"}})


@pytest.mark.parametrize("choices, msg", [
    ({"pillars": {"nope": "high"}}, "未知面向"),
    ({"pillars": {"quality": "超級重要"}}, "重要度只能是"),
    ({"signals": {"nope": "off"}}, "沒有訊號"),
    ({"signals": {"roic": 2}}, "重要度只能是"),
    ({"signals": ["roic"]}, "必須是物件"),
    ({"signals": {"roic": ["x"]}}, "重要度只能是"),
    ({"pillars": {"quality": {"a": 1}}}, "重要度只能是"),
    ("high", "必須是物件"),
])
def test_bad_choices(choices, msg):
    with pytest.raises(schema.ModelError, match=msg):
        wizard.apply_choices(wizard.load_template("value"), choices)


def test_result_model_shape():
    t = wizard.load_template("flows_tw")
    m = wizard.apply_choices(t, {"pillars": {"momentum": "很重要"}})
    schema.validate(m)
    assert "template" not in m and m["wizard"]["template"] == "flows_tw"
    assert m["wizard"]["choices"]["pillars"] == {"momentum": "high"}
    assert m["id"] == m["family_id"] == "wizard-flows-tw"
    from lab.models.store import ID_RE
    assert ID_RE.fullmatch(m["id"])
    assert m["based_on"] == f"template:flows_tw@{schema.score_hash(t)}"
    assert schema.score_hash(m) != schema.score_hash(t)
    m2 = copy.deepcopy(m)
    m2["wizard"] = {"template": "x"}
    assert schema.score_hash(m2) == schema.score_hash(m)                                # wizard 欄位不影響雜湊
    assert not BUY_SELL.search(json.dumps({k: v for k, v in m.items() if k != "metrics"}, ensure_ascii=False))


def test_warnings():
    t = wizard.load_template("value")
    m = wizard.apply_choices(t, {"pillars": {"momentum": "high"}})
    assert any("技術動能" in w and "不影響排名" in w for w in wizard.warnings(t, m))
    qk = {x["key"] for x in t["metrics"] if x["pillar"] == "quality"}
    m = wizard.apply_choices(t, {"signals": {k: "off" for k in qk}})
    assert any("品質" in w and "算不出分數" in w for w in wizard.warnings(t, m))
    assert wizard.warnings(t, wizard.apply_choices(t, {})) == []
    # 給市場時:回測資料沒有的面向要提醒(籌碼歷史關閉時,flows_tw 的籌碼在排名裡算不到)
    params = dict(config.load_params())
    params["tw_chips_history"] = False
    f = wizard.load_template("flows_tw")
    w = wizard.warnings(f, wizard.apply_choices(f, {}), "TW", params)
    assert any("籌碼" in x and "算不到" in x for x in w)
    assert not any("算不到" in x for x in wizard.warnings(t, wizard.apply_choices(t, {}), "TW", params))


# ---------------- 訊號目錄(§4.1) ----------------
def test_catalog_covers_shipped_metrics():
    c = catalog.catalog(None, config.load_params())
    keys = [s["key"] for s in c["signals"]]
    assert len(keys) == len(set(keys))
    assert {m["key"] for m in shipped()["metrics"]} <= set(keys)
    assert set(keys) <= set(catalog.PLAIN), "每個訊號都要有白話名稱"
    dic = {d["key"] for d in __import__("lab.metric_dictionary", fromlist=["x"]).as_list()}
    for s in c["signals"]:
        assert s["plain_name"]["zh-TW"] and s["one_liner"]["zh-TW"]
        assert len(s["one_liner"]["zh-TW"]) <= 40, s["key"]
        assert s["pillar"] in schema.PILLARS and s["good_when"]["text"]["zh-TW"]
        assert s["dictionary_key"] is None or s["dictionary_key"] in dic
        assert not ADVICE.search(json.dumps(s, ensure_ascii=False)), s["key"]
    assert [p["key"] for p in c["pillars"]] == list(schema.PILLARS)
    assert [x["value"] for x in c["levels"]] == ["off", "normal", "high"]


def test_good_when_from_score():
    sig = {s["key"]: s for s in catalog.signals(None, config.load_params())}
    assert sig["roic"]["good_when"]["direction"] == "higher"
    assert sig["ev_ebitda"]["good_when"]["direction"] == "lower"
    assert sig["accruals"]["good_when"]["direction"] == "lower"
    assert sig["rsi"]["good_when"] == {"direction": "target", "target": 58, "text": {"zh-TW": "接近 58 最好"}}
    assert sig["trend"]["good_when"]["direction"] == "higher"
    assert sig["net_debt_ebitda"]["good_when"]["direction"] == "lower"
    assert sig["vol_1y"]["good_when"]["direction"] == "lower"
    assert catalog.good_when({"type": "map", "table": {"0": 50, "1": 10, "2": 80}})["direction"] == "none"


def test_catalog_markets_and_backtestable():
    params = dict(config.load_params())
    params["tw_chips_history"] = False
    tw = {s["key"]: s for s in catalog.signals("TW", params)}
    us = {s["key"]: s for s in catalog.signals("US", params)}
    assert "foreign_20d" in tw and "foreign_20d" not in us and "insider" in us and "insider" not in tw
    assert "mr_yoy3" in tw and "mr_yoy3" not in us
    assert tw["roic"]["backtestable"] and us["roic"]["backtestable"]
    assert tw["mom_12_1"]["backtestable"] and tw["mr_sur"]["backtestable"]
    for k in ("news7", "fwd_eps", "peg", "fpe_vs_peers", "breadth", "big1000_chg"):
        assert not tw[k]["backtestable"], k
    assert not us["insider"]["backtestable"] and not us["short_float"]["backtestable"]
    assert tw["q_gm_yoy"]["backtestable"] and not us["q_gm_yoy"]["backtestable"]
    assert not tw["foreign_20d"]["backtestable"]                                         # tw_chips_history 關
    params["tw_chips_history"] = True
    tw2 = {s["key"]: s for s in catalog.signals("TW", params)}
    assert tw2["foreign_20d"]["backtestable"] and not tw2["big1000_chg"]["backtestable"]
    allm = {s["key"]: s for s in catalog.signals(None, params)}
    assert allm["q_gm_yoy"]["backtestable_by_market"] == {"TW": True, "US": False}
    assert allm["q_gm_yoy"]["backtestable"] is False
    assert allm["dividend_yield"]["ready"] is True and allm["dividend_yield"]["requires_vars"] == []


def test_backtest_missing_matches_real_raw_cache():
    """真實原始值快取存在時(有完整資料的電腦):標成不能回測的變數全部是 None;能回測的至少有一筆有值。雲端沒有快取 → skip。"""
    params = config.load_params()
    checked = 0
    for market in ("TW", "US"):
        if not BP.raw_env_path(market).exists():
            continue
        env, _ = ME.load_env(market)
        seen = set()
        for e, _st in env.values():
            seen |= {k for k, v in e.items() if v is not None}
        for s in catalog.signals(market, params):
            if not s["ready"]:
                continue
            src = shipped()["metrics"] + catalog._template_pending()      # S4 的新訊號只在範本的 pending_metrics 裡
            vars_ = {m["value"]["var"] for m in src if m["key"] == s["key"] and "var" in m["value"]}
            if s["backtestable"]:
                assert vars_ & seen, (market, s["key"])
            else:
                assert not (vars_ & seen), (market, s["key"])
        checked += 1
    if not checked:
        pytest.skip("沒有回測原始值快取(bt_pit/raw_*.sqlite),雲端 clone 沒有 data/")


# ---------------- 今天的排名 ----------------
@pytest.fixture()
def world_dirs(tmp_path, monkeypatch):
    from tests.test_model_honesty import _world
    allr, params, _px = _world(seed=5)
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(BP, "OUT", out)
    monkeypatch.setattr(config, "PRIVATE_DIR", tmp_path / "priv")
    ME._env_cache.clear()
    BP.write_raw_env(allr, params)
    allr.drop(columns=["_env", "market"]).to_csv(out / "rows_tw_pit50.csv", index=False)
    yield allr, params
    ME._env_cache.clear()


def test_score_rows_matches_rescore_rows(world_dirs):
    """開發集的某一天:精靈的打分和 model_eval.rescore_rows 逐筆相同(同一條路)。"""
    allr, params = world_dirs
    m = wizard.apply_choices(wizard.load_template("quality"), {"pillars": {"health": "high"}})
    rows = ME._load_rows("tw_pit50")
    d0 = "2020-06-30"
    sub = rows[rows["date"] == d0]
    assert len(sub)
    mine = {r["symbol"]: r for r in wizard.score_rows(m, sub, "TW", params)}
    ref = ME.rescore_rows(sub, m, "TW", params)
    for _, r in ref.iterrows():
        a, b = mine[r["symbol"]]["lt_score"], r["lt_score"]
        assert (a is None and b != b) or a == b
        a, b = mine[r["symbol"]]["st_score"], r["st_score"]
        assert (a is None and b != b) or a == b


def test_today_ranking(world_dirs):
    allr, params = world_dirs
    m = wizard.apply_choices(wizard.load_template("value"), {})
    r = wizard.today_ranking(m, "tw_pit50", params)
    last = max(allr["date"])
    assert r["as_of"] == last and r["market"] == "TW" and r["universe"] == "tw_pit50"
    assert r["n"] == r["n_universe"] == 24
    assert len(r["top"]) == 10 and len(r["bottom"]) == 10
    assert [x["rank"] for x in r["top"]] == list(range(1, 11)) and r["bottom"][-1]["rank"] == 24
    scores = [x["lt_score"] for x in r["top"] + r["bottom"]]
    assert scores == sorted(scores, reverse=True)
    assert all(x["grade"] in {g for _, g in SC.GRADES} | {"F"} for x in r["top"])
    assert "非投資建議" in r["disclaimer"] and last in r["note"]
    assert not BUY_SELL.search(json.dumps(r, ensure_ascii=False))
    assert all("name" in x and x["name"] is None for x in r["top"] + r["bottom"])         # 合成代號:找不到名稱給 null
    # 和逐筆打分一致
    env, _ = ME.load_env("TW")
    sym = r["top"][0]["symbol"]
    e = env[(last, sym)][0]
    lt = SC.composite(wizard.engine_pillars(m, e), m["pillar_weights"]["long_term"])["score"]
    assert lt == r["top"][0]["lt_score"]
    # 換成別的範本,排名跟著變
    r2 = wizard.today_ranking(wizard.apply_choices(wizard.load_template("momentum"), {}), "tw_pit50", params)
    assert [x["symbol"] for x in r2["top"]] != [x["symbol"] for x in r["top"]]


def test_today_ranking_errors(world_dirs, tmp_path):
    allr, params = world_dirs
    m = wizard.apply_choices(wizard.load_template("value"), {})
    with pytest.raises(ME.ModelEvalError, match="沒有這個股票池"):
        wizard.today_ranking(m, "nope", params)
    with pytest.raises(ME.ModelEvalError, match="沒有 rows_us_large40"):
        wizard.today_ranking(m, "us_large40", params)
    rows = ME._load_rows("tw_pit50")
    rows.loc[rows["date"] == rows["date"].max(), "st_score"] += 1                         # 和快取對不上
    rows.to_csv(BP.OUT / "rows_tw_pit50.csv", index=False)
    with pytest.raises(ME.ModelEvalError, match="對不上"):
        wizard.today_ranking(m, "tw_pit50", params)


def test_score_rows_refuses_cross_sectional_cache(world_dirs):
    allr, params = world_dirs
    p2 = dict(params)
    p2["cross_sectional_scoring"] = True
    BP.write_raw_env(allr, p2)                                                        # 快取 meta 記橫斷面開著
    ME._env_cache.clear()
    m = wizard.apply_choices(wizard.load_template("value"), {})
    with pytest.raises(ME.ModelEvalError, match="橫斷面"):
        wizard.today_ranking(m, "tw_pit50", params)


def test_preview(world_dirs):
    allr, params = world_dirs
    p = wizard.preview("value", {"pillars": {"growth": "off"}}, "TW", None, params)
    assert p["ok"] and p["universe"] == "tw_pit50" and p["ranking"]["n"] == 24 and p["score_hash"]
    p = wizard.preview("value", {"pillars": {q: "off" for q in schema.PILLARS}}, "TW", None, params)
    assert p["ok"] is False and p["error"] == "至少要看一個面向" and "ranking" not in p and "model" not in p
    p = wizard.preview("dividend", {}, "TW", None, params)
    assert p["ok"] and p["ranking"]["n"] == 24                                          # S4 合併後可選
    p = wizard.preview("value", {}, "US", None, params)
    assert p["ok"] and "rows_us_large40" in p["ranking_error"]
    with pytest.raises(ValueError, match="只能用在台股"):
        wizard.preview("flows_tw", {}, "US", None, params)
    with pytest.raises(ValueError, match="不是台股的股票池"):
        wizard.preview("value", {}, "TW", "us_large40", params)
    with pytest.raises(schema.ModelError, match="找不到範本"):
        wizard.preview("../jw-mf-2.0", {}, "TW", None, params)
    assert not (config.PRIVATE_DIR / "models").exists()                                # 不存檔、不計次


def test_frontend_default_choices_keep_template_hash():
    """精靈前端的預設選擇(面向全部普通;範本有的訊號,權重 > 0 普通、= 0 不看)換算後評分雜湊要等於範本原樣。
    S2 Verifier 抓到:預設把長期占比 0 的面向送「不看」會偷改短期權重;送範本沒有的訊號會報錯。"""
    for tid in TEMPLATE_IDS:
        t = wizard.load_template(tid)
        for mk in t["template"]["markets"]:
            base = wizard.apply_choices(t, {})
            sig = {}
            for m in base["metrics"]:
                if m.get("market") in (None, "all", mk):
                    sig[m["key"]] = "normal" if (m.get("weight") or 0) > 0 or sig.get(m["key"]) == "normal" else "off"
            ch = {"pillars": {p: "normal" for p in schema.PILLARS}, "signals": sig}
            assert schema.score_hash(wizard.apply_choices(t, ch)) == schema.score_hash(base), (tid, mk)


# ---------------- 存檔自動代號、排名公司名稱 ----------------
def _put_twse(day: str, rows: list[tuple[str, str]]):
    from lab import cache
    cache._write(cache._path("twse_daily_all", day.replace("-", "")),
                 {"fields": ["證券代號", "證券名稱", "成交股數"], "data": [[c, n, "1"] for c, n in rows]})


@pytest.fixture()
def names_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    wizard._tw_names_memo.clear()
    yield
    wizard._tw_names_memo.clear()


def test_stock_names_static_and_missing(names_cache):
    """沒有證交所快取:台股、美股都退回 lab/universe.py 的清單;清單沒有的給 None。不連網。"""
    assert wizard.stock_names(["2330.TW", "5274.TWO", "9999.TW"], "TW", "2026-09-30") == {
        "2330.TW": "台積電", "5274.TWO": "信驊", "9999.TW": None}
    assert wizard.stock_names(["AAPL", "BRK-B", "ZZZZ"], "US", "2026-09-30") == {
        "AAPL": "Apple", "BRK-B": "Berkshire", "ZZZZ": None}


def test_stock_names_from_twse_cache(names_cache):
    """證交所每日行情快取優先(判斷日當時的簡稱);只往前找 14 天,不用判斷日以後的檔。"""
    _put_twse("2026-10-01", [("9999", "以後的名字")])                                   # 判斷日之後:不用
    assert wizard.stock_names(["9999.TW"], "TW", "2026-09-30") == {"9999.TW": None}
    _put_twse("2026-09-10", [("9999", "太舊")])                                          # 20 天前:不用
    assert wizard.stock_names(["9999.TW"], "TW", "2026-09-30") == {"9999.TW": None}
    _put_twse("2026-09-28", [("9999", "測試股"), ("2330", "台積電新名")])
    got = wizard.stock_names(["9999.TW", "2330.TW", "8888.TW", "AAPL"], "TW", "2026-09-30")
    assert got == {"9999.TW": "測試股", "2330.TW": "台積電新名", "8888.TW": None, "AAPL": None}


def test_today_ranking_names(world_dirs, names_cache):
    allr, params = world_dirs
    m = wizard.apply_choices(wizard.load_template("value"), {})
    r = wizard.today_ranking(m, "tw_pit50", params)
    first, last_ = r["top"][0]["symbol"], r["bottom"][-1]["symbol"]
    _put_twse(r["as_of"], [(first.split(".")[0], "第一名公司")])
    wizard._tw_names_memo.clear()
    r = wizard.today_ranking(m, "tw_pit50", params)
    assert r["top"][0]["name"] == "第一名公司"
    assert next(x for x in r["bottom"] if x["symbol"] == last_)["name"] is None


def test_new_id_format_and_unique(tmp_path, monkeypatch):
    from lab.models import store
    monkeypatch.setattr(config, "PRIVATE_DIR", tmp_path / "priv")
    ids = {store.new_id("flows-tw") for _ in range(200)}
    assert len(ids) == 200
    assert all(store.ID_RE.fullmatch(i) and re.fullmatch(r"flows-tw-\d{8}-[0-9a-f]{6}", i) for i in ids)
    for pre in ("Value_Plus!!", "-x-", "", "很重要", "a" * 80, "jw-mf-2.0"):
        i = store.new_id(pre)
        assert store.ID_RE.fullmatch(i) and len(i) <= 48, (pre, i)
    # 撞號(現有、封存、出廠)都會換下一個亂數
    d = store.models_dir()
    (d / "_archive").mkdir(parents=True)
    today = __import__("datetime").date.today().strftime("%Y%m%d")
    (d / f"value-{today}-aaaaaa.json").write_text("{}")
    (d / "_archive" / f"value-{today}-bbbbbb.20261102120000.json").write_text("{}")
    seq = iter(["aaaaaa", "bbbbbb", "cccccc"])
    monkeypatch.setattr(store.secrets, "token_hex", lambda n: next(seq))
    assert store.new_id("value") == f"value-{today}-cccccc"
    monkeypatch.setattr(store.secrets, "token_hex", lambda n: "aaaaaa")
    with pytest.raises(schema.ModelError, match="自動產生模型代號失敗"):
        store.new_id("value")


def test_save_new_auto_and_create_only(tmp_path, monkeypatch):
    from lab.models import store
    monkeypatch.setattr(config, "PRIVATE_DIR", tmp_path / "priv")
    m = wizard.apply_choices(wizard.load_template("value"), {})
    a = store.save_new({**m, "id": "ignored", "name": "我的價值"}, auto_prefix="value")
    b = store.save_new({**m, "name": "我的價值"}, auto_prefix="value")
    assert a["id"] != b["id"] and a["id"].startswith("value-") and a["family_id"] == a["id"]
    assert not (store.models_dir() / "ignored.json").exists()
    assert store.get(a["id"])["name"] == "我的價值"
    c = store.save_new({**m, "id": "my-value", "name": "x"})
    assert c["id"] == "my-value"
    with pytest.raises(schema.ModelError, match="已經有人用了"):
        store.save_new({**m, "id": "my-value", "name": "y"})
    store.archive("my-value")
    with pytest.raises(schema.ModelError, match="已經有人用了"):                          # 封存過的代號也不能重用
        store.save_new({**m, "id": "my-value", "name": "y"})
    with pytest.raises(schema.ModelError, match="不能覆寫出廠模型"):
        store.save_new({**m, "id": SC.DEFAULT_MODEL, "name": "y"})
    with pytest.raises(schema.ModelError, match="小寫英文"):
        store.save_new({**m, "id": "Bad ID", "name": "y"})
    assert not list(store.models_dir().glob("*.tmp"))


def test_default_name():
    assert wizard.default_name("value") == "我的" + wizard.load_template("value")["template"]["title"][catalog.LANG]
    assert wizard.default_name("nope") == "我的模型"
    assert wizard.default_name("flows_tw") == "我的籌碼" and wizard.default_name("sample") == "我的範例模型"
