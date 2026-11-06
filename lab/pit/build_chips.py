"""P2-H07:台股籌碼歷史(FinMind 三大法人買賣超、融資融券、外資持股)轉成 PIT facts。

原始資料:`scripts/fetch_chips_hist.py` 抓的快取(key `<dataset>|<代號>|2016-01-01|hist`)。
facts:entity = 代號、period = "D"、period_end = 資料日、available_at = 資料日的下一個營業日
(FinMind / 證交所在收盤後傍晚公布,判斷日 d 收盤決策時當天的籌碼視為還沒拿到,保守)。
欄位:inst_net:<法人名稱>(買 − 賣,股)、MarginPurchaseTodayBalance、ShortSaleTodayBalance、MarginPurchaseLimit、
ForeignInvestmentSharesRatio。判斷日用 `fm_asof(view, code)` 還原成 `analysis.chips.tw` 吃的 FinMind 格式。
"""
from __future__ import annotations

import pandas as pd
from pandas.tseries.offsets import BDay

from ..analysis.chips import INST_GROUP
from ..cache import CacheMiss, cached
from . import PITStore

START = "2016-01-01"
DS_INST, DS_MARGIN, DS_SHARE = ("TaiwanStockInstitutionalInvestorsBuySell", "TaiwanStockMarginPurchaseShortSale",
                                "TaiwanStockShareholding")
MARGIN_FIELDS = ("MarginPurchaseTodayBalance", "ShortSaleTodayBalance", "MarginPurchaseLimit")
SHARE_FIELDS = ("ForeignInvestmentSharesRatio",)
LOOKBACK_DAYS = 420   # chips.tw 最長用到約 250 個交易日


def _raw(ds: str, code: str) -> pd.DataFrame | None:
    """只讀快取,沒有就回傳 None(不連網)。"""
    def _miss():
        raise LookupError("no cache")
    try:
        df = cached("finmind", f"{ds}|{code}|{START}|hist", 10 ** 10, _miss)
    except (LookupError, CacheMiss):   # 沒有快取(線上模式由 _miss、離線模式由 CacheMiss 表示)= 沒有資料
        return None
    return df if df is not None and len(df) else None


def facts_for(code: str) -> pd.DataFrame:
    parts = []
    inst = _raw(DS_INST, code)
    if inst is not None:
        x = inst[inst["name"].isin(INST_GROUP)].copy()
        x["value"] = x["buy"].astype(float) - x["sell"].astype(float)
        x["field"] = "inst_net:" + x["name"]
        parts.append(x[["date", "field", "value"]])
    for ds, fields in ((DS_MARGIN, MARGIN_FIELDS), (DS_SHARE, SHARE_FIELDS)):
        raw = _raw(ds, code)
        if raw is None:
            continue
        m = raw.melt(id_vars=["date"], value_vars=[f for f in fields if f in raw], var_name="field", value_name="value")
        m["value"] = pd.to_numeric(m["value"], errors="coerce")
        parts.append(m.dropna(subset=["value"]))
    if not parts:
        return pd.DataFrame()
    f = pd.concat(parts, ignore_index=True)
    f["period_end"] = pd.to_datetime(f["date"])
    f["available_at"] = f["period_end"] + BDay(1)
    f["entity"], f["period"], f["source"], f["filed"] = code, "D", "finmind_hist", pd.NaT
    return f.drop(columns="date")


def build_store(codes) -> tuple[PITStore, dict]:
    frames, info = [], {}
    for c in codes:
        f = facts_for(c)
        info[c] = int(len(f))
        if len(f):
            frames.append(f)
    facts = pd.concat(frames, ignore_index=True) if frames else None
    st = PITStore(facts=facts) if facts is not None else PITStore()
    return st, {"n_codes": len(codes), "with_data": sum(1 for v in info.values() if v), "rows": int(sum(info.values()))}


def fm_asof(view, code: str) -> dict:
    """d 當時拿得到的籌碼,還原成 FinMind 格式:{"institutional", "margin", "shareholding"}(沒有資料的鍵不放)。"""
    x = view.facts_long(code, period="D")
    if x.empty:
        return {}
    x = x[x["period_end"] > view.d - pd.Timedelta(days=LOOKBACK_DAYS)]
    x = x.sort_values("available_at").drop_duplicates(["field", "period_end"], keep="last")
    x = x.assign(date=pd.to_datetime(x["period_end"]).dt.strftime("%Y-%m-%d"))
    out = {}
    inst = x[x["field"].str.startswith("inst_net:")]
    if len(inst):
        out["institutional"] = pd.DataFrame({"date": inst["date"], "name": inst["field"].str.split(":").str[1],
                                             "buy": inst["value"].clip(lower=0), "sell": (-inst["value"]).clip(lower=0)})
    for key, fields in (("margin", MARGIN_FIELDS), ("shareholding", SHARE_FIELDS)):
        y = x[x["field"].isin(fields)]
        if len(y):
            w = y.pivot_table(index="date", columns="field", values="value", aggfunc="last").reset_index()
            if all(f in w for f in fields):
                out[key] = w.sort_values("date").reset_index(drop=True)
    return out
