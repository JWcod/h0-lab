"""網站用的含息大盤(算 beta_2y,見 lab/analysis/income_risk.py)。

- 台股:證交所「發行量加權股價報酬指數」(twse_hist,每月 1 次請求,歷史月份永久快取、當月 6 小時)
- 美股:Yahoo ^SP500TR(S&P 500 Total Return,快取同其他指數)
回測不走這裡:回測的含息大盤在總經 PIT store(TWSE_TR / ^SP500TR)。失敗回傳 None(beta_2y 顯示不可得,不改用不含息的大盤)。
"""
from __future__ import annotations

import pandas as pd

from .. import config


def recent(market: str, years: int = 2) -> pd.Series | None:
    try:
        if market == "TW":
            from . import twse_hist
            start = pd.Timestamp(config.today()) - pd.DateOffset(years=years, months=2)
            s = twse_hist.total_return_index(start.strftime("%Y-%m-01"))
        else:
            from . import yahoo
            h = yahoo.index_history("^SP500TR", "5y")
            s = h["Close"] if h is not None and len(h) else None
    except Exception:  # noqa: BLE001  抓不到(離線、限流):beta_2y 不可得
        return None
    if s is None or not len(s):
        return None
    s = s.astype(float)
    s.index = pd.DatetimeIndex(s.index)
    return s[s.index <= pd.Timestamp(config.today())]
