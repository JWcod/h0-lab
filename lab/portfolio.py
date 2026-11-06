"""投組層級風險規則(階段 4.1):給模擬帳戶 C 帳本用的純函式,不讀寫任何檔案、不連網。

規則(2026-10-26 選「穩健組」,參數都在 RISK,改規則要記錄):
- 候選依呼叫端給的順序取前 max_positions 檔,先等權
- 單檔上限 max_weight;同產業合計上限 max_sector(超過的部分按比例縮小,不轉給別檔,留現金)
- 波動目標:用個股年化波動與固定兩兩相關係數 rho 估算投組年化波動;高於 vol_target 就整體等比例縮小(不加槓桿,總曝險 ≤ 1)
- 再平衡:每月一次;目標權重和現有權重差距 ≤ rebalance_band 的不交易(新進場與出場不受此限)
- 成本:台股買進手續費 0.1425%,賣出手續費 0.1425% + 證交稅 0.3%;美股單邊滑價 0.05%

這些是風控規則,不代表能提高報酬;沒有經過回測驗證,畫面上要標「參考」。
"""
from __future__ import annotations

import math

RISK = {
    "vol_target": 0.15,        # 年化
    "max_weight": 0.10,
    "max_sector": 0.30,
    "max_positions": 15,
    "rebalance_band": 0.02,
    "rebalance_days": 28,      # 距上次再平衡至少幾天(約每月一次)
    "rho": 0.5,                # 估算投組波動用的固定兩兩相關係數(個股之間沒有共變異數矩陣)
    "default_vol": 0.35,       # 缺個股波動時用的保守值
}
FEE = {
    "buy": {"TW": 0.001425, "US": 0.0005},
    "sell": {"TW": 0.001425 + 0.003, "US": 0.0005},
}
OTHER_SECTOR = "(未分類)"


def _ok(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def portfolio_vol(weights: dict[str, float], vols: dict[str, float], rho: float = RISK["rho"],
                  default_vol: float = RISK["default_vol"]) -> float:
    """固定相關係數下的投組年化波動:σp² = Σ wᵢ²σᵢ² + ρ Σ_{i≠j} wᵢwⱼσᵢσⱼ = (1−ρ) Σ (wᵢσᵢ)² + ρ (Σ wᵢσᵢ)²。"""
    ws = [(w, vols.get(s) if _ok(vols.get(s)) and vols.get(s) > 0 else default_vol) for s, w in weights.items() if w > 0]
    a = sum(w * v for w, v in ws)
    b = sum((w * v) ** 2 for w, v in ws)
    return math.sqrt(max(0.0, (1 - rho) * b + rho * a * a))


def _cap_sectors(w: dict[str, float], sector: dict[str, str], cap: float) -> dict[str, float]:
    out = dict(w)
    by: dict[str, list[str]] = {}
    for s in out:
        by.setdefault(sector.get(s) or OTHER_SECTOR, []).append(s)
    for sec, members in by.items():
        if sec == OTHER_SECTOR:          # 不知道產業的不合併計算(否則所有缺資料的會被當成同一個產業)
            continue
        tot = sum(out[s] for s in members)
        if tot > cap + 1e-12:
            k = cap / tot
            for s in members:
                out[s] *= k
    return out


def target_weights(symbols: list[str], vols: dict[str, float], sector: dict[str, str], risk: dict | None = None) -> dict:
    """symbols:已排序的候選(最好的在前)。回傳 {weights, gross, est_vol, scale, notes}。"""
    r = {**RISK, **(risk or {})}
    picks = list(dict.fromkeys(symbols))[: r["max_positions"]]
    if not picks:
        return {"weights": {}, "gross": 0.0, "est_vol": 0.0, "scale": 1.0, "notes": ["沒有符合條件的候選"]}
    notes = []
    eq = min(1.0 / len(picks), r["max_weight"])
    if eq < 1.0 / len(picks):
        notes.append(f"單檔上限 {r['max_weight']:.0%}:{len(picks)} 檔等權只能投入 {eq * len(picks):.0%}")
    w = {s: eq for s in picks}
    capped = _cap_sectors(w, sector, r["max_sector"])
    if any(capped[s] < w[s] - 1e-12 for s in w):
        notes.append(f"同產業上限 {r['max_sector']:.0%}:部分產業縮小")
    w = capped
    est = portfolio_vol(w, vols, r["rho"], r["default_vol"])
    scale = 1.0
    if est > r["vol_target"] > 0:
        scale = r["vol_target"] / est
        w = {s: x * scale for s, x in w.items()}
        notes.append(f"估計波動 {est:.0%} 高於目標 {r['vol_target']:.0%}:整體縮小到 {scale:.0%}")
    return {"weights": w, "gross": sum(w.values()), "est_vol": portfolio_vol(w, vols, r["rho"], r["default_vol"]),
            "scale": scale, "notes": notes}


def rebalance_orders(current: dict[str, float], target: dict[str, float], band: float = RISK["rebalance_band"],
                     cap: float | None = None) -> dict[str, float]:
    """回傳要調整的權重差 {代號: 目標 − 現有}。出場(目標 0)、新進場、現有權重超過 cap 的一律交易;其他差距 ≤ band 的不動。"""
    out = {}
    for s in set(current) | set(target):
        c, t = current.get(s, 0.0), target.get(s, 0.0)
        d = t - c
        if abs(d) < 1e-12:
            continue
        if t == 0 or c == 0 or abs(d) > band or (cap is not None and c > cap + 1e-12):
            out[s] = d
    return out


def trade_cost(market: str, side: str, notional: float) -> float:
    """交易成本(正數)。side:buy / sell。"""
    if market not in ("TW", "US") or side not in FEE:
        raise ValueError(f"不支援的市場或方向:{market} {side}")
    return abs(notional) * FEE[side][market]
