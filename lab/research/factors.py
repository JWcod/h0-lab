"""因子實驗室:檢驗價格類因子和下個月報酬的關係(橫斷面)。階段 4.3 起改用 PIT 價格庫,離線可跑。

方法(學術標準做法的精簡版):
1. 每個月底(含息大盤的交易日曆),用「當時可得」的價格算出每檔股票的因子值
2. 看下一個月的報酬(含息報酬指數;下市股之後持平 = 下市報酬 0%)
3. Rank IC = 因子排名與下月報酬排名的 Spearman 相關;多空 = 前 1/3 平均報酬 − 後 1/3
4. 統計:平均 IC、Newey–West t 值與 fixed-b p 值(lab/research/evaluate.newey_west)、7 個因子一起做 Holm 多重檢定校正、
   IC>0 比例、ICIR、開發集最後 36 個月
   **所有統計與曲線只用開發集**:結果月份(下個月底)必須在 holdout 起點(2024-10-01)之前,
   之後的月份完全不計算、不回傳(階段 3 D0 稽核 B2b;holdout 只能經由 scripts/holdout.py 查看)。

資料(2026-10-26 決定用凍結 PIT 資料):回測建好的 PIT 價格庫 `OUT_DIR/bt_pit/pit_{tw,us}.sqlite`
(`python -m lab.research.backtest_pit build`,離線、讀 DATA_DIR 的凍結快取)。股票池:
- tw_pit50:每月當時成交金額前 50(universe_pit.tw_membership,含之後下市的公司)
- tw_large40 / us_large40:今天的名單(對照用,有存活者偏誤;美股沒有 PIT 成分資料)
"""
from __future__ import annotations

import datetime as dt
import threading

import numpy as np
import pandas as pd

from ..universe import TW_LARGE, US_LARGE
from .splits import HOLDOUT_START

UNIVERSES = {
    "tw_pit50": ("台股 PIT 前 50(含下市)", "TW"),
    "tw_large40": ("台股權值 40(今天的名單)", "TW"),
    "us_large40": ("美股大型 40(今天的名單)", "US"),
}
NW_LAG = 3            # 月 IC 不重疊,只留短 lag 處理輕微自相關
HOLM_ALPHA = 0.05
_memo: dict = {}
_memo_lock = threading.Lock()

FACTORS = [
    ("mom_12_1", "12-1 個月動能", "t−21 日價格 ÷ t−252 日價格 − 1(跳過最近一個月)"),
    ("mom_6_1", "6-1 個月動能", "t−21 日價格 ÷ t−126 日價格 − 1"),
    ("high_52w", "接近 52 週高點", "現價 ÷ 過去 252 日最高價"),
    ("trend_200", "200 日線乖離", "現價 ÷ 200 日均線 − 1"),
    ("reversal_1m", "短期反轉", "−(近 21 日報酬):上個月跌多的,下個月反彈?"),
    ("low_vol", "低波動", "−(近 60 日日報酬標準差):波動低的報酬比較好?"),
    ("volume_surge", "量能放大", "近 20 日均量 ÷ 近 120 日均量"),
]


def _vintage(key: str) -> str:
    """價格庫版本(資料版本 + 價格庫檔名與修改時間);價格庫不存在時丟 FileNotFoundError(附建立方法)。"""
    from . import backtest_pit as BP
    path = BP.paths()[UNIVERSES[key][1]]
    if not path.exists():
        raise FileNotFoundError("還沒有 PIT 價格庫:先在本機執行 `.venv/bin/python -m lab.research.backtest_pit build`"
                                f"(離線、讀凍結資料),會產生 {path.name}")
    return f"{BP.vintage_label()} · {path.name}@{int(path.stat().st_mtime)}"


