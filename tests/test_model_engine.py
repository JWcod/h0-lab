"""M1:出廠模型 + 評分引擎必須和 M1 前的 scoring.build 逐位元相同(tests/legacy/scoring_v2_0.py 是凍結對照組)。

用固定種子隨機產生 ctx,涵蓋所有分支(金融 / 非金融、台股 / 美股籌碼、技術面有無、月營收、beneish、內部人、
淨現金、同業、新聞門檻)與邊界值(None、NaN、0、負數)。真實資料的等價由 slow 基準回歸(v1.1 逐位元重現)把關。
"""
from __future__ import annotations

import copy
import json
import math
import random

import pytest

from lab.analysis import scoring as SC
from lab.models import engine, schema
from tests.legacy import scoring_v2_0 as LEGACY

N_CASES = 3000


def _num(r: random.Random, lo=-1.0, hi=1.0):
    u = r.random()
    if u < 0.12:
        return None
    if u < 0.15:
        return float("nan")
    if u < 0.20:
        return 0.0
    if u < 0.23:
        return 0
    if u < 0.30:
        return round(r.uniform(lo, hi), 2)   # 容易剛好落在門檻上
    return r.uniform(lo, hi)


def _maybe(r: random.Random, d: dict, p=0.85):
    return d if r.random() < p else {}


