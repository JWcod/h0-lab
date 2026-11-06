"""投組風險規則(lab/portfolio.py,階段 4.1)的單元測試:上限、波動目標、再平衡門檻、成本。"""
from __future__ import annotations

import math

import pytest

from lab import portfolio as P


def test_portfolio_vol_formula():
    # 單檔:就是個股波動 × 權重
    assert P.portfolio_vol({"A": 0.5}, {"A": 0.2}) == pytest.approx(0.1)
    # 兩檔、ρ = 0:σp = sqrt(Σ(wσ)²)
    assert P.portfolio_vol({"A": 0.5, "B": 0.5}, {"A": 0.2, "B": 0.2}, rho=0) == pytest.approx(math.sqrt(2) * 0.1)
    # ρ = 1:σp = Σ wσ
    assert P.portfolio_vol({"A": 0.5, "B": 0.5}, {"A": 0.2, "B": 0.4}, rho=1) == pytest.approx(0.3)
    # 缺波動用保守值,不是 0
    assert P.portfolio_vol({"A": 1.0}, {"A": None}) == pytest.approx(P.RISK["default_vol"])
    assert P.portfolio_vol({"A": 1.0}, {"A": float("nan")}) == pytest.approx(P.RISK["default_vol"])


def test_single_cap_and_max_positions():
    syms = [f"S{i}" for i in range(20)]
    r = P.target_weights(syms, {s: 0.01 for s in syms}, {}, {"vol_target": 1.0})
    assert len(r["weights"]) == P.RISK["max_positions"]
    assert list(r["weights"]) == syms[: P.RISK["max_positions"]]          # 依呼叫端順序取前面的
    assert max(r["weights"].values()) <= P.RISK["max_weight"] + 1e-12
    # 3 檔:每檔最多 10%,剩下留現金(不放大)
    r = P.target_weights(["A", "B", "C"], {}, {}, {"vol_target": 1.0})
    assert r["gross"] == pytest.approx(0.3)
    assert r["notes"]


def test_sector_cap_and_unknown_sector_not_pooled():
    syms = [f"S{i}" for i in range(10)]
    sec = {s: "半導體" for s in syms[:6]}          # 6 × 10% = 60% > 30%
    r = P.target_weights(syms, {s: 0.01 for s in syms}, sec, {"vol_target": 1.0})
    w = r["weights"]
    assert sum(w[s] for s in syms[:6]) == pytest.approx(0.30)
    assert all(w[s] == pytest.approx(0.10) for s in syms[6:])            # 沒有產業的不合併、不被縮
    assert r["gross"] == pytest.approx(0.70)                              # 縮下來的部分留現金


def test_vol_target_scales_down_never_levers():
    syms = [f"S{i}" for i in range(10)]
    hi = P.target_weights(syms, {s: 0.60 for s in syms}, {})
    assert hi["scale"] < 1
    assert hi["est_vol"] == pytest.approx(P.RISK["vol_target"])
    lo = P.target_weights(syms, {s: 0.05 for s in syms}, {})
    assert lo["scale"] == 1.0 and lo["gross"] == pytest.approx(1.0)     # 波動低也不加槓桿
    assert P.target_weights([], {}, {})["weights"] == {}


def test_rebalance_band():
    cur = {"A": 0.10, "B": 0.10, "C": 0.05}
    tgt = {"A": 0.115, "B": 0.07, "D": 0.01}
    o = P.rebalance_orders(cur, tgt, band=0.02)
    assert "A" not in o                          # 差 1.5% ≤ 2%:不動
    assert o["B"] == pytest.approx(-0.03)
    assert o["C"] == pytest.approx(-0.05)        # 出場一律交易
    assert o["D"] == pytest.approx(0.01)         # 新進場一律交易(再小也一樣)
    # 超過單檔上限的,就算在不交易帶內也要減回(Verifier38 問題 4)
    o = P.rebalance_orders({"A": 0.119}, {"A": 0.10}, band=0.02, cap=0.10)
    assert o["A"] == pytest.approx(-0.019)
    assert "A" not in P.rebalance_orders({"A": 0.119}, {"A": 0.10}, band=0.02)


def test_trade_cost():
    assert P.trade_cost("TW", "buy", 100_000) == pytest.approx(142.5)
    assert P.trade_cost("TW", "sell", 100_000) == pytest.approx(442.5)
    assert P.trade_cost("US", "sell", -1000) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        P.trade_cost("JP", "buy", 1)
