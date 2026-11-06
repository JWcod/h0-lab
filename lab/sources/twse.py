"""證交所 / 櫃買中心 OpenAPI(官方、免費):全市場本益比、殖利率、淨值比(做同業比較)。"""
from __future__ import annotations

import pandas as pd
import requests

from ..cache import cached
from ..config import TTL


def _num(x):
    try:
        v = float(str(x).replace(",", ""))
        return v
    except (TypeError, ValueError):
        return None


def trade_values() -> dict[str, float]:
    """上市股票當日成交金額(當作公司規模的粗略代理,挑同業用)。"""
    def fetch():
        j = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=40).json()
        return {x.get("Code"): _num(x.get("TradeValue")) or 0.0 for x in j}

    try:
        return cached("twse", "trade_values", TTL["info"], fetch)
    except Exception:  # noqa: BLE001
        return {}


def valuation_table() -> pd.DataFrame:
    """回傳 code, name, pe, pb, yield, market(twse/tpex)。"""
    def fetch():
        rows = []
        try:
            j = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", timeout=40).json()
            for x in j:
                rows.append({"code": x.get("Code"), "name": x.get("Name"), "pe": _num(x.get("PEratio")),
                             "pb": _num(x.get("PBratio")), "dy": _num(x.get("DividendYield")), "market": "twse"})
        except Exception:  # noqa: BLE001
            pass
        try:
            j = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
                             timeout=60).json()
            for x in j:
                rows.append({"code": x.get("SecuritiesCompanyCode"), "name": x.get("CompanyName"),
                             "pe": _num(x.get("PriceEarningRatio")), "pb": _num(x.get("PriceBookRatio")),
                             "dy": _num(x.get("YieldRatio")), "market": "tpex"})
        except Exception:  # noqa: BLE001
            pass
        if not rows:
            raise RuntimeError("TWSE/TPEx valuation table empty")
        return pd.DataFrame(rows)

    return cached("twse", "valuation_table", TTL["info"], fetch)