def make_ctx(r: random.Random) -> dict:
    fin = r.random() < 0.25
    peers = [] if r.random() < 0.3 else [
        {k: _num(r, -0.2, 1.0) for k in ("roe", "gross_margin", "operating_margin", "revenue_growth")}
        | {"ev_ebitda": _num(r, 0, 40), "forward_pe": _num(r, -10, 250)} for _ in range(r.randint(0, 8))]
    tech_ok = r.random() < 0.8
    tw = r.random() < 0.5
    ctx = {
        "fund": {
            "profitability": _maybe(r, {k: _num(r, -0.2, 0.6) for k in
                                        ("roic", "roe", "gp_assets", "gross_margin", "operating_margin", "cfo_to_ni",
                                         "accruals", "roa")}),
            "growth": _maybe(r, {**{k: _num(r, -0.3, 0.5) for k in
                                    ("revenue_yoy", "revenue_cagr_3y", "eps_yoy", "eps_cagr_3y", "ni_cagr_3y",
                                     "q_gm_change_yoy")},
                                 **({"yoy_basis": r.choice(["近四季", "年報", None])} if r.random() < 0.8 else {})}),
            "health": _maybe(r, {"altman_z": _num(r, 0, 5), "net_debt_ebitda": _num(r, -2, 6),
                                 "net_debt": _num(r, -1e9, 1e9), "interest_coverage": _num(r, 0, 30),
                                 "current_ratio": _num(r, 0, 3), "debt_to_equity": _num(r, 0, 3),
                                 "liabilities_to_assets": _num(r, 0.5, 1.0)}),
            "stability": _maybe(r, {"gm_std_10y": _num(r, 0, 0.1), "loss_years_10y": r.choice([None, 0, 1, 2, 3, 5])}),
            "piotroski": _maybe(r, {"score": r.choice([None, 0, 2, 5, 7, 9])}),
            "beneish": None if r.random() < 0.4 else {"m": _num(r, -4, 0), "flag": r.random() < 0.4,
                                                      "high_growth_caveat": r.random() < 0.5},
        },
        "valuation": {"company_type": "financial" if fin else r.choice(["mature", "growth", "unprofitable"]),
                      "upside": _num(r, -0.5, 0.8),
                      "pe_band": None if r.random() < 0.2 else {"current_pct_rank": _num(r, 0, 1)}},
        "technical": {"available": False} if not tech_ok else {
            "available": True, "mom_12_1": _num(r, -0.5, 0.8), "pct_from_52w_high": _num(r, -0.6, 0),
            "price_vs_ma": {"ma200": _num(r, -0.3, 0.3)}, "ma200_slope_20d": _num(r, -0.05, 0.05),
            "relative": _maybe(r, {"6m": _num(r, -0.4, 0.4)}), "rsi14": r.choice([None, 20.0, 58.0, 71.3, 99.0]),
            "trend": _maybe(r, {"code": r.choice([None, -2, -1, 0, 1, 2])})},
        "chips": ({"market": "TW",
                   "institutional": _maybe(r, {"外資": {"d20_pct": _num(r, -0.01, 0.01), "streak": r.randint(-9, 9)},
                                               "投信": {"d20_pct": _num(r, -0.01, 0.01)}}),
                   "foreign_holding": _maybe(r, {"chg_60d": _num(r, -3, 3)}),
                   "margin": _maybe(r, {"margin_chg_20d": _num(r, -0.3, 0.3),
                                        "margin_to_shares": r.choice([None, 0.001, 0.005, 0.02])}),
                   "tdcc": _maybe(r, {"big1000_chg": _num(r, -1, 1), "holders_chg": _num(r, -0.05, 0.05)})}
                  if tw else
                  {"market": r.choice(["US", None]),
                   "insiders": None if r.random() < 0.3 else {"buy_count_6m": r.choice([0, 1, 2, 3, 5]),
                                                               "sell_value_6m": r.choice([None, 0, 1e6, 1e9])},
                   "short": _maybe(r, {"pct_float": _num(r, 0, 0.2), "chg_vs_prior": _num(r, -0.3, 0.3)}),
                   "inst_net_adding": _num(r, 0, 1)}),
        "expectations": {
            "fwd_rev_growth_fy1": _num(r, -0.1, 0.3), "fwd_rev_growth_fy0": _num(r, -0.1, 0.3),
            "fwd_eps_growth_fy1": _num(r, -0.1, 0.3), "fwd_eps_growth_fy0": _num(r, -0.1, 0.3),
            "eps_trend": _maybe(r, {"0y": {"chg_90d": _num(r, -0.1, 0.1), "chg_30d": _num(r, -0.05, 0.05)},
                                    "+1y": {"chg_90d": _num(r, -0.1, 0.1)}}),
            "eps_revisions": _maybe(r, {"0y": {"breadth": _num(r, -1, 1)}}),
            "surprise_avg_4q": _num(r, -0.1, 0.1), "beats_4q": r.choice([None, 0, 2, 4]),
            "rec_score_chg_3m": _num(r, -0.5, 0.5),
            "rating_changes_90d": None if r.random() < 0.4 else {k: r.randint(0, 4) for k in
                                                                 ("up", "down", "pt_raises", "pt_lowers")}},
        "news_summary": {"d7": {"avg": _num(r, -0.6, 0.6), "n": r.choice([None, 0, 2, 3, 10])},
                         "d30": {"avg": _num(r, -0.5, 0.5), "n": r.choice([None, 0, 4, 5, 30])}},
        "regime": {"score": _num(r, 0, 100)},
        "peers": peers,
        "multiples": {"forward_pe": _num(r, -5, 80), "fcf_yield": _num(r, -0.05, 0.1), "ev_ebitda": _num(r, -5, 40),
                      "peg": _num(r, 0, 4), "pb": _num(r, 0, 3)},
        "month_revenue": None if r.random() < 0.4 else {"yoy_3m": _num(r, -0.2, 0.4), "sur": _num(r, -3, 3),
                                                         "record_high": r.random() < 0.3,
                                                         "record_same_month": r.random() < 0.3},
        "market_cap": r.choice([None, 0, 1e9, 1e12]),
    }
    return ctx


