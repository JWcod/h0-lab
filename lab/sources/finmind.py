"""FinMind(台股開源資料,免費額度不需 token)。

用到的資料集:三大法人、融資融券、外資持股、月營收、本益比/淨值比歷史、股利、
三大財報(2014 起,季資料)、借券、股票清單。
注意:現金流量表是「年初至今累計」,要自己拆成單季。
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import time

import pandas as pd
import requests

from ..cache import cached
from ..config import FINMIND_BUDGET_FILE, FINMIND_TOKEN, TTL, today
from .yahoo import Status

API = "https://api.finmindtrade.com/api/v4/data"
BUDGET_FILE = FINMIND_BUDGET_FILE
HOURLY_BUDGET = 280 if not FINMIND_TOKEN else 560  # 免費約 300 次/小時(有 token 約 600),留一點餘裕


class LimitError(RuntimeError):
    pass


def _budget_take() -> None:
    """跨程序共用的呼叫額度(伺服器與命令列掃描共用一個檔案計數),超過就不呼叫。"""
    BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(BUDGET_FILE, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            st = json.loads(f.read() or "{}")
        except json.JSONDecodeError:
            st = {}
        now = time.time()
        calls = [t for t in st.get("calls", []) if now - t < 3600]
        if now < st.get("cooldown_until", 0):
            raise LimitError("FinMind 免費額度用完,暫停中(約 1 小時內恢復;有快取的資料仍可用)")
        if len(calls) >= HOURLY_BUDGET:
            raise LimitError(f"FinMind 本小時已用 {len(calls)} 次(上限約 {HOURLY_BUDGET}),暫停呼叫;有快取的資料仍可用")
        calls.append(now)
        st["calls"] = calls
        f.seek(0)
        f.truncate()
        f.write(json.dumps(st))
        fcntl.flock(f, fcntl.LOCK_UN)


def _set_cooldown(seconds: int = 3600) -> None:
    with open(BUDGET_FILE, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            st = json.loads(f.read() or "{}")
        except json.JSONDecodeError:
            st = {}
        st["cooldown_until"] = time.time() + seconds
        f.seek(0)
        f.truncate()
        f.write(json.dumps(st))
        fcntl.flock(f, fcntl.LOCK_UN)


def budget_status() -> dict:
    try:
        st = json.loads(BUDGET_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        st = {}
    now = time.time()
    return {"used_last_hour": len([t for t in st.get("calls", []) if now - t < 3600]), "budget": HOURLY_BUDGET,
            "cooldown_remaining": max(0, int(st.get("cooldown_until", 0) - now))}


def _fetch(dataset: str, data_id: str | None, start: str | None) -> pd.DataFrame:
    _budget_take()
    params = {"dataset": dataset}
    if data_id:
        params["data_id"] = data_id
    if start:
        params["start_date"] = start
    headers = {"Authorization": f"Bearer {FINMIND_TOKEN}"} if FINMIND_TOKEN else {}
    r = requests.get(API, params=params, headers=headers, timeout=45)
    j = r.json()
    if j.get("status") != 200:
        msg = str(j.get("msg"))
        if r.status_code in (402, 429) or "limit" in msg.lower():
            _set_cooldown(3600)
            raise LimitError(f"FinMind 免費額度用完:{msg}")
        raise RuntimeError(f"FinMind {dataset}: {msg}")
    return pd.DataFrame(j.get("data", []))


def get(dataset: str, data_id: str | None, start: str | None, ttl_key: str, status: Status | None = None,
        refresh: bool = False, label: str | None = None) -> pd.DataFrame | None:
    label = label or dataset
    try:
        df = cached("finmind", f"{dataset}|{data_id}|{start}", TTL[ttl_key],
                    lambda: _fetch(dataset, data_id, start), refresh)
        if status:
            (status.ok if not df.empty else status.fail)("FinMind", label, len(df) if not df.empty else "無資料")
        return df
    except Exception as e:  # noqa: BLE001
        if status:
            status.fail("FinMind", label, str(e))
        return None


def _ago(days: int) -> str:
    # 起始日對齊到月初,讓快取 key 在一天內穩定
    d = today() - dt.timedelta(days=days)
    return d.replace(day=1).isoformat()


def needs_calls(stock_id: str) -> bool:
    """這檔股票的 FinMind 資料是否需要重新抓(有任何一項快取過期就算)。"""
    from ..cache import age
    checks = [("TaiwanStockInstitutionalInvestorsBuySell", _ago(400), "chips"),
              ("TaiwanStockMonthRevenue", "2013-01-01", "statements"),
              ("TaiwanStockFinancialStatements", "2013-01-01", "statements_slow")]
    for ds, start, ttl in checks:
        a = age("finmind", f"{ds}|{stock_id}|{start}")
        if a is None or a > TTL[ttl]:
            return True
    return False


def bundle(stock_id: str, status: Status, refresh: bool = False) -> dict:
    out = {}
    out["institutional"] = get("TaiwanStockInstitutionalInvestorsBuySell", stock_id, _ago(400), "chips", status, refresh, "三大法人買賣超")
    out["margin"] = get("TaiwanStockMarginPurchaseShortSale", stock_id, _ago(400), "chips", status, refresh, "融資融券")
    out["shareholding"] = get("TaiwanStockShareholding", stock_id, _ago(400), "chips", status, refresh, "外資持股比例")
    out["short_balance"] = get("TaiwanDailyShortSaleBalances", stock_id, _ago(200), "chips", status, refresh, "借券賣出餘額")
    out["month_revenue"] = get("TaiwanStockMonthRevenue", stock_id, "2013-01-01", "statements", status, refresh, "月營收")
    out["per"] = get("TaiwanStockPER", stock_id, _ago(365 * 10 + 40), "statements", status, refresh, "本益比/淨值比歷史")
    # 季財報、股利一季才變一次 → 快取 5 天(「重新分析」仍可強制更新)
    out["dividend"] = get("TaiwanStockDividend", stock_id, "2010-01-01", "statements_slow", status, refresh, "股利政策")
    out["income"] = get("TaiwanStockFinancialStatements", stock_id, "2013-01-01", "statements_slow", status, refresh, "綜合損益表(季)")
    out["balance"] = get("TaiwanStockBalanceSheet", stock_id, "2013-01-01", "statements_slow", status, refresh, "資產負債表(季)")
    out["cashflow"] = get("TaiwanStockCashFlowsStatement", stock_id, "2013-01-01", "statements_slow", status, refresh, "現金流量表(季)")
    # 個股新聞改用 Google News(FinMind 新聞多為論壇轉貼,且占額度)
    return out


def stock_info() -> pd.DataFrame | None:
    return get("TaiwanStockInfo", None, None, "universe")


# ---------- 財報整理 ----------

def pivot_statement(df: pd.DataFrame | None) -> pd.DataFrame:
    """長表 → 寬表:index=季末日期,columns=type。"""
    if df is None or df.empty:
        return pd.DataFrame()
    w = df.pivot_table(index="date", columns="type", values="value", aggfunc="last")
    w.index = pd.to_datetime(w.index)
    return w.sort_index()


def decumulate(w: pd.DataFrame) -> pd.DataFrame:
    """把年初至今累計數拆成單季(Q1 不變,Qn = YTDn − YTDn-1,同年度內)。"""
    if w.empty:
        return w
    idx = pd.Series(w.index)
    prev_idx = idx.shift(1)
    is_q1 = (w.index.month == 3)
    # 上一列必須是同年度的前一季,才能相減;否則(缺季)設為 NaN,避免算錯
    consecutive = ((idx.dt.year == prev_idx.dt.year) & (idx.dt.month - prev_idx.dt.month == 3)).values
    out = w.copy()
    for col in w.columns:
        s = w[col]
        q = s - s.shift(1)
        q = q.where(consecutive)
        q = q.where(~is_q1, s)
        out[col] = q
    return out
