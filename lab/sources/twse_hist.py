"""證交所歷史資料(免費、官方網站;robots.txt 允許):發行量加權股價報酬指數、每日收盤行情(全部上市股)。

禮貌存取:
- 全部請求共用一個跨程序限速器(檔案鎖),兩次請求至少間隔 MIN_INTERVAL 秒;
- 失敗時指數退避重試(最多 4 次),仍失敗就丟例外,由呼叫端決定降級;
- 歷史資料不會變,快取永久保存(只有當月 / 當天的資料會重抓)。
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import time

import pandas as pd
import requests

from ..cache import cached
from ..config import DATA_DIR

UA = "invest-lab personal research tool (low-rate, cached)"
MIN_INTERVAL = 3.2
_RATE_FILE = DATA_DIR / "twse_rate.json"
FOREVER = 60 * 60 * 24 * 365 * 20


def _wait_turn() -> None:
    _RATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_RATE_FILE, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            try:
                last = json.loads(f.read() or "{}").get("last", 0.0)
            except json.JSONDecodeError:
                last = 0.0
            gap = time.time() - last
            if gap < MIN_INTERVAL:
                time.sleep(MIN_INTERVAL - gap)
            f.seek(0)
            f.truncate()
            f.write(json.dumps({"last": time.time()}))
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _get_json(url: str, params: dict) -> dict:
    err = None
    for attempt in range(4):
        _wait_turn()
        try:
            r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=40)
            if r.status_code == 200:
                j = r.json()
                if j.get("stat") in ("OK", "很抱歉,沒有符合條件的資料!") or "沒有符合條件" in str(j.get("stat")):
                    return j
                err = RuntimeError(f"TWSE stat={j.get('stat')}")
            else:
                err = RuntimeError(f"TWSE HTTP {r.status_code}")
        except (requests.RequestException, ValueError) as e:
            err = e
        time.sleep(min(60, 5 * 2 ** attempt))
    raise err or RuntimeError("TWSE 失敗")


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def _roc(s: str) -> pd.Timestamp:
    y, m, d = s.split("/")
    return pd.Timestamp(int(y) + 1911, int(m), int(d))


def total_return_month(year: int, month: int) -> pd.Series:
    """某個月的發行量加權股價報酬指數(日)。"""
    key = f"{year:04d}{month:02d}"
    cur = dt.date.today()
    ttl = 60 * 60 * 6 if (year, month) >= (cur.year, cur.month) else FOREVER

    def fetch():
        j = _get_json("https://www.twse.com.tw/rwd/zh/TAIEX/MFI94U", {"date": f"{key}01", "response": "json"})
        return [(r[0], r[1]) for r in j.get("data") or []]

    rows = cached("twse_tr", key, ttl, fetch)
    return pd.Series({_roc(d): _num(v) for d, v in rows}, dtype=float).sort_index()


def total_return_index(start: str = "2014-01-01", end: str | None = None) -> pd.Series:
    s, e = pd.Timestamp(start), pd.Timestamp(end or dt.date.today())
    parts = [total_return_month(p.year, p.month) for p in pd.period_range(s, e, freq="M")]
    out = pd.concat([p for p in parts if len(p)]).sort_index()
    return out[(out.index >= s) & (out.index <= e)]


def daily_all(day: dt.date | str) -> pd.DataFrame | None:
    """某一天全部上市股票(不含權證、牛熊證)的收盤行情;休市回傳 None。"""
    d = pd.Timestamp(day)
    key = d.strftime("%Y%m%d")
    ttl = 60 * 60 * 6 if d.date() >= dt.date.today() else FOREVER

    def fetch():
        j = _get_json("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
                      {"date": key, "type": "ALLBUT0999", "response": "json"})
        for t in j.get("tables") or []:
            if "證券代號" in (t.get("fields") or []):
                return {"fields": t["fields"], "data": t["data"]}
        return None

    raw = cached("twse_daily_all", key, ttl, fetch)
    if not raw:
        return None
    df = pd.DataFrame(raw["data"], columns=raw["fields"])
    out = pd.DataFrame({
        "code": df["證券代號"].str.strip(), "name": df["證券名稱"].str.strip(),
        "volume": df["成交股數"].map(_num), "value": df["成交金額"].map(_num),
        "open": df["開盤價"].map(_num), "high": df["最高價"].map(_num), "low": df["最低價"].map(_num),
        "close": df["收盤價"].map(_num), "pe": df["本益比"].map(_num),
    })
    out["date"] = d
    return out


def stock_month(code: str, year: int, month: int) -> pd.DataFrame | None:
    """單一上市股某個月的日成交資訊(原始價格;下市股也有)。沒有資料回傳 None。"""
    key = f"{code}|{year:04d}{month:02d}"
    cur = dt.date.today()
    ttl = 60 * 60 * 6 if (year, month) >= (cur.year, cur.month) else FOREVER

    def fetch():
        j = _get_json("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY",
                      {"date": f"{year:04d}{month:02d}01", "stockNo": code, "response": "json"})
        return {"fields": j.get("fields"), "data": j.get("data")} if j.get("data") else None

    raw = cached("twse_stock_month", key, ttl, fetch)
    if not raw:
        return None
    df = pd.DataFrame(raw["data"], columns=raw["fields"])
    out = pd.DataFrame({
        "date": df["日期"].map(_roc), "volume": df["成交股數"].map(_num), "value": df["成交金額"].map(_num),
        "open": df["開盤價"].map(_num), "high": df["最高價"].map(_num), "low": df["最低價"].map(_num),
        "close": df["收盤價"].map(_num),
    })
    return out.set_index("date").sort_index()
