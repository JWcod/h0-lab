"""重抓 fetch_finmind.log 裡失敗的呼叫(不抓股利);每次間隔 3 秒。"""
import time

from lab.cache import age
from lab.config import DATA_DIR
from lab.sources import finmind as F

TTL = {"TaiwanStockMonthRevenue": "statements"}
todo = []
for line in open(DATA_DIR / "research" / "fetch_finmind.log", encoding="utf-8"):
    if "失敗或無資料" in line:
        _, code, ds, _ = line.split(maxsplit=3)
        if ds != "TaiwanStockDividend" and age("finmind", f"{ds}|{code}|2013-01-01") is None:
            todo.append((code, ds))
print("重抓", len(todo), flush=True)
bad = []
for code, ds in todo:
    while F.budget_status()["used_last_hour"] >= F.HOURLY_BUDGET - 5:
        time.sleep(60)
    df = F.get(ds, code, "2013-01-01", TTL.get(ds, "statements_slow"))
    if df is None:
        bad.append((code, ds))
    time.sleep(3)
print("仍失敗", bad, flush=True)
