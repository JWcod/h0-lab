"""Fetcher:台股 PIT 股票池候選(每月依近 12 個月平均成交金額取前 50 名,2016 起的聯集)的 FinMind 財報。

遵守免費額度:每次呼叫前看額度,快用完就等;資料進快取(與網站、回測同一套 key)。
用法:PYTHONPATH=. .venv/bin/python scripts/fetch_finmind_universe.py → 進度寫 data/research/fetch_finmind.log
"""
import datetime as dt
import time

import pandas as pd

from lab.config import DATA_DIR
from lab.sources import finmind as F
from lab.sources import twse_hist as T

log = DATA_DIR / "research" / "fetch_finmind.log"


def say(m):
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%H:%M:%S} {m}\n")


def candidates(n=50, months_window=12, start="2016-01") -> list[str]:
    days = pd.bdate_range("2015-01-01", dt.date.today() - dt.timedelta(days=1))
    frames = []
    for w in [d for d in days if d.weekday() == 2]:
        for k in range(3):
            df = T.daily_all(w + pd.Timedelta(days=k))
            if df is not None and len(df):
                frames.append(df)
                break
    a = pd.concat(frames)
    a = a[a.code.str.fullmatch(r"[1-9]\d{3}")]
    a["month"] = a.date.dt.to_period("M")
    mv = a.groupby(["month", "code"])["value"].mean().reset_index()
    u = set()
    for m in [m for m in sorted(mv.month.unique()) if m >= pd.Period(start, "M")]:
        r = mv[(mv.month <= m) & (mv.month > m - months_window)].groupby("code")["value"].mean()
        u |= set(r.sort_values(ascending=False).index[:n])
    return sorted(u)


JOBS = [("TaiwanStockFinancialStatements", "2013-01-01", "statements_slow"),
        ("TaiwanStockBalanceSheet", "2013-01-01", "statements_slow"),
        ("TaiwanStockCashFlowsStatement", "2013-01-01", "statements_slow"),
        ("TaiwanStockMonthRevenue", "2013-01-01", "statements"),
        ("TaiwanStockDividend", "2010-01-01", "statements_slow")]

codes = candidates()
(DATA_DIR / "research" / "tw_pit50_union.txt").write_text("\n".join(codes), encoding="utf-8")
say(f"候選 {len(codes)} 檔,資料集 {len(JOBS)} 個")
done = 0
for c in codes:
    for ds, start, ttl in JOBS:
        from lab.cache import age
        a = age("finmind", f"{ds}|{c}|{start}")
        if a is not None:
            continue  # 已有快取(歷史資料,不用重抓)
        while True:
            st = F.budget_status()
            if st["cooldown_remaining"] == 0 and st["used_last_hour"] < st["budget"] - 5:
                break
            time.sleep(60)
        df = F.get(ds, c, start, ttl)
        done += 1
        if df is None:
            say(f"{c} {ds} 失敗或無資料")
        time.sleep(1.0)
    if done and done % 50 == 0:
        say(f"進度:呼叫 {done} 次(目前 {c})")
say(f"完成:共呼叫 {done} 次")
