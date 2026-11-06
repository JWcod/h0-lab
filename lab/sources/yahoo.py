"""Yahoo Finance(透過 yfinance,免費、非官方)。

台股與美股共用:基本資料、股價、財報(年 4–5 年、季 5–6 季)、分析師預估、目標價、
評等變動、內部人交易、機構持股、財報日。
"""
from __future__ import annotations

import logging

import pandas as pd
import yfinance as yf

from ..cache import cached
from ..config import TTL

log = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


LABELS = {
    "info": "公司基本資料與報價", "income_stmt": "年度損益表", "quarterly_income_stmt": "季度損益表",
    "balance_sheet": "年度資產負債表", "quarterly_balance_sheet": "季度資產負債表", "cashflow": "年度現金流量表",
    "quarterly_cashflow": "季度現金流量表", "recommendations": "分析師評等分布", "upgrades_downgrades": "升降評紀錄",
    "earnings_estimate": "EPS 預估", "revenue_estimate": "營收預估", "eps_trend": "EPS 預估趨勢", "eps_revisions": "預估修正",
    "growth_estimates": "成長預估", "earnings_history": "財報驚喜", "analyst_price_targets": "分析師目標價",
    "calendar": "財報日與除息日", "insider_transactions": "內部人交易", "insider_purchases": "內部人買賣彙總",
    "institutional_holders": "機構持股", "mutualfund_holders": "基金持股", "major_holders": "持股結構",
    "dividends": "股利紀錄", "splits": "股票分割", "earnings_dates": "歷次財報日",
}


class Status:
    """記錄每個資料來源成功/失敗,報告裡會顯示資料完整度。listener:即時回報給前端的 agent 畫面。"""

    def __init__(self, listener=None):
        self.items: list[dict] = []
        self.listener = listener

    def _emit(self, rec):
        if self.listener:
            try:
                self.listener(rec)
            except Exception:  # noqa: BLE001
                pass

    def ok(self, source: str, what: str, n: int | None = None):
        rec = {"source": source, "item": LABELS.get(what, what), "ok": True, "n": n}
        self.items.append(rec)
        self._emit(rec)

    def fail(self, source: str, what: str, err: str):
        rec = {"source": source, "item": LABELS.get(what, what), "ok": False, "error": err[:200]}
        self.items.append(rec)
        self._emit(rec)


def _empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, (pd.DataFrame, pd.Series)):
        return v.empty
    if isinstance(v, (dict, list)):
        return len(v) == 0
    return False


def _get(symbol: str, attr: str, ttl_key: str, status: Status, refresh: bool):
    def fetch():
        t = yf.Ticker(symbol)
        v = getattr(t, attr)
        if callable(v):
            v = v()
        return v

    try:
        v = cached("yahoo", f"{symbol}|{attr}", TTL[ttl_key], fetch, refresh)
        if _empty(v):
            status.fail("Yahoo", attr, "無資料")
        else:
            status.ok("Yahoo", attr, len(v) if hasattr(v, "__len__") else None)
        return v
    except Exception as e:  # noqa: BLE001
        status.fail("Yahoo", attr, str(e))
        return None


def history(symbol: str, status: Status, refresh: bool = False, period: str = "10y") -> pd.DataFrame | None:
    def fetch():
        h = yf.Ticker(symbol).history(period=period, auto_adjust=False, actions=True)
        if h is None or h.empty:
            raise ValueError("no history")
        h.index = pd.to_datetime(h.index).tz_localize(None)
        return h

    try:
        h = cached("yahoo_hist", f"{symbol}|{period}", TTL["price"], fetch, refresh)
        status.ok("Yahoo", f"股價歷史 {period}", len(h))
        return h
    except Exception as e:  # noqa: BLE001
        status.fail("Yahoo", f"股價歷史 {period}", str(e))
        return None


def history_since(symbol: str, start: str = "2013-01-01", refresh: bool = False) -> pd.DataFrame | None:
    """研究用的長歷史(固定起始日,快取 key 不會隨日期移動;含 Dividends / Stock Splits)。Fetcher 建資料版本時使用。"""
    def fetch():
        h = yf.Ticker(symbol).history(start=start, auto_adjust=False, actions=True)
        if h is None or h.empty:
            raise ValueError("no history")
        h.index = pd.to_datetime(h.index).tz_localize(None)
        return h

    try:
        return cached("px_since", f"{symbol}|{start}", TTL["statements"], fetch, refresh)
    except Exception:  # noqa: BLE001
        return None


