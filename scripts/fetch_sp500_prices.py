"""Fetcher:S&P 500 現有成分(Wikipedia,含加入日與 CIK)與其 2013 起日價格(Yahoo 批次下載,批次間暫停)。

用途:量化「用今天名單選股」的偏誤(A3)。注意:只有現有成分,已被剔除的公司不在裡面(Wikipedia 已無剔除紀錄表)。
"""
import io
import time

import pandas as pd
import requests
import yfinance as yf

from lab.cache import cached
from lab.config import DATA_DIR

UA = "invest-lab personal research tool (low-rate, cached)"
html = cached("wiki", "List_of_S&P_500_companies", 60 * 60 * 24 * 30, lambda: requests.get(
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers={"User-Agent": UA}, timeout=40).text)
tbl = pd.read_html(io.StringIO(html))[0]
out = DATA_DIR / "research" / "sp500_current.csv"
tbl.to_csv(out, index=False)
syms = [s.replace(".", "-") for s in tbl["Symbol"].astype(str)]
print("成分", len(syms), flush=True)
for i in range(0, len(syms), 85):
    batch = syms[i:i + 85]

    def fetch(batch=batch):
        df = yf.download(batch, start="2013-01-01", auto_adjust=False, actions=True, progress=False,
                         group_by="ticker", threads=False)
        res = {}
        for s in batch:
            try:
                d = df[s].dropna(how="all")
            except KeyError:
                continue
            if len(d):
                d.index = pd.to_datetime(d.index).tz_localize(None)
                res[s] = d
        return res
    got = cached("sp500_px", "|".join(batch), 60 * 60 * 24 * 7, fetch)
    print(f"批次 {i // 85 + 1}: {len(got)}/{len(batch)}", flush=True)
    time.sleep(5)
