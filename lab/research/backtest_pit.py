"""v1.1 在 PIT 新資料上的回測(v2 階段 1.7,C2)。比較鏈的第二段:特徵與模型完全不變(`backtest.judge_at` 原封不動),
只把輸入換成 point-in-time 資料。方法與數字見 docs/v1.1新資料基準.md。

和 v1.1 回測(lab/research/backtest.py)的差異(全部是「資料 / 股票池 / 期間 / 評估」,不是模型):
- 價格:PITStore 的原始價格,只套用判斷日 d 以前的分割 / 配股 / 減資(`view.prices('split')`,轉成 yfinance 欄名);
  Yahoo 2013 起的長歷史(快取 namespace px_since);台股沒有 Yahoo 的下市股改讀證交所 STOCK_DAY 原始日價格,
  配股 / 現金股利由 FinMind 股利表套用(lab/pit/build_tw.py,price_source='since')。
- 財報:台股 `tw_fdat_asof`(法定期限 + 1 交易日、每股數字換到 d 的股數基準、本益比由 PIT 價格 ÷ TTM EPS);
  美股 `annual_table_asof`(每個申報版本、申報日 + 1 交易日;_avail = 這個年度第一次公布的日期)。
- 大盤與總經(^TWII、^VIX、^TNX、TWD=X、^GSPC):Yahoo 長歷史,截到 d。
- 股票池:台股每月 `universe_pit.tw_universe(d, 50)`(PIT,含之後下市的公司);另跑固定 TW_LARGE 40 當對照;美股 US_LARGE 40。
- 期間:台股 2016-01 起、美股 2015-01 起,到資料最後一個完整月份。
- 評估:`evaluate.evaluate` 新模式(t+1 成交、含息報酬 = store 的 'total'、含息大盤:證交所報酬指數 / ^SP500TR),**只算開發集**。

- 時效檢查(1.9d):判斷前 `staleness_reason` 擋掉過期的價格 / 財報(跳過並記「時效:…」原因、計入覆蓋率),常數見該段。

同一個 (股票, 判斷日) 的判斷只和這兩個有關,所以兩個台股股票池共用判斷結果(只算一次)。

用法(離線,讀 $INVEST_LAB_DATA_DIR 的快取,寫 $INVEST_LAB_OUT_DIR/bt_pit/):
    python -m lab.research.backtest_pit all [--workers 8]    # build → judge → eval
    python -m lab.research.backtest_pit build | judge | evaluate
    python -m lab.research.backtest_pit cutpoints [--rows 資料夾] [--out json]   # P2-H10:網站評等用的分位切點
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from .. import config
from ..pit import PITStore, PITView
from ..pit import build_tw as BTW
from ..pit import build_us as BUS
from ..universe import TW_LARGE, US_LARGE

OUT = config.OUT_DIR / "bt_pit"
START = {"TW": "2016-01-01", "US": "2015-01-01"}
# A3(docs/存活者偏誤.md)列出的曾入選、之後下市的 11 檔:沒有 Yahoo,價格改讀證交所日價格
UNIVERSE_COVERAGE_WARN = 0.90
DELISTED = ("2311", "2325", "2448", "2456", "2823", "2888", "3454", "3474", "3514", "4733", "6452")  # 2026-10 的已知清單(後備)
_delisted_cache: set | None = None


def delisted_codes() -> set:
    """台股下市代號(Red-team L3:不再只靠寫死的清單)= FinMind 官方下市清單 ∪ DELISTED。
    只讀磁碟快取、不管 TTL、絕不連網(評估不能有抓資料的副作用,Verifier39 問題 3);沒有快取就只用 DELISTED,
    而且不記住這個退回結果(之後有快取時會重新讀)。2026-10 時兩者和股票池的交集相同,評估數字不變。"""
    global _delisted_cache
    if _delisted_cache is not None:
        return _delisted_cache
    from ..cache import _path, _read
    codes: set = set()
    try:
        p = _path("finmind", "TaiwanStockDelisting|None|None")
        old = _read(p) if p.exists() else None
        dl = old["v"] if old else None
        if dl is not None and len(dl):
            codes = set(dl["stock_id"].astype(str))
    except Exception:  # noqa: BLE001
        codes = set()
    if not codes:
        return set(DELISTED)
    _delisted_cache = codes | set(DELISTED)
    return _delisted_cache
EXTRA_TW = ("3167",)
MACRO_SYMS = ("^TWII", "^VIX", "^TNX", "TWD=X", "^GSPC")
MACRO = {"TW": {"bench": "^TWII", "vix": "^VIX", "tnx": "^TNX", "fx": "TWD=X"},
         "US": {"bench": "^GSPC", "vix": "^VIX", "tnx": "^TNX", "fx": None}}  # 和 v1.1 一樣:美股沒有匯率
TR_BENCH = {"TW": "TWSE_TR", "US": "^SP500TR"}  # 含息大盤(只用在評估)
UNIVERSES = {"tw_pit50": "TW", "tw_large40": "TW", "us_large40": "US"}
SUBPERIODS = {"2016-2020": ("2016-01-01", "2020-12-31"), "2021+": ("2021-01-01", None)}  # v1.1 設計時沒看過 / 之後
FAR = pd.Timestamp("2100-01-01")
YF_COLS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def paths() -> dict:
    return {"TW": OUT / "pit_tw.sqlite", "US": OUT / "pit_us.sqlite", "macro": OUT / "pit_macro.sqlite",
            "chips": OUT / "pit_chips.sqlite"}


# ---------------------------------------------------------------- 代號
_TW_SYM = {s.split(".")[0]: s for s, _ in TW_LARGE}


def tw_symbol(code: str) -> str:
    """台股代號 → 回測列的 symbol(和 v1.1 一樣帶 .TW / .TWO)。"""
    return _TW_SYM.get(code, f"{code}.TW")


def entity_of(sym: str, market: str) -> str:
    return sym.split(".")[0] if market == "TW" else sym


def union_codes(start=START["TW"], end=None) -> list[str]:
    """PIT 前 50 在 start ~ end 期間曾入選的代號 ∪ data/research/tw_pit50_union.txt(有的話)∪ 3167 ∪ TW_LARGE。"""
    from .. import universe_pit as U
    codes = set(EXTRA_TW) | set(_TW_SYM)
    f = config.DATA_DIR / "research" / "tw_pit50_union.txt"
    if f.exists():
        codes |= {x.strip() for x in f.read_text(encoding="utf-8").split() if x.strip()}
    m = U.tw_membership(pd.Timestamp(start) - pd.DateOffset(months=1), pd.Timestamp(end or config.today()), 50)
    if len(m):
        codes |= set(m["code"].astype(str))
    return sorted(codes)


# ---------------------------------------------------------------- 1. 建 store
def _macro_raw(h: pd.DataFrame) -> pd.DataFrame:
    h = h.copy()
    h.index = pd.DatetimeIndex(h.index).tz_localize(None).normalize()
    h = h[~h.index.duplicated(keep="last")].sort_index()
    return pd.DataFrame({"open": h["Open"], "high": h["High"], "low": h["Low"], "close": h["Close"],
                         "volume": h.get("Volume", pd.Series(0.0, index=h.index)), "cash_div": 0.0}, index=h.index)


def build_macro() -> tuple[PITStore, dict]:
    from ..sources import twse_hist, yahoo
    prices, info = {}, {}
    for s in MACRO_SYMS + ("^SP500TR",):
        h = yahoo.history_since(s)
        if h is None or h.empty:
            info[s] = "沒有快取"
            continue
        prices[s] = _macro_raw(h)
        info[s] = [str(prices[s].index[0].date()), str(prices[s].index[-1].date()), len(prices[s])]
    try:
        tr = twse_hist.total_return_index("2014-01-01")
    except Exception as e:  # noqa: BLE001  某個月沒有快取:記下來,評估時會缺含息大盤
        tr, info["TWSE_TR"] = None, f"{type(e).__name__}: {e}"[:160]
    if tr is not None and len(tr):
        prices["TWSE_TR"] = pd.DataFrame({"open": np.nan, "high": np.nan, "low": np.nan, "close": tr.astype(float),
                                          "volume": np.nan, "cash_div": 0.0}, index=pd.DatetimeIndex(tr.index))
        info["TWSE_TR"] = [str(tr.index[0].date()), str(tr.index[-1].date()), len(tr)]
    from ..sources import macro_rates   # P2-H01:台灣 10 年期公債年平均、Damodaran ERP(沒有 json 就是空表)
    rf = macro_rates.facts()
    info["RATES"] = len(rf)
    if len(rf):
        return PITStore(facts=rf, prices=prices, vintage=f"macro@{config.DATA_DIR.name}"), info
    return PITStore(prices=prices, vintage=f"macro@{config.DATA_DIR.name}"), info


def build(markets=("TW", "US"), tw_codes=None, us_syms=None) -> dict:
    """tw_codes / us_syms:只建這些代號(新使用者的精簡版,scripts/setup_research.py);None = 原本的完整股票池。"""
    OUT.mkdir(parents=True, exist_ok=True)
    P = paths()
    rep: dict = {"data_dir": str(config.DATA_DIR), "as_of": str(config.today())}
    t0 = time.time()
    macro, rep["macro"] = build_macro()
    macro.save(P["macro"])
    if "TW" in markets:
        codes = sorted(tw_codes) if tw_codes is not None else union_codes()
        names = dict(TW_LARGE)
        uni = [(tw_symbol(c), names.get(tw_symbol(c), "")) for c in codes]
        store, audits, _ = BTW.build_store(uni, vintage=f"tw-pit-since@{config.DATA_DIR.name}", price_source="since")
        store.save(P["TW"])
        rep["TW"] = {"n_codes": len(codes), "n_prices": len(store.prices), "n_facts": int(len(store.facts)),
                     "no_price": sorted(c for c in codes if c not in store.prices),
                     "price_source": {c: a.coverage.get("price_source") for c, a in audits.items()},
                     "price_range": {c: [a.coverage.get("price_first"), a.coverage.get("price_last")] for c, a in audits.items()},
                     "q_range": {c: [a.coverage.get("q_first"), a.coverage.get("q_last")] for c, a in audits.items()}}
        from ..pit import build_chips as BC   # P2-H07:籌碼歷史(沒有快取就是空表,v2.1 以前的資料版本不受影響)
        chips, rep["TW_chips"] = BC.build_store(codes)
        chips.save(P["chips"])
    if "US" in markets:
        store, r = BUS.build_store(list(us_syms) if us_syms is not None else [s for s, _ in US_LARGE], price_source="since")
        store.save(P["US"])
        rep["US"] = {s: {k: v.get(k) for k in ("status", "reason", "price_range")} for s, v in r["symbols"].items()}
    rep["elapsed_sec"] = round(time.time() - t0)
    (OUT / "build_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return rep


def build_combined(tw_codes=(), us_syms=()) -> PITStore:
    """測試用:把幾檔台股、美股與大盤 / 總經放進同一個 store(竄改測試要一起竄改)。只讀快取。"""
    parts = [build_macro()[0]]
    if tw_codes:
        parts.append(BTW.build_store([(tw_symbol(c), dict(TW_LARGE).get(tw_symbol(c), "")) for c in tw_codes],
                                     price_source="since")[0])
    if us_syms:
        parts.append(BUS.build_store(list(us_syms), price_source="since")[0])
    facts = pd.concat([p.facts for p in parts if len(p.facts)], ignore_index=True)
    events = pd.concat([p.events for p in parts if len(p.events)], ignore_index=True)
    prices = {k: v for p in parts for k, v in p.prices.items()}
    return PITStore(facts, prices, events, "combined-sample")


# ---------------------------------------------------------------- 2. 轉接(只透過 view)
def _yf(p: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"Open": p["open"], "High": p["high"], "Low": p["low"], "Close": p["close"],
                         "Adj Close": p["close"], "Volume": p["volume"]}, index=p.index)


def macro_asof(view: PITView, market: str) -> dict:
    """d 以前的大盤與總經(yfinance 欄名);沒有資料的項目是 None。"""
    out = {}
    for k, s in MACRO[market].items():
        p = view.prices(s, adjust="none") if s else None
        out[k] = _yf(p) if p is not None and len(p) else None
    from ..sources import macro_rates
    out["rates"] = macro_rates.rates_asof(view)   # P2-H01;只有參數 valuation.rates_asof 打開時估值才使用
    return out


def us_fdat_asof(view: PITView, sym: str) -> dict | None:
    """和 backtest.us_data 同結構 {"a": 年表(含 _avail)};數字是 d 當時看得到的最新版本,EPS / 股數在 d 的股數基準。

    _avail = 這個年度第一次公布(最早 available_at)的日期:v1.1 用「年度結束 + 75 天」,這裡用實際申報日 + 1 交易日。
    同一份申報第一次帶出好幾個年度(資料最早那份 10-K 的比較數字)時,較舊的年度往前錯開幾奈秒,
    讓 judge_at 的本益比階梯不會有重複索引;判斷日都是午夜,錯開幾奈秒不改變「_avail ≤ d」的結果,也不會提前用到資料。
    """
    a = BUS.annual_table_asof(view, sym)
    if a.empty:
        return None
    a = a.copy()
    if "cfo" in a and "capex" in a:
        a["fcf"] = a["cfo"] - a["capex"].abs()
    if "total_debt" not in a:
        a["total_debt"] = a.get("long_term_debt")
    a["ebit"] = a.get("operating_income")
    first = view.facts_long(sym, period="FY").groupby("period_end")["available_at"].min()
    av = pd.Series(pd.DatetimeIndex(first.reindex(a.index).to_numpy()), index=a.index)
    av = av.fillna(av.max())
    rank_back = av.groupby(av).cumcount(ascending=False)  # 同一天(index 依年度排序):最新年度 0、較舊的 1、2…
    a["_avail"] = (av - pd.to_timedelta(rank_back.astype(int), unit="ns")).to_numpy()
    # P2-H08:近四季 TTM 與一年前的 TTM(只有參數 us_quarterly_ttm 打開時 judge_at 才使用)
    return {"a": a, "ttm": BUS.ttm_asof(view, sym), "ttm_prev": BUS.ttm_asof(view, sym, skip=4)}


def judge_pit(view: PITView, market: str, sym: str, params: dict, ind_tw=None, ind_key=None,
              macro_view: PITView | None = None, chips_fm: dict | None = None) -> dict | None:
    """在 view(判斷日 d 收盤後看得到的資料)上呼叫完全不改的 backtest.judge_at。macro_view 沒給就用同一個 view。"""
    from . import backtest as BT
    ent = entity_of(sym, market)
    px = BTW.price_asof(view, ent)
    if px.empty:
        return None
    fdat = BTW.tw_fdat_asof(view, ent) if market == "TW" else us_fdat_asof(view, ent)
    if fdat is None:
        return None
    macro = macro_asof(macro_view or view, market)
    if macro.get("bench") is None:
        return None
    from ..analysis import income_risk as IR   # S4:股利、波動、beta(只透過 view;每股股利用 split_factor 換基準)
    ir = IR.from_view(view, ent, market, macro_view or view, px)
    return BT.judge_at(view.d, market, sym, px, fdat, macro, params, ind_tw, ind_key, chips_fm=chips_fm, income_risk=ir)


def skip_reason(view: PITView, market: str, sym: str) -> str:
    """judge_pit 回傳 None 時的原因(只用來報告;條件和 judge_at 的前置檢查相同)。"""
    ent = entity_of(sym, market)
    p = view.prices(ent, adjust="none")
    if p.empty:
        return "沒有價格"
    if len(p.tail(760)) < 300:
        return "價格不足 300 個交易日"
    if market == "TW":
        if len(BTW.quarterly_asof(view, ent)) < 8:
            return "已公布財報不足 8 季"
        return "近四季財報不完整(TTM 缺)"
    a = view.facts(ent, ["revenue"], period="FY")
    if len(a) == 0 and view.facts_long(ent).empty:  # 只用當下可見資料報告原因,不影響判斷
        return "沒有 SEC 年報(20-F 申報者等,PIT 管線不支援)"
    if len(a) < 4:
        return "已公布年報不足 4 年"
    return "其他(年報欄位不足)"


# ---------------------------------------------------------------- 時效檢查(1.9d,Red-team M1)
# judge_at 只看「d 以前有沒有資料」,不看資料是否過期:停牌 / 下市後的最後價格、停止申報後的舊財報都會被當成現況。
# 這裡在判斷前擋掉過期資料(跳過並記原因、計入覆蓋率);只用 view(d 收盤後看得到的資料),不改 judge_at。
PRICE_STALE_TRADING_DAYS = 5
"""價格:d 往前 5 個大盤交易日(含 d)內必須有有效收盤價。一週內沒有成交 = 停牌、下市或資料中斷;
這時 judge_at 的技術面與本益比用的是舊價格,而 t+1 成交的報酬也算不出來。5 天容許個股零星缺值與短暫暫停交易。"""
TW_FIN_LAG_QUARTERS = 1
"""台股財報:最新已公布季可以比「依法定期限 d 時應該已公布的最新一季」晚 1 季。
寬限 1 季是因為 available_at 用法定期限推算(沒有真實申報日)、FinMind 偶有個別季缺漏;晚 2 季以上 = 停止申報 / 資料中斷。"""
US_FY_MAX_AGE = pd.DateOffset(months=15)
"""美股年報:最新年報期末不得早於 d − 15 個月。10-K 最晚在年度結束後 60–90 天申報(依申報人類別),
12 個月的年度 + 90 天 ≈ 15 個月;再舊就是漏報一整年。"""
US_Q_MAX_AGE = pd.DateOffset(months=7)
"""美股季報(有季報 TTM 的公司):最新一季期末不得早於 d − 7 個月。10-Q 在季末後 40–45 天、Q4 隨 10-K 在 60–90 天,
正常情況最新一季期末距 d 最多約 3 + 3 = 6 個月;7 個月多給 1 個月緩衝,超過代表至少漏了一季。"""


def _bench_calendar(view: PITView, market: str) -> pd.DatetimeIndex:
    p = view.prices(MACRO[market]["bench"], adjust="none")
    if p.empty:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(p.index[p["close"].notna()])


_KIND: dict[str, tuple[bool, bool]] = {}


def _tw_kind(code: str) -> tuple[bool, bool]:
    """(金融業, 外國發行人):和建 store 時決定 available_at 的分類相同(股票清單,和 d 無關)。"""
    if code not in _KIND:
        _KIND[code] = (BTW.is_financial(code), BTW.is_foreign(code))
    return _KIND[code]


def expected_tw_quarter(d, financial: bool, foreign: bool, cal: pd.DatetimeIndex) -> pd.Timestamp:
    """d 時依法定期限應該已公布的最新一季期末(quarter_available_at ≤ d)。cal 只含 d 以前的交易日。"""
    d = pd.Timestamp(d)
    pe = pd.offsets.QuarterEnd(startingMonth=3).rollback(d.normalize()).normalize()
    for _ in range(8):
        if BTW.quarter_available_at(pe, financial, cal, foreign) <= d:
            return pe
        pe = (pe - pd.offsets.QuarterEnd(1)).normalize()
    return pe


def staleness_reason(view: PITView, market: str, sym: str, macro_view: PITView | None = None) -> str | None:
    """過期就回傳原因(字串開頭「時效:」),否則 None。只用 view 與 macro_view(都截在 d)。
    完全沒有價格 / 財報的情況交給 judge_pit 與 skip_reason(原本的原因不變)。"""
    d = pd.Timestamp(view.d)
    ent = entity_of(sym, market)
    p = view.prices(ent, adjust="none")
    if p.empty:
        return None
    cal = _bench_calendar(macro_view or view, market)
    cal = cal[cal <= d]
    valid = p.index[(p["close"] > 0) & p["close"].notna()]
    if len(cal):
        lo = cal[-PRICE_STALE_TRADING_DAYS] if len(cal) >= PRICE_STALE_TRADING_DAYS else cal[0]
        if not len(valid) or valid[-1] < lo:
            last = valid[-1].date() if len(valid) else "無"
            return f"時效:價格中斷(d 前 {PRICE_STALE_TRADING_DAYS} 個交易日內沒有收盤價;最後 {last})"
    if market == "TW":
        q = view.facts_long(ent, BTW.Q_COLS, period="Q")
        if q.empty:
            return None
        latest = pd.Timestamp(q["period_end"].max())
        exp = expected_tw_quarter(d, *_tw_kind(ent), cal)
        floor = (exp - pd.offsets.QuarterEnd(TW_FIN_LAG_QUARTERS)).normalize()
        if latest < floor:
            return f"時效:財報中斷(最新 {latest.date()},應至少 {floor.date()})"
        return None
    fy = view.facts_long(ent, period="FY")
    if not fy.empty:
        latest = pd.Timestamp(fy["period_end"].max())
        if latest < d - US_FY_MAX_AGE:
            return f"時效:年報中斷(最新年度 {latest.date()} 早於 d − 15 個月)"
    q = view.facts_long(ent, period="Q")
    if not q.empty:
        latest = pd.Timestamp(q["period_end"].max())
        if latest < d - US_Q_MAX_AGE:
            return f"時效:季報中斷(最新一季 {latest.date()} 早於 d − 7 個月)"
    return None


# ---------------------------------------------------------------- 判斷日
def decision_dates(market: str, macro: PITStore | None = None) -> list[pd.Timestamp]:
    """和 v1.1 相同:每月最後一個營業日,對齊到大盤實際交易日;到資料最後一個完整月份。"""
    macro = macro or PITStore.load(paths()["macro"])
    idx = macro.prices[MACRO[market]["bench"]].index
    last = idx[-1]
    ds = [d for d in pd.date_range(START[market], last, freq="BME") if d <= last]
    return sorted({idx[idx.searchsorted(d, side="right") - 1] for d in ds})


def _industry(market: str, sym: str) -> tuple[str | None, str | None]:
    try:
        if market == "TW":
            from .. import resolver
            return (resolver.tw_lookup(sym.split(".")[0]) or {}).get("industry"), None
        from ..sources import yahoo
        return None, (yahoo.quick_info(sym) or {}).get("industryKey")
    except Exception:  # noqa: BLE001  中繼資料缺:產業留空(和 v1.1 一樣只影響景氣循環偵測與金融業判斷)
        return None, None


_MACRO_CACHE: dict = {}


def _judge_entity(task: tuple) -> tuple[list[dict], list[dict], float]:
    """worker:一檔股票、多個判斷日。每個 worker 自己載入 store(只載這一檔)。"""
    from ..config import load_params
    market, sym, dates = task
    t0 = time.time()
    P = paths()
    if "m" not in _MACRO_CACHE:
        _MACRO_CACHE["m"] = PITStore.load(P["macro"])
        _MACRO_CACHE["params"] = load_params()
    macro, params = _MACRO_CACHE["m"], _MACRO_CACHE["params"]
    ent = entity_of(sym, market)
    st = PITStore.load(P[market], entities=[ent])
    chips_st = None
    if market == "TW" and params.get("tw_chips_history") and P["chips"].exists():   # P2-H07(參數開關)
        from ..pit import build_chips as BC
        chips_st = PITStore.load(P["chips"], entities=[ent])
    rows, skips = [], []
    if ent not in st.prices:
        return rows, [{"symbol": sym, "date": str(pd.Timestamp(d).date()), "reason": "沒有價格(store 沒有這檔)"} for d in dates], 0.0
    ind_tw, ind_key = _industry(market, sym)
    for d in dates:
        d = pd.Timestamp(d)
        v = st.as_of(d)
        try:
            mv = macro.as_of(d)
            stale = staleness_reason(v, market, sym, mv)
            if stale:
                skips.append({"symbol": sym, "date": str(d.date()), "reason": stale})
                continue
            fm = BC.fm_asof(chips_st.as_of(d), ent) if chips_st is not None else None
            r = judge_pit(v, market, sym, params, ind_tw, ind_key, mv, chips_fm=fm)
            if r:
                rows.append(r)
            else:
                skips.append({"symbol": sym, "date": str(d.date()), "reason": skip_reason(v, market, sym)})
        except Exception as e:  # noqa: BLE001
            skips.append({"symbol": sym, "date": str(d.date()), "reason": f"例外 {type(e).__name__}: {e}"[:160]})
    return rows, skips, time.time() - t0


def plan(universes=None, symbols=None) -> dict:
    """每個股票池的 (判斷日, symbol) 清單。universes:只排這些股票池;symbols:只留這些代號(None = 全部,原本行為)。"""
    from .. import universe_pit as U
    macro = PITStore.load(paths()["macro"])
    want = set(universes) if universes is not None else set(UNIVERSES)
    out = {}
    tw_d = decision_dates("TW", macro)
    if "tw_pit50" in want:
        out["tw_pit50"] = [(d, tw_symbol(c)) for d in tw_d for c in U.tw_universe(d, 50)]
    if "tw_large40" in want:
        out["tw_large40"] = [(d, s) for d in tw_d for s, _ in TW_LARGE]
    if "us_large40" in want:
        out["us_large40"] = [(d, s) for d in decision_dates("US", macro) for s, _ in US_LARGE]
    if symbols is not None:
        keep = set(symbols)
        out = {u: [(d, s) for d, s in pairs if s in keep] for u, pairs in out.items()}
        out = {u: pairs for u, pairs in out.items() if pairs}   # 空的股票池沒有東西可算,留著會讓 judge 在合併空表時崩潰
    return out


def fixed_quantile_levels(r: pd.DataFrame, cuts) -> pd.Series:
    """P2-H10:每個判斷日在股票池內依 lt_score 排百分位,用固定分位給評等(cuts 例 [0.1, 0.3, 0.7, 0.9]:
    後 10% 賣出 0、次 20% 減碼 1、中間 40% 持有 2、次 20% 買進 3、前 10% 強力買進 4)。
    原本就沒有評等的列(例:估值不可得)維持 None,也不參與排名。只用同一天的分數,不會用到未來。"""
    ok = r["lt_level"].notna() & r["lt_score"].notna()
    pct = r["lt_score"].where(ok).groupby(r["date"]).rank(pct=True, method="average")
    lv = pd.Series(np.nan, index=r.index)
    lv[ok] = np.searchsorted(np.asarray(cuts, dtype=float), pct[ok].to_numpy(), side="left").astype(float)
    return lv


def cross_sectional_rescore(r: pd.DataFrame, lt_weights: dict) -> pd.DataFrame:
    """P2-H05:每個判斷日在股票池內把每個指標換成橫斷面百分位(0–100),面向分數 = 指標百分位的加權平均,
    長期分數 = 面向分數依 lt_weights 加權(缺資料的指標 / 面向不計入,和舊算法相同)。
    排名依「絕對分數」(越大越好);被門檻截在 0 / 100 而同分的,用原始值再排,原始值的方向只用同一天沒被截斷的樣本判斷。
    只用同一天的資料,不看報酬。回傳新的 lt_score 與 pillars(只更新長期用到的面向)。"""
    r = r.copy()
    recs = []
    for idx, items in r["_items"].items():
        if not isinstance(items, dict):
            continue
        for pk, lst in items.items():
            for key, val, sc, w in lst:
                if sc is not None:
                    recs.append((idx, pk, key, val, float(sc), float(w)))
    if not recs:
        return r
    x = pd.DataFrame(recs, columns=["idx", "pillar", "key", "value", "abs", "w"])
    x["date"] = r.loc[x["idx"], "date"].to_numpy()

    def _rank(g: pd.DataFrame) -> pd.Series:
        v = pd.to_numeric(g["value"], errors="coerce")
        mid = g["abs"].between(0, 100, inclusive="neither") & v.notna()
        sign = 0.0
        if mid.sum() >= 2:
            c = np.corrcoef(v[mid], g.loc[mid, "abs"])[0, 1]
            sign = float(np.sign(c)) if np.isfinite(c) else 0.0
        tieb = (v.fillna(0) * sign).rank(pct=True) * 1e-6 if sign else 0.0   # 只在同分時起作用
        return (g["abs"] + tieb).rank(pct=True, method="average") * 100

    x["pct"] = x.groupby(["date", "pillar", "key"], group_keys=False)[["value", "abs"]].apply(_rank)
    x["pw"] = x["pct"] * x["w"]
    p = x.groupby(["idx", "pillar"])[["pw", "w"]].sum()
    p["score"] = p["pw"] / p["w"]
    ps = p["score"].unstack()
    new_pillars, new_lt = [], []
    for idx in r.index:
        old = dict(r.at[idx, "pillars"]) if isinstance(r.at[idx, "pillars"], dict) else {}
        if idx in ps.index:
            for k, v in ps.loc[idx].dropna().items():
                old[k] = round(float(v), 1)
        num = sum(old[k] * w for k, w in lt_weights.items() if w and old.get(k) is not None)
        den = sum(w for k, w in lt_weights.items() if w and old.get(k) is not None)
        new_pillars.append(old)
        new_lt.append(round(num / den, 1) if den else None)
    r["pillars"], r["lt_score"] = new_pillars, new_lt
    return r


NEUTRAL_KEYS = ("fcf_yield", "ev_ebitda")   # P2-H11:回測裡有的估值倍數(同業本益比回測沒有)
MIN_GROUP = 8                                  # 同產業至少 8 檔才用產業內百分位(預先登記,事先固定)


def industry_neutral_valuation(r: pd.DataFrame, lt_weights: dict, industry: dict, keys=NEUTRAL_KEYS,
                               min_group: int = MIN_GROUP) -> pd.DataFrame:
    """P2-H11:估值面向裡的估值倍數(keys)每個判斷日改成百分位:同產業 ≥ min_group 檔用產業內排名,
    其他用整個股票池排名;估值面向的其他指標維持絕對分數。長期分數依 lt_weights 重算。只用同一天的資料。
    排名依絕對分數(越大越好),同分(被門檻截斷)用平均名次。產業別是今天的分類(不是 PIT,已知限制)。"""
    r = r.copy()
    recs = []
    for idx, items in r["_items"].items():
        if isinstance(items, dict):
            for key, val, sc, w in items.get("valuation", []):
                if sc is not None:
                    recs.append((idx, key, float(sc), float(w)))
    if not recs:
        return r
    x = pd.DataFrame(recs, columns=["idx", "key", "abs", "w"])
    x["date"] = r.loc[x["idx"], "date"].to_numpy()
    x["ind"] = r.loc[x["idx"], "symbol"].map(industry).fillna("—").to_numpy()
    nx = x[x["key"].isin(keys)].copy()
    size = nx.groupby(["date", "key", "ind"])["abs"].transform("size")
    nx["grp"] = np.where(size >= min_group, nx["ind"], "__all__")
    nx["score"] = nx.groupby(["date", "key", "grp"])["abs"].rank(pct=True, method="average") * 100
    x.loc[nx.index, "abs"] = nx["score"]
    x["pw"] = x["abs"] * x["w"]
    v = x.groupby("idx")[["pw", "w"]].sum()
    val = (v["pw"] / v["w"]).round(1)
    new_p, new_lt = [], []
    for idx in r.index:
        p = dict(r.at[idx, "pillars"]) if isinstance(r.at[idx, "pillars"], dict) else {}
        if idx in val.index:
            p["valuation"] = float(val[idx])
        num = sum(p[k] * w for k, w in lt_weights.items() if w and p.get(k) is not None)
        den = sum(w for k, w in lt_weights.items() if w and p.get(k) is not None)
        new_p.append(p)
        new_lt.append(round(num / den, 1) if den else None)
    r["pillars"], r["lt_score"] = new_p, new_lt
    return r


CUTPOINT_POOLS = {"TW": "tw_pit50", "US": "us_large40"}   # 正式股票池(網站單檔評等對照用)
CUTPOINT_FALLBACK = {"TW": "tw_large40"}   # 只有正式股票池缺席時才用(scripts/setup_research.py 的精簡版)
MIN_POOL = 10


def write_cutpoints(frames: dict, path=None, source: str = "judge") -> dict | None:
    """P2-H10 網站版:取每個市場正式股票池「最近一個判斷日」(有評等的列 ≥ MIN_POOL 檔)的 lt_score,
    算出固定分位的分數切點寫成小 json(預設 OUT_DIR/lt_cutpoints.json),網站單檔報告用它換算評等。
    frames = {股票池: rows DataFrame(date, lt_score, lt_level;lt_level 有值 = 有公平價、有評等)}。
    只用該判斷日的分數,不看任何報酬。沒有分位參數時不寫。"""
    from ..analysis.decision import CUTPOINTS_FILE, quantile_thresholds
    from ..config import load_params
    params = load_params()
    cuts = (params.get("decision_rules") or {}).get("fixed_quantile_levels")
    if not cuts:
        return None
    out = {"cuts": cuts, "lt_weights": (params.get("pillar_weights") or {}).get("long_term"),
           "params_version": params.get("version"), "cross_sectional_scoring": bool(params.get("cross_sectional_scoring")), "data_dir": config.DATA_DIR.name, "as_of": str(config.today()),
           "source": source, "markets": {}}
    for m, u in CUTPOINT_POOLS.items():
        r = frames.get(u)
        if (r is None or r.empty) and m in CUTPOINT_FALLBACK:   # 新使用者的精簡版沒有正式股票池,退回大型股池
            u = CUTPOINT_FALLBACK[m]
            r = frames.get(u)
        if r is None or r.empty:
            continue
        r = r[r["lt_level"].notna() & r["lt_score"].notna()]
        dates = pd.to_datetime(r["date"])
        n = r.groupby(dates).size()
        ok = n[n >= MIN_POOL]
        if ok.empty:
            continue
        d = ok.index.max()
        scores = sorted(float(x) for x in r.loc[dates == d, "lt_score"])
        th = quantile_thresholds(scores, cuts)
        if th is None:
            continue
        out["markets"][m] = {"universe": u, "date": str(d.date()), "n": len(scores), "thresholds": th, "scores": scores}
    path = path or (config.OUT_DIR / CUTPOINTS_FILE)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _json_default(o):
    """numpy 型別轉原生(NaN 照樣保留,json 寫成 NaN)。"""
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"無法序列化 {type(o).__name__}")


def raw_env_path(market: str) -> Path:
    return OUT / f"raw_{market.lower()}.sqlite"


def write_raw_env(allr: pd.DataFrame, params: dict) -> dict:
    """M4:把每筆判斷的基礎變數寫進 OUT/raw_{tw,us}.sqlite(整個重寫)。回傳 {市場: 筆數}。
    每筆同時存當時的短期分數(st),讀的人逐筆核對快取和逐筆檔是不是同一次 judge(lab/research/model_eval.py);
    meta 記資料版本、參數版本與雜湊、橫斷面開關、git sha。"""
    import sqlite3

    from .registry import config_hash
    OUT.mkdir(parents=True, exist_ok=True)
    out = {}
    for m, g in allr.groupby("market"):
        p = raw_env_path(m)
        tmp = p.with_name(f"{p.stem}.{os.getpid()}.tmp")
        tmp.unlink(missing_ok=True)
        con = sqlite3.connect(tmp)
        try:
            con.execute("CREATE TABLE env (date TEXT, symbol TEXT, env TEXT, st REAL, PRIMARY KEY (date, symbol))")
            con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
            con.executemany("INSERT INTO env VALUES (?, ?, ?, ?)",
                            [(str(d)[:10], s, json.dumps(e, default=_json_default, sort_keys=True),
                              None if st is None or pd.isna(st) else float(st))
                             for d, s, e, st in zip(g["date"], g["symbol"], g["_env"], g["st_score"])])
            con.executemany("INSERT INTO meta VALUES (?, ?)", [
                ("market", m), ("data_dir", config.DATA_DIR.name), ("params_version", str(params.get("version"))),
                ("params_hash", config_hash(params)),
                ("cross_sectional_scoring", str(bool(params.get("cross_sectional_scoring")))),
                ("tw_industry_neutral_valuation", str(bool(params.get("tw_industry_neutral_valuation")))),
                ("git_sha", config.git_sha()), ("created", time.strftime("%Y-%m-%dT%H:%M:%S")), ("n", str(len(g)))])
            con.commit()
        except BaseException:
            con.close()
            tmp.unlink(missing_ok=True)
            raise
        con.close()
        tmp.replace(p)
        out[m] = len(g)
    return out


def judge(workers: int | None = None, universes=None, symbols=None) -> dict:
    from ..config import load_params
    _params = load_params()
    cuts = (_params.get("decision_rules") or {}).get("fixed_quantile_levels")
    xs_scoring = bool(_params.get("cross_sectional_scoring"))
    ind_neutral = bool(_params.get("tw_industry_neutral_valuation"))
    t0 = time.time()
    pl = plan(universes, symbols)
    need: dict[tuple[str, str], set] = {}
    for u, pairs in pl.items():
        for d, s in pairs:
            need.setdefault((UNIVERSES[u], s), set()).add(d)
    tasks = [(m, s, sorted(ds)) for (m, s), ds in need.items()]
    tasks.sort(key=lambda t: -len(t[2]))
    workers = workers or max(1, min(8, (os.cpu_count() or 2) - 1))
    rows, skips, per_sym = [], [], {}
    if workers == 1:
        res = map(_judge_entity, tasks)
    else:
        ex = ProcessPoolExecutor(max_workers=workers)
        res = ex.map(_judge_entity, tasks, chunksize=1)
    for t, (r, s, sec) in zip(tasks, res):
        for x in r:
            x["market"] = t[0]
        rows += r
        skips += [dict(x, market=t[0]) for x in s]
        per_sym[t[1]] = round(sec, 1)
    if workers != 1:
        ex.shutdown()
    allr = pd.DataFrame(rows)
    alls = pd.DataFrame(skips, columns=["symbol", "date", "reason", "market"])
    if "_env" in allr:   # M4:每筆唯一判斷的基礎變數 → raw_{tw,us}.sqlite(自訂模型快速重算),逐筆檔不帶這欄
        try:
            write_raw_env(allr, _params)
        except Exception as e:  # noqa: BLE001  快取失敗不影響逐筆檔;舊快取刪掉,免得和新的逐筆檔對不上
            print(f"[警告] 基礎變數快取寫入失敗,自訂模型重算要等下次 judge:{type(e).__name__}: {e}", file=sys.stderr)
            for m in allr["market"].unique():
                raw_env_path(m).unlink(missing_ok=True)
        allr = allr.drop(columns="_env")
    allr["date"] = pd.to_datetime(allr["date"])
    alls["date"] = pd.to_datetime(alls["date"])
    OUT.mkdir(parents=True, exist_ok=True)
    frames: dict = {}
    cov: dict = {"elapsed_sec": round(time.time() - t0), "workers": workers, "n_tasks": len(tasks),
                 "n_unique_judgments": int(len(allr)), "universes": {}}
    for u, pairs in pl.items():
        m = UNIVERSES[u]
        key = pd.DataFrame(pairs, columns=["date", "symbol"])
        r = key.merge(allr[allr["market"] == m], on=["date", "symbol"], how="inner").drop(columns="market")
        r.insert(2, "universe", u)
        if xs_scoring:   # P2-H05(參數開關;v1.1 參數沒有這個鍵):先改算長期分數,再給固定分位評等
            r = cross_sectional_rescore(r, _params["pillar_weights"]["long_term"])
        if ind_neutral and m == "TW":   # P2-H11(參數開關;只做台股)
            r = industry_neutral_valuation(r, _params["pillar_weights"]["long_term"],
                                           {s: _industry("TW", s)[0] for s in r["symbol"].unique()})
        if "_items" in r:
            r = r.drop(columns="_items")
        if cuts:
            r["lt_level"] = fixed_quantile_levels(r, cuts)   # P2-H10(參數開關;v1.1 參數沒有這個鍵)
        frames[u] = r
        s = key.merge(alls[alls["market"] == m], on=["date", "symbol"], how="inner")
        r_out = r.copy()
        r_out["date"] = r_out["date"].dt.strftime("%Y-%m-%d")
        r_out.to_csv(OUT / f"rows_{u}.csv", index=False)
        s.assign(date=s["date"].dt.strftime("%Y-%m-%d")).to_csv(OUT / f"skipped_{u}.csv", index=False)
        per_month = key.groupby(key["date"].dt.strftime("%Y-%m")).size().rename("planned").to_frame()
        per_month["judged"] = r.groupby(r["date"].dt.strftime("%Y-%m")).size()
        per_month = per_month.fillna(0).astype(int)
        cov["universes"][u] = {
            "market": m, "n_dates": int(key["date"].nunique()), "first": str(key["date"].min().date()),
            "last": str(key["date"].max().date()), "planned": int(len(key)), "judged": int(len(r)), "skipped": int(len(s)),
            "skip_reasons": {k: int(v) for k, v in s["reason"].str.replace(r"^例外 .*", "例外", regex=True)
                             .str.replace("^(時效[^(\uff08]*)[(\uff08].*", r"\1", regex=True).value_counts().items()},
            "skip_by_symbol": {k: int(v) for k, v in s["symbol"].value_counts().head(25).items()},
            "per_month": {k: [int(a), int(b)] for k, a, b in zip(per_month.index, per_month["planned"], per_month["judged"])},
            "judged_per_month_min_max": [int(per_month["judged"].min()), int(per_month["judged"].max())],
        }
    # Verifier22 問題 3:rates_asof 打開但總經 store 沒有 RATES(例如用 v2.0)時,只有「永續成長 ≤ 參數 rf」生效,要警告
    if (load_params().get("valuation") or {}).get("rates_asof"):
        mf = PITStore.load(paths()["macro"]).facts
        if not (mf["entity"] == "RATES").any():
            w = "參數 valuation.rates_asof 打開,但總經 store 沒有 RATES 資料(資料版本早於 v2.1?):rf / ERP 退回參數值,只有永續成長上限生效"
            cov["warnings"] = cov.get("warnings", []) + [w]
            print("警告:" + w, file=sys.stderr)
    if cuts:
        write_cutpoints(frames)   # 網站單檔評等用的切點(OUT_DIR/lt_cutpoints.json)
    cov["slowest_symbols_sec"] = dict(sorted(per_sym.items(), key=lambda kv: -kv[1])[:5])
    (OUT / "coverage.json").write_text(json.dumps(cov, ensure_ascii=False, indent=1), encoding="utf-8")
    return cov


# ---------------------------------------------------------------- 3. 評估(只算開發集)
def total_return_index(store: PITStore, market: str, symbols, cal: pd.DatetimeIndex) -> dict:
    """{symbol: 含息報酬指數},用 store 在最後一天的 'total' 調整(現金股利再投入、公司行動換算)。
    下市股:最後一筆以後用最後價格持平延伸到日曆結束(= 下市報酬 0%,A3 查到的下市都是併購 / 股份轉換;
    沒有延伸的話,持有期間跨過下市的判斷會因為沒有出場價而被排除,等於另一種存活者偏誤)。"""
    v = store.as_of(FAR)
    out = {}
    for s in symbols:
        ent = entity_of(s, market)
        p = v.prices(ent, adjust="total")
        if p.empty:
            continue
        tr = p["close"].dropna()
        tr = tr[tr > 0]
        if market == "TW" and ent in delisted_codes() and len(tr) and len(cal) and tr.index[-1] < cal[-1] - pd.Timedelta(days=30):
            ext = cal[cal > tr.index[-1]]
            tr = pd.concat([tr, pd.Series(float(tr.iloc[-1]), index=ext)])
        out[s] = tr
    return out


def _load_rows(u: str) -> pd.DataFrame:
    r = pd.read_csv(OUT / f"rows_{u}.csv")
    r["date"] = pd.to_datetime(r["date"])
    return r


def _brief(res: dict) -> dict:
    """主要指標與次要指標的精簡版(開發集)。"""
    out = {"n_rows": res["n_rows"], "first_date": res["first_date"], "last_date": res["last_date"], "primary": {}}
    for k, v in res["primary"].items():
        out["primary"][k] = {x: v.get(x) for x in ("ic", "ci95", "t_nw", "n_months", "first_month", "last_month")} | {
            "cohort_weighted_ic": (v.get("sensitivity_cohort_weighted") or {}).get("ic"),
            "tercile_spread_ann": (v.get("tercile_spread_monthly") or {}).get("annualized")}
    sec = res["secondary"]
    out["st_3m_ic"] = {x: sec["st_3m_ic"].get(x) for x in ("ic", "ci95", "n_months")}
    out["hit"] = {}
    for k, h in sec["hit"].items():
        if not h.get("n_rows_with_return"):
            continue
        g, f = h["general"], h["fixed_coverage_20"]
        out["hit"][k] = {"hit": g["hit"], "ci95": (g.get("ci95_block") or {}).get("ci"), "coverage": g["coverage"],
                         "right": g["right"], "wrong": g["wrong"], "ties": g["ties"], "fixed20_hit": f["hit"],
                         "fixed20_ci95": (f.get("ci95_block") or {}).get("ci"), "unrated": h["unrated"]["n"],
                         "independent_periods": h["independent_periods"]}
    rb = sec["rating_12m"]
    out["rating_12m"] = {"buckets": [{k: b[k] for k in ("rating", "n", "win", "mean_x")} for b in rb["buckets"]],
                         "inversions": rb["adjacent_inversions"], "monotonic_ic": rb["monotonic_ic"]}
    pf = sec["portfolio"]
    out["portfolio"] = {"months": pf["months"], "first": pf.get("first_month"), "last": pf.get("last_month"),
                        "cagr": {k: v.get("cagr") for k, v in pf["strategies"].items()},
                        "max_dd": {k: v.get("max_dd") for k, v in pf["strategies"].items()},
                        "turnover": {k: v.get("avg_turnover_oneway") for k, v in pf["strategies"].items() if k != "bench"},
                        "diffs": [{k: d[k] for k in ("a", "b", "ann_mean_diff", "ci95_nw", "verdict")} for d in pf["diffs"]]}
    return out


def vintage_label() -> str:
    d = config.DATA_DIR
    if d.parent.name == "frozen":
        return f"frozen:{d.name}"
    return f"live-cache:{d.name}@{config.today()}(未凍結)"


def pit_prices(market: str, symbols) -> dict:
    """評估用的價格輸入(含息報酬指數、含息大盤、交易日曆),和 evaluate_all 同一套;scripts/holdout.py --prices pit 也用這個。"""
    P = paths()
    macro = PITStore.load(P["macro"])
    store = PITStore.load(P[market])
    bench_tr = macro.prices.get(TR_BENCH[market])
    bench_tr = bench_tr["close"].dropna() if bench_tr is not None else None
    cal = pd.DatetimeIndex(bench_tr.index) if bench_tr is not None else pd.DatetimeIndex([])
    tr = total_return_index(store, market, sorted(set(symbols)), cal)
    return {"tr_index": tr, "bench_tr": bench_tr, "bench_kind": "total" if bench_tr is not None else None, "calendar": cal}


def evaluate_all(universes=tuple(UNIVERSES), subperiods: bool = True, family: str = "baseline") -> dict:
    """新模式評估,只算開發集(split='dev')。每次呼叫都會寫實驗登記簿(family=baseline)。"""
    from . import evaluate as EV
    P = paths()
    macro = PITStore.load(P["macro"])
    stores: dict = {}
    vintage = vintage_label()
    out: dict = {"eval_version": EV.EVAL_VERSION, "split": "dev", "data_vintage": vintage, "results": {},
                 "universe_coverage": {}, "warnings": []}
    cov_path = OUT / "coverage.json"
    planned = json.loads(cov_path.read_text(encoding="utf-8")).get("universes", {}) if cov_path.exists() else {}
    for u in universes:
        m = UNIVERSES[u]
        rows = _load_rows(u)
        if m not in stores:
            stores[m] = PITStore.load(P[m])
        bench_tr = macro.prices.get(TR_BENCH[m])
        bench_tr = bench_tr["close"].dropna() if bench_tr is not None else None
        cal = pd.DatetimeIndex(bench_tr.index) if bench_tr is not None else None
        tr = total_return_index(stores[m], m, sorted(rows["symbol"].unique()), cal if cal is not None else pd.DatetimeIndex([]))
        n_judged = len(rows)
        rows = rows[rows["symbol"].isin(tr)]
        # Red-team L2:評估前核對「應有股票池」(預先登記 §8):應判斷的列(含沒有財報、時效不足而跳過的)有多少真的進了評估
        exp = (planned.get(u) or {}).get("planned")
        c = {"planned": exp, "judged": n_judged, "evaluated": int(len(rows)),
             "share": round(len(rows) / exp, 4) if exp else None}
        out["universe_coverage"][u] = c
        if c["share"] is None:
            out["warnings"].append(f"{u}:找不到 coverage.json 的應判斷列數,無法核對股票池覆蓋率")
        elif c["share"] < UNIVERSE_COVERAGE_WARN:
            out["warnings"].append(f"{u}:只有 {c['share']:.0%} 的應判斷列進了評估(沒有財報、沒有價格或跳過),結果可能偏離登記的股票池")
        variants = {"all": rows}
        if subperiods:
            for name, (lo, hi) in SUBPERIODS.items():
                rr = rows
                if lo:
                    rr = rr[rr["date"] >= pd.Timestamp(lo)]
                if hi:
                    rr = rr[rr["date"] <= pd.Timestamp(hi)]
                variants[name] = rr
        for name, rr in variants.items():
            res = EV.evaluate(rr, tr, market=m, bench_tr=bench_tr, bench_kind="total" if bench_tr is not None else None,
                              calendar=cal, split="dev", family=family, universe=u, data_vintage=vintage,
                              cfg={"source": "backtest_pit", "universe": u, "period": name, "model": "v1.1 judge_at 不變"},
                              note=f"C2 v1.1 在 PIT 新資料上({u},{name})")
            out["results"][f"{u}|{name}"] = _brief(res)
            (OUT / f"eval_{u}_{name}.json").write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
    (OUT / "eval_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="v1.1 在 PIT 新資料上的回測(只算開發集)")
    ap.add_argument("step", choices=["build", "judge", "evaluate", "all", "sample", "cutpoints"])
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--tw", default="", help="sample:台股代號(逗號分隔)")
    ap.add_argument("--us", default="", help="sample:美股代號(逗號分隔)")
    ap.add_argument("--out", help="sample:輸出 sqlite 路徑;cutpoints:輸出 json 路徑(預設 OUT_DIR/lt_cutpoints.json)")
    ap.add_argument("--rows", help="cutpoints:已跑好的 rows_*.csv 所在資料夾(預設 OUT_DIR/bt_pit)")
    ap.add_argument("--universes", default="", help="judge:只算這些股票池(逗號分隔;預設全部)")
    ap.add_argument("--symbols", default="", help="build / judge:只用這些代號(逗號分隔,台股帶 .TW;預設全部)")
    a = ap.parse_args(argv)
    t0 = time.time()
    if a.step == "cutpoints":
        from pathlib import Path
        rows = Path(a.rows) if a.rows else OUT
        frames = {u: pd.read_csv(rows / f"rows_{u}.csv") for u in {*CUTPOINT_POOLS.values(), *CUTPOINT_FALLBACK.values()} if (rows / f"rows_{u}.csv").exists()}
        cp = write_cutpoints(frames, Path(a.out) if a.out else None, source=f"rows@{rows}")
        if cp is None:
            print("目前參數沒有 decision_rules.fixed_quantile_levels,不寫切點")
            return 1
        print(json.dumps({m: {k: v[k] for k in ("universe", "date", "n", "thresholds")} for m, v in cp["markets"].items()},
                         ensure_ascii=False))
        return 0
    if a.step == "sample":
        st = build_combined([c for c in a.tw.split(",") if c], [c for c in a.us.split(",") if c])
        st.save(a.out)
        print(f"已寫入 {a.out}({len(st.prices)} 檔價格、facts {len(st.facts)} 列)")
        return 0
    syms = [x for x in a.symbols.split(",") if x] or None
    if a.step in ("build", "all"):
        r = build() if syms is None else build(tw_codes=[x.split(".")[0] for x in syms if x.endswith((".TW", ".TWO"))],
                                                 us_syms=[x for x in syms if not x.endswith((".TW", ".TWO"))])
        print(json.dumps({"build_sec": r["elapsed_sec"], "tw_no_price": r.get("TW", {}).get("no_price")}, ensure_ascii=False))
    if a.step in ("judge", "all"):
        c = judge(a.workers, [x for x in a.universes.split(",") if x] or None, syms)
        print(json.dumps({"judge_sec": c["elapsed_sec"], **{u: {k: v[k] for k in ("planned", "judged", "skipped")}
                                                            for u, v in c["universes"].items()}}, ensure_ascii=False))
    if a.step in ("evaluate", "all"):
        evaluate_all()
        print(f"開發集評估已寫入 {OUT / 'eval_summary.json'}")
    print(f"總耗時 {round(time.time() - t0)} 秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