def index_history(symbol: str, period: str = "10y") -> pd.DataFrame | None:
    """指數 / 總經序列(^GSPC、^TWII、^TNX、^VIX、TWD=X)。"""
    def fetch():
        h = yf.Ticker(symbol).history(period=period, auto_adjust=True)
        if h is None or h.empty:
            raise ValueError("no history")
        h.index = pd.to_datetime(h.index).tz_localize(None)
        return h

    try:
        return cached("yahoo_idx", f"{symbol}|{period}", TTL["macro"], fetch)
    except Exception:  # noqa: BLE001
        return None


def bundle(symbol: str, status: Status, refresh: bool = False) -> dict:
    """抓一家公司在 Yahoo 上的所有資料。"""
    out: dict = {}
    out["info"] = _get(symbol, "info", "info", status, refresh) or {}
    for attr in ("income_stmt", "quarterly_income_stmt", "balance_sheet", "quarterly_balance_sheet",
                 "cashflow", "quarterly_cashflow"):
        out[attr] = _get(symbol, attr, "statements", status, refresh)
    for attr in ("recommendations", "upgrades_downgrades", "earnings_estimate", "revenue_estimate",
                 "eps_trend", "eps_revisions", "growth_estimates", "earnings_history",
                 "analyst_price_targets", "calendar"):
        out[attr] = _get(symbol, attr, "info", status, refresh)
    for attr in ("insider_transactions", "insider_purchases", "institutional_holders",
                 "mutualfund_holders", "major_holders", "dividends", "splits"):
        out[attr] = _get(symbol, attr, "info", status, refresh)
    out["earnings_dates"] = _get_earnings_dates(symbol, status, refresh)
    out["history"] = history(symbol, status, refresh)
    return out


def _get_earnings_dates(symbol: str, status: Status, refresh: bool):
    def fetch():
        return yf.Ticker(symbol).get_earnings_dates(limit=24)

    try:
        v = cached("yahoo", f"{symbol}|earnings_dates", TTL["info"], fetch, refresh)
        if _empty(v):
            status.fail("Yahoo", "earnings_dates", "無資料")
        else:
            status.ok("Yahoo", "earnings_dates", len(v))
        return v
    except Exception as e:  # noqa: BLE001
        status.fail("Yahoo", "earnings_dates", str(e))
        return None


def quick_info(symbol: str) -> dict:
    """同業比較用:只抓 info(有快取)。"""
    try:
        return cached("yahoo", f"{symbol}|info", TTL["peers"], lambda: yf.Ticker(symbol).info) or {}
    except Exception:  # noqa: BLE001
        return {}


def industry_peers(industry_key: str | None, limit: int = 10) -> list[dict]:
    if not industry_key:
        return []

    def fetch():
        ind = yf.Industry(industry_key)
        tc = ind.top_companies
        if tc is None or tc.empty:
            return []
        rows = []
        for sym, r in tc.head(limit + 5).iterrows():
            rows.append({"symbol": sym, "name": r.get("name"), "weight": float(r.get("market weight") or 0)})
        return rows

    try:
        return cached("yahoo_ind", industry_key, TTL["peers"], fetch)
    except Exception:  # noqa: BLE001
        return []


def sector_top(sector_key: str | None, limit: int = 12) -> list[dict]:
    if not sector_key:
        return []

    def fetch():
        tc = yf.Sector(sector_key).top_companies
        if tc is None or tc.empty:
            return []
        return [{"symbol": s, "name": r.get("name")} for s, r in tc.head(limit).iterrows()]

    try:
        return cached("yahoo_sec", sector_key, TTL["peers"], fetch)
    except Exception:  # noqa: BLE001
        return []


def search(q: str, n: int = 8) -> list[dict]:
    def fetch():
        s = yf.Search(q, max_results=n, news_count=0)
        return [
            {"symbol": x.get("symbol"), "name": x.get("shortname") or x.get("longname"),
             "exchange": x.get("exchange"), "type": x.get("quoteType")}
            for x in (s.quotes or [])
        ]

    try:
        return cached("yahoo_search", q.lower(), TTL["universe"], fetch)
    except Exception:  # noqa: BLE001
        return []


def news(q: str, n: int = 20) -> list[dict]:
    def fetch():
        s = yf.Search(q, max_results=1, news_count=n)
        out = []
        for x in s.news or []:
            out.append({
                "title": x.get("title"), "link": x.get("link"), "publisher": x.get("publisher"),
                "time": x.get("providerPublishTime"), "related": x.get("relatedTickers") or [],
            })
        return out

    try:
        return cached("yahoo_news", q.lower(), TTL["news"], fetch)
    except Exception:  # noqa: BLE001
        return []
