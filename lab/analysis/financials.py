"""財報標準化:把 Yahoo / FinMind / SEC 不同欄位名稱統一成同一套欄位。

輸出:
- annual:年度表(Yahoo,4–5 年,欄位最完整)
- quarterly:季度表(台股用 FinMind 2014 起;美股用 Yahoo 5–6 季)
- long:長期年度表(台股 FinMind 年化;美股 SEC 10-K),給 10 年趨勢與長期 CAGR
- ttm:近四季合計(流量項目)+ 最新一季(存量項目)
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..sources import finmind

YF_MAP = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "cogs": ["Cost Of Revenue", "Reconciled Cost Of Revenue"],
    "gross_profit": ["Gross Profit"],
    "rnd": ["Research And Development"],
    "sga": ["Selling General And Administration"],
    "operating_income": ["Operating Income", "Total Operating Income As Reported"],
    "ebit": ["EBIT"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "interest_expense": ["Interest Expense", "Interest Expense Non Operating"],
    "pretax_income": ["Pretax Income"],
    "tax": ["Tax Provision"],
    "tax_rate": ["Tax Rate For Calcs"],
    "net_income": ["Net Income Common Stockholders", "Net Income"],
    "eps_diluted": ["Diluted EPS", "Basic EPS"],
    "shares_diluted": ["Diluted Average Shares", "Basic Average Shares"],
    "da": ["Reconciled Depreciation", "Depreciation And Amortization", "Depreciation Amortization Depletion"],
    "total_assets": ["Total Assets"],
    "current_assets": ["Current Assets"],
    "cash": ["Cash And Cash Equivalents", "Cash Financial"],
    "cash_sti": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"],
    "receivables": ["Accounts Receivable", "Receivables"],
    "inventory": ["Inventory"],
    "ppe": ["Net PPE"],
    "current_liabilities": ["Current Liabilities"],
    "total_liabilities": ["Total Liabilities Net Minority Interest"],
    "total_debt": ["Total Debt"],
    "long_term_debt": ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"],
    "payables": ["Accounts Payable", "Payables"],
    "equity": ["Stockholders Equity", "Common Stock Equity"],
    "retained_earnings": ["Retained Earnings"],
    "shares_out": ["Ordinary Shares Number", "Share Issued"],
    "goodwill_intangibles": ["Goodwill And Other Intangible Assets"],
    "cfo": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure", "Purchase Of PPE"],
    "fcf": ["Free Cash Flow"],
    "dividends_paid": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
    "buybacks": ["Repurchase Of Capital Stock", "Common Stock Payments"],
    "sbc": ["Stock Based Compensation"],
    "change_wc": ["Change In Working Capital"],
}

FLOW_ITEMS = ["revenue", "cogs", "gross_profit", "rnd", "sga", "operating_income", "ebit", "ebitda", "interest_expense",
              "pretax_income", "tax", "net_income", "eps_diluted", "da", "cfo", "capex", "fcf", "dividends_paid",
              "buybacks", "sbc", "change_wc"]


def from_yahoo(*frames: pd.DataFrame | None) -> pd.DataFrame:
    """Yahoo 財報(columns=期末日)→ index=期末日(升冪)、columns=標準欄位。"""
    rows: dict[str, pd.Series] = {}
    for key, names in YF_MAP.items():
        for f in frames:
            if f is None or f.empty:
                continue
            for n in names:
                if n in f.index:
                    s = pd.to_numeric(f.loc[n], errors="coerce")
                    if key in rows:
                        rows[key] = rows[key].combine_first(s)
                    else:
                        rows[key] = s
                    break
            if key in rows and rows[key].notna().any():
                break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    # 補算
    if "fcf" not in df or df["fcf"].isna().all():
        if "cfo" in df and "capex" in df:
            df["fcf"] = df["cfo"] + df["capex"]
    if ("gross_profit" not in df or df["gross_profit"].isna().all()) and "revenue" in df and "cogs" in df:
        df["gross_profit"] = df["revenue"] - df["cogs"]
    if "ebit" not in df or df["ebit"].isna().all():
        if "operating_income" in df:
            df["ebit"] = df["operating_income"]
    # 去掉幾乎全空的期別(Yahoo 常在最舊一期只有零星欄位)
    core = [c for c in ("revenue", "net_income", "total_assets") if c in df]
    if core:
        df = df[df[core].notna().sum(axis=1) >= 2]
    return df


# ---------- 台股 FinMind ----------

def _first(w: pd.DataFrame, names: list[str]) -> pd.Series:
    for n in names:
        if n in w.columns:
            return w[n]
    return pd.Series(np.nan, index=w.index)


def _sum(w: pd.DataFrame, names: list[str]) -> pd.Series:
    cols = [n for n in names if n in w.columns]
    if not cols:
        return pd.Series(np.nan, index=w.index)
    return w[cols].sum(axis=1, min_count=1)


def tw_quarterly(fm: dict) -> pd.DataFrame:
    inc = finmind.pivot_statement(fm.get("income"))
    bal = finmind.pivot_statement(fm.get("balance"))
    cf = finmind.decumulate(finmind.pivot_statement(fm.get("cashflow")))
    if inc.empty:
        return pd.DataFrame()
    q = pd.DataFrame(index=inc.index.union(bal.index))
    q["revenue"] = _first(inc, ["Revenue"])
    q["cogs"] = _first(inc, ["CostOfGoodsSold"])
    q["gross_profit"] = _first(inc, ["GrossProfit"])
    q["operating_expenses"] = _first(inc, ["OperatingExpenses"])
    q["operating_income"] = _first(inc, ["OperatingIncome"])
    q["pretax_income"] = _first(inc, ["PreTaxIncome"])
    q["tax"] = _first(inc, ["TAX"])
    q["net_income"] = _first(inc, ["EquityAttributableToOwnersOfParent", "IncomeAfterTaxes"])
    q["eps_diluted"] = _first(inc, ["EPS"])
    if not bal.empty:
        b = bal.reindex(q.index)
        q["total_assets"] = _first(b, ["TotalAssets"])
        q["current_assets"] = _first(b, ["CurrentAssets"])
        q["cash"] = _first(b, ["CashAndCashEquivalents"])
        q["receivables"] = _sum(b, ["AccountsReceivableNet", "AccountsReceivableDuefromRelatedPartiesNet", "NotesReceivableNet"])
        q["inventory"] = _first(b, ["Inventories"])
        q["ppe"] = _first(b, ["PropertyPlantAndEquipment"])
        q["current_liabilities"] = _first(b, ["CurrentLiabilities"])
        q["total_liabilities"] = _first(b, ["Liabilities"])
        q["payables"] = _sum(b, ["AccountsPayable", "AccountsPayableToRelatedParties", "NotesPayable"])
        q["total_debt"] = _sum(b, ["ShorttermBorrowings", "ShortTermBorrowings", "LongtermBorrowings", "BondsPayable",
                                   "CurrentPortionOfLongtermBorrowings", "LongTermBorrowingsCurrentPortion",
                                   "ShortTermNotesAndBillsPayable", "LeaseLiabilitiesCurrent", "LeaseLiabilitiesNonCurrent"])
        q["equity"] = _first(b, ["EquityAttributableToOwnersOfParent", "Equity"])
        q["retained_earnings"] = _first(b, ["RetainedEarnings"])
        share_cap = _first(b, ["OrdinaryShare", "CapitalStock"])
        q["shares_out"] = share_cap / 10.0  # 台股面額 10 元
    if not cf.empty:
        c = cf.reindex(q.index)
        q["cfo"] = _first(c, ["CashFlowsFromOperatingActivities", "NetCashInflowFromOperatingActivities"])
        q["capex"] = _first(c, ["PropertyAndPlantAndEquipment", "AcquisitionOfPropertyPlantAndEquipment"])
        q["da"] = _sum(c, ["Depreciation", "AmortizationExpense"])
        q["interest_expense"] = _first(c, ["InterestExpense", "PayTheInterest"]).abs()
        q["dividends_paid"] = _first(c, ["CashDividendsPaid", "DividendsPaid"])
        q["fcf"] = q["cfo"] + q["capex"]
    q["ebit"] = q["operating_income"]
    if "da" in q:
        q["ebitda"] = q["operating_income"] + q["da"]
    q = q[q["revenue"].notna()]
    return q.sort_index()


def annualize_quarters(q: pd.DataFrame) -> pd.DataFrame:
    """季 → 年(流量加總,只取四季齊全的年度;存量取年底)。"""
    if q.empty:
        return q
    out = {}
    for y, g in q.groupby(q.index.year):
        if len(g) < 4:
            continue
        row = {}
        for c in g.columns:
            if c in FLOW_ITEMS or c == "operating_expenses":
                row[c] = g[c].sum(min_count=4)
            else:
                row[c] = g[c].iloc[-1]
        out[pd.Timestamp(year=y, month=12, day=31)] = row
    return pd.DataFrame(out).T.sort_index()


def ttm(q: pd.DataFrame, offset: int = 0) -> dict | None:
    """最近四季(offset=4 → 前一年同期的 TTM)。需要四個連續季度。"""
    if q is None or q.empty or len(q) < 4 + offset:
        return None
    g = q.iloc[len(q) - 4 - offset: len(q) - offset]
    months = (g.index.year * 12 + g.index.month).to_numpy()
    if not np.all(np.diff(months) == 3):
        return None
    row = {}
    for c in g.columns:
        if c in FLOW_ITEMS or c == "operating_expenses":
            v = g[c].sum(min_count=4)
            row[c] = None if pd.isna(v) else float(v)
        else:
            v = g[c].iloc[-1]
            row[c] = None if pd.isna(v) else float(v)
    row["period_end"] = g.index[-1].date().isoformat()
    return row


def ttm_series(q: pd.DataFrame, col: str) -> pd.Series:
    """每一季往回四季加總的序列(例如 TTM EPS 序列,畫本益比河流用)。"""
    if q is None or q.empty or col not in q:
        return pd.Series(dtype=float)
    s = q[col]
    months = pd.Series(q.index.year * 12 + q.index.month, index=q.index)
    ok = months.diff(3) == 9
    return s.rolling(4, min_periods=4).sum().where(ok)


def safe(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def table_to_records(df: pd.DataFrame, cols: list[str] | None = None, date_fmt: str = "%Y-%m-%d") -> list[dict]:
    if df is None or df.empty:
        return []
    cols = cols or list(df.columns)
    out = []
    for idx, r in df.iterrows():
        rec = {"period": idx.strftime(date_fmt) if hasattr(idx, "strftime") else str(idx)}
        for c in cols:
            rec[c] = safe(r.get(c)) if c in df.columns else None
        out.append(rec)
    return out
