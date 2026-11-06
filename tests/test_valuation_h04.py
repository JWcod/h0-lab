"""P2-H04(Red-team M3):只剩以現價推算的方法時不給公平價與評等;不確定性「很高」時信心最高「低」。"""
import json

from lab import config
from lab.analysis import decision as DE
from lab.valuation import model as VM


def _params():
    p = json.loads((config.ROOT / "lab" / "model_params.json").read_text(encoding="utf-8"))
    p["valuation"]["mc_draws"] = 300
    return p


def _ctx(**kw):
    ctx = {"price": 20.0, "market": "US", "currency": "USD", "shares": 4e9, "net_debt": 1e10, "revenue_ttm": 5e10,
           "tax_rate": 0.2, "industry_key": "semiconductors", "industry_tw": None, "ni_ttm": -2e9, "eps_ttm": -0.5,
           "growth_ref": -0.05, "beta": 1.1, "rf_us": 0.042, "market_cap": 8e10, "total_debt": 5e10, "interest_expense": 1e9,
           "fcff_margin": -0.05, "g_start": -0.05, "pe_hist": None, "ntm_eps": None, "peers": [], "ebitda_ttm": 5e9,
           "targets": {"median": 25.0, "n": 20}, "vol_1y": 0.4, "financial_currency": "USD", "cyclical": {}}
    ctx.update(kw)
    return ctx


def test_only_price_anchored_method_gives_no_fair_value():
    val = VM.run(_ctx(), _params())       # 負自由現金流 → 沒有 DCF;只剩分析師調整價(以現價推算)
    assert val["fair_value"] is None and val["uncertainty"] == "很高"
    assert "現價" in val["no_fv_reason"]
    an = [m for m in val["methods"] if m["key"] == "analyst"]
    assert an and not an[0]["included"] and "循環引用" in an[0]["excluded_reason"]
    lt = DE.long_term(val, {"score": 50}, {}, {}, "US", {})
    assert lt["level"] is None and lt["rating"].startswith("不評等") and "現價" in lt["reasons"][0]


def test_non_price_method_still_gives_fair_value():
    val = VM.run(_ctx(ni_ttm=5e9, eps_ttm=1.25, fcff_margin=0.12, g_start=0.03, growth_ref=0.03), _params())
    keys = {m["key"] for m in val["methods"] if m["included"]}
    assert "dcf" in keys and val["fair_value"] is not None and "no_fv_reason" not in val


def test_confidence_capped_when_uncertainty_very_high():
    pillars = {k: {"coverage": 1.0} for k in ("quality", "growth", "valuation", "health", "momentum")}
    status = [{"item": "x", "ok": True}]
    methods = [{"key": k, "included": True} for k in ("dcf", "pe_band", "peers", "analyst")]
    hi = DE.confidence(pillars, {"uncertainty": "低", "fair_value": 10, "methods": methods}, status)
    assert hi["label"] == "高"
    capped = DE.confidence(pillars, {"uncertainty": "很高", "fair_value": 10, "methods": methods}, status)
    assert capped["label"] == "低"


# ---------------- P2-H03:只剩一種有效估值方法時不確定性「很高」 ----------------

def test_single_method_forces_very_high_uncertainty():
    prof = dict(ni_ttm=5e9, eps_ttm=1.25, fcff_margin=0.12, g_start=0.03, growth_ref=0.03)
    one = VM.run(_ctx(**prof, targets={}), _params())                # 只有 DCF
    assert [m["key"] for m in one["methods"] if m["included"]] == ["dcf"]
    assert one["uncertainty"] == "很高" and one["single_method"] == "dcf"
    assert one["margin_of_safety"] == _params()["valuation"]["margin_of_safety"]["很高"]
    assert one["uncertainty_score"] >= 0.9
    two = VM.run(_ctx(**prof), _params())                            # DCF + 分析師:可以交叉驗證
    assert len([m for m in two["methods"] if m["included"]]) == 2 and "single_method" not in two


def test_single_method_rule_off_with_v11_params():
    """v1.1 的參數沒有 single_method_very_high → 行為和 v1.1 相同(基準可逐位元重現)。"""
    p11 = json.loads((config.ROOT / "tests" / "baseline" / "model_params_v1.1.json").read_text(encoding="utf-8"))
    assert "single_method_very_high" not in p11["valuation"]
    p11["valuation"]["mc_draws"] = 300
    one = VM.run(_ctx(ni_ttm=5e9, eps_ttm=1.25, fcff_margin=0.12, g_start=0.03, growth_ref=0.03, targets={}), p11)
    assert "single_method" not in one
