"""SEC EDGAR(美國官方、免費):股票清單、10 年以上年度財報(XBRL companyfacts)、申報紀錄。

SEC 要求宣告 User-Agent,設定在 .env 的 SEC_USER_AGENT;沒填就不連 SEC。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import requests

from .. import config, runtime
from ..cache import cached
from ..config import SEC_USER_AGENT, TTL

HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


class SecUserAgentRequired(RuntimeError):
    """每位使用者要宣告自己的聯絡方式(SEC 規定),還沒填就不連 SEC;前端看到 runtime.sec_blocked 會提示填寫。"""


def _require_user_agent() -> None:
    if config.SEC_UA_IS_DEFAULT:
        runtime.sec_blocked = True
        raise SecUserAgentRequired("使用 SEC 資料前,請先在設定填寫你的名字與聯絡信箱")

CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets", "PaymentsForCapitalImprovements"],
    "rnd": ["ResearchAndDevelopmentExpense"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "dividends": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "total_assets": ["Assets"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    # 以下供長期表與歷史回測(Altman、流動比、負債)使用
    "total_liabilities": ["Liabilities"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "tax": ["IncomeTaxExpenseBenefit"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
}


def tickers() -> pd.DataFrame:
    def fetch():
        _require_user_agent()
        r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=40)
        r.raise_for_status()   # SEC 拒絕(403)時要看得出原因,不是「對照表找不到」
        j = r.json()
        return pd.DataFrame([{"ticker": v["ticker"], "cik": int(v["cik_str"]), "title": v["title"]} for v in j.values()])

    return cached("sec", "tickers", TTL["universe"], fetch)


# SEC company_tickers.json 對錯或對到非主體申報者時的手動覆寫(代號 → 正確 CIK)。
# XOM:對照表給 2115436「ExxonMobil Holdings Corp」(新設控股,us-gaap 只有 94 個概念),實際申報 10-K 的是 34088。
CIK_OVERRIDES: dict[str, int] = {"XOM": 34088}


def cik_for(ticker: str, overrides: bool = False) -> int | None:
    """代號 → CIK。

    overrides=True 先查 CIK_OVERRIDES(v2 PIT 管線用)。預設 False 維持 v1.1 行為,
    讓凍結資料上的 v1.1 基準回測可以逐位元重現(XOM 改 CIK 會改變基準的逐筆結果)。
    """
    if overrides:
        key = ticker.upper().replace(".", "-")
        if key in CIK_OVERRIDES:
            return CIK_OVERRIDES[key]
    try:
        df = tickers()
        m = df[df["ticker"].str.upper() == ticker.upper().replace("-", ".")]
        if m.empty:
            m = df[df["ticker"].str.upper() == ticker.upper()]
        return int(m.iloc[0]["cik"]) if not m.empty else None
    except Exception:  # noqa: BLE001
        return None


def companyfacts(cik: int) -> dict:
    def fetch():
        _require_user_agent()
        r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", headers=HEADERS, timeout=60)
        r.raise_for_status()
        return r.json()

    return cached("sec_facts", str(cik), TTL["statements"], fetch)


def submissions(cik: int) -> dict:
    def fetch():
        _require_user_agent()
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=HEADERS, timeout=40)
        r.raise_for_status()
        return r.json()

    return cached("sec_sub", str(cik), TTL["info"], fetch)


def _annual(facts: dict, concepts: list[str]) -> dict[str, float]:
    """回傳 {會計年度結束日: 值},只取 10-K 的全年數字。多個 concept 依優先順序合併。"""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    series: list[dict[str, float]] = []
    for c in concepts:
        node = gaap.get(c)
        if not node:
            continue
        per_concept: dict[str, tuple[str, float]] = {}
        for vals in node.get("units", {}).values():
            for v in vals:
                if v.get("form") not in ("10-K", "10-K/A") or v.get("fp") != "FY":
                    continue
                end, start = v.get("end"), v.get("start")
                if start:
                    try:
                        days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
                    except ValueError:
                        continue
                    if not 340 <= days <= 380:
                        continue
                filed = v.get("filed", "")
                if end not in per_concept or filed > per_concept[end][0]:  # 取最新申報(含重編)
                    per_concept[end] = (filed, float(v["val"]))
        if per_concept:
            series.append({e: val for e, (_, val) in per_concept.items()})
    if not series:
        return {}
    # 主序列:涵蓋最新年度、且近年筆數最多的 concept;其他 concept 只在「重疊年度數值一致(±5%)」時才拿來補缺
    latest_year = max(max(s) for s in series)[:4]
    series.sort(key=lambda s: (max(s)[:4] == latest_year, len(s)), reverse=True)
    merged = dict(series[0])
    for s in series[1:]:
        overlap = [e for e in s if e in merged and merged[e]]
        consistent = all(abs(s[e] / merged[e] - 1) <= 0.05 for e in overlap) if overlap else False
        if consistent:
            for e, val in s.items():
                merged.setdefault(e, val)
    return dict(sorted(merged.items()))


def annual_table(cik: int) -> pd.DataFrame:
    """10+ 年年度表:index=年度結束日,columns=標準欄位。"""
    facts = companyfacts(cik)
    cols = {}
    for k, cs in CONCEPTS.items():
        cols[k] = _annual(facts, cs)
    df = pd.DataFrame(cols)
    if df.empty:
        return df
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    # 同一年度可能有 52/53 週差異造成兩個結束日,保留每個年份最後一筆(保留真實的會計年度結束日)
    df = df.groupby(df.index.year).tail(1)
    df.index.name = "fiscal_year_end"
    if "cogs" in df and "revenue" in df:
        gp = df["revenue"] - df["cogs"]
        df["gross_profit"] = df["gross_profit"].combine_first(gp) if "gross_profit" in df else gp
    return df.tail(15)


def split_adjust(df: pd.DataFrame, splits: pd.Series | None) -> pd.DataFrame:
    """SEC 是「當時申報」的數字,EPS 與股數沒有經過之後的股票分割調整,這裡補上。"""
    if df is None or df.empty or splits is None or len(splits) == 0:
        return df
    s = splits.copy()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    out = df.copy()
    factors = []
    for end in out.index:
        f = float(s[s.index > end].prod()) if (s.index > end).any() else 1.0
        factors.append(f)
    factors = pd.Series(factors, index=out.index)
    if "eps_diluted" in out:
        out["eps_diluted"] = out["eps_diluted"] / factors
    if "shares_diluted" in out:
        out["shares_diluted"] = out["shares_diluted"] * factors
    return out


def recent_filings(cik: int, n: int = 25) -> list[dict]:
    try:
        sub = submissions(cik)
        rec = sub["filings"]["recent"]
        out = []
        for i in range(min(len(rec["form"]), 200)):
            form = rec["form"][i]
            if form in ("10-K", "10-Q", "8-K", "4", "DEF 14A", "S-1", "13D", "SC 13D", "SC 13G", "144"):
                acc = rec["accessionNumber"][i].replace("-", "")
                out.append({
                    "form": form, "date": rec["filingDate"][i], "desc": rec.get("primaryDocDescription", [""] * 999)[i],
                    "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{rec['primaryDocument'][i]}",
                })
            if len(out) >= n:
                break
        return out
    except Exception:  # noqa: BLE001
        return []
