"""美股 point-in-time 資料管線:SEC companyfacts + Yahoo 股價 → `lab.pit.PITStore`。

和舊的 `sec.annual_table` / `split_adjust` 不同的地方(Verifier 在 1.3 查到的問題):
- 每個期間保留**每一個申報版本**(原始申報、之後年報 / 季報裡的比較數字、重編),`filed` = 申報日,
  `available_at` = 申報日的**下一個交易日**;回測在 d 只看得到 available_at ≤ d 的最新版本。
- 同一欄位有多個 concept 時,**在每一份申報裡**只用這份與更早的申報決定 concept(沿用上一份 → 和舊數字一致 →
  固定優先順序,見 `_choose_concepts`),不用「涵蓋最新年度的 concept」決定主序列(那會用到未來資訊)。
- 除了年報(10-K、10-K/A,期間 340–380 天)也收季報(10-Q、10-Q/A):
  單季 3 個月數字直接用;現金流量表在季報只有「年初至今」累計數(6 個月、9 個月),單季 = 累計差;
  Q4 = 全年 − Q1 − Q2 − Q3(只做流量科目;股數是加權平均,Q4 = 4 × 全年 − Q1 − Q2 − Q3)。
  衍生值在「所有組成數字都已公布」那一天才出現,任何一個組成數字之後改版,就在改版那天產生新版本。
- 價格存**原始**(未經分割回溯調整)數字;分割存成 events,`PITView.prices(adjust='split')` 只往回套用 d 以前的分割。
  每股數字(EPS)與股數照「申報當時」的股數基準存,轉接函式用 `view.split_factor(entity, filed)` 換到 d 的基準,
  所以分割後重新申報的比較數字(已是分割後)不會被再除一次。分割事件含價格期間以前的完整分割史;
  Yahoo 記成「分割」的分拆調整(IBM 2021、DELL)不當分割(見 is_true_split)。
- 股數單位錯誤(XBRL 少 1,000 倍等)在衍生前先丟掉(只和過去比);20-F 申報者(IFRS 或沒有 10-K / 10-Q)
  標記為不支援,不硬做;CIK 會檢查 us-gaap 概念數量是否合理。資料品質見 docs/資料品質_US.md。

執行(離線、讀凍結資料、輸出到 $INVEST_LAB_OUT_DIR/pit_us.sqlite):
    INVEST_LAB_OFFLINE=1 INVEST_LAB_DATA_DIR=... INVEST_LAB_OUT_DIR=... .venv/bin/python -m lab.pit.build_us
"""
from __future__ import annotations

import datetime as dt
import json
import math
from collections import defaultdict

import numpy as np
import pandas as pd

from .. import config
from ..sources import sec
from . import FACT_COLS, PITStore, PITView

# ---------------- 欄位分類 ----------------
INSTANT = {"total_assets", "equity", "total_liabilities", "current_assets", "current_liabilities", "cash",
           "long_term_debt", "retained_earnings", "receivables", "inventory"}
