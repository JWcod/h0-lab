"""Fetcher:PIT 股票池裡 Yahoo 沒有資料的已下市股,從證交所抓 2013 起到下市為止的日價格(每月一個請求,限速見 twse_hist)。"""
import pandas as pd

from lab.sources import finmind as F
from lab.sources import twse_hist as T

CODES = ["2311", "2325", "2448", "2456", "2823", "2888", "3454", "3474", "3514", "4733", "6452"]
dl = F.get("TaiwanStockDelisting", None, None, "universe")
end = {r.stock_id: pd.Timestamp(r.date) for r in dl.itertuples()} if dl is not None else {}
n, bad = 0, []
for c in CODES:
    last = end.get(c, pd.Timestamp.today())
    for p in pd.period_range("2013-01", last, freq="M"):
        try:
            T.stock_month(c, p.year, p.month)
        except Exception as e:  # noqa: BLE001  單月失敗(例如網路中斷)不中斷整批;重跑時已抓的月份走快取
            bad.append(f"{c}-{p}")
            print(c, p, "失敗", type(e).__name__, flush=True)
        n += 1
    print(c, "到", last.date(), flush=True)
print("完成,月份數", n, "失敗", len(bad), bad[:20])
