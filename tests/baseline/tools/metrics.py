"""v1.1 基準的統一指標(階段 0 版本;階段 1 會正式化成 lab/research/evaluate.py,兩者結果必須一致)。

用法(在專案根目錄):
  .venv/bin/python tests/baseline/tools/metrics.py <rows 與 backtest_*.json 所在資料夾> <凍結快取資料夾> <輸出 json>

指標(每個市場、每個期間):
- 命中率:看多跑贏同日股票池中位數 / 看空跑輸中位數算「對」。95% 信賴區間用「判斷月份」的循環區塊 bootstrap,
  區塊長度 = 預測期月數(12 個月報酬每月重疊計算,一般的二項分布 CI 會窄得不合理;兩種都列出來對照)。
- IC:每月橫斷面 Spearman(分數, 之後超額報酬)的平均,同樣用區塊 bootstrap 給 CI;另列「網站目前用的天真 t 值」。
- 三分位價差:分數最高 1/3 減最低 1/3 的平均超額報酬(每月計算再平均)。
- 評等分組:各評等的次數、跑贏中位數比例、超額報酬平均與中位數(檢查單調性)。
- 投組:由回測輸出的淨值曲線算年化、最大回撤、夏普,以及策略之間的年化報酬差與區塊 bootstrap CI。
- Brier:v1.1 沒有機率輸出,無法計算(參考值 0.25 = 永遠猜 50%)。

資料切分(見 docs/holdout 評估日誌.md):
- holdout:判斷日 >= 2024-10-01
- 開發集:判斷日 + 預測期 <= 2024-10-01(去除結果視窗跨進 holdout 的判斷)

holdout 保護(任務 1.9b):只有輸入的逐筆檔(解壓後)SHA-256 等於 tests/baseline/data_manifest.json 記的 v1.1 基準值,
才算全期 / holdout / 全期評等分組 / 投組(那是已凍結、已記在 holdout 日誌的基準數字);其他逐筆檔一律只輸出開發集。
基準模式的投組淨值曲線讀凍結的 tests/baseline/outputs/backtest_<市場>.json(回測程式 v2 起只輸出開發集投組;
逐筆檔 SHA 相同就保證是同一份判斷)。
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import pathlib
import pickle
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "tests" / "baseline" / "data_manifest.json"
FROZEN_OUT = ROOT / "tests" / "baseline" / "outputs"
sys.path.insert(0, str(ROOT))
from lab.universe import TW_LARGE, US_LARGE  # noqa: E402

H = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
HM = {"1m": 1, "3m": 3, "6m": 6, "12m": 12}
HOLD_START = pd.Timestamp("2024-10-01")
B = 2000
SEED = 20261015


def load_px(cache: pathlib.Path, symbols: list[str]) -> dict:
    h = hashlib.sha1(f"bt_px|{'|'.join(sorted(symbols))}".encode()).hexdigest()
    with open(cache / "bt_px" / f"{h}.pkl", "rb") as f:
        return pickle.load(f)["v"]


def fwd(adj: pd.Series, d, n: int) -> float:
    i = adj.index.searchsorted(d, side="right") - 1
    if i < 0 or i + n >= len(adj):
        return np.nan
    a, b = adj.iloc[i], adj.iloc[i + n]
    return float(b / a - 1) if a and b and not math.isnan(a) and not math.isnan(b) else np.nan


def block_ci(n: int, stat_on_idx, L: int, rng) -> tuple | None:
    """循環區塊 bootstrap;獨立期數(n/L)不到 2 時不給 CI(只有一個獨立期間,區間沒有意義)。"""
    if n < 2 * L or n < 3:
        return None
    vals = []
    for _ in range(B):
        starts = rng.integers(0, n, size=math.ceil(n / L))
        idx = np.concatenate([(np.arange(s, s + L) % n) for s in starts])[:n]
        v = stat_on_idx(idx)
        if v is not None and not np.isnan(v):
            vals.append(v)
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))) if vals else None


def naive_ci(r: int, w: int) -> tuple | None:
    n = r + w
    if not n:
        return None
    p = r / n
    se = math.sqrt(p * (1 - p) / n)
    return (p - 1.96 * se, p + 1.96 * se)


def split_frame(d: pd.DataFrame, split: str, months: int) -> pd.DataFrame:
    if split == "dev":
        return d[d["date"] + pd.DateOffset(months=months) <= HOLD_START]
    if split == "holdout":
        return d[d["date"] >= HOLD_START]
    return d


def rows_file(rows_dir: pathlib.Path, market: str) -> pathlib.Path:
    for name in (f"backtest_{market}_rows.csv", f"backtest_{market}_rows.csv.gz"):
        if (rows_dir / name).exists():
            return rows_dir / name
    raise FileNotFoundError(rows_dir / f"backtest_{market}_rows.csv")


def is_baseline_rows(path: pathlib.Path, market: str) -> bool:
    """逐筆檔(解壓後)的 SHA-256 是否等於 data_manifest.json 記的 v1.1 基準值。"""
    import gzip
    try:
        b = path.read_bytes()
        if path.suffix == ".gz":
            b = gzip.decompress(b)
        return hashlib.sha256(b).hexdigest() == json.loads(MANIFEST.read_text(encoding="utf-8"))["rows_csv_sha256"][market]
    except (OSError, KeyError, ValueError):
        return False


def analyze(market: str, rows_dir: pathlib.Path, cache: pathlib.Path, rng) -> dict:
    lst = TW_LARGE if market == "TW" else US_LARGE
    syms = [s for s, _ in lst]
    bench = "^TWII" if market == "TW" else "^GSPC"
    px = load_px(cache, syms + [bench, "^VIX", "^TNX"] + (["TWD=X"] if market == "TW" else []))
    rf = rows_file(rows_dir, market)
    full = is_baseline_rows(rf, market)
    df = pd.read_csv(rf)
    df["date"] = pd.to_datetime(df["date"])
    pil = pd.DataFrame([ast.literal_eval(p) for p in df["pillars"]]).add_prefix("p_")
    df = pd.concat([df.reset_index(drop=True), pil], axis=1)
    adj = {s: (px[s]["Adj Close"] if "Adj Close" in px[s] else px[s]["Close"]).dropna() for s in syms if s in px}
    for k, n in H.items():
        df[f"r_{k}"] = [fwd(adj[s], d, n) if s in adj else np.nan for s, d in zip(df["symbol"], df["date"])]
        df[f"x_{k}"] = df[f"r_{k}"] - df.groupby("date")[f"r_{k}"].transform("median")

    if full:
        out: dict = {"n_rows": int(len(df)), "first_date": str(df["date"].min().date()), "last_date": str(df["date"].max().date())}
    else:
        dv = df[df["date"] < HOLD_START]
        out = {"n_rows_dev_period": int(len(dv)), "first_date": str(dv["date"].min().date()) if len(dv) else None}
    for split in (("all", "dev", "holdout") if full else ("dev",)):
        res = {}
        for hz in ("12m", "6m", "3m", "1m"):
            L = HM[hz]
            d = split_frame(df[df[f"x_{hz}"].notna()], split, L)
            if d.empty:
                continue
            by = {k: g for k, g in d.groupby("date")}
            dates = sorted(by)
            lt = hz in ("12m", "6m")
            col = "lt_level" if lt else "st_code"
            score = "lt_score" if lt else "st_score"
            per = []  # 每月的 (對, 錯, IC, 三分位價差)
            for dd in dates:
                g = by[dd]
                bull = g[g[col] >= 3] if lt else g[g[col] > 0]
                bear = g[g[col] <= 1] if lt else g[g[col] < 0]
                r = int((bull[f"x_{hz}"] > 0).sum() + (bear[f"x_{hz}"] < 0).sum())
                w = int((bull[f"x_{hz}"] <= 0).sum() + (bear[f"x_{hz}"] >= 0).sum())
                x = g[[score, f"x_{hz}"]].dropna()
                ic = x[score].rank().corr(x[f"x_{hz}"].rank()) if len(x) >= 10 and x[score].nunique() > 1 else np.nan
                spr = np.nan
                if len(x) >= 12:
                    q = x[score].rank(pct=True)
                    spr = float(x[q > 2 / 3][f"x_{hz}"].mean() - x[q <= 1 / 3][f"x_{hz}"].mean())
                per.append((r, w, ic, spr))
            P = np.array(per, dtype=float)
            n = len(P)
            R, W = int(P[:, 0].sum()), int(P[:, 1].sum())

            def hit(idx):
                rr, ww = P[idx, 0].sum(), P[idx, 1].sum()
                return rr / (rr + ww) if rr + ww else np.nan

            def mean_col(c):
                return lambda idx: float(np.nanmean(P[idx, c])) if np.isfinite(P[idx, c]).any() else np.nan

            ics = P[:, 2][np.isfinite(P[:, 2])]
            res[hz] = {
                "signal": col, "right": R, "wrong": W, "hit": R / (R + W) if R + W else None,
                "hit_ci95_block": block_ci(n, hit, L, rng), "hit_ci95_naive_iid": naive_ci(R, W),
                "n_months": n, "independent_periods": round(n / L, 1), "first": str(dates[0].date()), "last": str(dates[-1].date()),
                "ic_score": score, "ic_mean": float(ics.mean()) if len(ics) else None,
                "ic_ci95_block": block_ci(n, mean_col(2), L, rng),
                "ic_t_naive": float(ics.mean() / ics.std() * math.sqrt(len(ics))) if len(ics) > 2 and ics.std() > 0 else None,
                "tercile_spread": float(np.nanmean(P[:, 3])) if np.isfinite(P[:, 3]).any() else None,
                "tercile_spread_ci95_block": block_ci(n, mean_col(3), L, rng),
            }
        out[split] = res

    if not full:
        pil_out = []
        for c in [c for c in df.columns if c.startswith("p_")] + ["upside", "lt_score", "st_score"]:
            for hz in ("3m", "12m"):
                d = split_frame(df[df[f"x_{hz}"].notna()], "dev", HM[hz])
                ics = [g[[c, f"x_{hz}"]].dropna() for _, g in d.groupby("date")]
                ics = [x[c].rank().corr(x[f"x_{hz}"].rank()) for x in ics if len(x) >= 10 and x[c].nunique() > 1]
                if len(ics) >= 6:
                    a = np.array(ics)
                    pil_out.append({"key": c.replace("p_", ""), "horizon": hz, "split": "dev", "ic": float(a.mean()), "n_months": len(a)})
        out["pillar_ic"] = pil_out
        out["restricted"] = "不是凍結的 v1.1 基準逐筆檔:只輸出開發集(全期、holdout、全期評等分組、投組都不算)"
        return out

    # 評等分組(12 個月,全期)
    lv = ["賣出", "減碼", "持有", "買進", "強力買進"]
    buckets = []
    for k in (4, 3, 2, 1, 0):
        g = df[(df["lt_level"] == k) & df["x_12m"].notna()]
        if len(g):
            buckets.append({"rating": lv[k], "n": int(len(g)), "win": float((g["x_12m"] > 0).mean()),
                            "mean_x12": float(g["x_12m"].mean()), "median_x12": float(g["x_12m"].median())})
    out["rating_buckets_12m"] = buckets

    # 各面向 IC(全期與開發集),附區塊 CI
    pil_out = []
    for c in [c for c in df.columns if c.startswith("p_")] + ["upside", "lt_score", "st_score"]:
        for hz in ("3m", "12m"):
            for split in ("all", "dev"):
                d = split_frame(df[df[f"x_{hz}"].notna()], split, HM[hz])
                ics = []
                for _, g in d.groupby("date"):
                    x = g[[c, f"x_{hz}"]].dropna()
                    if len(x) >= 10 and x[c].nunique() > 1:
                        ics.append(x[c].rank().corr(x[f"x_{hz}"].rank()))
                if len(ics) >= 6:
                    a = np.array(ics)
                    pil_out.append({"key": c.replace("p_", ""), "horizon": hz, "split": split, "ic": float(a.mean()),
                                    "n_months": len(a), "t_naive": float(a.mean() / a.std() * math.sqrt(len(a))) if a.std() > 0 else None,
                                    "ci95_block": block_ci(len(a), lambda idx, a=a: float(a[idx].mean()), HM[hz], rng)})
    out["pillar_ic"] = pil_out

    # 投組(回測輸出的淨值曲線)
    bt = json.loads((FROZEN_OUT / f"backtest_{market}.json").read_text(encoding="utf-8"))
    curve = pd.DataFrame(bt["portfolio"]["curve"]).set_index("date")
    rets = curve.pct_change()
    rets.iloc[0] = curve.iloc[0] - 1
    yrs = len(curve) / 12
    stats = {}
    for k in curve.columns:
        s = curve[k]
        r = rets[k]
        stats[k] = {"cagr": float(s.iloc[-1] ** (1 / yrs) - 1), "max_dd": float((s / s.cummax() - 1).min()),
                    "sharpe": float(r.mean() / r.std() * math.sqrt(12)) if r.std() > 0 else None}
    diffs = []
    for a, b in (("ex_sell", "universe"), ("model", "universe"), ("model_st", "universe"), ("ex_sell", "bench"), ("universe", "bench")):
        dlt = (rets[a] - rets[b]).to_numpy()
        hold = (rets[a] - rets[b])[rets.index >= "2024-11"].to_numpy()
        diffs.append({"a": a, "b": b, "ann_mean_diff": float(dlt.mean() * 12),
                      "ci95_block3": block_ci(len(dlt), lambda idx, dlt=dlt: float(dlt[idx].mean() * 12), 3, rng),
                      "holdout_ann_mean_diff": float(hold.mean() * 12) if len(hold) else None, "holdout_months": int(len(hold))})
    out["portfolio"] = {"stats": stats, "diffs": diffs, "avg_turnover_model": bt["portfolio"].get("avg_turnover"),
                        "cost_per_turnover": bt["portfolio"].get("cost"),
                        "note": f"大盤 {bench} 是價格指數(不含股利);個股用還原權值(含股利)報酬,對大盤的比較偏向個股"}
    out["brier"] = {"value": None, "reference_coin_flip": 0.25, "note": "v1.1 只輸出評等、沒有機率,無法計算 Brier"}
    return out


def main():
    rows_dir, cache, out_path = (pathlib.Path(a).resolve() for a in sys.argv[1:4])
    rng = np.random.default_rng(SEED)
    res = {"holdout_start": str(HOLD_START.date()), "bootstrap_draws": B, "seed": SEED,
           "markets": {m: analyze(m, rows_dir, cache, rng) for m in ("TW", "US")}}
    out_path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    def f(t):
        return "—(獨立期數不足)" if not t else f"[{t[0]:.3f}, {t[1]:.3f}]"
    for m, r in res["markets"].items():
        if "restricted" in r:
            print(m, r["restricted"])
        for sp in ("all", "dev", "holdout"):
            for hz, v in r.get(sp, {}).items():
                print(f"{m} {sp:7s} {hz:3s} 命中 {v['right']:4d}/{v['wrong']:4d}={v['hit']:.3f} 區塊CI{f(v['hit_ci95_block'])} "
                      f"天真CI{f(v['hit_ci95_naive_iid'])} | IC {v['ic_mean']:+.3f} CI{f(v['ic_ci95_block'])} 天真t {v['ic_t_naive']} | "
                      f"三分位差 {v['tercile_spread']:+.3f} CI{f(v['tercile_spread_ci95_block'])} | 獨立期 {v['independent_periods']}")
        for dd in r.get("portfolio", {}).get("diffs", []):
            print(m, f"{dd['a']}−{dd['b']} 年化差 {dd['ann_mean_diff']:+.3f} CI{f(dd['ci95_block3'])} holdout {dd['holdout_ann_mean_diff']:+.3f}")


if __name__ == "__main__":
    main()