def _inputs(key: str) -> tuple[pd.DataFrame, pd.DataFrame, dict | None, str]:
    """回傳 (含息報酬指數表, 成交量表, 每月成員 {Period: set(symbol)} 或 None, 價格庫版本)。欄位是 symbol,索引是交易日。"""
    from .. import universe_pit as U
    from ..pit import PITStore
    from . import backtest_pit as BP
    market = UNIVERSES[key][1]
    path = BP.paths()[market]
    vintage = _vintage(key)
    members = None
    if key == "tw_pit50":
        m = U.tw_membership(pd.Timestamp(BP.START["TW"]) - pd.DateOffset(months=13), HOLDOUT_START, 50)
        members = {p: {BP.tw_symbol(c) for c in g["code"].astype(str)} for p, g in m.groupby("month")}
        syms = sorted(set().union(*members.values())) if members else []
    else:
        syms = [s for s, _ in (TW_LARGE if key == "tw_large40" else US_LARGE)]
    px = BP.pit_prices(market, syms)
    cal = px["calendar"]
    close = pd.DataFrame({s: v for s, v in px["tr_index"].items()}).reindex(cal)
    store = PITStore.load(path, entities=[BP.entity_of(s, market) for s in close.columns])
    view = store.as_of(BP.FAR)
    vol = pd.DataFrame({s: view.prices(BP.entity_of(s, market), adjust="split")["volume"] for s in close.columns}).reindex(cal)
    return close, vol, members, vintage


def holm(pvals: list) -> list:
    """Holm–Bonferroni 校正後的 p 值(None 保持 None,不計入 m)。"""
    idx = [i for i, p in enumerate(pvals) if p is not None]
    m = len(idx)
    out = [None] * len(pvals)
    run = 0.0
    for rank, i in enumerate(sorted(idx, key=lambda i: pvals[i])):
        run = max(run, min(1.0, (m - rank) * pvals[i]))
        out[i] = run
    return out


def _spearman(a: pd.Series, b: pd.Series) -> float | None:
    m = a.notna() & b.notna()
    if m.sum() < 8:
        return None
    return float(a[m].rank().corr(b[m].rank()))


def run(key: str) -> dict:
    if key not in UNIVERSES:
        raise ValueError(f"未知的股票池:{key}")
    label = UNIVERSES[key][0]
    vintage = _vintage(key)
    with _memo_lock:                       # 先查記憶體快取,命中就不用載入價格庫(階段 5 效能:3.8 秒 → 毫秒)
        hit = _memo.get((key, vintage))
    if hit:
        return hit
    close, vol, members, vintage = _inputs(key)
    res = compute(key, label, close, vol, members, vintage)
    with _memo_lock:
        _memo[(key, vintage)] = res
    return res


