"""Fetcher:抓證交所歷史資料進快取(報酬指數每月一次、每日收盤行情每週抽一個交易日)。限速見 lab/sources/twse_hist.py。

用法:.venv/bin/python scripts/fetch_twse_history.py [起始年] → 進度寫到 data/research/fetch_twse.log
"""
import datetime as dt
import sys
import time

import pandas as pd

from lab.config import DATA_DIR
from lab.sources import twse_hist as T

start_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2015
log = DATA_DIR / "research" / "fetch_twse.log"
log.parent.mkdir(parents=True, exist_ok=True)


def say(msg):
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%H:%M:%S} {msg}\n")


t0 = time.time()
say(f"開始:報酬指數 {start_year - 1}-01 起")
months = pd.period_range(f"{start_year - 1}-01", dt.date.today(), freq="M")
for i, p in enumerate(months):
    try:
        s = T.total_return_month(p.year, p.month)
        if i % 12 == 0:
            say(f"報酬指數 {p} {len(s)} 天")
    except Exception as e:  # noqa: BLE001
        say(f"報酬指數 {p} 失敗:{e}")
# 每週抽一個交易日:每週三(休市就往後找到週五為止)
days = pd.bdate_range(f"{start_year}-01-01", dt.date.today() - dt.timedelta(days=1))
weds = [d for d in days if d.weekday() == 2]
done = 0
for w in weds:
    for k in range(3):
        d = w + pd.Timedelta(days=k)
        try:
            df = T.daily_all(d)
        except Exception as e:  # noqa: BLE001
            say(f"每日行情 {d.date()} 失敗:{e}")
            df = None
            break
        if df is not None and len(df):
            done += 1
            break
    if done % 50 == 0:
        say(f"每日行情 進度 {done}/{len(weds)}(最新 {w.date()})")
say(f"完成:每日行情 {done} 天,耗時 {round((time.time() - t0) / 60)} 分鐘")
