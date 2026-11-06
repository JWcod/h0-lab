"""Fetcher:研究用長歷史股價(2013 起,含股利與分割),股票池 40 + 40 與大盤 / 總經序列。每檔之間停 1 秒。"""
import time

from lab.sources import yahoo
from lab.universe import TW_LARGE, US_LARGE

EXTRA = ["^TWII", "^GSPC", "^SP500TR", "SPY", "0050.TW", "^VIX", "^TNX", "TWD=X"]
syms = [s for s, _ in TW_LARGE] + [s for s, _ in US_LARGE] + EXTRA
bad = []
for s in syms:
    h = yahoo.history_since(s)
    if h is None:
        bad.append(s)
    else:
        print(s, len(h), h.index.min().date(), h.index.max().date(), flush=True)
    time.sleep(1.0)
print("失敗:", bad)
