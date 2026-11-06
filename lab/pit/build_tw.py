"""台股 point-in-time(PIT)資料管線:把凍結快取裡的 FinMind 財報 / 月營收 / 股利與 Yahoo 價格轉成 `PITStore`。

為什麼需要這個(Verifier 查到的高嚴重度問題):
- Yahoo 的台股 Close(auto_adjust=False)被往回調整了「之後才發生」的配股、分割、減資(Yahoo 把配股當成分割),
  但 FinMind 的 EPS、股數、本益比是當時的原始數字,兩者混用 → 本益比、市值、估值整段錯位(緯穎除權前 0.335 倍)。
- 這裡把 Yahoo 價格還原成**原始成交價**(原始價 = Close × 該日之後所有 Yahoo Stock Splits 比率的乘積),
  公司行動另外存成 events,由 `PITView.prices(adjust='split')` 只套用判斷日以前發生的。

資料約定(和 lab/pit/__init__.py 一致):
- entity = 台股代號(不含 .TW / .TWO),例 "2330"。
- facts:
  - 季資料(period = Q1..Q4,period_end = 季末):欄位名稱 = `FN.tw_quarterly` 的標準欄位。
    `eps_diluted` 與 `shares_out` 已換到「available_at 當時的股數基準」(見 `_normalize_share_basis`),
    所以使用者只要再乘除 `view.split_factor(entity, available_at)` 就能換到判斷日 d 的基準。
    另存 `eps_as_reported`(FinMind 原值)與 `shares_out_capital`(股本 ÷ 10,原值)供稽核,轉接函式不會放進季表。
  - 月營收:field = `month_revenue`,period = M01..M12,period_end = 月底(避免和季營收同一天撞欄)。
  - available_at:法定期限的**下一個交易日**(期限當天晚上才申報的話,隔天收盤才能用),見 `statutory_deadline`。
- prices:原始 open/high/low/close/volume,cash_div = 當天除息的原始現金股利(每股,當天股數基準);
  現金減資退還的股款也算成 cash_div(換到減資後每股),讓 `adjust='total'` 的含息報酬正確。
  Yahoo 在停牌期間(減資換股、面額變更)會塞入成交量 0 的假列,這些列會被剔除。
- events:kind = stock_div(盈餘 / 公積配股)、reduction(減資)、split(面額變更、股份轉換);ratio = 每 1 股變成幾股。

用法:
    python -m lab.pit.build_tw            # 建 40 檔 store → $INVEST_LAB_OUT_DIR/pit_tw.sqlite,另寫品質報告 md / json
轉接(給 backtest.judge_at 用,和 backtest.tw_data 同結構):
    fdat = tw_fdat_asof(store.as_of(d), "2330");  px = price_asof(store.as_of(d), "2330")
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config
from ..analysis import financials as FN
from ..sources import finmind, yahoo
from ..universe import TW_LARGE
from . import PITStore, PITView

SOURCE_FM = "finmind"
PAR = 10.0  # 台股標準面額(元)
BIG = 0.1  # 股本(股數)基準偵測只看 |log(比率)| ≥ 0.1 的事件;EPS 基準偵測見 SMALL / _eps_tol
SMALL = 0.005  # EPS 基準偵測考慮的最小事件(|log 比率| ≥ 0.5%);能不能分辨由 _eps_tol(EPS 四捨五入)決定

# FN.tw_quarterly 的欄位順序(轉接函式輸出的季表欄位)
Q_COLS = ["revenue", "cogs", "gross_profit", "operating_expenses", "operating_income", "pretax_income", "tax",
          "net_income", "eps_diluted", "total_assets", "current_assets", "cash", "receivables", "inventory", "ppe",
          "current_liabilities", "total_liabilities", "payables", "total_debt", "equity", "retained_earnings",
          "shares_out", "cfo", "capex", "da", "interest_expense", "dividends_paid", "fcf", "ebit", "ebitda"]
AUDIT_FIELDS = ["eps_as_reported", "shares_out_capital", "eps_alt_basis"]
MR_COLS = ["date", "stock_id", "country", "revenue", "revenue_month", "revenue_year", "create_time"]

# 股票清單抓不到時的金融業備援清單(TW_LARGE 裡的金控)
FIN_FALLBACK = {"2881", "2882", "2891", "2886", "2884", "2885", "5880"}

# 資料本身無法判斷、需要人工註記的公司行動(來源:Yahoo 價格跳空 + FinMind 股本;說明寫在品質報告)
KNOWN_ACTIONS = {
    ("3711", "2018-04-30"): {"kind": "split", "refund": 0.0, "par_change": False,
                             "note": "股份轉換:日月光(2311)1 股換日月光投控(3711)0.5 股;2018-04-30 以前的價格是 2311 的"},
}


# ---------------------------------------------------------------- 交易日曆

def trading_calendar(price_source: str = "10y") -> pd.DatetimeIndex:
    """交易日曆 = 快取中 ^TWII 的價格日期(預設 Yahoo 10 年,約 2016-10 起;price_source='since' 用 2013 起的長歷史)。
    更早或更晚的日期由呼叫端退回平日曆。"""
    h = yahoo.history_since("^TWII") if price_source == "since" else yahoo.index_history("^TWII")
    if h is None or h.empty:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(sorted(set(pd.to_datetime(h.index).normalize())))


def next_trading_day(cal: pd.DatetimeIndex, date, strictly_after: bool = True) -> pd.Timestamp:
    """date 之後(strictly_after=False 時含當天)的第一個交易日。日曆範圍外退回平日(週一到週五,不含國定假日)。"""
    date = pd.Timestamp(date).normalize()
    if len(cal) and cal[0] <= date < cal[-1]:
        i = cal.searchsorted(date, side="right" if strictly_after else "left")
        return cal[i]
    d = date + pd.offsets.BDay(1) if strictly_after else date
    return d if d.dayofweek < 5 else d + pd.offsets.BDay(1)


# ---------------------------------------------------------------- 法定期限

_FIN_CACHE: dict[str, bool] = {}


def is_financial(code: str) -> bool:
    """金融業(銀行、金控、保險、證券):FinMind 股票清單的產業別含「金融」「保險」「銀行」「證券」;抓不到就用備援清單。"""
    if code in _FIN_CACHE:
        return _FIN_CACHE[code]
    out = code in FIN_FALLBACK
    try:
        si = finmind.stock_info()
        if si is not None and not si.empty:
            cats = set(si.loc[si["stock_id"] == code, "industry_category"].astype(str))
            if cats:
                out = any(k in c for c in cats for k in ("金融", "保險", "銀行", "證券"))
    except Exception:  # noqa: BLE001
        pass
    _FIN_CACHE[code] = out
    return out


def is_foreign(code: str, name: str = "") -> bool:
    """外國發行人(第一上市櫃 KY 公司):名稱帶 KY。"""
    if "KY" in name.upper():
        return True
    try:
        si = finmind.stock_info()
        if si is not None and not si.empty:
            return bool(si.loc[si["stock_id"] == code, "stock_name"].astype(str).str.upper().str.contains("KY").any())
    except Exception:  # noqa: BLE001
        pass
    return False


def statutory_deadline(period_end, financial: bool, foreign: bool = False) -> pd.Timestamp:
    """證券交易法第 36 條的申報期限(假設與依據見 docs/資料品質_TW.md):
    - 一般公司:Q1 → 5/15、Q2 → 8/14、Q3 → 11/14(季終了後 45 日)、年報(Q4)→ 次年 3/31。
    - 金融業(金控、銀行、保險、證券):半年報 → 8/31(季終了後 2 個月);Q1 / Q3 保守取 5/31、11/30
      (法定應為 45 日,但金融業實務上常見較晚公告的情形,取月底當保守上界;屬推測,資料檢查見品質報告)。
    - 外國發行人(KY):半年報 → 8/31(推測;TWSE 本益比在 8/14 之後才更新,見品質報告),其餘同一般公司。
    """
    pe = pd.Timestamp(period_end)
    y, m = pe.year, pe.month
    if m == 12:
        return pd.Timestamp(y + 1, 3, 31)
    if foreign and m == 6:
        return pd.Timestamp(y, 8, 31)
    if financial:
        return pd.Timestamp(y, {3: 5, 6: 8, 9: 11}[m], {3: 31, 6: 31, 9: 30}[m])
    return pd.Timestamp(y, {3: 5, 6: 8, 9: 11}[m], {3: 15, 6: 14, 9: 14}[m])


def deadline_available_at(deadline, cal: pd.DatetimeIndex) -> pd.Timestamp:
    """法定期限 → 可用日:期限落在非交易日時先順延到下一個交易日(實務上可在順延後的那天申報),再加 1 個交易日。"""
    d0 = next_trading_day(cal, deadline, strictly_after=False)
    return next_trading_day(cal, d0, strictly_after=True)


def quarter_available_at(period_end, financial: bool, cal: pd.DatetimeIndex, foreign: bool = False) -> pd.Timestamp:
    return deadline_available_at(statutory_deadline(period_end, financial, foreign), cal)


def month_revenue_available_at(year: int, month: int, cal: pd.DatetimeIndex) -> pd.Timestamp:
    """月營收:次月 10 日前申報 → 次月 10 日(非交易日先順延)的下一個交易日。"""
    nxt = pd.Timestamp(year, month, 1) + pd.offsets.MonthBegin(1)
    return deadline_available_at(nxt + pd.Timedelta(days=9), cal)


def _period_label(pe: pd.Timestamp) -> str:
    return f"Q{(pe.month - 1) // 3 + 1}"


# ---------------------------------------------------------------- 原始資料(全部離線讀凍結快取)

PRICE_SOURCES = ("10y", "since")
TWSE_START = "2013-01"


def twse_daily_hist(code: str, start: str = TWSE_START, end=None) -> pd.DataFrame | None:
    """證交所 STOCK_DAY 個股月資料(原始成交價;下市股也有)→ 和 yfinance 同欄名的日表
    (Dividends / Stock Splits 全 0:配股與現金股利由 build_actions 從 FinMind 股利表套用)。
    只讀快取;某個月沒有快取(Fetcher 還沒抓到)就跳過那個月。全部沒有回傳 None。"""
    from ..sources import twse_hist

    parts = []
    for p in pd.period_range(pd.Period(start, "M"), pd.Period(pd.Timestamp(end or config.today()), "M"), freq="M"):
        try:
            df = twse_hist.stock_month(code, p.year, p.month)
        except Exception:  # noqa: BLE001  離線沒有快取(CacheMiss)/ 來源失敗:跳過那個月(降級)
            continue
        if df is not None and len(df):
            parts.append(df)
    if not parts:
        return None
    x = pd.concat(parts).sort_index()
    x = x[~x.index.duplicated(keep="last")]
    out = pd.DataFrame({"Open": x["open"], "High": x["high"], "Low": x["low"], "Close": x["close"],
                        "Adj Close": x["close"], "Volume": x["volume"].fillna(0.0), "Dividends": 0.0, "Stock Splits": 0.0},
                       index=pd.DatetimeIndex(x.index))
    # 沒有成交的日子(收盤 "--")成交量是 0 → 由 build_actions 當成停牌列剔除
    out.loc[out["Close"].isna(), "Volume"] = 0.0
    return out


def load_raw(code: str, sym: str, price_source: str = "10y") -> dict:
    """price_source:'10y'(預設,Yahoo 10 年)或 'since'(Yahoo 2013 起的長歷史;沒有 Yahoo 的上市股改讀證交所日價格)。"""
    if price_source not in PRICE_SOURCES:
        raise ValueError(f"price_source 只能是 {PRICE_SOURCES}")
    st = yahoo.Status()
    hist, px_src = (yahoo.history(sym, st), "yahoo_10y") if price_source == "10y" else (yahoo.history_since(sym), "yahoo_since")
    if price_source == "since" and (hist is None or hist.empty) and not sym.endswith(".TWO"):
        hist, px_src = twse_daily_hist(code), "twse_stock_day"
    return {
        "hist": hist, "price_source": px_src,
        "dividend": finmind.get("TaiwanStockDividend", code, "2010-01-01", "statements_slow"),
        "income": finmind.get("TaiwanStockFinancialStatements", code, "2013-01-01", "statements_slow"),
        "balance": finmind.get("TaiwanStockBalanceSheet", code, "2013-01-01", "statements_slow"),
        "cashflow": finmind.get("TaiwanStockCashFlowsStatement", code, "2013-01-01", "statements_slow"),
        "month_revenue": finmind.get("TaiwanStockMonthRevenue", code, "2013-01-01", "statements"),
        "per": finmind.get("TaiwanStockPER", code, finmind._ago(365 * 10 + 40), "statements"),
    }


def per_series(per_df: pd.DataFrame | None) -> pd.Series:
    """FinMind TaiwanStockPER → 每日本益比(0 = 虧損 → NaN)。"""
    if per_df is None or per_df.empty:
        return pd.Series(dtype=float)
    return per_df.set_index(pd.to_datetime(per_df["date"]))["PER"].astype(float).replace(0, np.nan).sort_index()


# ---------------------------------------------------------------- 公司行動與原始價格

def _yahoo_factor(h: pd.DataFrame) -> pd.Series:
    """每一天之後(不含當天)所有 Yahoo Stock Splits 比率的乘積:原始價 = Close × 這個值。"""
    sp = h["Stock Splits"].astype(float).where(lambda s: s > 0, 1.0).fillna(1.0)
    after = sp[::-1].cumprod()[::-1]  # 含當天
    return (after / sp).astype(float)


def _first_on_or_after(idx: pd.DatetimeIndex, date) -> pd.Timestamp | None:
    i = idx.searchsorted(pd.Timestamp(date), side="left")
    return idx[i] if i < len(idx) else None


@dataclass
class EntityAudit:
    code: str
    actions: list[dict] = field(default_factory=list)
    cash: list[dict] = field(default_factory=list)
    basis: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    par_events: list = field(default_factory=list)  # 面額變更(股本 ÷ 10 不再等於股數)


def build_actions(code: str, h: pd.DataFrame, dv: pd.DataFrame | None, cal: pd.DatetimeIndex, audit: EntityAudit,
                  per_raw: pd.Series | None = None):
    """回傳 (events DataFrame, 原始價格表)。

    events 來源:Yahoo Stock Splits(價格有跳空的那天)與 FinMind TaiwanStockDividend 的盈餘 / 公積配股(1 + 配股元數 / 面額)。
    - 兩者都有(日期 ±5 天、比率差 < 1%):日期用 Yahoo(原始價格的換算邊界),比率用 FinMind(官方配股數)。
    - 只有 FinMind:價格資料期間以前的配股(只影響每股數字換算),或 Yahoo 漏記(會在品質報告標出)。
    - 只有 Yahoo:比率 < 1 → 減資;> 1 → 分割(面額變更);特殊個案見 KNOWN_ACTIONS。
    """
    h = h.copy()
    h.index = pd.to_datetime(h.index).normalize()
    h = h[~h.index.duplicated(keep="last")].sort_index()
    fac = _yahoo_factor(h)
    raw = pd.DataFrame({
        "open": h["Open"] * fac, "high": h["High"] * fac, "low": h["Low"] * fac, "close": h["Close"] * fac,
        "volume": h["Volume"] / fac, "cash_div": (h["Dividends"].fillna(0.0) * fac),
    }, index=h.index)
    # 成交量 0 的列:TWSE 當天沒有本益比 → 停牌期間 Yahoo 塞的假列,剔除;
    # TWSE 當天有本益比 → 真的交易日(例:2021-08-17 Yahoo 全部台股成交量 0),保留整列、成交量設缺值;
    # 價格再用「TWSE 本益比 × 前後日隱含 EPS」檢查,差 > 0.5% 表示 Yahoo 沿用前一天價格 → OHLC 也設缺值。
    zero = raw["volume"].fillna(0) <= 0
    per_dates = set(per_raw.index) if per_raw is not None else set()
    traded = zero & raw.index.isin(list(per_dates))
    halted = zero & ~traded
    stale = pd.Series(False, index=raw.index)
    if traded.any():
        perv = per_raw.reindex(raw.index).astype(float).replace(0, np.nan)
        imp = (raw["close"] / perv).where(~zero)
        for d in raw.index[traded]:
            i = raw.index.get_loc(d)
            nb = imp.iloc[max(0, i - 3): i + 4].dropna()
            exp_close = perv.loc[d] * float(nb.median()) if len(nb) else np.nan
            ok = pd.notna(exp_close) and abs(raw.at[d, "close"] / exp_close - 1) <= 0.005
            stale[d] = not ok
        raw.loc[traded, "volume"] = np.nan
        raw.loc[stale, ["open", "high", "low", "close"]] = np.nan
        audit.notes.append(f"成交量 0 但 TWSE 有交易的 {int(traded.sum())} 列:保留、成交量設缺值;其中 {int(stale.sum())} 列"
                           "價格和 TWSE 本益比隱含價不符(Yahoo 沿用前一天價格)→ 價格也設缺值:"
                           + ", ".join(d.date().isoformat() for d in raw.index[traded]))
    audit.coverage["halt_rows_dropped"] = int(halted.sum())
    audit.coverage["zero_volume_kept"] = int(traded.sum())
    audit.coverage["stale_price_rows"] = int(stale.sum())
    if halted.any():
        audit.notes.append("剔除成交量 0、TWSE 當天也沒有本益比的停牌假列 " + str(int(halted.sum())) + " 列:"
                           + ", ".join(sorted({d.strftime("%Y-%m") for d in raw.index[halted]})))
    yahoo_div_raw = raw["cash_div"].copy()
    raw = raw[~halted].copy()
    raw["cash_div"] = 0.0
    px_idx = raw.index
    full_idx = h.index
    first_px = px_idx[0] if len(px_idx) else None

    ysplits = [(d, float(r)) for d, r in h["Stock Splits"].items() if r and r > 0 and abs(r - 1) > 1e-9]
    used_y = set()
    rows = []
    # ---- FinMind 配股 ----
    fm_stock, fm_cash = [], []
    if dv is not None and not dv.empty:
        for _, r in dv.iterrows():
            s_amt = float(r.get("StockEarningsDistribution") or 0) + float(r.get("StockStatutorySurplus") or 0)
            c_amt = float(r.get("CashEarningsDistribution") or 0) + float(r.get("CashStatutorySurplus") or 0)
            sd, cd = str(r.get("StockExDividendTradingDate") or "").strip(), str(r.get("CashExDividendTradingDate") or "").strip()
            if s_amt > 0 and sd:
                fm_stock.append((pd.Timestamp(sd), s_amt))
            if c_amt > 0 and cd:
                fm_cash.append((pd.Timestamp(cd), c_amt))
    for ex, amt in sorted(fm_stock):
        ratio = 1.0 + amt / PAR  # 限制:面額變更後的配股(面額不是 10 元)會算錯比率 → 對不上 Yahoo 時會被標出
        mapped = _first_on_or_after(full_idx, ex) if first_px is not None and ex >= full_idx[0] - pd.Timedelta(days=5) else None
        match = None
        if mapped is not None:
            for j, (yd, yr) in enumerate(ysplits):
                if j not in used_y and abs((yd - mapped).days) <= 5 and abs(math.log(yr / ratio)) < 0.01:
                    match = j
                    break
        if match is not None:
            used_y.add(match)
            yd, yr = ysplits[match]
            rows.append({"date": yd, "kind": "stock_div", "ratio": ratio})
            if yd == ex:
                msg = "一致"
            elif yd == mapped:
                msg = f"一致(FinMind 除權日 {ex.date()} 休市,順延到下一個交易日)"
            else:
                msg = f"日期不同(FinMind {ex.date()},Yahoo {yd.date()})"
            if abs(yr / ratio - 1) > 1e-4:
                msg += f";比率差 {yr / ratio - 1:+.4%}(用 FinMind)"
            audit.actions.append({"date": yd.date().isoformat(), "kind": "stock_div", "ratio": round(ratio, 6),
                                  "source": "Yahoo + FinMind", "yahoo_ratio": round(yr, 6), "fm_ex_date": ex.date().isoformat(),
                                  "consistent": msg})
        else:
            in_px = first_px is not None and ex >= first_px
            date = next_trading_day(cal, ex, strictly_after=False)
            rows.append({"date": date, "kind": "stock_div", "ratio": ratio})
            audit.actions.append({"date": date.date().isoformat(), "kind": "stock_div", "ratio": round(ratio, 6),
                                  "source": "FinMind", "yahoo_ratio": None, "fm_ex_date": ex.date().isoformat(),
                                  "consistent": "Yahoo 未記錄(價格期間內,需查)" if in_px else "早於價格資料(只影響每股數字換算)"})
            if in_px:
                audit.notes.append(f"FinMind 配股 {ex.date()} 比率 {ratio:.4f} 在 Yahoo 沒有對應的 Stock Splits")
    # ---- Yahoo 獨有(減資、面額變更、股份轉換) ----
    refunds = []
    par_events: list[tuple[pd.Timestamp, float]] = []
    for j, (yd, yr) in enumerate(ysplits):
        if j in used_y:
            continue
        known = KNOWN_ACTIONS.get((code, yd.date().isoformat()))
        par_change = False
        if known:
            kind, refund, note = known["kind"], known["refund"], known["note"]
            par_change = known.get("par_change", False)
        elif yr < 1:
            kind, refund = "reduction", PAR * (1 - yr)
            note = f"現金減資(假設退還被銷除股份的面額:每舊股 {PAR * (1 - yr):.3f} 元)"
        else:
            kind, refund, note = "split", 0.0, "面額變更(股本不變、股數 × 比率;FinMind 無對應配股)"
            par_change = True
        if par_change:
            par_events.append((yd, yr))
        rows.append({"date": yd, "kind": kind, "ratio": yr})
        if refund > 0:
            refunds.append((yd, yr, refund))
        audit.actions.append({"date": yd.date().isoformat(), "kind": kind, "ratio": round(yr, 6), "source": "Yahoo",
                              "yahoo_ratio": round(yr, 6), "fm_ex_date": None, "consistent": note})
    ev = pd.DataFrame(rows, columns=["date", "kind", "ratio"])
    ev["entity"] = code
    ev = ev[["entity", "date", "kind", "ratio"]].sort_values("date").reset_index(drop=True)

    # ---- 現金股利:FinMind 為主,Yahoo 交叉驗證 ----
    ydivs = {d: v for d, v in yahoo_div_raw.items() if v and v > 0}
    used_yd = set()
    for ex, amt in sorted(fm_cash):
        if first_px is None or ex < first_px or ex > px_idx[-1]:
            continue
        d = _first_on_or_after(px_idx, ex)
        near = [yd for yd in ydivs if yd not in used_yd and abs((yd - ex).days) <= 7]
        ymatch = min(near, key=lambda yd: abs(ydivs[yd] - amt)) if near else None
        if ymatch is not None:
            used_yd.add(ymatch)
        raw.loc[d, "cash_div"] += amt
        ok = ymatch is not None and abs(ydivs[ymatch] / amt - 1) < 0.02
        audit.cash.append({"date": d.date().isoformat(), "amount": round(amt, 4), "source": "FinMind",
                           "yahoo": round(ydivs[ymatch], 4) if ymatch is not None else None,
                           "consistent": ok})
    for yd, yr, refund in refunds:
        d = _first_on_or_after(px_idx, yd)
        if d is None:
            continue
        for k in [k for k in ydivs if k not in used_yd and abs((k - yd).days) <= 7]:
            used_yd.add(k)  # Yahoo 有時把減資退款記成股利(每舊股),改用下面換算後的金額
            audit.notes.append(f"{k.date()} Yahoo 股利 {ydivs[k]:.4f}(每舊股)視為減資退款,和假設的 {refund:.3f} 比較")
        per_new = refund / yr
        raw.loc[d, "cash_div"] += per_new
        audit.cash.append({"date": d.date().isoformat(), "amount": round(per_new, 4), "source": "減資退款(推算)",
                           "yahoo": None, "consistent": None})
    for yd, v in ydivs.items():
        if yd in used_yd or first_px is None or yd < first_px:
            continue
        d = _first_on_or_after(px_idx, yd)
        if d is None:
            continue
        raw.loc[d, "cash_div"] += v
        audit.cash.append({"date": d.date().isoformat(), "amount": round(v, 4), "source": "Yahoo(FinMind 無)",
                           "yahoo": round(v, 4), "consistent": False})
    audit.par_events = par_events
    return ev, raw


# ---------------------------------------------------------------- 每股數字的股數基準

def _cum_index(ev: pd.DataFrame):
    """N(t) = date ≤ t 的所有改變股數的事件比率乘積(累積股數指數;spinoff 不改股數,排除)。"""
    if len(ev) and "kind" in ev:
        ev = ev[ev["kind"] != "spinoff"]
    dates = pd.DatetimeIndex(ev["date"]).values if len(ev) else np.array([], dtype="datetime64[ns]")
    cum = np.cumprod(ev["ratio"].astype(float).to_numpy()) if len(ev) else np.array([])

    def N(t) -> float:
        k = int(np.searchsorted(dates, np.datetime64(pd.Timestamp(t)), side="right"))
        return float(cum[k - 1]) if k else 1.0
    return N


def _eps_tol(E: float) -> float:
    """EPS 基準判斷的容忍度(log):EPS 四捨五入到 0.01 元造成的相對誤差 × 2.5,至少 2%。"""
    return max(0.02, 2.5 * 0.005 / abs(E))


def _normalize_share_basis(q: pd.DataFrame, ev: pd.DataFrame, avail: pd.Series, audit: EntityAudit,
                           financial: bool = False) -> pd.DataFrame:
    """把每季的 eps_diluted、shares_out 換到「available_at 當時」的股數基準(= facts 的 basis_at)。

    - 股數:股本 ÷ 10 是期末的登記股本,但(1)減資的基準日可能早於 Yahoo 的換股日、(2)面額變更不會改變股本、
      (3)配股的增資基準日常晚於除權日。做法:用累積股數指數 N(t) 把每季股數換成「基準股數」K = 股數 / N(b),
      b 從候選基準(期初前後的事件逐一算進去)中選和上一季 K 最接近的(只看 |log 比率| ≥ BIG 的事件);
      再用 K × N(available_at) 得到 available_at 基準的股數。
    - EPS:IAS 33 規定配股 / 分割若發生在期末後、財報通過前要追溯調整,但財報若在除權前就通過,EPS 是舊基準
      (研華 2020 Q2、台泥 2019 Q2、統一 2013–2015 Q2 等,配股只有 4–10%)。期末後到 available_at 之間的事件
      (|log 比率| ≥ SMALL)都要判斷:隱含加權股數 w = 歸屬母公司淨利 ÷ EPS,和各候選基準的股數比較,
      殘差先扣掉這家公司「沒有事件的季」的中位偏差(特別股股利、庫藏股等造成的固定差)。
      - 預設(事件都已反映)的殘差 ≤ 容忍度 → 照預設;
      - 否則某個候選基準 ≤ 容忍度且明顯較好 → 換到那個基準;
      - 都不吻合,且是金融業(FinMind 用不同基準的累計 EPS 相減推出單季)或現金減資(不追溯、加權股數混合新舊股數)
        → 用 淨利 ÷(available_at 基準股數 × 中位偏差)重算,並標記 mixed;
      - 其餘分不出來(一般公司的殘差也可能來自股本:增資基準日晚於期末)→ 照預設,標記 ambiguous。
      期中(期初到期末)發生的配股 / 分割依 IAS 33 視為已追溯,不當候選;金融業同一套規則(結果在品質報告逐筆列出)。
    另存 eps_alt_basis = 另一個候選基準下的 EPS(給驗證時檢查「是不是我們的基準錯了」)。
    """
    N = _cum_index(ev)
    ev = ev[ev["kind"] != "spinoff"] if len(ev) and "kind" in ev else ev
    out = q.copy()
    out["eps_as_reported"] = q["eps_diluted"]
    out["shares_out_capital"] = q.get("shares_out", pd.Series(np.nan, index=q.index))
    out["eps_alt_basis"] = np.nan
    par_mult = pd.Series([math.prod(r for d, r in audit.par_events if d <= pe) for pe in q.index], index=q.index, dtype=float)
    K_prev = None
    # ---- 第一輪:股數 ----
    for pe in q.index:
        a = avail[pe]
        w0 = pe - pd.Timedelta(days=92)
        n0 = N(w0)
        S = out.at[pe, "shares_out_capital"] * par_mult[pe]  # 面額變更後,股本 ÷ 10 要再乘上分割比率才是股數
        win = ev[(ev["date"] > w0) & (ev["date"] <= a)]
        rs = win["ratio"].astype(float).tolist()
        big = [abs(math.log(r)) >= BIG for r in rs]
        k_def = int((win["date"] <= pe).sum())
        K = None
        if pd.notna(S) and S > 0:
            cands = [S / (n0 * math.prod(rs[:k])) for k in range(len(rs) + 1)]
            k = k_def
            if K_prev and any(big):
                errs = [abs(math.log(c / K_prev)) for c in cands]
                kb = int(np.argmin(errs))
                if errs[kb] < errs[k_def] - 0.02:
                    k = kb
            K = cands[k]
            if k != k_def:
                audit.basis.append({"period_end": pe.date().isoformat(), "item": "shares_out",
                                    "note": f"股本反映的事件數 {k}(預設 {k_def}),和上一季基準股數最接近"})
            K_prev = K
        out.at[pe, "shares_out"] = K * N(a) if K else np.nan
    # ---- 第二輪:EPS ----
    E_all, NI_all, S_all = q["eps_diluted"], q["net_income"], out["shares_out"]
    lw = {}
    for pe in q.index:
        E, NI, S = E_all[pe], NI_all[pe], S_all[pe]
        if pd.notna(E) and pd.notna(NI) and pd.notna(S) and S > 0 and abs(E) >= 0.05 and NI * E > 0:
            lw[pe] = math.log(float(NI) / float(E) / float(S))  # 隱含加權股數 ÷ available_at 基準股數(預設基準)
    lw = pd.Series(lw, dtype=float)

    def window(pe, back):
        return ev[(ev["date"] > pe - pd.Timedelta(days=back)) & (ev["date"] <= avail[pe])]
    clean = [pe for pe in lw.index if window(pe, 92).empty]
    for pe in q.index:
        E = E_all[pe]
        if pd.isna(E):
            continue
        a = avail[pe]
        after = ev[(ev["date"] > pe) & (ev["date"] <= a)]  # 期末後、可用日前:基準不確定
        after = after[np.abs(np.log(after["ratio"].astype(float))) >= SMALL]
        mix_win = window(pe, 365 if pe.month == 12 else 92)  # Q4 = 全年 − 前三季,一整年的事件都可能混進來
        eps = float(E)
        if pe in lw.index and (len(after) or len(mix_win)):
            near = [c for c in clean if abs((c - pe).days) <= 365 * 3]
            bias = float(np.median(lw[near])) if len(near) >= 3 else 0.0
            tol = _eps_tol(float(E))
            rs = after["ratio"].astype(float).tolist()
            # 候選 k:期末後的事件只反映前 k 個(k = len(rs) 是預設)
            shift = [math.log(math.prod(rs[k:])) for k in range(len(rs) + 1)]  # 舊基準股數較少 → log 殘差加上這個
            res = [abs(lw[pe] + sh - bias) for sh in shift]
            k_def = len(rs)
            kb = int(np.argmin(res))
            if len(rs):
                out.at[pe, "eps_alt_basis"] = float(E) * math.exp(-shift[0])  # 另一個候選:期末後的事件都沒反映
            if res[k_def] <= tol:
                pass
            elif len(rs) and res[kb] <= tol and res[kb] < res[k_def] - tol / 2:
                eps = float(E) * math.exp(-shift[kb])  # 舊基準 EPS ÷ 之後的配股倍數 = available_at 基準
                out.at[pe, "eps_alt_basis"] = float(E)
                audit.basis.append({"period_end": pe.date().isoformat(), "item": "eps", "kind": "old_basis",
                                    "small": all(abs(math.log(r)) < BIG for r in rs),
                                    "note": f"EPS 是財報在除權前通過的舊基準(反映 {kb}/{k_def} 個期末後事件;殘差 "
                                            f"{res[kb]:.3f} vs 預設 {res[k_def]:.3f}),換到 available_at 基準:{float(E):.4f} → {eps:.4f}"})
            elif min(res) > max(tol, 0.03) and (financial or "reduction" in mix_win["kind"].tolist()):
                # 只有兩種情況有把握是 EPS 本身混了基準:現金減資(不追溯),金融業的推導單季 EPS(累計相減)。
                # 一般公司的小配股殘差也可能來自股本(增資基準日晚於期末),不改 EPS,標 ambiguous。
                S = float(S_all[pe])
                eps = float(NI_all[pe]) / (S * math.exp(bias))
                out.at[pe, "eps_alt_basis"] = float(E)
                why = ("現金減資不追溯,加權股數混合新舊股數" if "reduction" in mix_win["kind"].tolist()
                       else "單季 EPS 可能是不同基準的累計 EPS 相減推出")
                audit.basis.append({"period_end": pe.date().isoformat(), "item": "eps", "kind": "mixed",
                                    "small": all(abs(math.log(r)) < BIG for r in mix_win["ratio"].astype(float)),
                                    "note": f"{why}(最小殘差 {min(res):.3f} > 容忍度 {tol:.3f}),改用 淨利 ÷ 現行股數"
                                            f"(含公司固定偏差 {bias:+.3f}):{float(E):.4f} → {eps:.4f}"})
            elif len(rs) or min(res) > max(tol, 0.03):
                audit.basis.append({"period_end": pe.date().isoformat(), "item": "eps", "kind": "ambiguous",
                                    "small": all(abs(math.log(r)) < BIG for r in mix_win["ratio"].astype(float)),
                                    "note": f"無法分辨新舊基準(殘差 預設 {res[k_def]:.3f} / 最佳 {res[kb]:.3f},容忍度 {tol:.3f}),照預設(已追溯)"})
        out.at[pe, "eps_diluted"] = eps
    return out


# ---------------------------------------------------------------- facts

def quarter_facts(code: str, qn: pd.DataFrame, avail: pd.Series) -> pd.DataFrame:
    cols = [c for c in Q_COLS + AUDIT_FIELDS if c in qn.columns]
    long = qn[cols].rename_axis("period_end").reset_index().melt(id_vars="period_end", var_name="field", value_name="value")
    long = long[long["value"].notna() & np.isfinite(long["value"].astype(float))]
    long["entity"] = code
    long["period"] = long["period_end"].map(_period_label)
    long["available_at"] = long["period_end"].map(avail)
    long["source"] = SOURCE_FM
    long["filed"] = pd.NaT
    long["basis_at"] = long["available_at"]  # 規約:台股每股數字已正規化到 available_at 的股數基準
    long["value"] = long["value"].astype(float)
    return long


def month_facts(code: str, mr: pd.DataFrame | None, cal: pd.DatetimeIndex) -> pd.DataFrame:
    if mr is None or mr.empty:
        return pd.DataFrame()
    m = mr.drop_duplicates(["revenue_year", "revenue_month"], keep="last")
    ym = pd.to_datetime(dict(year=m["revenue_year"], month=m["revenue_month"], day=1))
    return pd.DataFrame({
        "entity": code, "field": "month_revenue", "period_end": (ym + pd.offsets.MonthEnd(0)).to_numpy(),
        "period": [f"M{int(x):02d}" for x in m["revenue_month"]], "value": m["revenue"].astype(float).to_numpy(),
        "available_at": [month_revenue_available_at(int(y), int(mo), cal) for y, mo in zip(m["revenue_year"], m["revenue_month"])],
        "source": SOURCE_FM, "filed": pd.NaT,
    }).assign(basis_at=lambda x: x["available_at"])


# ---------------------------------------------------------------- 組裝

def _drop_ytd_quarters(q: pd.DataFrame, audit: EntityAudit) -> pd.DataFrame:
    """前一季缺資料時(例:緯穎 2018 Q1 不在 FinMind),FinMind 的「單季」損益常其實是年初至今累計(緯穎 2018-06-30 的 EPS 11.07
    = 上半年)。無法拆單季 → 這一季的損益表流量欄位設成 NaN(存量欄位保留)。"""
    q = q.copy()
    flows = [c for c in q.columns if c in FN.FLOW_ITEMS or c == "operating_expenses"]
    for pe in q.index:
        if pe.month == 3:
            continue
        prev = pe - pd.offsets.QuarterEnd(1)
        if prev not in q.index:
            q.loc[pe, flows] = np.nan
            audit.basis.append({"period_end": pe.date().isoformat(), "item": "flows",
                                "note": "前一季缺資料,單季損益可能是年初至今累計 → 流量欄位設為缺值"})
    return q


def build_entity(code: str, sym: str, cal: pd.DatetimeIndex, name: str = "", price_source: str = "10y"):
    audit = EntityAudit(code)
    raw = load_raw(code, sym, price_source)
    if price_source != "10y":
        audit.coverage["price_source"] = raw.get("price_source")
    fin = is_financial(code)
    foreign = is_foreign(code, name)
    audit.coverage["financial"] = fin
    audit.coverage["foreign"] = foreign
    h = raw["hist"]
    if h is None or h.empty:
        audit.notes.append("沒有 Yahoo 價格")
        ev = pd.DataFrame(columns=["entity", "date", "kind", "ratio"])
        px = None
    else:
        per_raw = None
        if raw["per"] is not None and not raw["per"].empty:
            per_raw = raw["per"].set_index(pd.to_datetime(raw["per"]["date"]))["PER"].astype(float)
            per_raw = per_raw[~per_raw.index.duplicated(keep="last")]
        ev, px = build_actions(code, h, raw["dividend"], cal, audit, per_raw)
        audit.coverage.update({"price_first": px.index[0].date().isoformat(), "price_last": px.index[-1].date().isoformat(),
                               "price_rows": int(len(px))})
    q = FN.tw_quarterly({k: raw[k] for k in ("income", "balance", "cashflow")})
    facts = []
    if not q.empty:
        q = _drop_ytd_quarters(q, audit)
        avail = pd.Series({pe: quarter_available_at(pe, fin, cal, foreign) for pe in q.index})
        qn = _normalize_share_basis(q, ev, avail, audit, fin)
        facts.append(quarter_facts(code, qn, avail))
        audit.coverage.update({"q_first": q.index[0].date().isoformat(), "q_last": q.index[-1].date().isoformat(), "q_rows": int(len(q))})
    mf = month_facts(code, raw["month_revenue"], cal)
    if len(mf):
        facts.append(mf)
        audit.coverage.update({"mr_first": pd.Timestamp(mf["period_end"].min()).strftime("%Y-%m"),
                               "mr_last": pd.Timestamp(mf["period_end"].max()).strftime("%Y-%m"), "mr_rows": int(len(mf))})
    per = per_series(raw["per"])
    if len(per):
        audit.coverage.update({"per_first": per.index[0].date().isoformat(), "per_last": per.index[-1].date().isoformat()})
    f = pd.concat(facts, ignore_index=True) if facts else pd.DataFrame()
    return f, px, ev, audit, per


def build_store(universe=TW_LARGE, vintage: str | None = None, price_source: str = "10y"):
    """回傳 (store, audits: dict[code, EntityAudit], per: dict[code, Series])。只讀快取(離線模式下不會連網)。
    price_source 見 load_raw(預設 '10y' 和既有行為相同)。"""
    cal = trading_calendar(price_source)
    facts, prices, events, audits, pers = [], {}, [], {}, {}
    for sym, name in universe:
        code = sym.split(".")[0]
        f, px, ev, audit, per = build_entity(code, sym, cal, name, price_source)
        if len(f):
            facts.append(f)
        if px is not None:
            prices[code] = px
        if len(ev):
            events.append(ev)
        audits[code], pers[code] = audit, per
    from . import FACT_COLS, _empty_events, _empty_facts
    fa = pd.concat(facts, ignore_index=True)[FACT_COLS] if facts else _empty_facts()
    for c in ("period_end", "available_at", "filed", "basis_at"):
        fa[c] = pd.to_datetime(fa[c])
    fa["value"] = fa["value"].astype(float)
    evs = pd.concat(events, ignore_index=True) if events else _empty_events()
    evs["date"] = pd.to_datetime(evs["date"])
    evs["ratio"] = evs["ratio"].astype(float)
    if vintage is None:
        vintage = f"tw-pit@{config.DATA_DIR.name}"
    return PITStore(fa, prices, evs, vintage), audits, pers


def default_path():
    return config.OUT_DIR / "pit_tw.sqlite"


# ---------------------------------------------------------------- 轉接函式(只透過 view 取資料)

def _q_long_asof(view: PITView, code: str) -> pd.DataFrame:
    return view.facts_long(code, Q_COLS, period="Q")


def quarterly_asof(view: PITView, code: str) -> pd.DataFrame:
    """d 時已公布的季表(欄位同 FN.tw_quarterly,另加 _avail);每股數字與股數換到 d 的股數基準。"""
    x = _q_long_asof(view, code)
    if x.empty:
        return pd.DataFrame(columns=Q_COLS + ["_avail"])
    x = x.copy()
    # 規約(docs/架構.md):after = 該數字的 basis_at(台股 = available_at,建置時已正規化);不可用 filed
    after = pd.to_datetime(x["basis_at"])
    sf = {a: view.split_factor(code, a) for a in after.unique()}
    f = after.map(sf).astype(float)
    x.loc[x["field"] == "eps_diluted", "value"] = x["value"] / f
    x.loc[x["field"] == "shares_out", "value"] = x["value"] * f
    w = x.pivot_table(index="period_end", columns="field", values="value", aggfunc="last").reindex(columns=Q_COLS)
    w["_avail"] = x.groupby("period_end")["available_at"].max()
    w.index = pd.DatetimeIndex(w.index).rename(None)
    w.columns.name = None
    return w.sort_index()


def month_revenue_asof(view: PITView, code: str) -> pd.DataFrame | None:
    x = view.facts_long(code, ["month_revenue"], period="M")
    if x.empty:
        return None
    ym = pd.DatetimeIndex(x["period_end"]).to_period("M").to_timestamp()
    mr = pd.DataFrame({
        "date": (ym + pd.offsets.MonthBegin(1)).strftime("%Y-%m-%d"), "stock_id": code, "country": "Taiwan",
        "revenue": x["value"].round().astype("int64").to_numpy(), "revenue_month": ym.month.astype("int64"),
        "revenue_year": ym.year.astype("int64"), "create_time": "",
    })
    mr["_avail"] = pd.to_datetime(x["available_at"]).to_numpy()
    return mr.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)


def ttm_eps_steps(view: PITView, code: str, q: pd.DataFrame | None = None) -> pd.Series:
    """d 基準的 TTM EPS,index = 這個 TTM 最早可用的日期(四季都已公布),只取連續四季。"""
    q = quarterly_asof(view, code) if q is None else q
    if q.empty:
        return pd.Series(dtype=float)
    t = FN.ttm_series(q, "eps_diluted")
    ns = pd.Series(pd.DatetimeIndex(q["_avail"]).as_unit("ns").asi8.astype(float), index=q.index)
    avail = pd.to_datetime(ns.rolling(4, min_periods=4).max(), unit="ns")
    s = pd.Series(t.to_numpy(), index=pd.DatetimeIndex(avail.to_numpy()))
    s = s[s.index.notna() & s.notna()]
    return s.groupby(level=0).last().sort_index()


def pe_asof(view: PITView, code: str, q: pd.DataFrame | None = None, px: pd.DataFrame | None = None) -> pd.Series | None:
    """d 以前每天的本益比 = split 調整價格(d 基準)÷ 當天已公布的 TTM EPS(d 基準);TTM ≤ 0 → NaN。"""
    steps = ttm_eps_steps(view, code, q)
    px = view.prices(code, adjust="split") if px is None else px
    if px.empty or steps.empty:
        return None
    e = steps.reindex(px.index.union(steps.index)).ffill().reindex(px.index)
    pe = (px["close"] / e).where(e > 0)
    pe.name = "PER"
    return pe.astype(float)


def tw_fdat_asof(view: PITView, code: str) -> dict:
    """和 backtest.tw_data 同結構:{"q": 季表(含 _avail), "mr": 月營收(FinMind 格式 + _avail), "pe": 每日本益比}。
    全部只用 view(d 時看得到的資料),每股數字與價格都在 d 的股數基準。"""
    q = quarterly_asof(view, code)
    return {"q": q, "mr": month_revenue_asof(view, code), "pe": pe_asof(view, code, q)}


def price_asof(view: PITView, code: str) -> pd.DataFrame:
    """d 以前、以 d 股數為基準的價格表,欄名同 yfinance(Open/High/Low/Close/Adj Close/Volume/Dividends)。
    Adj Close = 含息還原(只用 d 以前的股利與公司行動)。"""
    p = view.prices(code, adjust="split")
    t = view.prices(code, adjust="total")
    if p.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends"])
    raw = view.prices(code, adjust="none")
    with np.errstate(divide="ignore", invalid="ignore"):
        f = (p["close"] / raw["close"]).fillna(1.0)
    return pd.DataFrame({"Open": p["open"], "High": p["high"], "Low": p["low"], "Close": p["close"],
                         "Adj Close": t["close"], "Volume": p["volume"], "Dividends": p["cash_div"].fillna(0.0) * f},
                        index=p.index)


# ---------------------------------------------------------------- 驗證:和 FinMind(TWSE)本益比比較

def _raw_ttm_by_quarter(view: PITView, code: str) -> pd.Series:
    """原始申報 EPS 的 TTM(不做股數換算),index = 季末。用來辨認 TWSE 當天用的是哪四季。"""
    x = view.facts_long(code, ["eps_as_reported"], period="Q")
    if x.empty:
        return pd.Series(dtype=float)
    s = x.set_index("period_end")["value"].sort_index()
    return FN.ttm_series(pd.DataFrame({"eps_diluted": s}), "eps_diluted").dropna()


def _close(a: float, b: float) -> bool:
    """TWSE 隱含 EPS 與候選 TTM 是否吻合:相對 2% 或絕對 0.03 元(四季 EPS 各四捨五入到 0.01 元,加上 PER 的小數位)。"""
    return bool(pd.notna(a) and pd.notna(b) and abs(a - b) <= max(0.02 * abs(b), 0.03))


def _alt_basis_matches(view: PITView, code: str, q: pd.DataFrame, ttm: float, close: float, twse: float) -> bool:
    """TTM 四季中任一季改用另一個股數基準的 EPS(eps_alt_basis,換到 d 基準)後,是否就和 TWSE 一致(±3% 或隱含 EPS 吻合)。"""
    alt = view.facts_long(code, ["eps_alt_basis"], period="Q")
    if alt.empty:
        return False
    last4 = q.index[-4:]
    for _, r in alt[alt["period_end"].isin(last4)].iterrows():
        a_d = float(r["value"]) / view.split_factor(code, r["basis_at"])
        t2 = ttm - float(q.at[r["period_end"], "eps_diluted"]) + a_d
        if t2 > 0 and (abs(close / t2 / twse - 1) <= 0.03 or _close(close / twse, t2)):
            return True
    return False


def per_consistency(store: PITStore, code: str, per: pd.Series, yahoo_close: pd.Series | None = None) -> pd.DataFrame:
    """每月最後一個交易日:我的本益比(split 調整價 ÷ d 基準 TTM EPS)÷ FinMind PER(TWSE 當天公布)。

    偏離 > 3% 的月份依 TWSE 隱含 EPS(原始收盤 ÷ PER)分類:
    - rounding:和我的 TTM 只差 0.03 元以內(TTM 很小時,四季 EPS 的四捨五入就會造成大比率差)
    - timing:等於較新一季的原始 TTM(公司比法定期限早申報,TWSE 已更新;我們依期限 +1 交易日才用)
    - twse_unadjusted:等於同一季的原始申報 TTM(TWSE 沒有隨配股 / 減資 / 分割調整)
    - basis_suspect:把 TTM 裡某一季的 EPS 換成另一個股數基準(eps_alt_basis)就和 TWSE 一致 → 可能是我們的基準判斷錯,
      不算已解釋(在判斷 twse_current_shares 之前先檢查)
    - twse_current_shares:等於「近四季歸屬母公司淨利 ÷ 最新一季股本股數」(TWSE 以最新股本重算,含現金增資稀釋)
    - unexplained:以上都不是
    """
    px = store.prices.get(code)
    if px is None or per.empty:
        return pd.DataFrame()
    me = px.index.to_series().groupby(px.index.to_period("M")).max()
    rows = []
    full = store.as_of(px.index[-1])
    raw_ttm_all = _raw_ttm_by_quarter(full, code)
    w = full.facts(code, ["net_income", "shares_out_capital"], period="Q")
    ni_ttm = FN.ttm_series(w.rename(columns={"net_income": "eps_diluted"}), "eps_diluted") if len(w) else pd.Series(dtype=float)
    cap = w["shares_out_capital"] if "shares_out_capital" in w else pd.Series(dtype=float)
    # 每季 EPS(available_at 基準)與可用日:驗證時換到任一天 d 的基準,判斷 TWSE 是否已用較新一季
    el = full.facts_long(code, ["eps_diluted"], period="Q").set_index("period_end").sort_index()
    N = _cum_index(store.events[store.events["entity"] == code].sort_values("date"))
    for d in me:
        if d not in per.index or pd.isna(per.loc[d]):
            continue
        v = store.as_of(d)
        q = quarterly_asof(v, code)
        steps = ttm_eps_steps(v, code, q)
        if steps.empty:
            continue
        ttm = float(steps.iloc[-1])
        close = float(px.loc[d, "close"])
        if not ttm > 0 or not np.isfinite(close):
            continue
        mine = close / ttm
        twse = float(per.loc[d])
        ratio = mine / twse
        imp = close / twse  # TWSE 隱含 TTM EPS(原始股數基準)
        last_q = q.index[-1]
        raw_ttm_now = raw_ttm_all.get(last_q, np.nan)
        cls = "ok"
        if abs(ratio - 1) > 0.03:
            newer = raw_ttm_all[(raw_ttm_all.index > last_q) & (raw_ttm_all.index < d)]
            if len(el):
                nd = N(d)
                e_d = el["value"] / np.array([nd / N(a) for a in el["available_at"]])  # 每股數字 ÷ 之後的股數倍數
                t_d = FN.ttm_series(pd.DataFrame({"eps_diluted": e_d}), "eps_diluted")
                newer = pd.concat([newer, t_d[(t_d.index > last_q) & (t_d.index < d)].dropna()])
            cand_q = [k for k in ni_ttm.index if last_q <= k < d]
            if abs(ttm - imp) <= 0.03:
                cls = "rounding"
            elif any(_close(imp, x) for x in newer.to_numpy()):
                cls = "timing"
            elif _close(imp, raw_ttm_now):
                cls = "twse_unadjusted"
            elif _alt_basis_matches(v, code, q, ttm, close, twse):
                cls = "basis_suspect"
            elif any(_close(imp, ni_ttm.get(k, np.nan) / cap.get(k, np.nan)) for k in cand_q):
                cls = "twse_current_shares"
            else:
                cls = "unexplained"
        y = None
        if yahoo_close is not None and d in yahoo_close.index and pd.notna(raw_ttm_now) and raw_ttm_now > 0:
            y = float(yahoo_close.loc[d]) / (twse * raw_ttm_now)
        rows.append({"date": d, "mine_pe": mine, "twse_pe": twse, "ratio": ratio, "class": cls,
                     "verifier_ratio": y, "ttm_quarter": last_q, "my_ttm": ttm, "twse_implied_eps": imp})
    return pd.DataFrame(rows)


def twse_update_lags(store: PITStore, code: str, per: pd.Series) -> pd.DataFrame:
    """每一季:TWSE 隱含 EPS 第一次等於這一季 TTM(原始申報)的日期 vs 我們的 available_at。
    TWSE 更新日晚於 available_at 太多,代表公司可能晚於法定期限申報(我們的 available_at 可能偷看)。"""
    px = store.prices.get(code)
    if px is None or per.empty:
        return pd.DataFrame()
    v = store.as_of(px.index[-1])
    raw_ttm = _raw_ttm_by_quarter(v, code)
    x = v.facts_long(code, ["eps_as_reported"], period="Q").set_index("period_end")["available_at"]
    w = v.facts(code, ["net_income", "shares_out_capital"], period="Q")
    ni = FN.ttm_series(w.rename(columns={"net_income": "eps_diluted"}), "eps_diluted") / w["shares_out_capital"]
    imp = (px["close"] / per.reindex(px.index)).dropna()
    rows = []
    for pe, t in raw_ttm.items():
        if pe < imp.index[0] - pd.Timedelta(days=30) or pe not in x.index:
            continue
        win = imp[(imp.index > pe) & (imp.index <= pe + pd.Timedelta(days=200))]
        alt = ni.get(pe, np.nan)
        m = np.abs(win - t) <= max(0.005 * abs(t), 0.011)
        if pd.notna(alt):
            m |= np.abs(win - alt) <= max(0.005 * abs(alt), 0.011)
        hit = win[m]
        first = hit.index[0] if len(hit) else pd.NaT
        rows.append({"period_end": pe, "available_at": x.loc[pe], "twse_first": first,
                     "lag_vs_avail_days": (first - x.loc[pe]).days if pd.notna(first) else None})
    return pd.DataFrame(rows)


def implied_eps_jumps(store: PITStore, code: str, per: pd.Series) -> pd.DataFrame:
    """找「原始價格 ÷ TWSE 本益比」(TWSE 隱含 EPS)在非財報更新日的跳動:可能是漏記的減資 / 原始價格還原錯誤。"""
    px = store.prices.get(code)
    if px is None or per.empty:
        return pd.DataFrame()
    v = store.as_of(px.index[-1])
    raw_ttm = _raw_ttm_by_quarter(v, code)
    w = v.facts(code, ["net_income", "shares_out_capital"], period="Q")
    ni = FN.ttm_series(w.rename(columns={"net_income": "eps_diluted"}), "eps_diluted") if len(w) else pd.Series(dtype=float)
    vals = list(raw_ttm.to_numpy()) + list((ni / w["shares_out_capital"]).dropna().to_numpy() if len(w) else [])
    imp = (px["close"] / per.reindex(px.index)).dropna()
    ch = np.log(imp / imp.shift(1)).dropna()
    rows = []
    for d, c in ch[np.abs(ch) > 0.02].items():
        new = imp.loc[d]
        explained = any(_close(new, x) for x in vals)
        ev = store.events[(store.events["entity"] == code) & (store.events["kind"] != "spinoff")
                          & (np.abs((store.events["date"] - d).dt.days) <= 20)]
        rows.append({"date": d, "log_change": float(c), "matches_reported_ttm": explained, "near_event": len(ev) > 0})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 品質報告

def quality(store: PITStore, audits: dict, pers: dict, universe=TW_LARGE) -> dict:
    out = {}
    for sym, name in universe:
        code = sym.split(".")[0]
        st = yahoo.Status()
        h = yahoo.history(sym, st)
        yc = None
        if h is not None:
            yc = h["Close"].copy()
            yc.index = pd.to_datetime(yc.index).normalize()
        pc = per_consistency(store, code, pers[code], yc)
        lags = twse_update_lags(store, code, pers[code])
        jumps = implied_eps_jumps(store, code, pers[code])
        a = audits[code]
        rec = {"name": name, "coverage": a.coverage, "actions": a.actions, "cash": a.cash, "basis": a.basis, "notes": a.notes,
               "basis_summary": {k: sum(1 for b in a.basis if b.get("kind") == k) for k in ("old_basis", "mixed", "ambiguous")}}
        if len(pc):
            ok = pc[pc["class"] == "ok"]
            worst = pc.iloc[int(np.argmax(np.abs(np.log(pc["ratio"]))))]
            vr = pc["verifier_ratio"].dropna()
            rec["per"] = {
                "months": int(len(pc)), "median": float(pc["ratio"].median()),
                "within_3pct": float((np.abs(pc["ratio"] - 1) <= 0.03).mean()),
                "worst_date": worst["date"].date().isoformat(), "worst_ratio": float(worst["ratio"]),
                "worst_class": worst["class"], "classes": pc["class"].value_counts().to_dict(),
                "unexplained": [(r["date"].date().isoformat(), round(float(r["ratio"]), 4)) for _, r in pc[pc["class"] == "unexplained"].iterrows()],
                "ok_median": float(ok["ratio"].median()) if len(ok) else None,
                "monthly": [(r["date"].date().isoformat(), round(float(r["ratio"]), 4), r["class"]) for _, r in pc.iterrows()],
                "verifier_median": float(vr.median()) if len(vr) else None,
                "verifier_min": float(vr.min()) if len(vr) else None, "verifier_max": float(vr.max()) if len(vr) else None,
            }
        if len(lags):
            lg = lags.dropna(subset=["lag_vs_avail_days"])
            rec["lags"] = {"quarters": int(len(lags)), "matched": int(len(lg)),
                           "late": [(r["period_end"].date().isoformat(), r["available_at"].date().isoformat(),
                                     r["twse_first"].date().isoformat(), int(r["lag_vs_avail_days"]))
                                    for _, r in lg[lg["lag_vs_avail_days"] > 0].iterrows()],
                           "median_lead_days": float(-lg["lag_vs_avail_days"].median()) if len(lg) else None}
        if len(jumps):
            un = jumps[~jumps["matches_reported_ttm"] & ~jumps["near_event"]]
            rec["jumps_unexplained"] = [(r["date"].date().isoformat(), round(float(r["log_change"]), 4)) for _, r in un.iterrows()]
        out[code] = rec
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="建台股 PIT store(只讀凍結快取)")
    ap.add_argument("--codes", help="只建這些代號(逗號分隔,例 6669,2330);預設 TW_LARGE 40 檔")
    ap.add_argument("--out", help="sqlite 輸出路徑;預設 $INVEST_LAB_OUT_DIR/pit_tw.sqlite")
    ap.add_argument("--no-quality", action="store_true", help="不算品質報告(較快)")
    ap.add_argument("--per-json", help="另把 FinMind 本益比(TWSE)存成 JSON {代號: {日期: PER}}(測試用)")
    ap.add_argument("--price-source", default="10y", choices=PRICE_SOURCES,
                    help="價格來源:10y(預設,Yahoo 10 年)或 since(Yahoo 2013 起;沒有 Yahoo 的上市股改讀證交所日價格)")
    ap.add_argument("--any-code", action="store_true", help="--codes 不限 TW_LARGE(不在名單的代號用「代號.TW」)")
    a = ap.parse_args(argv)
    universe = TW_LARGE
    if a.codes:
        want = a.codes.split(",")
        universe = [(s, n) for s, n in TW_LARGE if s.split(".")[0] in set(want)]
        if a.any_code:
            have = {s.split(".")[0] for s, _ in universe}
            universe += [(f"{c}.TW", "") for c in want if c not in have]
    store, audits, pers = build_store(universe, price_source=a.price_source)
    path = Path(a.out) if a.out else default_path()
    store.save(path)
    print(f"已寫入 {path}(facts {len(store.facts)} 列、events {len(store.events)} 筆、{len(store.prices)} 檔價格)")
    if a.per_json:
        Path(a.per_json).write_text(json.dumps({c: {d.strftime("%Y-%m-%d"): float(v) for d, v in s.dropna().items()}
                                                for c, s in pers.items()}), encoding="utf-8")
    if not a.no_quality:
        q = quality(store, audits, pers, universe)
        js = path.with_name(path.stem + "_quality.json")
        js.write_text(json.dumps(q, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"品質報告資料:{js}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
