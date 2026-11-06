"""P2-H07 Fetcher:抓台股籌碼歷史(FinMind 三大法人、融資融券、外資持股),2016-01-01 起,一檔一個資料集一次。

只有 Fetcher(維護者)執行;已核准(docs/預先登記.md P2-H07、§4 第 3 點;預估約 630 次 / 2.5 小時)。
- 每小時最多 SELF_LIMIT 次(低於 FinMind 免費額度,留給網站),額度不夠就等,不繞過限制。
- 快取 key「<dataset>|<代號>|2016-01-01|hist」和網站的(近 400 天)分開,歷史只抓一次。
- 可中斷重跑:已有快取的跳過。進度寫 <out>/progress.jsonl。

用法:.venv/bin/python scripts/fetch_chips_hist.py <代號清單檔> <輸出資料夾>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lab.cache import cached  # noqa: E402
from lab.sources import finmind as FM  # noqa: E402

DATASETS = ("TaiwanStockInstitutionalInvestorsBuySell", "TaiwanStockMarginPurchaseShortSale", "TaiwanStockShareholding")
START = "2016-01-01"
SELF_LIMIT = 240
TTL_FOREVER = 60 * 60 * 24 * 365 * 20   # 已結束期間的歷史不重抓


def key(ds: str, code: str) -> str:
    return f"{ds}|{code}|{START}|hist"


def main(codes_file: str, out_dir: str) -> int:
    codes = [c.strip() for c in Path(codes_file).read_text(encoding="utf-8").splitlines() if c.strip()]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log = (out / "progress.jsonl").open("a", encoding="utf-8")
    done = fail = 0
    for ds in DATASETS:
        for code in codes:
            while FM.budget_status()["used_last_hour"] >= SELF_LIMIT or FM.budget_status()["cooldown_remaining"] > 0:
                time.sleep(60)
            t0 = time.time()
            try:
                df = cached("finmind", key(ds, code), TTL_FOREVER, lambda ds=ds, code=code: FM._fetch(ds, code, START))
                rec = {"ds": ds, "code": code, "rows": int(len(df)), "first": str(df["date"].min()) if len(df) else None,
                       "last": str(df["date"].max()) if len(df) else None}
                done += 1
            except FM.LimitError as e:
                rec = {"ds": ds, "code": code, "error": f"LimitError: {e}"}
                fail += 1
                time.sleep(300)
            except Exception as e:  # noqa: BLE001  單檔失敗不中斷,記下來之後補抓
                rec = {"ds": ds, "code": code, "error": f"{type(e).__name__}: {e}"[:200]}
                fail += 1
                time.sleep(5)
            rec["sec"] = round(time.time() - t0, 1)
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log.flush()
    print(json.dumps({"done": done, "fail": fail}, ensure_ascii=False))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