AVG = {"shares_diluted"}  # 期間加權平均(加總時要乘上季數)
PER_SHARE = {"eps_diluted"}
SHARE_COUNT = {"shares_diluted"}
FLOW = [k for k in sec.CONCEPTS if k not in INSTANT]  # 含 eps_diluted、shares_diluted
UNITS = {"eps_diluted": "USD/shares", "shares_diluted": "shares"}  # 其他欄位一律 USD
# PIT 管線額外收的 concept(排在 sec.CONCEPTS 之後,優先順序最低;sec.py 的清單不動,網站行為不變)
# AAPL 2016 年的季報與年報只用 …ContinuingOperations 標營業現金流,沒有它整年 cfo 都缺
EXTRA_CONCEPTS = {"cfo": ["NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]}
PIT_CONCEPTS = {k: v + EXTRA_CONCEPTS.get(k, []) for k, v in sec.CONCEPTS.items()}

ANNUAL_FORMS = {"10-K", "10-K/A"}
QUARTER_FORMS = {"10-Q", "10-Q/A"}
MIN_US_GAAP_CONCEPTS = 200  # US_LARGE 正常申報 10-K 的最少是 MRVL 378 個;XOM 錯的 CIK 只有 94 個
SOURCE = "sec"

# ---------------- 交易日曆 ----------------


PRICE_SOURCES = ("10y", "since")


def trading_calendar(price_source: str = "10y") -> pd.DatetimeIndex:
    """快取裡 ^GSPC(優先)或 SPY 的交易日。只涵蓋價格期間;更早的日期由 next_trading_day 用平日補。
    price_source='since' 用 2013 起的長歷史(預設 '10y' 和既有行為相同)。"""
    from ..sources import yahoo
    h = yahoo.history_since("^GSPC") if price_source == "since" else yahoo.index_history("^GSPC")
    if h is None or h.empty:
        h = yahoo.history("SPY", yahoo.Status())
    if h is None or h.empty:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(pd.to_datetime(h.index).tz_localize(None).normalize()).unique().sort_values()


def next_trading_day(dates, cal: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """每個日期之後(不含當天)的第一個交易日。

    日曆範圍外(價格期間以前或之後)改用下一個平日:若那天剛好是假日,決策日一定在它之後,
    「available_at ≤ d」的判斷結果和用真正的下一個交易日完全一樣。
    """
    d = pd.DatetimeIndex(pd.to_datetime(dates)).normalize()
    out = np.empty(len(d), dtype="datetime64[ns]")
    cal_v = cal.values
    lo, hi = (cal[0], cal[-1]) if len(cal) else (pd.Timestamp.max, pd.Timestamp.min)
    for i, x in enumerate(d):
        if len(cal) and lo <= x < hi:
            out[i] = cal_v[np.searchsorted(cal_v, x.to_datetime64(), side="right")]
        else:
            out[i] = (x + pd.offsets.BDay(1)).to_datetime64()
    return pd.DatetimeIndex(out)


# ---------------- 價格與公司行動 ----------------


# Yahoo 的價格在這天以前不是這家公司的普通股(換股、追蹤股),store 不收更早的價格
PRICE_START = {
    "DELL": ("2018-12-28", "2018-12-28 以前 Yahoo 的 DELL 價格是 DVMT(VMware 追蹤股),Dell Technologies 普通股當天才掛牌"),
}


def is_true_split(ratio: float) -> bool:
    """Yahoo 的「Stock Splits」也拿來記分拆(spin-off)的價格調整(IBM 2021-11-04 Kyndryl 1.046、
    DELL 2021-11-02 VMware 1.973、DELL 2018-12-28 換股 1.806)。那些股數沒有變,不能拿來調整 EPS / 股數。
    真的分割 / 反分割:比率(或倒數)≥ 1.2 且接近 n/m(m ≤ 4,誤差 < 0.5%),例如 2、3、4、7、10、20、1.5;
    GOOGL 2014-04-03 的 1.998(C 股股利,SEC 申報當成一拆二追溯調整)也算。"""
    if not ratio or not np.isfinite(ratio) or ratio <= 0:
        return False
    r = ratio if ratio >= 1 else 1 / ratio
    if r < 1.2:
        return False
    return any(abs(r - round(r * m) / m) / r < 0.005 for m in (1, 2, 3, 4))


def split_events(entity: str, hist: pd.DataFrame | None, full: pd.Series | None = None) -> pd.DataFrame:
    """分割事件:Yahoo 價格期間內的 Stock Splits 欄,加上完整分割史(yfinance Ticker.splits,涵蓋價格期間以前),
    只收真的分割(見 is_true_split)。"""
    parts = []
    if hist is not None and "Stock Splits" in hist:
        parts.append(hist["Stock Splits"])
    if full is not None and len(full):
        parts.append(full)
    if not parts:
        return pd.DataFrame({"entity": pd.Series(dtype=object), "date": pd.Series(dtype="datetime64[ns]"),
                             "kind": pd.Series(dtype=object), "ratio": pd.Series(dtype=float)})
    s = pd.concat([pd.Series(p.astype(float).to_numpy(), index=pd.DatetimeIndex(p.index).tz_localize(None).normalize())
                   for p in parts])
    s = s[(s > 0) & (s != 1)]
    s = s[~s.index.duplicated(keep="first")].sort_index()
    s = s[[is_true_split(v) for v in s]]
    return pd.DataFrame({"entity": entity, "date": s.index, "kind": "split", "ratio": s.to_numpy()})


def non_split_adjustments(hist: pd.DataFrame | None) -> list[list]:
    """Yahoo 記成分割、但不是真分割的價格調整(分拆等),回報用。"""
    if hist is None or "Stock Splits" not in hist:
        return []
    s = hist["Stock Splits"]
    s = s[(s > 0) & (s != 1)]
    return [[str(pd.Timestamp(i).date()), float(v)] for i, v in s.items() if not is_true_split(float(v))]


def spinoff_events(entity: str, hist: pd.DataFrame | None, px: pd.DataFrame | None) -> pd.DataFrame:
    """Yahoo 記成「分割」但不是真分割的調整 → kind='spinoff' 事件(只補償含息報酬,不改股數基準)。

    ratio = 1 ÷ Yahoo 的調整係數:Yahoo 把分拆日以前的價格除以係數 F 讓價格連續,F ≈ 前一日收盤 ÷(前一日收盤 − 分拆出去的價值),
    所以「分拆日前價格要乘上的倍數」= 1/F(IBM 2021-11-04:1/1.046 = 0.956;DELL 2021-11-02:1/1.973 = 0.507)。
    F 是 Yahoo 在分拆生效日依當天的分拆價值算出的,事件日期 = 分拆生效日,as_of 只在那天以後才套用。
    store 裡沒有更早價格的(DELL 2018-12-28 換股,之前的價格不收)不建事件。
    """
    rows = []
    if hist is not None and "Stock Splits" in hist and px is not None and len(px):
        s = hist["Stock Splits"]
        for d, f in s[(s > 0) & (s != 1)].items():
            d = pd.Timestamp(d).tz_localize(None).normalize() if pd.Timestamp(d).tzinfo else pd.Timestamp(d).normalize()
            if not is_true_split(float(f)) and f > 1 and (px.index < d).any():
                rows.append({"entity": entity, "date": d, "kind": "spinoff", "ratio": 1.0 / float(f)})
    return pd.DataFrame(rows, columns=["entity", "date", "kind", "ratio"])


def raw_prices(hist: pd.DataFrame) -> pd.DataFrame:
    """yfinance(auto_adjust=False)的 OHLC、成交量、股利都已被分割回溯調整;換回當天實際的原始數字。

    原始價 = 調整價 × 該日之後(不含當天;分割生效日當天已是分割後價格)所有分割比率的乘積。
    這裡用 Yahoo Stock Splits 欄的**全部**比率(含分拆調整),因為 Yahoo 的調整價就是這樣算出來的。
    """
    h = hist.copy()
    h.index = pd.DatetimeIndex(h.index).tz_localize(None).normalize()
    sp = h["Stock Splits"] if "Stock Splits" in h else pd.Series(0.0, index=h.index)
    r = sp.where((sp > 0) & (sp != 1), 1.0).astype(float)
    # 每一天之後的累積分割倍數:反向累乘後往後移一天
    after = r[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    out = pd.DataFrame(index=h.index)
    for src, dst in (("Open", "open"), ("High", "high"), ("Low", "low"), ("Close", "close")):
        out[dst] = h[src].astype(float) * after
    out["volume"] = h["Volume"].astype(float) / after
    div = h["Dividends"].astype(float) if "Dividends" in h else pd.Series(0.0, index=h.index)
    out["cash_div"] = div.fillna(0.0) * after
    return out.rename_axis(None)


def _split_mult(events: pd.DataFrame, after, upto) -> float:
    """(after, upto] 之間的分割倍數乘積(和 PITView.split_factor 同定義)。"""
    if events is None or events.empty:
        return 1.0
    after, upto = pd.Timestamp(after), pd.Timestamp(upto)
    m = (events["date"] > after) & (events["date"] <= upto) & (events["kind"] != "spinoff")
    r = events[m]["ratio"].astype(float)
    r = r[(r > 0) & np.isfinite(r)]
    return float(r.prod()) if len(r) else 1.0


# ---------------- SEC 原始紀錄 ----------------


def filer_status(facts: dict) -> tuple[str, int, set[str]]:
    """('ok' | 'no_us_gaap' | '20-F' | 'few_concepts' | 'no_10k', us-gaap 概念數, 出現的申報表單)。"""
    gaap = (facts or {}).get("facts", {}).get("us-gaap", {})
    forms: set[str] = set()
    for node in gaap.values():
        for vals in node.get("units", {}).values():
            for v in vals:
                forms.add(v.get("form") or "")
    n = len(gaap)
    if n == 0:
        return "no_us_gaap", n, forms
    if not (forms & (ANNUAL_FORMS | QUARTER_FORMS)):
        return ("20-F" if any(f.startswith("20-F") for f in forms) else "no_10k"), n, forms
    if n < MIN_US_GAAP_CONCEPTS:
        return "few_concepts", n, forms
    return "ok", n, forms


def _days(start: str | None, end: str) -> int | None:
    if not start:
        return None
    try:
        return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
    except ValueError:
        return None


def _kind(form: str, days: int | None) -> str | None:
    """紀錄類型:FY(全年)、Q3M(單季 3 個月)、YTD(季報裡的年初至今 6 / 9 個月)、I(時點)、
    K3M(年報裡標註的 3–4 個月數字,只拿來核對推算的 Q4)。

    YTD 範圍放寬到 160–195、245–290 天:COST 這類 12/12/12/16 週的公司,半年 = 168 天、9 個月 = 252 天。
    """
    if days is None:
        return "I"
    if form in ANNUAL_FORMS:
        if 340 <= days <= 380:
            return "FY"
        if 80 <= days <= 120:
            return "K3M"
        return None
    if form in QUARTER_FORMS:
        if 80 <= days <= 100:
            return "Q3M"
        if 160 <= days <= 195 or 245 <= days <= 290:
            return "YTD"
    return None


def raw_records(facts: dict, keep_candidates: bool = False) -> pd.DataFrame:
    """把 companyfacts 攤平成一筆一列:field、rank(concept 優先順序)、concept、accn、form、fp、filed、start、end、days、kind、val。

    預設只回「選定的 concept」(每份申報、每個期間一筆,見 _choose_concepts);keep_candidates=True 回全部候選
    並加 chosen 欄(衍生季資料與 concept 等價判斷要用到沒被選中的 concept)。"""
    gaap = (facts or {}).get("facts", {}).get("us-gaap", {})
    rows = []
    for field, concepts in PIT_CONCEPTS.items():
        unit = UNITS.get(field, "USD")
        for rank, c in enumerate(concepts):
            node = gaap.get(c)
            if not node:
                continue
            for v in node.get("units", {}).get(unit, []):
                form = v.get("form") or ""
                if form not in ANNUAL_FORMS and form not in QUARTER_FORMS:
                    continue
                end, start = v.get("end"), v.get("start")
                if not end or v.get("val") is None or not v.get("filed"):
                    continue
                days = _days(start, end)
                if start and days is None:
                    continue
                if (field in INSTANT) != (days is None):  # 時點科目只收時點值,流量科目只收期間值
                    continue
                k = _kind(form, days)
                if k is None:
                    continue
                rows.append((field, rank, c, v.get("accn") or "", form, v.get("fp") or "", v["filed"], start, end, days, k,
                             float(v["val"])))
    df = pd.DataFrame(rows, columns=["field", "rank", "concept", "accn", "form", "fp", "filed", "start", "end", "days", "kind",
                                     "val"])
    if df.empty:
        return df
    df["filed"] = pd.to_datetime(df["filed"])
    df["end"] = pd.to_datetime(df["end"])
    df["start"] = pd.to_datetime(df["start"])
    allc = _choose_concepts(df)
    return allc if keep_candidates else allc[allc["chosen"]].reset_index(drop=True)


def _choose_concepts(df: pd.DataFrame) -> pd.DataFrame:
    """同一份申報、同一欄位、同一期間只留一個 concept(逐申報、只看「這份與更早的申報」,不看未來)。

    規則(依申報日先後逐份處理,每份申報決定一個「主 concept」):
      1. 上一份申報的主 concept 這份也有 → 沿用;
      2. 否則選「這份裡的數字和更早申報的同期間數字一致(±1%)」次數最多的 concept;
      3. 都沒有 → 固定優先順序(sec.CONCEPTS 的順序)。
    每個期間:更早版本用過的 concept 這份也有 → 沿用(避免 AVGO FY2018 淨利 NetIncomeLoss 122.6 億 →
    ProfitLoss 126.1 億這種「換定義」被當成重編);否則用主 concept;主 concept 在該期間沒有值才退回優先順序。
    同一份申報裡不同期間可能因此用不同 concept,所以衍生值(_combine)只合併同一個(或已證實等價的)concept。
    為什麼不是純優先順序:同一份申報裡兩個 concept 意義不同的情況實際存在,例如
      MA 2021 10-K:RevenueFromContractWithCustomer… = 236 億(折讓前總額)、Revenues = 153 億(損益表淨營收);
      PG 2014 10-K:Revenues = 294 億(某個子項)、SalesRevenueNet = 831 億(損益表淨銷售)。
    純優先順序會在這些申報裡突然換成另一個意義的數字,造成假的「重編」。規則 1、2 只用到這份與更早的申報。
    """
    df = df.sort_values(["field", "filed", "accn", "rank"])
    # filings[field][accn] = {(start, end): [(rank, concept, row_index, value), ...]},依申報日順序
    filings: dict = {}
    for i, fld, accn, start, end, rank, concept, val in zip(df.index, df["field"], df["accn"], df["start"], df["end"],
                                                            df["rank"], df["concept"], df["val"]):
        per = (None if pd.isna(start) else start, end)
        filings.setdefault(fld, {}).setdefault(accn, {}).setdefault(per, []).append((rank, concept, i, val))
    keep = []
    for _fld, by_accn in filings.items():
        prev = None
        known: dict = {}  # 更早的申報裡,這個欄位每個期間已選定的數字
        known_c: dict = {}  # 以及選定的 concept
        for _accn, periods in by_accn.items():  # dict 保留插入順序 = 申報日順序
            present = {c[1] for cands in periods.values() for c in cands}
            if prev in present:
                main = prev
            else:
                # 上一份用的 concept 這份沒有:選「和更早申報的同期間數字一致」最多的 concept;都沒有就照優先順序
                score: dict = defaultdict(int)
                for per, cands in periods.items():
                    if per in known:
                        for c in cands:
                            if math.isclose(c[3], known[per], rel_tol=0.01, abs_tol=0.5):
                                score[c[1]] += 1
                ranks = {c[1]: c[0] for cands in periods.values() for c in cands}
                main = min(present, key=lambda c: (-score.get(c, 0), ranks[c]))
            for per, cands in periods.items():
                cands = sorted(cands)
                # 同一期間沿用「更早版本用的 concept」(版本之間的差異才是數字真的改了,不是換了定義);
                # 沒有才用這份申報的主 concept,再沒有才照優先順序
                pick = (next((c for c in cands if c[1] == known_c.get(per)), None)
                        or next((c for c in cands if c[1] == main), cands[0]))
                keep.append(pick[2])
                known[per] = pick[3]
                known_c[per] = pick[1]
            prev = main
    df["chosen"] = False
    df.loc[keep, "chosen"] = True
    return df.sort_values(["field", "filed", "accn", "end", "rank"]).reset_index(drop=True)


def _filter_instants(df: pd.DataFrame) -> pd.DataFrame:
    """時點科目只留各申報「自己的」期末:
    - 年報:期末日 = 該年報任一全年期間的結束日(本年與比較年度的年底)→ period = FY
    - 季報:期末日 = 該季報單季期間的最晚結束日(本季季底)→ period = 季(由日期推算)
    季報裡附帶的上年度年底餘額不收(那是年報數字的重複)。
    """
    inst = df[df["kind"] == "I"]
    dur = df[df["kind"] != "I"]
    fy_ends = dur[dur["kind"] == "FY"].groupby("accn")["end"].apply(set).to_dict()
    q_end = dur[dur["kind"] == "Q3M"].groupby("accn")["end"].max().to_dict()
    keep = []
    for i, r in inst.iterrows():
        if r["form"] in ANNUAL_FORMS:
            if r["end"] in fy_ends.get(r["accn"], ()):
                keep.append((i, "FY"))
        elif r["form"] in QUARTER_FORMS:
            if q_end.get(r["accn"]) == r["end"]:
                keep.append((i, "QI"))
    if not keep:
        return inst.iloc[0:0].assign(kind=[])
    idx, kinds = zip(*keep)
    out = inst.loc[list(idx)].copy()
    out["kind"] = list(kinds)
    return out


# ---------------- 會計年度與季別 ----------------


def _fiscal_starts_by_filed(df: pd.DataFrame) -> dict:
    """{申報日: 那天(含)以前的申報已經透露的會計年度起始日清單}。只用過去,建置期沒有未來依賴。

    每份申報透露的起始日:全年期間與年初至今期間的 start、全年期間 end 的隔天(下一年度起始),
    以及第一季季報(fp = Q1)本季 3 個月期間的 start(新上市 / 換 CIK 的公司第一份季報還沒有年報可參考)。
    相差 ≤ 7 天視為同一個起始日(先出現的為準,之後不改)。
    """
    canon: list[pd.Timestamp] = []
    out: dict = {}
    q1_end = df[(df["kind"] == "Q3M") & (df["fp"] == "Q1")].groupby("accn")["end"].max().to_dict()
    for filed, g in df.sort_values("filed").groupby("filed", sort=True):
        cand = list(g.loc[g["kind"].isin(["FY", "YTD"]), "start"].dropna())
        cand += [e + pd.Timedelta(days=1) for e in g.loc[g["kind"] == "FY", "end"]]
        q1 = g[(g["kind"] == "Q3M") & (g["fp"] == "Q1")]
        cand += [r.start for r in q1.itertuples() if pd.notna(r.start) and q1_end.get(r.accn) == r.end]
        for x in sorted(set(cand)):
            if all(abs((x - c).days) > 7 for c in canon):
                canon.append(x)
        out[filed] = sorted(canon)
    return out


def _match_start(x: pd.Timestamp, starts: list[pd.Timestamp]) -> pd.Timestamp | None:
    for s in starts:
        if abs((x - s).days) <= 7:
            return s
    return None


def _group_for_end(end: pd.Timestamp, starts: list[pd.Timestamp]) -> pd.Timestamp | None:
    """end 落在哪個會計年度(起始 < end ≤ 起始 + 380 天,取最晚的起始)。"""
    cand = [s for s in starts if s < end <= s + pd.Timedelta(days=380)]
    return max(cand) if cand else None


def _quarter_no(fy_start: pd.Timestamp, end: pd.Timestamp) -> int:
    return int(round((end - fy_start).days / 91.31))


# ---------------- 版本合成(衍生值的 PIT 規則) ----------------


def _basis(field: str, value: float, filed, t, events) -> float:
    """把申報當時(filed)股數基準的數字換到 t 的基準。

    「申報當時的基準」用申報日而不是 available_at:會計準則要求分割若發生在期末後、報表發布前要追溯調整,
    所以申報日以前生效的分割已反映在數字裡,申報日之後的沒有。若用 available_at(申報日 +1 交易日),
    剛好在下一個交易日生效的分割會漏調。
    """
    if field not in PER_SHARE and field not in SHARE_COUNT:
        return value
    m = _split_mult(events, filed, t)
    return value / m if field in PER_SHARE else value * m


class ConceptEquiv:
    """兩個 concept 在 t 是否可視為同一個定義(只用 t 以前的證據):
    t 以前至少有一次「同一期間、兩個 concept 的數字一致(±0.1%)」,而且 t 以前從來沒有不一致。
    例:NVDA 2019 年報改用 RevenueFromContract…,但它對 FY2018 的比較數字和舊申報的 Revenues 一致 → 之後可以混用;
    AVGO 的 NetIncomeLoss 與 ProfitLoss 在 2018 Q1 不一致(差非控制權益)→ 永遠不混用。"""

    def __init__(self, dur: pd.DataFrame):
        self.ev: dict = defaultdict(list)  # (field, frozenset{a, b}) → [(t, agree)]
        for (fld, _s, _e), g in dur.groupby(["field", "start", "end"]):
            if g["concept"].nunique() < 2:
                continue
            vs = list(zip(g["available_at"], g["concept"], g["val"]))
            for i in range(len(vs)):
                for j in range(i + 1, len(vs)):
                    (ta, ca, va), (tb, cb, vb) = vs[i], vs[j]
                    if ca != cb:
                        agree = math.isclose(va, vb, rel_tol=0.001, abs_tol=0.5 if abs(va) > 1 else 0.005)
                        self.ev[(fld, frozenset((ca, cb)))].append((max(ta, tb), agree))

    def same(self, field: str, a: str, b: str, t) -> bool:
        if a == b:
            return True
        ev = [ag for tt, ag in self.ev.get((field, frozenset((a, b))), []) if tt <= t]
        return bool(ev) and all(ev)


def _combine(field: str, comps: list[list[tuple]], coefs: list[float], events, stats: dict | None = None,
             equiv: ConceptEquiv | None = None) -> list[tuple]:
    """comps:每個組成數字的版本清單 [(available_at, filed, value, concept), ...]。

    在每個「組成數字有新版本」的時間點 t,用 t 當時各組成的最新版本算一次;數值有變才產生新版本。
    **只合併同一個 concept 的數字**:依序試第一個組成(全年 / 年初至今)在 t 以前用過的 concept(越新越先),
    每個組成取「該 concept、t 以前」的最新版本;沒有任何 concept 能湊齊所有組成就不衍生
    (例 AVGO FY2018:全年用 NetIncomeLoss、某季用 ProfitLoss,相減會得到錯的 Q4)。
    回傳 [(available_at, filed, value, concept)]。
    """
    if not comps or any(not c for c in comps):
        return []
    comps = [sorted(c, key=lambda v: (v[0], v[1])) for c in comps]
    t0 = max(c[0][0] for c in comps)
    times = sorted({v[0] for c in comps for v in c if v[0] >= t0})
    out: list[tuple] = []
    last = None
    for t in times:
        # 候選 concept:第一個組成在 t 以前用過的 concept,越新的越優先
        seen = [v[3] for v in comps[0] if v[0] <= t][::-1]
        cur = []
        for concept in dict.fromkeys(seen):
            cur = []
            for c in comps:
                vs = [v for v in c if v[0] <= t and (v[3] == concept or (equiv is not None and
                                                                          equiv.same(field, v[3], concept, t)))]
                if not vs:
                    break
                cur.append(vs[-1])
            if len(cur) == len(comps):
                break
        if len(cur) != len(comps):
            if stats is not None:
                stats["anomalies"]["derived_skipped_mixed_concept"] += 1
            continue
        filed_max = max(v[1] for v in cur)
        # 衍生值的股數基準 = 最晚一個組成數字的申報日(和直接申報的數字同一個定義:filed 當時的基準)
        total = sum(k * _basis(field, v[2], v[1], filed_max, events) for v, k in zip(cur, coefs))
        if last is None or not math.isclose(total, last, rel_tol=1e-9, abs_tol=1e-9):
            out.append((t, filed_max, total, concept))
            last = total
    return out


def _drop_share_unit_errors(out: pd.DataFrame, stats: dict) -> pd.DataFrame:
    """股數單位錯誤(XBRL 把「百萬股」當成「股」申報,例如 KO 2018 Q2、TXN 2010 Q3、NVDA 2010):
    和「這份申報以前最近一次看到的股數」差 500 倍以上就丟掉(真的分割最多 50 倍;只和過去比,不看未來)。
    同一天申報的其他數字不算「過去」。第一份申報本身若就是錯的單位,這裡抓不到(只能事後在資料品質報告標出)。"""
    m = out["field"].isin(SHARE_COUNT)
    if not m.any():
        return out
    sh = out[m].sort_values(["filed", "end"])
    drop, last_ok, last_avail = [], None, None
    pending = []
    for i, v, a in zip(sh.index, sh["val"], sh["filed"]):
        if last_avail is not None and a != last_avail and pending:
            last_ok = pending[-1]
            pending = []
        if last_ok and v and (v / last_ok > 500 or last_ok / v > 500):
            drop.append(i)
        else:
            pending.append(v)
        last_avail = a
    if drop:
        stats["anomalies"]["share_unit_error_dropped"] += len(drop)
    return out.drop(index=drop)


def _split_near_filing(rec: pd.DataFrame, events: pd.DataFrame, cal: pd.DatetimeIndex) -> list[dict]:
    """申報日落在分割生效日前後 3 個交易日內的申報:用「前一份申報的同期 EPS ÷ 這份申報的同期 EPS」
    判斷這份申報是否已經反映分割。我們的規則假設「申報日 < 生效日 = 沒反映、申報日 ≥ 生效日 = 已反映」,
    觀察值和假設不同的要人工處理(rule_ok = False)。只回報,不改數字。"""
    out = []
    if events is None or events.empty:
        return out
    eps = rec[rec["field"].isin(PER_SHARE)]
    for e in events[events["kind"] == "split"].itertuples():
        d = pd.Timestamp(e.date)
        lo = d - pd.offsets.BDay(3)
        hi = d + pd.offsets.BDay(3)
        near = eps[(eps["filed"] >= lo) & (eps["filed"] <= hi)]
        for accn, g in near.groupby("accn"):
            ratios = []
            for r in g.itertuples():
                old = eps[(eps["filed"] < r.filed) & (eps["end"] == r.end) & (eps["start"] == r.start)]
                if len(old) and r.val:
                    ratios.append(float(old.sort_values("filed")["val"].iloc[-1]) / r.val)
            obs = float(np.median(ratios)) if ratios else None
            filed = pd.Timestamp(g["filed"].iloc[0])
            reflected = None if obs is None else abs(obs / float(e.ratio) - 1) < 0.05
            out.append({"split_date": str(d.date()), "ratio": float(e.ratio), "accn": accn,
                        "filed": str(filed.date()), "observed_old_over_new": obs, "already_reflected": reflected,
                        "rule_ok": None if reflected is None else reflected == (filed >= d)})
    return out


# ---------------- 主轉換 ----------------


def build_facts(entity: str, facts: dict, cal: pd.DatetimeIndex, events: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """單一公司的 facts 長表(FACT_COLS)與統計。"""
    stats: dict = {"anomalies": defaultdict(int)}
    rec_all = raw_records(facts, keep_candidates=True)  # 全部候選 concept;選定的 chosen = True
    if rec_all.empty:
        return pd.DataFrame(columns=FACT_COLS), stats
    rec_all = _drop_share_unit_errors(rec_all, stats)  # 在衍生 Q2–Q4 之前先清掉,錯的股數不會流進衍生值
    rec_all["available_at"] = next_trading_day(rec_all["filed"], cal)
    rec = rec_all[rec_all["chosen"]]
    k3m = rec_all[rec_all["kind"] == "K3M"]
    dur_all = rec_all[rec_all["kind"].isin(["FY", "YTD", "Q3M"])]
    dur = dur_all[dur_all["chosen"]]
    inst = _filter_instants(rec)
    starts_at = _fiscal_starts_by_filed(rec_all[rec_all["kind"] != "I"])
    rows: list[dict] = []

    def starts_for(filed) -> list[pd.Timestamp]:
        return starts_at.get(filed, [])

    def add(field, end, period, value, avail, filed, src):
        rows.append({"entity": entity, "field": field, "period_end": pd.Timestamp(end), "period": period,
                     "value": float(value), "available_at": pd.Timestamp(avail), "source": src,
                     "filed": pd.Timestamp(filed), "basis_at": pd.Timestamp(filed)})  # 美股股數基準日 = 申報日

    def src_of(r):
        return f"{SOURCE}|{r['form']}|{r['accn']}|{r['concept']}"

    # 1) 年度(全年期間、年底時點)
    for _, r in dur[dur["kind"] == "FY"].iterrows():
        add(r["field"], r["end"], "FY", r["val"], r["available_at"], r["filed"], src_of(r))
    for _, r in inst[inst["kind"] == "FY"].iterrows():
        add(r["field"], r["end"], "FY", r["val"], r["available_at"], r["filed"], src_of(r))

    # 2) 季度時點(季報本季季底);季別只用該申報當天以前已知的會計年度起始日推算
    for _, r in inst[inst["kind"] == "QI"].iterrows():
        g = _group_for_end(r["end"], starts_for(r["filed"]))
        q = _quarter_no(g, r["end"]) if g is not None else 0
        if q not in (1, 2, 3):
            stats["anomalies"]["instant_quarter_unmatched"] += 1
            continue
        add(r["field"], r["end"], f"Q{q}", r["val"], r["available_at"], r["filed"], src_of(r))

    # 3) 季度流量:依會計年度整理成 cum[k](年初至今 k 季)與 direct[k](單季 3 個月)
    #    by[(field, fy_start)] = {"cum": {k: [版本]}, "direct": {k: [版本]}, "end": {k: 期末日}}
    #    版本 = (available_at, filed, value, concept, accn, chosen);衍生用全部候選,輸出的直接數字只用選定的
    by: dict = defaultdict(lambda: {"cum": defaultdict(list), "direct": defaultdict(list), "end": {}})
    # 等價證據用全部候選 concept(含沒被選中的);年報標註的季數字(K3M)也算
    equiv = ConceptEquiv(rec_all[rec_all["kind"] != "I"])
    direct_rows = []
    for _, r in dur_all.iterrows():
        ver = (r["available_at"], r["filed"], r["val"], r["concept"], r["accn"], bool(r["chosen"]))
        starts = starts_for(r["filed"])
        if r["kind"] == "FY":
            s = _match_start(r["start"], starts)
            if s is None:
                continue
            b = by[(r["field"], s)]
            b["cum"][4].append(ver)
            b["end"].setdefault(4, r["end"])
        elif r["kind"] == "YTD":
            s = _match_start(r["start"], starts)
            k = int(round(r["days"] / 91.31))
            if s is None or k not in (2, 3):
                stats["anomalies"]["ytd_unmatched"] += 1
                continue
            b = by[(r["field"], s)]
            b["cum"][k].append(ver)
            b["end"].setdefault(k, r["end"])
        elif r["kind"] == "Q3M":
            s = _group_for_end(r["end"], starts)
            if s is None:
                stats["anomalies"]["q3m_unmatched"] += 1
                continue
            k = _quarter_no(s, r["end"])
            if k not in (1, 2, 3):
                stats["anomalies"]["q3m_bad_quarter"] += 1
                continue
            b = by[(r["field"], s)]
            if k == 1 and _match_start(r["start"], [s]) is not None:
                b["cum"][1].append(ver)
            b["direct"][k].append(ver)
            b["end"].setdefault(k, r["end"])
            if r["chosen"]:
                direct_rows.append((r, k))

    # 年報標註的 Q4(3–4 個月、期末 = 該年報的年度結束日):(欄位, 期末, 申報日, concept) → 值
    fy_end_of = dur[dur["kind"] == "FY"].groupby("accn")["end"].max().to_dict()
    tagged = {(r.field, r.end, r.filed, r.concept): r.val for r in k3m.itertuples() if fy_end_of.get(r.accn) == r.end}

    def strip(vs):
        return [v[:4] for v in vs]

    def chosen(vs):
        return [v[:4] for v in vs if v[5]]

    for (field, s), b in by.items():
        w = {k: (k if field in AVG else 1) for k in (1, 2, 3, 4)}  # 年初至今「加總」= 平均 × 季數
        eff: dict[int, list[tuple] | None] = {}
        for k in (1, 2, 3):
            direct = sorted(chosen(b["direct"].get(k, [])), key=lambda v: (v[0], v[1]))
            direct_all = sorted(strip(b["direct"].get(k, [])), key=lambda v: (v[0], v[1]))
            derived: list[tuple] = []
            if k >= 2 and b["cum"].get(k) and all(eff.get(j) for j in range(1, k)):
                # 單季 = 年初至今(k) − 前 k−1 季(平均型:k·C_k − 前 k−1 季)
                prev = [eff[j] for j in range(1, k)]
                derived = _combine(field, [chosen(b["cum"][k])] + prev, [float(w[k])] + [-1.0] * len(prev), events,
                                   stats, equiv)
                # 只在「當時還沒有直接的單季數字」時用衍生值(只比較過去:直接數字可用日 ≤ 衍生值可用日就不用衍生值)
                first_direct = direct[0][0] if direct else None
                derived = [x for x in derived if first_direct is None or x[0] < first_direct]
            if derived:
                stats["anomalies"]["info_quarters_derived_from_ytd"] += 1
                for a, f, v, c in derived:  # 直接的單季數字在下方逐版本寫入
                    add(field, b["end"][k], f"Q{k}", v, a, f, f"{SOURCE}-derived|Q{k}=YTD{k}-前{k - 1}季|{c}")
            eff[k] = sorted(direct_all + derived, key=lambda v: (v[0], v[1])) or None
        # Q4 = 全年 − Q1 − Q2 − Q3(股數:4·全年 − ΣQ)
        if b["cum"].get(4) and all(eff.get(k) for k in (1, 2, 3)):
            q4 = _combine(field, [chosen(b["cum"][4]), eff[1], eff[2], eff[3]], [float(w[4]), -1.0, -1.0, -1.0], events,
                          stats, equiv)
            for a, f, v, c in q4:
                tag = tagged.get((field, b["end"][4], f, c))
                if tag is not None and math.isclose(tag, v, rel_tol=0.02, abs_tol=0.011 if field in PER_SHARE else 0.5):
                    # 同一份年報有標註 Q4、且和推算值差 < 2%:用標註值(推算值是相減的近似)
                    stats["anomalies"]["info_q4_tagged_used"] += 1
                    add(field, b["end"][4], "Q4", tag, a, f, f"{SOURCE}|10-K-tagged-Q4|{c}")
                    continue
                if tag is not None:
                    # 維護者決定:推算值和同份年報標註值差 ≥ 2% → 不給這個 Q4(例 DELL FY2022 毛利:全年重編、前三季沒重編)
                    stats["anomalies"]["q4_tag_mismatch_dropped"] += 1
                    stats.setdefault("q4_tag_mismatch", []).append(
                        [field, str(pd.Timestamp(b["end"][4]).date()), round(float(v), 4), round(float(tag), 4)])
                    continue
                add(field, b["end"][4], "Q4", v, a, f, f"{SOURCE}-derived|Q4=FY-Q1-Q2-Q3|{c}")

    # 單季 3 個月的直接數字(每個申報版本一列)
    for r, k in direct_rows:
        add(r["field"], r["end"], f"Q{k}", r["val"], r["available_at"], r["filed"], src_of(r))

    stats["split_near_filing"] = _split_near_filing(rec, events, cal)

    out = pd.DataFrame(rows, columns=FACT_COLS)
    if out.empty:
        return out, stats
    out = out.drop_duplicates(["field", "period_end", "period", "value", "available_at"])
    out = out.sort_values(["field", "period_end", "period", "available_at"]).reset_index(drop=True)
    stats["anomalies"] = dict(stats["anomalies"])
    return out, stats


# ---------------- 轉接函式(讓現有回測可以換資料源) ----------------


def _to_d_basis(view: PITView, entity: str, x: pd.DataFrame) -> pd.Series:
    """facts_long 的 value 換到 d 的股數基準(只動 EPS 與股數;基準日 = basis_at,美股 = 申報日,見 _basis)。"""
    vals = x["value"].astype(float).copy()
    m = x["field"].isin(PER_SHARE | SHARE_COUNT)
    for i in x.index[m]:
        basis = x.at[i, "basis_at"]  # 美股 = 申報日(建置時填入;共用規約見 lab/pit/__init__.py)
        f = view.split_factor(entity, basis)
        if f != 1.0:
            vals.at[i] = vals.at[i] / f if x.at[i, "field"] in PER_SHARE else vals.at[i] * f
    return vals


def annual_table_asof(view: PITView, sym: str) -> pd.DataFrame:
    """和 `sec.annual_table` 同樣的欄位與格式(index = 年度結束日,name = fiscal_year_end),
    但每格是 d 當時看得到的最新版本;EPS 與股數換到 d 的股數基準(和 view.prices(adjust='split') 一致)。"""
    cols = list(sec.CONCEPTS)
    x = view.facts_long(sym, period="FY")
    if x.empty:
        return pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], name="fiscal_year_end"), dtype=float)
    x = x.assign(value=_to_d_basis(view, sym, x))
    df = x.pivot_table(index="period_end", columns="field", values="value", aggfunc="last").reindex(columns=cols)
    df.index = pd.DatetimeIndex(df.index)
    df = df.sort_index()
    # 同一年度兩個結束日(相差 ≤ 30 天)只留後面那個。舊版 sec.annual_table 是「每個日曆年留最後一筆」,
    # 會把 JNJ 這種年度結束在 1/1 與 12/31 的兩個不同會計年度(2023-01-01、2023-12-31)併掉一個,這裡不沿用。
    ends = df.index
    keep = [i == len(ends) - 1 or (ends[i + 1] - ends[i]).days > 30 for i in range(len(ends))]
    df = df[keep]
    df.index.name = "fiscal_year_end"
    df.columns.name = None
    gp = df["revenue"] - df["cogs"]
    df["gross_profit"] = df["gross_profit"].combine_first(gp)
    return df.tail(15)


def ttm_asof(view: PITView, sym: str, skip: int = 0) -> dict | None:
    """近四季合計(流量科目;加權平均股數取四季平均)與最新一季季底(時點科目)。

    最新四個單季期末必須是 Q1–Q4 各一、照季別順序連續,且間隔 75–125 天(COST 的第 4 季是 16 週 = 112 天),
    否則回 None。個別欄位四季不齊 → 該欄位 None。EPS 與股數換到 d 的股數基準。
    skip:往前跳過最新的幾季(skip = 4 → 一年前的 TTM,給成長率比較用;P2-H08)。
    """
    q = view.facts_long(sym, period="Q")
    q = q[q["field"].isin(FLOW)]
    if q.empty:
        return None
    allends = sorted(pd.DatetimeIndex(q["period_end"].unique()))
    ends = allends[max(0, len(allends) - 4 - skip):len(allends) - skip]
    if len(ends) < 4:
        return None
    gaps = [(b - a).days for a, b in zip(ends[:-1], ends[1:])]
    if not all(75 <= g <= 125 for g in gaps):
        return None
    sel = q[q["period_end"].isin(ends)]
    labels = [int(p[1]) for p in sel.groupby("period_end")["period"].first().reindex(ends)]
    if sorted(labels) != [1, 2, 3, 4] or any((b - a) % 4 != 1 for a, b in zip(labels[:-1], labels[1:])):
        return None
    sel = sel.assign(value=_to_d_basis(view, sym, sel))
    w = sel.pivot_table(index="period_end", columns="field", values="value", aggfunc="last").reindex(index=ends)
    out: dict = {"period_end": ends[-1].date().isoformat(), "quarters": [e.date().isoformat() for e in ends]}
    for f in FLOW:
        s = w[f] if f in w else pd.Series(dtype=float)
        if len(s) != 4 or s.isna().any():
            out[f] = None
        else:
            out[f] = float(s.mean()) if f in AVG else float(s.sum())
    if out.get("gross_profit") is None and out.get("revenue") is not None and out.get("cogs") is not None:
        out["gross_profit"] = out["revenue"] - out["cogs"]
    inst = view.facts_long(sym, fields=sorted(INSTANT))
    inst = inst[inst["period_end"] == ends[-1]]
    for f in sorted(INSTANT):
        v = inst.loc[inst["field"] == f, "value"]
        out[f] = float(v.iloc[-1]) if len(v) else None
    return out


# ---------------- 建 store 與資料品質 ----------------


def _reason(status: str, n: int, forms: set[str]) -> str:
    return {
        "no_us_gaap": "沒有 us-gaap 財報(IFRS 的 20-F 申報者),不支援",
        "20-F": f"只申報 20-F / 6-K(外國發行人,沒有 10-K / 10-Q 與季報),不支援;表單 {sorted(f for f in forms if f)[:6]}",
        "no_10k": "沒有 10-K / 10-Q 申報",
        "few_concepts": f"us-gaap 概念只有 {n} 個(< {MIN_US_GAAP_CONCEPTS}),CIK 可能對錯",
    }.get(status, status)


def build_store(symbols: list[str] | None = None, price_source: str = "10y") -> tuple[PITStore, dict]:
    """離線從快取建 store。回傳 (store, 報告);報告含每檔的狀態、排除原因與資料品質統計。
    price_source:'10y'(預設,Yahoo 10 年)或 'since'(Yahoo 2013 起的長歷史,快取 namespace px_since)。"""
    from ..cache import CacheMiss
    from ..sources import yahoo
    from ..universe import US_LARGE

    if price_source not in PRICE_SOURCES:
        raise ValueError(f"price_source 只能是 {PRICE_SOURCES}")
    symbols = symbols or [s for s, _ in US_LARGE]
    cal = trading_calendar(price_source)
    facts_all, ev_all, prices = [], [], {}
    report: dict = {"calendar": [str(cal[0].date()), str(cal[-1].date())] if len(cal) else None, "symbols": {}}
    for sym in symbols:
        info: dict = {}
        hist = yahoo.history(sym, yahoo.Status()) if price_source == "10y" else yahoo.history_since(sym)
        if price_source != "10y":
            info["price_source"] = "yahoo_since"
        full = yahoo._get(sym, "splits", "info", yahoo.Status(), False)  # 完整分割史(快取 key「代號|splits」)
        full = full if isinstance(full, pd.Series) else None
        info["full_split_history"] = full is not None
        ev = split_events(sym, hist, full)
        if hist is not None and not hist.empty:
            px = raw_prices(hist)
            if sym in PRICE_START:
                px = px[px.index >= pd.Timestamp(PRICE_START[sym][0])]
                info["price_note"] = PRICE_START[sym][1]
            prices[sym] = px
            info["price_range"] = [str(px.index[0].date()), str(px.index[-1].date())]
        else:
            info["price_range"] = None
        if len(ev):
            ev_all.append(ev)
        info["splits"] = [[str(pd.Timestamp(r.date).date()), float(r.ratio)] for r in ev.itertuples()]
        info["non_split_price_adjustments"] = non_split_adjustments(hist)
        spin = spinoff_events(sym, hist, prices.get(sym))
        if len(spin):
            ev_all.append(spin)
        info["spinoffs"] = [[str(pd.Timestamp(r.date).date()), round(float(r.ratio), 6)] for r in spin.itertuples()]
        legacy = sec.cik_for(sym)
        cik = sec.cik_for(sym, overrides=True)
        info["cik"] = cik
        if legacy != cik:
            info["cik_legacy"] = legacy
        if not cik:
            info.update(status="excluded", reason="SEC 對照表找不到 CIK")
            report["symbols"][sym] = info
            continue
        try:
            fj = sec.companyfacts(cik)
        except CacheMiss:
            info.update(status="excluded", reason=f"凍結快取沒有 CIK {cik} 的 companyfacts(需要 Fetcher 補抓)")
            report["symbols"][sym] = info
            continue
        st, n, forms = filer_status(fj)
        info["us_gaap_concepts"] = n
        if st != "ok":
            info.update(status="excluded", reason=_reason(st, n, forms))
            report["symbols"][sym] = info
            continue
        f, stt = build_facts(sym, fj, cal, ev)
        info.update(status="ok", n_facts=len(f), anomalies=stt.get("anomalies", {}),
                    split_near_filing=stt.get("split_near_filing", []), q4_tag_mismatch=stt.get("q4_tag_mismatch", [])[:20])
        facts_all.append(f)
        report["symbols"][sym] = info
    facts = pd.concat(facts_all, ignore_index=True) if facts_all else pd.DataFrame(columns=FACT_COLS)
    for c in ("period_end", "available_at", "filed", "basis_at"):
        facts[c] = pd.to_datetime(facts[c])
    events = pd.concat(ev_all, ignore_index=True) if ev_all else split_events("", None)
    events["date"] = pd.to_datetime(events["date"])
    vintage = f"us_pit@{config.DATA_DIR.name}"
    store = PITStore(facts.reset_index(drop=True), prices, events.reset_index(drop=True), vintage)
    for sym, info in report["symbols"].items():
        if info.get("status") == "ok":
            info["quality"] = quality(store, sym)
    return store, report


def quality(store: PITStore, sym: str) -> dict:
    """單檔資料品質:年度覆蓋、季報覆蓋率、重編比例、申報落後天數、疑似未記錄的分割。"""
    f = store.facts[(store.facts["entity"] == sym)]
    ev = store.events[store.events["entity"] == sym]
    out: dict = {}
    direct = f[~f["source"].str.startswith(f"{SOURCE}-derived")]
    fy = direct[direct["period"] == "FY"]
    ni = fy[fy["field"] == "net_income"]
    years = sorted(pd.DatetimeIndex(ni["period_end"].unique()))
    out["fy_years"] = len(years)
    out["fy_range"] = [str(years[0].date()), str(years[-1].date())] if years else None
    # 季報覆蓋率:淨利單季期末數 ÷ 第一個年度起到最新一季之間應有的季數
    qn = f[(f["field"] == "net_income") & f["period"].str.startswith("Q")]
    if years and len(qn):
        first_start = years[0] - pd.Timedelta(days=365)
        qe = sorted(e for e in pd.DatetimeIndex(qn["period_end"].unique()) if e > first_start)
        expected = int(round((qe[-1] - first_start).days / 91.31)) if qe else 0
        out["quarters"] = len(qe)
        out["quarter_coverage"] = round(len(qe) / expected, 3) if expected else None
        # 回測主要期間(2015 起)的覆蓋率
        s15 = pd.Timestamp("2015-01-01")
        q15 = [e for e in qe if e >= s15]
        exp15 = int(round((qe[-1] - s15).days / 91.31)) if qe and qe[-1] > s15 else 0
        out["quarter_coverage_2015"] = round(len(q15) / exp15, 3) if exp15 else None
    # 版本:同一 (欄位, 期末, 期別) 第一次申報 vs 最新申報
    g = direct.sort_values(["filed", "available_at"]).groupby(["field", "period_end", "period"])
    first, last = g.first(), g.last()
    n_ver = g.size()
    changed = pd.Series(~np.isclose(first["value"], last["value"], rtol=1e-6, atol=1e-9), index=first.index)
    split_expl = pd.Series(False, index=first.index)
    suspicious = []
    for idx in first.index[changed]:
        a, b = first.at[idx, "value"], last.at[idx, "value"]
        if idx[0] not in PER_SHARE | SHARE_COUNT or not a or not b:
            continue
        r = a / b if idx[0] in PER_SHARE else b / a
        m = _split_mult(ev, first.at[idx, "filed"], last.at[idx, "filed"])
        # 分割後重新申報的 EPS 只留兩位小數(NVDA FY2023:1.74 → 0.17),容許 ±0.006 的四捨五入誤差
        rounding_ok = idx[0] in PER_SHARE and abs(a / m - b) <= 0.0051
        if m != 1.0 and (abs(r / m - 1) < 0.02 or rounding_ok):
            split_expl.at[idx] = True
        elif r >= 1.9 and abs(r - round(r)) < 0.05 * r:
            suspicious.append([idx[0], str(pd.Timestamp(idx[1]).date()), idx[2], round(float(r), 2)])
    is_fy = pd.Series(first.index.get_level_values("period") == "FY", index=first.index)
    out["periods_fy"] = int(is_fy.sum())
    out["periods_fy_multi_version"] = int(((n_ver > 1) & is_fy).sum())
    out["restated_fy"] = int((changed & ~split_expl & is_fy).sum())
    out["split_only_fy"] = int((changed & split_expl & is_fy).sum())
    out["periods_q"] = int((~is_fy).sum())
    out["restated_q"] = int((changed & ~split_expl & ~is_fy).sum())
    # concept 混用:首次 ≠ 最新、且兩個版本的 concept 不同(可能是定義不同造成的假重編);版本來回跳(A → B → A)
    conc = direct["source"].str.split("|").str[-1]
    dd = direct.assign(concept=conc).sort_values(["filed", "available_at"])
    g2 = dd.groupby(["field", "period_end", "period"])
    fc, lc = g2["concept"].first(), g2["concept"].last()
    sw = changed & ~split_expl & (fc != lc)
    out["concept_switch_by_field"] = {k: int(v) for k, v in sw[sw].groupby(level="field").size().items()}
    out["changed_by_field"] = {k: int(v) for k, v in (changed & ~split_expl)[changed & ~split_expl]
                               .groupby(level="field").size().items()}
    flip = {}
    for (fld, _pe, _p), h in g2:
        vals = h["value"].round(6).tolist()
        seq = [v for i, v in enumerate(vals) if i == 0 or v != vals[i - 1]]
        if len(seq) >= 3 and len(set(seq)) < len(seq):
            flip[fld] = flip.get(fld, 0) + 1
    out["flip_flop_by_field"] = flip
    out["unexplained_integer_ratio"] = suspicious[:10]
    out["n_unexplained_integer_ratio"] = len(suspicious)
    # 申報落後天數:每個報告期(期末日 × 期別)第一次出現的申報日 − 期末日(一個報告期算一次,不按欄位重複計)。
    # 年度 > 365 天、季度 > 180 天的是「該期間沒有自己的報告,第一次出現是一年後的比較數字」
    # (XBRL 以前的年度、公司換 CIK 或剛上市),另外計數,不放進落後天數分布
    per = direct.groupby(["period_end", "period"])["filed"].min()
    lag = (per - per.index.get_level_values("period_end")).dt.days
    pfy = per.index.get_level_values("period") == "FY"
    ok = (lag >= 0) & ((pfy & (lag <= 365)) | (~pfy & (lag <= 180)))
    out["lag_fy_days"] = lag[ok & pfy].astype(int).tolist()
    out["lag_q_days"] = lag[ok & ~pfy].astype(int).tolist()
    out["periods_first_seen_as_comparative"] = int((~ok).sum())
    # 舊做法「年度結束 +75 天」:實際首次申報晚於 75 天的年度比例(= 舊回測會提早用到的比例)
    out["fy_filed_after_75d"] = int((lag[ok & pfy] > 75).sum())
    return out


def coverage_monthly(store: PITStore, sym: str, start: str = "2015-01-01") -> dict:
    """2015 年起每個月最後一個交易日跑一次 ttm_asof / annual_table_asof,統計「TTM 整段是 None」與
    每個欄位是 None 的月數(逐欄位覆蓋率)。"""
    sub = PITStore(store.facts[store.facts["entity"] == sym], {sym: store.prices[sym]} if sym in store.prices else {},
                   store.events[store.events["entity"] == sym], store.vintage)
    px = store.prices.get(sym)
    if px is None or px.empty:
        return {}
    idx = px.index[px.index >= pd.Timestamp(start)]
    months = pd.Series(idx, index=idx).groupby([idx.year, idx.month]).max().tolist()
    none_ttm, field_none, fy_none = 0, defaultdict(int), defaultdict(int)
    for d in months:
        v = sub.as_of(d)
        t = ttm_asof(v, sym)
        if t is None:
            none_ttm += 1
        else:
            for f in FLOW + sorted(INSTANT):
                if t.get(f) is None:
                    field_none[f] += 1
        a = annual_table_asof(v, sym)
        for f in ("eps_diluted", "shares_diluted", "revenue", "net_income"):
            if a.empty or pd.isna(a[f].iloc[-1]):
                fy_none[f] += 1
    return {"months": len(months), "ttm_none_months": none_ttm, "ttm_field_none_months": dict(field_none),
            "fy_latest_none_months": dict(fy_none)}


def summarize(report: dict) -> dict:
    ok = {s: i for s, i in report["symbols"].items() if i.get("status") == "ok"}
    lf = np.array([x for i in ok.values() for x in i["quality"]["lag_fy_days"]])
    lq = np.array([x for i in ok.values() for x in i["quality"]["lag_q_days"]])

    def dist(a):
        if not len(a):
            return {"n": 0}
        return {"n": int(len(a)), "median": float(np.median(a)), "p90": float(np.percentile(a, 90)),
                "max": int(a.max()), "share_gt_75": round(float((a > 75).mean()), 4)}

    keys = ("periods_fy", "periods_fy_multi_version", "restated_fy", "split_only_fy", "periods_q", "restated_q",
            "n_unexplained_integer_ratio", "fy_filed_after_75d", "periods_first_seen_as_comparative")
    tot = {k: sum(i["quality"][k] for i in ok.values()) for k in keys}
    return {"n_ok": len(ok), "excluded": {s: i["reason"] for s, i in report["symbols"].items() if i.get("status") != "ok"},
            "lag_fy": dist(lf), "lag_q": dist(lq), "totals": tot}


def main() -> None:
    store, report = build_store()
    out = config.OUT_DIR / "pit_us.sqlite"
    store.save(out)
    for sym, info in report["symbols"].items():
        if info.get("status") == "ok":
            info["coverage_monthly"] = coverage_monthly(store, sym)
    report["summary"] = summarize(report)
    (config.OUT_DIR / "pit_us_quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str),
                                                       encoding="utf-8")
    print(json.dumps({"saved": str(out), "facts": len(store.facts), "prices": len(store.prices),
                      "events": len(store.events), **report["summary"]}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