def _same(a, b) -> bool:
    """逐位元相同(NaN 視為相同;float 與 int 的值相同也要型別一致才算)。"""
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return list(a) == list(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def test_shipped_model_matches_legacy_scoring_bit_for_bit():
    r = random.Random(20261102)
    bad = []
    for i in range(N_CASES):
        ctx = make_ctx(r)
        try:
            old = LEGACY.build(copy.deepcopy(ctx))
        except Exception as e:  # noqa: BLE001  舊程式會丟例外的輸入:新程式也要丟(不要求同一種)
            with pytest.raises(Exception):
                SC.build(copy.deepcopy(ctx))
            bad.append(("legacy-raises", i, type(e).__name__))
            continue
        new = SC.build(copy.deepcopy(ctx))
        assert _same(old, new), f"第 {i} 組不同:\n{json.dumps(old, ensure_ascii=False, default=str)[:600]}\n" \
                                f"{json.dumps(new, ensure_ascii=False, default=str)[:600]}"
    assert len(bad) < N_CASES * 0.05   # 產生器不應該大多數都是壞輸入


def test_shipped_model_valid_and_weights_match_params():
    from lab import config
    m = schema.load_shipped("jw-mf-2.0")
    p = config.load_params()
    assert p["pillar_weights"] == m["pillar_weights"]
    assert p["short_term_thresholds"] == m["short_term_thresholds"]
    assert list(p)[list(p).index("pillar_weights") + 1] == "short_term_thresholds"


def test_score_hash_ignores_names_but_not_weights():
    m = schema.load_shipped("jw-mf-2.0")
    h = schema.score_hash(m)
    m2 = copy.deepcopy(m)
    m2["name"], m2["metrics"][0]["label"], m2["metrics"][0]["note"] = "改名", "改標籤", "改說明"
    assert schema.score_hash(m2) == h
    m2["metrics"][0]["weight"] = 2.5
    assert schema.score_hash(m2) != h


def test_zero_weight_pillar_is_unavailable_not_crash():
    m = schema.load_shipped("jw-mf-2.0")
    for x in m["metrics"]:
        if x["pillar"] == "sentiment":
            x["weight"] = 0
    ctx = make_ctx(random.Random(1))
    ctx["news_summary"] = {"d7": {"avg": 0.2, "n": 10}, "d30": {"avg": 0.1, "n": 30}}
    assert SC.build(ctx, m)["sentiment"]["score"] is None


def test_disabled_metric_is_dropped():
    m = schema.load_shipped("jw-mf-2.0")
    for x in m["metrics"]:
        if x["key"] == "news30":
            x["enabled"] = False
    ctx = make_ctx(random.Random(2))
    keys = [i["key"] for i in SC.build(ctx, m)["sentiment"]["items"]]
    assert keys == ["news7"]


@pytest.mark.parametrize("patch, msg", [
    ({"schema": "x"}, "不支援的模型格式"),
    ({"pillar_weights": {"long_term": {"magic": 1}, "short_term_tw": {"momentum": 1}, "short_term_us": {"momentum": 1}}},
     "未知面向"),
    ({"short_term_thresholds": {"bullish": 40, "bearish": 60}}, "bearish < bullish"),
])
def test_validate_rejects_bad_models(patch, msg):
    m = schema.load_shipped("jw-mf-2.0") | patch
    with pytest.raises(schema.ModelError, match=msg):
        schema.validate(m)


def test_engine_rejects_bad_metric_fields():
    m = schema.load_shipped("jw-mf-2.0")
    m["metrics"][0]["score"] = {"type": "builtin", "fn": "rm_rf"}
    with pytest.raises(schema.ModelError, match="未知內建打分"):
        schema.validate(m)
    m = schema.load_shipped("jw-mf-2.0")
    m["metrics"][0]["weight"] = -1
    with pytest.raises(schema.ModelError, match="weight"):
        schema.validate(m)
    assert engine.PILLAR_LABEL["quality"] == "品質"


def test_base_vars_names_match_static_list():
    r = random.Random(11)
    seen = set()
    for _ in range(2000):
        ks = set(SC.base_vars(make_ctx(r)))
        assert ks <= set(SC.BASE_VARS)
        seen |= ks
    assert seen == set(SC.BASE_VARS)


def _bad(mutate):
    m = schema.load_shipped("jw-mf-2.0")
    mutate(m)
    with pytest.raises(schema.ModelError):
        schema.validate(m)


@pytest.mark.parametrize("mutate", [
    lambda m: m["metrics"][0].update(label="{nope}"),
    lambda m: m["metrics"][0].update(label="ROE {"),
    lambda m: m["metrics"][0].update(label="{yoy_basis.__class__}"),
    lambda m: m["metrics"][0].update(label=3),
    lambda m: m["metrics"][0].pop("label"),
    lambda m: m["metrics"][0].update(value={"var": "no_such_var"}),
    lambda m: m["metrics"][0].update(requires=["mr_present"]),
    lambda m: m["metrics"][0].update(gate={"var": "nope", "min": 1}),
    lambda m: m["metrics"][0].update(weight=float("inf")),
    lambda m: m["metrics"][0].update(score={"type": "linear", "bad": float("nan"), "good": 1}),
    lambda m: m["metrics"][0].update(score={"type": "map", "table": {"abc": 1}}),
    lambda m: m["metrics"][0].update(peer_blend={"peer_var": "peer_pct_roe", "abs_weight": 1.5}),
    lambda m: m["metrics"][0].update(key="Bad Key"),
    lambda m: m["metrics"].__setitem__(0, "not a dict"),
    lambda m: m.update(metrics=[]),
    lambda m: next(x for x in m["metrics"] if x["key"] == "net_debt_ebitda")["score"].update(bad="4"),
])
def test_validate_rejects_models_that_would_crash_engine(mutate):
    _bad(mutate)


def test_expr_rejected_until_expression_language_exists():
    if schema.expr_available():
        pytest.skip("運算式語言已合併,改由 M3 的測試把關")
    _bad(lambda m: m["metrics"][0].update(value={"expr": "roe * 2"}))


def test_beneish_missing_flag_is_unavailable_not_crash():
    ctx = make_ctx(random.Random(4))
    ctx["valuation"]["company_type"] = "mature"
    ctx["fund"]["beneish"] = {"m": -2.0}
    it = next(i for i in SC.build(ctx)["health"]["items"] if i["key"] == "beneish")
    assert it["available"] is False
    ctx["valuation"]["company_type"] = "financial"
    ctx["fund"]["beneish"] = {"flag": True}
    SC.build(ctx)   # 金融業不用 beneish,不能因為缺 m 而崩潰


def test_custom_expr_metric_scores_and_missing_branch_vars_are_none():
    """M3 接線:自訂指標用完整變數清單編譯;這一筆沒有的分支變數(例如技術面不可得)算成 None,不報錯。"""
    assert schema.expr_available()
    m = schema.load_shipped("jw-mf-2.0")
    m["metrics"].append({"key": "my_q", "pillar": "quality", "applies": "all", "market": "all", "label": "我的品質",
                         "value": {"expr": "safe_div(roe, max(debt_to_equity, 0.1)) + mom_12_1 * 0"},
                         "score": {"type": "linear", "bad": 0, "good": 1}, "weight": 2, "fmt": "num", "note": "",
                         "peer_blend": None, "requires": None, "gate": None, "enabled": True})
    schema.validate(m)
    ctx = make_ctx(random.Random(31))
    ctx["fund"]["profitability"]["roe"], ctx["fund"]["health"]["debt_to_equity"] = 0.2, 0.5
    ctx["technical"] = {"available": True, "mom_12_1": 0.1, "pct_from_52w_high": -0.1, "price_vs_ma": {"ma200": 0.0},
                        "ma200_slope_20d": 0.0, "relative": {}, "rsi14": 55.0, "trend": {"code": 1}}
    it = next(i for i in SC.build(ctx, m)["quality"]["items"] if i["key"] == "my_q")
    assert it["available"] and it["value"] == pytest.approx(0.4) and it["abs_score"] == 40.0
    ctx["technical"] = {"available": False}
    it = next(i for i in SC.build(ctx, m)["quality"]["items"] if i["key"] == "my_q")
    assert it["available"] is False   # mom_12_1 不存在 → None → 整個運算式 None


def test_expr_validation_errors_are_model_errors():
    m = schema.load_shipped("jw-mf-2.0")
    m["metrics"][0]["value"] = {"expr": "__import__('os')"}
    with pytest.raises(schema.ModelError, match="運算式錯誤"):
        schema.validate(m)