def compute(key: str, label: str, close: pd.DataFrame, vol: pd.DataFrame, members: dict | None, vintage: str = "") -> dict:
    """純計算(測試直接呼叫):close 是含息報酬指數表,members 是每月成員(None = 全部欄位都算)。"""
    from .evaluate import newey_west
    close = close.dropna(how="all", axis=1)
    rets = close.pct_change(fill_method=None)
    month_ends = close.resample("ME").last().index
    month_ends = [d for d in month_ends if d >= close.index[0] + pd.Timedelta(days=380)]
    # 每個月底找實際最後一個交易日
    idx = close.index
    dates = []
    for d in month_ends:
        pos = idx.searchsorted(d, side="right") - 1
        if pos >= 260:
            dates.append(idx[pos])
    dates = sorted(set(dates))
    rows = {f[0]: {"ic": [], "ls": []} for f in FACTORS}
    used_dates, label_dates = [], []
    for i in range(len(dates) - 1):
        t, t1 = dates[i], dates[i + 1]
        if t1 >= HOLDOUT_START:          # 結果月份進到 holdout:之後全部不算
            break
        p = idx.get_loc(t)
        px = close.iloc[: p + 1]
        fwd = close.loc[t1] / close.loc[t] - 1
        if members is not None:            # 只算當月 PIT 股票池裡的股票(其他設成 NaN)
            mem = members.get(t.to_period("M"))
            if not mem:
                continue
            fwd = fwd.where(fwd.index.isin(list(mem)))
        fac = {
            "mom_12_1": px.iloc[-22] / px.iloc[-253] - 1,
            "mom_6_1": px.iloc[-22] / px.iloc[-127] - 1,
            "high_52w": px.iloc[-1] / px.iloc[-252:].max(),
            "trend_200": px.iloc[-1] / px.iloc[-200:].mean() - 1,
            "reversal_1m": -(px.iloc[-1] / px.iloc[-22] - 1),
            "low_vol": -rets.iloc[max(0, p - 59): p + 1].std(),
            "volume_surge": vol.iloc[max(0, p - 19): p + 1].mean() / vol.iloc[max(0, p - 119): p + 1].mean(),
        }
        used_dates.append(t)
        label_dates.append(t1)
        for k, f in fac.items():
            f = f.replace([np.inf, -np.inf], np.nan)
            ic = _spearman(f, fwd)
            rows[k]["ic"].append(ic)
            ok = f.notna() & fwd.notna()
            if ok.sum() >= 9:
                q = f[ok].rank(pct=True)
                top = fwd[ok][q > 2 / 3].mean()
                bot = fwd[ok][q <= 1 / 3].mean()
                rows[k]["ls"].append(float(top - bot))
            else:
                rows[k]["ls"].append(None)
    out = []
    for k, lbl, formula in FACTORS:
        ic = pd.Series(rows[k]["ic"], dtype=float)
        ls = pd.Series(rows[k]["ls"], dtype=float)
        n = int(ic.notna().sum())
        mean, sd = float(ic.mean()), float(ic.std())
        # 近 36 個月:開發集的最後 36 個月(迴圈已經只算到 holdout 起點之前)
        dev = pd.Series([d1 < HOLDOUT_START for d1 in label_dates], dtype=bool)
        assert dev.all(), "因子實驗室不得計算 holdout 期間"
        recent = ic[dev.values].tail(36)
        r_mean, r_sd = float(recent.mean()), float(recent.std())
        cum = (1 + ls.fillna(0)).cumprod()
        nw = newey_west(ic.to_numpy(), NW_LAG)
        out.append({
            "key": k, "label": lbl, "formula": formula, "n": n,
            "ic_mean": mean, "ic_t": mean / sd * np.sqrt(n) if sd > 0 else None, "ic_hit": float((ic > 0).mean()),
            "nw_t": nw["t"] if nw else None, "nw_p": nw["p"] if nw else None,
            "icir": mean / sd * np.sqrt(12) if sd > 0 else None,
            "ls_mean": float(ls.mean()), "ls_ann": float((1 + ls.mean()) ** 12 - 1) if ls.notna().any() else None,
            "recent_ic": r_mean, "recent_end": f"{label_dates[int(np.flatnonzero(dev.values)[-1])]:%Y-%m}" if dev.any() else None, "recent_t": r_mean / r_sd * np.sqrt(recent.notna().sum()) if r_sd > 0 else None,
            "cum_ls": [round(float(x), 4) for x in cum],
            "rolling_ic": [None if pd.isna(x) else round(float(x), 4) for x in ic.rolling(12, min_periods=6).mean()],
        })
    for f, hp in zip(out, holm([f["nw_p"] for f in out]), strict=True):
        f["holm_p"] = hp
        f["holm_sig"] = hp is not None and hp < HOLM_ALPHA
    out.sort(key=lambda f: -abs(f["nw_t"] or 0))
    return {
        "universe": key, "label": label, "n_stocks": int(close.shape[1]), "n_months": len(used_dates),
        "period": f"{used_dates[0]:%Y-%m} ~ {used_dates[-1]:%Y-%m}" if used_dates else "",
        "dates": [d.strftime("%Y-%m") for d in used_dates], "factors": out,
        "split": "dev", "holdout_start": str(HOLDOUT_START.date()),
        "computed": dt.datetime.now().isoformat(timespec="seconds"), "vintage": vintage,
        "multiple_testing": {"method": "Holm", "m": sum(f["nw_p"] is not None for f in out), "alpha": HOLM_ALPHA,
                             "nw_lag": NW_LAG, "p": "Newey–West fixed-b 雙尾"},
        "caveats": [
            f"只用開發期:結果月份截到 {HOLDOUT_START - pd.Timedelta(days=1):%Y-%m}。之後的期間是樣本外測試期,保留給一次性的檢驗,這裡不顯示。",
            ("股票池是每月當時的成交金額前 50(名單含之後下市的公司);證交所 / FinMind 下市清單上的股票下市後報酬以 0% 計,"
             "其他下個月底沒有價格的股票那個月不計入(開發期有少數月份如此)。只有上市,沒有上櫃。"
             if members is not None else
             "存活者偏誤:股票池是「今天」的名單,過去下市或被剔除的公司不在裡面,結果會偏樂觀。"),
            "多重檢定:7 個因子一起做 Holm 校正(p 值用 Newey–West fixed-b);校正後顯著也只代表開發期上的統計關係,"
            "沒有經過正式檢定與樣本外測試,不算證實有效。",
            "這頁的因子是長年公開的價格因子,而且之前已經看過同一份資料,p 值會偏樂觀(資料窺探)。",
            "沒有計入交易成本與滑價;台股來回成本約 0.6%,每月換股的策略要扣很多。",
            "這是研究起點,不是交易策略。",
        ],
    }
