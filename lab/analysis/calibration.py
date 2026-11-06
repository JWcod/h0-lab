"""P2-H09:長期分數的機率校準(2 參數 logistic,輸入 = 當日股票池內的分數百分位排名)。

P(未來 12 個月跑贏同日股票池中位數) = σ(a + b × rank)。只有 a、b 兩個參數(升級計畫 §5.6)。
walk-forward:判斷日 d 的 a、b 只用「出場日 ≤ d」的樣本擬合(結果在 d 當天已經知道),不會用到未來。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

MIN_TRAIN = 300   # 訓練樣本少於這個數就不給機率(事先固定,不調)


def fit_logistic(x: np.ndarray, y: np.ndarray, iters: int = 50) -> tuple[float, float] | None:
    """最大概似(Newton–Raphson),回傳 (a, b);樣本不夠或只有一種結果時回傳 None。"""
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 2 or y.min() == y.max():
        return None
    X = np.column_stack([np.ones_like(x), x])
    w = np.zeros(2)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w)))
        g = X.T @ (y - p)
        Hm = (X * (p * (1 - p))[:, None]).T @ X
        try:
            step = np.linalg.solve(Hm, g)
        except np.linalg.LinAlgError:
            return None
        w += step
        if np.max(np.abs(step)) < 1e-10:
            break
    return float(w[0]), float(w[1])


def prob(a: float, b: float, rank) -> np.ndarray:
    return 1 / (1 + np.exp(-(a + b * np.asarray(rank, float))))


def walk_forward(df: pd.DataFrame, min_train: int = MIN_TRAIN) -> pd.DataFrame:
    """df 欄位:date(判斷日)、exit(出場日)、rank(0–1)、y(1 = 跑贏、0 = 輸;未知 = NaN)。
    回傳 df 加上 p(機率)、a、b;訓練樣本不足的日期 p = NaN。"""
    df = df.copy()
    df["date"], df["exit"] = pd.to_datetime(df["date"]), pd.to_datetime(df["exit"])
    lab = df[df["y"].notna() & df["rank"].notna() & df["exit"].notna()]
    out_p = pd.Series(np.nan, index=df.index)
    out_a, out_b = out_p.copy(), out_p.copy()
    for d in sorted(df["date"].unique()):
        tr = lab[lab["exit"] <= d]           # 判斷日 d 當天已經知道結果的樣本
        if len(tr) < min_train:
            continue
        ab = fit_logistic(tr["rank"].to_numpy(), tr["y"].to_numpy())
        if ab is None:
            continue
        m = (df["date"] == d) & df["rank"].notna()
        out_p[m] = prob(ab[0], ab[1], df.loc[m, "rank"])
        out_a[m], out_b[m] = ab
    return df.assign(p=out_p, a=out_a, b=out_b)


def bucket(p: float | None, width: float = 0.05) -> str | None:
    """網站顯示用的機率級距(例 0.57 → 「55–60%」),不顯示到小數。"""
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return None
    lo = math.floor(p / width) * width
    return f"{lo * 100:.0f}–{(lo + width) * 100:.0f}%"
