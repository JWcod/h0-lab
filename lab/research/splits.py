"""開發集 / holdout 切分(共用介面,只有維護者改;規則見 docs/holdout 評估日誌.md 與 docs/架構.md)。

v2 規則(2026-10-15 定案,比階段 0 計畫的 Q1(b) 更嚴格,理由見日誌):
- 單一邊界 HOLDOUT_START = 2024-10-01:這天以後的股價對「所有」預測期都是 holdout。
  (若短期用 2025-10 起當 holdout,短期的開發集就會用到 2024-10 ~ 2025-09 的股價,
   而那正是長期評等的 holdout;兩者共用面向,等於間接偷看。)
- holdout 列:判斷日 ≥ HOLDOUT_START。
- 開發集列:結果視窗的最後一個交易日 < HOLDOUT_START(用實際交易日,不用日曆月)。
- 其餘(判斷日在邊界前、但結果視窗跨過邊界):剔除(purged),兩邊都不用。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HOLDOUT_START = pd.Timestamp("2024-10-01")
HORIZON_DAYS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
DEV, HOLDOUT, PURGED, NO_LABEL = "dev", "holdout", "purged", "no_label"


def label_end(dates: pd.Series | pd.DatetimeIndex, horizon: str, calendar: pd.DatetimeIndex) -> pd.Series:
    """每個判斷日的結果視窗最後一個交易日(判斷日當天或之前最後一個交易日 + n 個交易日);超出資料範圍為 NaT。"""
    n = HORIZON_DAYS[horizon]
    cal = pd.DatetimeIndex(calendar).sort_values()
    d = pd.DatetimeIndex(pd.to_datetime(dates))
    base = cal.searchsorted(d, side="right") - 1
    pos = base + n
    valid = (base >= 0) & (pos < len(cal))
    vals = np.full(len(d), np.datetime64("NaT", "ns"))
    vals[valid] = cal[pos[valid]].values
    return pd.Series(vals, index=getattr(dates, "index", None))


def assign(dates: pd.Series, horizon: str, calendar: pd.DatetimeIndex) -> pd.Series:
    """回傳每列屬於 dev / holdout / purged / no_label。"""
    d = pd.to_datetime(pd.Series(dates).reset_index(drop=True))
    end = label_end(d, horizon, calendar).reset_index(drop=True)
    out = pd.Series(PURGED, index=d.index, dtype=object)
    out[end.isna()] = NO_LABEL
    out[(end < HOLDOUT_START) & end.notna()] = DEV
    out[d >= HOLDOUT_START] = HOLDOUT
    out[(d >= HOLDOUT_START) & end.isna()] = NO_LABEL
    return out.set_axis(pd.Series(dates).index)
