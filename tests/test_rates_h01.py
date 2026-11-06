"""P2-H01:判斷日當時的台灣 10 年期公債年平均與 Damodaran ERP(PIT)、永續成長 ≤ rf、v1.1 參數不受影響。"""
import json

import pandas as pd
import pytest

from lab import config
from lab.pit import PITStore
from lab.pit.tamper import assert_pit_invariant
from lab.sources import macro_rates as MR
from lab.valuation import model as VM


@pytest.fixture
def rates_store(tmp_path, monkeypatch):
    out = tmp_path / "macro_rates"
    out.mkdir()
    (out / "tw10y_annual.json").write_text(json.dumps({"annual_pct": {"2014": 1.6, "2015": 1.39, "2016": 0.82}}),
                                           encoding="utf-8")
    (out / "damodaran_erp.json").write_text(json.dumps({"by_year": {
        "2016": {"file": "ctryprem15.xls", "tw_total_erp": 0.065, "mature_erp": 0.0625, "available_at": "2016-01-01"},
        "2017": {"file": "ctryprem16.xls", "tw_total_erp": 0.062, "mature_erp": 0.0569, "available_at": "2017-01-01"}}}),
        encoding="utf-8")
    monkeypatch.setattr(MR, "OUT", out)
    f = MR.facts()
    px = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0, "cash_div": 0.0},
                      index=pd.bdate_range("2014-01-01", "2018-12-31"))
    return PITStore(facts=f, prices={"^TWII": px})


def test_rates_asof_uses_only_published_values(rates_store):
    r = MR.rates_asof(rates_store.as_of(pd.Timestamp("2016-06-30")))
    assert r == {"tw_rf": pytest.approx(0.0139), "tw_erp": 0.065, "erp_mature": 0.0625}   # 2015 平均;2016 年初 ERP
    r = MR.rates_asof(rates_store.as_of(pd.Timestamp("2016-12-30")))
    assert r["tw_rf"] == pytest.approx(0.0139)          # 2016 年平均要到 2017-01-01 才可用
    r = MR.rates_asof(rates_store.as_of(pd.Timestamp("2017-01-03")))
    assert r["tw_rf"] == pytest.approx(0.0082) and r["tw_erp"] == 0.062
    assert MR.rates_asof(rates_store.as_of(pd.Timestamp("2014-06-30"))) == {}


@pytest.mark.parametrize("d", ["2015-12-31", "2016-01-04", "2016-12-30", "2017-06-30"])
def test_rates_asof_tamper(rates_store, d):
    assert_pit_invariant(MR.rates_asof, rates_store, pd.Timestamp(d))


def _params(flag: bool):
    p = json.loads((config.ROOT / "lab" / "model_params.json").read_text(encoding="utf-8"))
    p["valuation"]["mc_draws"] = 200
    if not flag:
        p["valuation"].pop("rates_asof", None)
    else:
        p["valuation"]["rates_asof"] = True
    return p


def test_cost_of_capital_uses_rates_only_when_enabled():
    rates = {"tw_rf": 0.0044, "tw_erp": 0.05, "erp_mature": 0.045}
    on = VM.cost_of_capital("TW", 1.0, None, _params(True), 1e11, 0, None, 0.2, rates)
    assert on["rf"] == 0.0044 and on["cost_of_equity"] == pytest.approx(max(0.0044 + 0.05, 0.065))
    assert on["rates_source"] == "as_of" and on["erp"] == 0.045
    off = VM.cost_of_capital("TW", 1.0, None, _params(False), 1e11, 0, None, 0.2, rates)
    assert off["rf"] == _params(False)["valuation"]["tw_rf"] and off["rates_source"] == "params"
    us = VM.cost_of_capital("US", 1.2, 0.02, _params(True), 1e11, 0, None, 0.2, rates)
    assert us["cost_of_equity"] == pytest.approx(max(0.02 + 1.2 * 0.045, 0.07))
    v11 = json.loads((config.ROOT / "tests" / "baseline" / "model_params_v1.1.json").read_text(encoding="utf-8"))
    assert "rates_asof" not in v11["valuation"]
    assert VM.cost_of_capital("TW", 1.0, None, v11, 1e11, 0, None, 0.2, rates)["rf"] == v11["valuation"]["tw_rf"]


def test_terminal_growth_capped_by_rf():
    ctx = {"price": 50.0, "market": "TW", "currency": "TWD", "shares": 1e9, "net_debt": 0.0, "revenue_ttm": 5e10,
           "tax_rate": 0.2, "industry_key": None, "industry_tw": "電子", "ni_ttm": 5e9, "eps_ttm": 5.0, "growth_ref": 0.05,
           "beta": 1.0, "rf_us": None, "market_cap": 5e10, "total_debt": 0.0, "interest_expense": None,
           "fcff_margin": 0.1, "g_start": 0.05, "pe_hist": None, "ntm_eps": None, "peers": [], "targets": {},
           "vol_1y": 0.3, "financial_currency": "TWD", "cyclical": {}, "rates_asof": {"tw_rf": 0.0044}}
    on = VM.run(ctx, _params(True))
    assert on["dcf"]["inputs"]["g_terminal"] == pytest.approx(0.0044)
    off = VM.run(ctx, _params(False))
    assert off["dcf"]["inputs"]["g_terminal"] == _params(False)["valuation"]["terminal_growth_tw"]
