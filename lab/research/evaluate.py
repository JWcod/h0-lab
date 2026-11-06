"""統一評估框架(v2 階段 1,C1)。介面見 docs/架構.md §3.1、§3.2;校準結果見 docs/閘門校準.md。

兩種模式
- 舊模式 `legacy_metrics()`:完全照 v1.1 的定義(同日收盤成交、平手算錯、日曆月切分、全部指標共用一個亂數產生器),
  在凍結資料上必須算出和 tests/baseline/metrics_v1.1.json 一模一樣的數字。只用來重現基準,不要拿來做決策。
- 新模式 `evaluate()`(預設):
  - 判斷日 d 收盤決策、d 的下一個交易日收盤成交(lag=1),報酬用含息報酬指數(`forward_returns`)。
  - 超額報酬 = 個股報酬 − 同日股票池中位數;平手(超額 = 0)不算對也不算錯,另外計數。
  - 沒有評等(lt_level 是 NaN)的列不當中性:長期指標全部排除,另外報告數量與它們的報酬。
  - 切分用 `lab.research.splits.assign`,另外要求「含成交延遲的出場日」也在邊界前;預設只回傳開發集。
  - 主要指標:長期分數 → 重疊世代月 IC(Jegadeesh–Titman:每個月把過去 H 個判斷世代各自的
    「分數排名 vs 這個月的報酬排名」IC 平均,得到不重疊的月序列)+ Newey–West 標準誤(lag = H,fixed-b 臨界值);
    短期分數 → 1 個月 IC + Newey–West。**只用湊滿 H 個世代的月份**(每個世代各 1/H);頭尾不滿的月份不進主要指標,
    另外報告「依世代數加權」的敏感度數字。
  - 次要:固定覆蓋率命中率(前 / 後 20%)、一般命中率(附覆蓋率)、三分位價差、評等分組與單調性、
    投組模擬(等權、t+1 成交、台股 / 美股實際成本、權重漂移後的實際週轉)、最大回撤與相對大盤回撤。
  - 區塊 bootstrap 只在獨立期數 ≥ 8 時使用;每個指標用 sha256(指標名稱) 衍生自己的 seed(Python 內建 hash() 每次執行不同,不能用)。
  - 每次呼叫自動寫實驗登記簿(dev / replay;holdout 由 scripts/holdout.py 自己記)。
- `paired_gate()`:合併閘門(不對稱,docs/架構.md §3.2)。

數字一律由這裡的 Python 計算。

holdout 防護是「誠實制 + 日誌」:授權碼、呼叫者檢查、worktree / agent 模式拒絕都只是防止「不小心」看到 holdout,
擋不住刻意繞過(任何人都能改程式)。真正的保障是 scripts/holdout.py 自動寫的 holdout 日誌與實驗登記簿,
以及 Verifier / 維護者重跑。不要另外做更複雜的防護。
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import inspect
import json
import math
import os
import pickle
import secrets
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as _st

from .. import config, runtime
from . import prereg, registry, splits

EVAL_VERSION = "evaluate-v2.1 (階段 5 M4 固定覆蓋率排名與平手, 2026-10-26)"
HORIZON_DAYS = splits.HORIZON_DAYS
HORIZON_MONTHS = {"1m": 1, "3m": 3, "6m": 6, "12m": 12}
MONTHS_TO_HORIZON = {v: k for k, v in HORIZON_MONTHS.items()}
# 台股:買 0.1425%、賣 0.1425% + 證交稅 0.3%;美股:每邊 0.05%(不含滑價)
COSTS = {"TW": {"buy": 0.001425, "sell": 0.001425 + 0.003}, "US": {"buy": 0.0005, "sell": 0.0005}}
# 主要指標:名稱 → (分數欄位, 判斷欄位, 預測期月數)
PRIMARY = {"lt_12m": ("lt_score", "lt_level", 12), "lt_6m": ("lt_score", "lt_level", 6), "st_1m": ("st_score", "st_code", 1)}
MIN_XS = 10          # 一個橫斷面至少幾檔才算 IC
MIN_SPREAD = 12      # 三分位價差至少幾檔
MIN_INDEPENDENT = 8  # 區塊 bootstrap 的最少獨立期數
BOOT_DRAWS = 2000
FIXED_COVERAGE = 0.20
# 'fix' 閘門的非劣性門檻(主要指標月 IC 差異的 95% 下界 ≥ −門檻)。數值由閘門校準決定,見 docs/閘門校準.md
# (2026-10-16 校準,只用滿世代月份:lt_12m 只有約 21 個月、區間很寬用 0.03;lt_6m 0.02;st_1m 雜訊大用 0.03)
GATE_ALPHA = 0.05
OFFICIAL_REGISTRY = (config.MAIN_ROOT / "data" / "research" / "experiments.jsonl").resolve()   # 正式閘門只認這一份
FIX_MARGIN = {"lt_12m": 0.03, "lt_6m": 0.02, "st_1m": 0.03}
# 配對後至少幾個滿世代月份才算數。12 個月期的開發集只有約 21 個滿世代月份,所以 12 個月期放寬到 18
MIN_GATE_MONTHS = {12: 18, 6: 24, 3: 24, 1: 24}
COVERAGE_DROP_WARN = 0.05  # 新版本覆蓋率比基準低超過 5 個百分點就醒目標示
MISSING_PRICE_MAX = 0.05   # 逐筆檔裡沒有價格的代號超過 5% 就報錯(價格輸入和逐筆檔不配套,IC 會被悄悄高估)


# ---------------- 小工具 ----------------

def metric_seed(name: str) -> int:
    """每個指標自己的固定 seed(sha256,跨程序穩定)。"""
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)


def _rng(name: str) -> np.random.Generator:
    return np.random.default_rng(metric_seed(name))


def _f(x):
    """轉成可寫進 JSON 的 float(NaN / inf → None)。"""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def rows_fingerprint(rows: pd.DataFrame) -> str:
    cols = [c for c in ("date", "symbol", "lt_level", "lt_score", "st_code", "st_score") if c in rows.columns]
    d = rows[cols].copy()
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d = d.sort_values(["date", "symbol"]).reset_index(drop=True)
    return hashlib.sha256(d.to_csv(index=False).encode()).hexdigest()[:16]


_PART_COLS = {"lt": ("lt_level", "lt_score"), "st": ("st_code", "st_score")}


def rank_fingerprint(rows: pd.DataFrame, part: str | None = None) -> str:
    """排序不變的指紋(Verifier30 問題 2):分數換成每個判斷日內的排名,再加上判斷欄。
    lt_score*2+1 這類單調轉換的 IC 完全相同,指紋也相同,所以試跑換個尺度仍會被計次。
    part = "lt" / "st" 時只看那一組(判斷欄 + 分數);閘門用主要指標那一組比對,改動用不到的欄也躲不掉計次。"""
    keep = _PART_COLS[part] if part else ("lt_level", "lt_score", "st_code", "st_score")
    d = rows[["date", "symbol"] + [c for c in keep if c in rows.columns]].copy()
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    for c in ("lt_score", "st_score"):
        if c in d.columns:
            d[c] = d.groupby("date")[c].rank(method="min")
    d = d.sort_values(["date", "symbol"]).reset_index(drop=True)
    return hashlib.sha256(d.to_csv(index=False).encode()).hexdigest()[:16]


def rank_parts(rows: pd.DataFrame) -> dict:
    """{"lt": 長期那組的排名指紋, "st": 短期那組}(只算 rows 有的欄)。"""
    return {k: rank_fingerprint(rows, k) for k, cols in _PART_COLS.items() if cols[1] in rows.columns}


def prices_fingerprint(tr_index: dict) -> str:
    h = hashlib.sha256()
    for s in sorted(tr_index):
        v = tr_index[s].dropna()
        if len(v):
            h.update(f"{s}|{len(v)}|{v.index[0].date()}|{v.index[-1].date()}|{float(v.iloc[-1]):.8g}|{float(v.sum()):.8g}".encode())
    return h.hexdigest()[:16]


def load_rows(path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    return df


# ---------------- 價格輸入(之後換成 PIT store 的含息報酬) ----------------

def bt_px_key(symbols) -> str:
    return hashlib.sha1(f"bt_px|{'|'.join(sorted(symbols))}".encode()).hexdigest()


def load_bt_px(cache_dir, symbols) -> dict:
    with open(Path(cache_dir) / "bt_px" / f"{bt_px_key(symbols)}.pkl", "rb") as f:
        return pickle.load(f)["v"]


def _universe(market: str) -> tuple[list[str], str, list[str]]:
    from ..universe import TW_LARGE, US_LARGE
    syms = [s for s, _ in (TW_LARGE if market == "TW" else US_LARGE)]
    bench = "^TWII" if market == "TW" else "^GSPC"
    extra = [bench, "^VIX", "^TNX"] + (["TWD=X"] if market == "TW" else [])
    return syms, bench, syms + extra


def v11_prices(market: str, cache_dir=None) -> dict:
    """v1.1 凍結快取的價格:個股用 yfinance 的 Adj Close(還原權值,含股利)當含息報酬指數。
    大盤只有價格指數(^TWII / ^GSPC 不含股利),所以 bench_kind = 'price',跟大盤比較會偏向個股。"""
    syms, bench, allsyms = _universe(market)
    px = load_bt_px(cache_dir or config.CACHE_DIR, allsyms)
    tr = {s: (px[s]["Adj Close"] if "Adj Close" in px[s] else px[s]["Close"]).dropna() for s in syms if s in px}
    return {"tr_index": tr, "bench_tr": px[bench]["Close"].dropna(), "bench_kind": "price", "bench_symbol": bench,
            "source": "bt_px(v1.1 凍結快取,Adj Close)"}


def make_calendar(tr_index: dict) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex([])
    for s in tr_index.values():
        idx = idx.union(pd.DatetimeIndex(s.dropna().index))
    return idx.sort_values()


def _values_at(ser: pd.Series, when: pd.DatetimeIndex, max_gap_days: int = 10) -> np.ndarray:
    """ser 在 when 這些日期的值(用當天或之前最近一筆;超過最後一筆或間隔太久 → NaN,避免下市後被當成持平)。"""
    s = ser.dropna()
    s = s[s > 0]
    out = np.full(len(when), np.nan)
    if s.empty:
        return out
    idx = s.index
    w = pd.DatetimeIndex(when)
    ok = ~pd.isna(w)
    pos = np.full(len(w), -1)
    pos[ok] = idx.searchsorted(w[ok], side="right") - 1
    good = ok & (pos >= 0)
    gp = pos[good]
    gap = (w[good] - idx[gp]).days
    vals = s.to_numpy()[gp]
    vals = np.where((gap <= max_gap_days) & (w[good] <= idx[-1]), vals, np.nan)
    out[good] = vals
    return out


def _entry_exit(dates, n: int, lag: int, cal: pd.DatetimeIndex):
    d = pd.DatetimeIndex(pd.to_datetime(dates))
    base = cal.searchsorted(d, side="right") - 1
    ent, ex = base + lag, base + lag + n
    ok_e = (base >= 0) & (ent < len(cal))
    ok_x = (base >= 0) & (ex < len(cal))
    entry = pd.DatetimeIndex([cal[i] if o else pd.NaT for i, o in zip(ent, ok_e)])
    exit_ = pd.DatetimeIndex([cal[i] if o else pd.NaT for i, o in zip(ex, ok_x)])
    return entry, exit_


def forward_returns(tr_index, dates, horizon, lag: int = 1, kind: str = "total", calendar=None) -> pd.DataFrame:
    """判斷日 d 收盤決策、d 之後第 lag 個交易日收盤進場、持有 n 個交易日的含息報酬。

    tr_index: {代號: 含息報酬指數(日)};之後會換成 PIT store。kind 目前只支援 'total'(呼叫端負責傳含息指數)。
    回傳 DataFrame(index = 排序後不重複的判斷日,columns = 代號);attrs['entry'] / attrs['exit'] 是進出場日。
    """
    if not isinstance(tr_index, dict):
        raise TypeError("forward_returns 目前只接受 {代號: 含息報酬指數} 的 dict(PITView 接上後再擴充)")
    if kind != "total":
        raise ValueError("新模式一律用含息報酬(kind='total')")
    n = HORIZON_DAYS[horizon] if isinstance(horizon, str) else int(horizon)
    cal = pd.DatetimeIndex(calendar) if calendar is not None else make_calendar(tr_index)
    d = pd.DatetimeIndex(sorted(pd.to_datetime(pd.Series(dates)).unique()))
    entry, exit_ = _entry_exit(d, n, lag, cal)
    out = {}
    for s, ser in tr_index.items():
        a, b = _values_at(ser, entry), _values_at(ser, exit_)
        out[s] = b / a - 1
    df = pd.DataFrame(out, index=d)
    df.attrs["entry"] = pd.Series(entry, index=d)
    df.attrs["exit"] = pd.Series(exit_, index=d)
    return df


# ---------------- 統計 ----------------

FIXED_B_SIMS = 40000
_NULL_CACHE: dict[tuple[int, int], np.ndarray] = {}


def _nw_var(U: np.ndarray, L: int) -> np.ndarray:
    """每一列(已去平均)的 Bartlett 長期變異數。"""
    T = U.shape[-1]
    s = (U * U).sum(-1) / T
    for k in range(1, L + 1):
        s = s + 2 * (1 - k / (L + 1)) * (U[..., k:] * U[..., :-k]).sum(-1) / T
    return s


def nw_t_null(T: int, L: int) -> np.ndarray:
    """NW t 統計量在虛無假設下的分布(排序後的 |t|),用長度 T、lag L 的獨立常態序列模擬。
    月數少、lag 大(b = (L+1)/T 約 0.3)時,NW 標準誤明顯偏低,t(T−1) 的臨界值會過度拒絕;
    這是 Kiefer–Vogelsang「fixed-b」的做法(臨界值依 b 放大),用模擬取代查表,seed 固定。"""
    key = (int(T), int(L))
    if key not in _NULL_CACHE:
        rng = np.random.default_rng(metric_seed(f"fixed-b|{T}|{L}"))
        X = rng.standard_normal((FIXED_B_SIMS, T))
        m = X.mean(1)
        s = _nw_var(X - m[:, None], L)
        _NULL_CACHE[key] = np.sort(np.abs(m / np.sqrt(s / T)))
    return _NULL_CACHE[key]


def newey_west(x, lag: int, alpha: float = 0.05) -> dict | None:
    """平均數的 Newey–West(Bartlett 核)標準誤。x 依時間排序,NaN 會被丟掉。
    p 值與 CI 用 fixed-b 臨界值(nw_t_null);另附 t(T−1) 的 p_t 對照(會過度拒絕,不用來判定)。"""
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    T = len(a)
    if T < 3:
        return None
    m = float(a.mean())
    u = a - m
    L = int(max(0, min(lag, T - 1)))
    s = float(_nw_var(u, L))
    se = math.sqrt(max(s, 0.0) / T)
    se_iid = float(a.std(ddof=1) / math.sqrt(T))
    if se <= 1e-15:
        return {"mean": m, "se": 0.0, "t": None, "p": None, "p_t": None, "ci": [m, m], "crit": None, "n": T, "lag": L,
                "se_iid": se_iid, "alpha": alpha}
    t = m / se
    null = nw_t_null(T, L)
    p = float(1 - np.searchsorted(null, abs(t), side="left") / len(null))
    q = crit_value(T, L, alpha)
    return {"mean": m, "se": se, "t": t, "p": max(p, 1 / len(null)), "p_t": float(2 * _st.t.sf(abs(t), T - 1)),
            "ci": [m - q * se, m + q * se], "crit": q, "n": T, "lag": L, "se_iid": se_iid, "alpha": alpha}


AR1_WARN = 0.5


def ar1(x) -> float | None:
    """一階自相關係數 φ̂(去平均後相鄰兩期的相關);少於 4 期或沒有變異時回傳 None。"""
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) < 4:
        return None
    u = a - a.mean()
    den = float((u * u).sum())
    return float((u[1:] * u[:-1]).sum() / den) if den > 1e-15 else None


def crit_value(T: int, L: int, alpha: float) -> float:
    """雙尾 fixed-b 臨界值(|t| 的 1−α 分位數)。"""
    return float(np.quantile(nw_t_null(T, min(L, T - 1)), 1 - alpha))


def one_sided_p(nw: dict | None, worse_if: str) -> float | None:
    """由對稱的 fixed-b 分布得到單尾 p 值。worse_if = 'negative':差異為負才算變差。"""
    if not nw:
        return None
    if nw["t"] is None:
        return 1.0
    p2 = nw["p"]
    toward = nw["t"] < 0 if worse_if == "negative" else nw["t"] > 0
    return p2 / 2 if toward else 1 - p2 / 2


def _rank(a: np.ndarray) -> np.ndarray:
    return _st.rankdata(a)  # 平手取平均名次(與 pandas rank() 相同)


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = _rank(a), _rank(b)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    den = math.sqrt(float(ra @ ra) * float(rb @ rb))
    return float(ra @ rb) / den if den > 0 else np.nan


def block_bootstrap(n: int, stat_on_idx, L: int, name: str, draws: int = BOOT_DRAWS, idx_sets=None) -> dict | None:
    """循環區塊 bootstrap。獨立期數 n / L < 8 時不給區間(回傳 None)。
    idx_sets:配對比較時傳入事先抽好的索引,讓兩邊共用同一組重抽。"""
    if L <= 0 or n / L < MIN_INDEPENDENT:
        return None
    if idx_sets is None:
        idx_sets = draw_blocks(n, L, name, draws)
    vals = [v for v in (stat_on_idx(ix) for ix in idx_sets) if v is not None and np.isfinite(v)]
    if not vals:
        return None
    return {"ci": [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))], "draws": len(vals),
            "block": L, "seed_name": name, "vals": np.asarray(vals)}


def draw_blocks(n: int, L: int, name: str, draws: int = BOOT_DRAWS) -> list[np.ndarray]:
    rng = _rng(name)
    out = []
    for _ in range(draws):
        starts = rng.integers(0, n, size=math.ceil(n / L))
        out.append(np.concatenate([(np.arange(s, s + L) % n) for s in starts])[:n])
    return out


def _strip(bs: dict | None) -> dict | None:
    if bs is None:
        return None
    return {k: v for k, v in bs.items() if k != "vals"}


# ---------------- 月格線與重疊世代 IC ----------------

def month_grid(decision_dates, cal: pd.DatetimeIndex, lag: int = 1):
    """月格線:所有判斷日 + 最後一個判斷日之後每個月最後一個交易日(讓最後的世代也有之後的月報酬)。
    回傳 (grid 判斷日, entries 成交日);第 t 個月的報酬 = TR(E_t) / TR(E_{t−1}) − 1。"""
    d = pd.DatetimeIndex(sorted(pd.to_datetime(pd.Series(decision_dates)).unique()))
    if len(d) and len(cal):
        after = cal[cal > d[-1]]
        if len(after):
            me = pd.Series(after, index=after).groupby(after.to_period("M")).max()
            d = d.append(pd.DatetimeIndex(me.values)).unique().sort_values()
    entries, _ = _entry_exit(d, 0, lag, cal)
    return d, entries


def monthly_returns(tr_index: dict, entries: pd.DatetimeIndex, symbols: list[str]) -> np.ndarray:
    """R[t, i] = 第 i 檔在 (E_{t−1}, E_t] 的含息報酬;R[0] 全是 NaN。"""
    R = np.full((len(entries), len(symbols)), np.nan)
    for i, s in enumerate(symbols):
        if s not in tr_index:
            continue
        v = _values_at(tr_index[s], entries)
        R[1:, i] = v[1:] / v[:-1] - 1
    return R


def score_panel(rows: pd.DataFrame, col: str, grid: pd.DatetimeIndex, symbols: list[str]) -> np.ndarray:
    p = rows.pivot_table(index="date", columns="symbol", values=col, aggfunc="first", dropna=False)
    p = p.reindex(index=grid, columns=symbols)
    return p.to_numpy(dtype=float)


WINSOR = 0.01


def winsorize(x: np.ndarray, q: float = WINSOR) -> np.ndarray:
    """截尾:把小於第 q 分位、大於第 1−q 分位的值拉到分位數上(只用同一個橫斷面,不看其他月份)。"""
    lo, hi = np.quantile(x, [q, 1 - q])
    return np.clip(x, lo, hi)


def cohort_series(S: np.ndarray, R: np.ndarray, H: int, cohort_ok: np.ndarray, min_n: int = MIN_XS,
                  spread: bool = True) -> pd.DataFrame:
    """重疊世代(Jegadeesh–Titman)月序列。世代 j(在 E_j 成交)持有第 j+1 … j+H 個月;
    第 t 個月的 IC = 所有持有中的世代各自 Spearman(分數, 第 t 個月報酬) 的平均。
    同一個橫斷面減掉常數(股票池中位數)不影響排名,所以 IC 用原始報酬或超額報酬結果相同。"""
    J = S.shape[0]
    ic_sum, ic_cnt = np.zeros(J), np.zeros(J)
    sp_sum, sp_cnt = np.zeros(J), np.zeros(J)
    spw_sum = np.zeros(J)     # Red-team L1:報酬先在橫斷面截尾(WINSOR)再算價差,當敏感度(IC 用排名,不受影響)
    for j in np.flatnonzero(cohort_ok):
        s = S[j]
        ms = np.isfinite(s)
        if ms.sum() < min_n:
            continue
        for k in range(1, H + 1):
            t = j + k
            if t >= J:
                break
            r = R[t]
            m = ms & np.isfinite(r)
            n = int(m.sum())
            if n < min_n:
                continue
            a, b = s[m], r[m]
            if a.min() == a.max() or b.min() == b.max():
                continue
            ra = _rank(a)
            rb = _rank(b)
            rac, rbc = ra - ra.mean(), rb - rb.mean()
            ic_sum[t] += float(rac @ rbc) / math.sqrt(float(rac @ rac) * float(rbc @ rbc))
            ic_cnt[t] += 1
            if spread and n >= MIN_SPREAD:
                q = ra / n
                top, bot = q > 2 / 3, q <= 1 / 3
                if top.any() and bot.any():  # 離散分數(例如評等)平手太多時可能有一組是空的
                    sp_sum[t] += float(b[top].mean() - b[bot].mean())
                    bw = winsorize(b)
                    spw_sum[t] += float(bw[top].mean() - bw[bot].mean())
                    sp_cnt[t] += 1
    with np.errstate(invalid="ignore", divide="ignore"):
        ic = np.where(ic_cnt > 0, ic_sum / np.maximum(ic_cnt, 1), np.nan)
        sp = np.where(sp_cnt > 0, sp_sum / np.maximum(sp_cnt, 1), np.nan)
        spw = np.where(sp_cnt > 0, spw_sum / np.maximum(sp_cnt, 1), np.nan)
    return pd.DataFrame({"ic": ic, "n_cohorts": ic_cnt, "spread": sp, "spread_w": spw})


class _Ctx:
    """一個市場的共用資料:日曆、月格線、月報酬矩陣、各期間的切分。"""

    def __init__(self, rows: pd.DataFrame, tr_index: dict, *, lag: int = 1, calendar=None, bench_tr=None,
                 holdout_ticket: str | None = None):
        self.rows = rows.copy()
        self.rows["date"] = pd.to_datetime(self.rows["date"])
        self.ticket = holdout_ticket
        self.tr = tr_index
        self.lag = lag
        self.cal = pd.DatetimeIndex(calendar) if calendar is not None else make_calendar(tr_index)
        self.symbols = sorted(set(self.rows["symbol"]))
        missing = [x for x in self.symbols if x not in tr_index or not len(tr_index[x].dropna())]
        if self.symbols and len(missing) / len(self.symbols) > MISSING_PRICE_MAX:
            raise ValueError(f"逐筆檔有 {len(missing)} / {len(self.symbols)} 個代號沒有價格(超過 {MISSING_PRICE_MAX:.0%}),"
                             f"價格輸入可能和逐筆檔不配套(例如 PIT 逐筆檔要用 --prices pit):{', '.join(missing)}")
        self.dates = pd.DatetimeIndex(sorted(self.rows["date"].unique()))
        _check_monthly(self.dates)
        self.grid, self.entries = month_grid(self.dates, self.cal, lag)
        self.R = monthly_returns(tr_index, self.entries, self.symbols)
        self.gpos = {d: i for i, d in enumerate(self.grid)}
        self.bench_tr = bench_tr
        self._split_cache: dict = {}

    def _labels(self, H: int) -> pd.Series:
        """每個判斷日在預測期 H 個月下屬於 dev / holdout / purged / no_label(splits.assign + 成交延遲 + 月格線出場日)。"""
        if H in self._split_cache:
            return self._split_cache[H]
        hz = MONTHS_TO_HORIZON[H]
        lab = splits.assign(pd.Series(self.dates), hz, self.cal).to_numpy().copy()
        _, ex = _entry_exit(self.dates, HORIZON_DAYS[hz], self.lag, self.cal)
        for i, d in enumerate(self.dates):
            j = self.gpos[d]
            e_grid = self.entries[j + H] if j + H < len(self.entries) else pd.NaT
            last = max([x for x in (ex[i], e_grid) if not pd.isna(x)], default=pd.NaT)
            if pd.isna(ex[i]) or pd.isna(e_grid):
                if lab[i] == splits.DEV:
                    lab[i] = splits.NO_LABEL
            elif lab[i] == splits.DEV and last >= splits.HOLDOUT_START:
                lab[i] = splits.PURGED
            elif lab[i] == splits.HOLDOUT and pd.isna(last):
                lab[i] = splits.NO_LABEL
        s = pd.Series(lab, index=self.dates)
        self._split_cache[H] = s
        return s

    def dates_in(self, H: int, split: str) -> set:
        """共用的切分過濾點:所有指標都經過這裡取得某個切分的判斷日;非開發集必須有 holdout 授權。"""
        _check_split(split, self.ticket)
        lab = self._labels(H)
        if split == "all":
            return {d for d, v in lab.items() if v in (splits.DEV, splits.HOLDOUT, splits.PURGED)}
        return {d for d, v in lab.items() if v == split}

    def _cohort_ok(self, H: int, split: str) -> np.ndarray:
        ok = np.zeros(len(self.grid), dtype=bool)
        for d in self.dates_in(H, split):
            ok[self.gpos[d]] = True
        return ok

    def month_index(self) -> pd.DatetimeIndex:
        return self.entries

    def decision_months(self, entries) -> pd.PeriodIndex:
        """月序列的鍵:成交日 E_t → 對應判斷日 grid[t] 所在月份。
        不用成交日月份:交易日曆有週六補班日時(TWSE_TR 2017-09-30、2018-03-31),9/29 判斷的成交日 9/30 會和 8/31 判斷的
        成交日 9/01 落在同一個月。"""
        m: dict = {}
        for e, g in zip(self.entries, self.grid):
            if pd.isna(e):
                continue
            if e in m:
                raise ValueError(f"兩個判斷日對應到同一個成交日 {e.date()}")
            m[e] = g
        keys = pd.DatetimeIndex([m[pd.Timestamp(e)] for e in entries]).to_period("M")
        if keys.has_duplicates:
            dup = sorted({str(k) for k in keys[keys.duplicated()]})
            raise ValueError(f"同一個判斷月份出現兩個月序列點:{', '.join(dup)}")
        return keys


def _check_monthly(dates: pd.DatetimeIndex) -> None:
    """判斷日必須是月頻(相鄰間隔約 1 個月);重疊世代的月格線建立在這個假設上。"""
    if len(dates) < 2:
        return
    gaps = np.diff(dates.values).astype("timedelta64[D]").astype(int)
    if gaps.min() < 20 or gaps.max() > 40:
        raise ValueError(f"判斷日不是月頻(相鄰間隔 {gaps.min()} ~ {gaps.max()} 天,應約 1 個月)")


def _judged(rows: pd.DataFrame, score_col: str, judge_col: str) -> pd.DataFrame:
    """只留「有判斷」的列:分數有值,而且長期必須有評等(lt_level 不是 NaN)。"""
    m = rows[score_col].notna()
    if judge_col in rows.columns:
        m &= rows[judge_col].notna()
    return rows[m]


def _ic_series(ctx: _Ctx, rows: pd.DataFrame, score_col: str, H: int, split: str, judge_col: str | None = None,
               full_only: bool = True) -> pd.DataFrame:
    """重疊世代月序列。full_only=True(主要指標)只留湊滿 H 個世代的月份;False 留下所有有世代的月份(敏感度)。"""
    r = _judged(rows, score_col, judge_col) if judge_col else rows[rows[score_col].notna()]
    S = score_panel(r, score_col, ctx.grid, ctx.symbols)
    out = cohort_series(S, ctx.R, H, ctx._cohort_ok(H, split))
    out.index = ctx.month_index()
    out = out[out["n_cohorts"] > 0]
    return out[out["n_cohorts"] == H] if full_only else out


def _ic_block(ctx: _Ctx, rows, score_col: str, H: int, split: str, judge_col: str | None = None) -> dict:
    """主要指標摘要(只用滿世代月份)+ 敏感度:所有月份依世代數加權的平均。"""
    full = _ic_series(ctx, rows, score_col, H, split, judge_col)
    allm = _ic_series(ctx, rows, score_col, H, split, judge_col, full_only=False)
    out = _ic_summary(full, H)
    ok = allm["ic"].notna()
    w = allm.loc[ok, "n_cohorts"]
    out["sensitivity_cohort_weighted"] = {
        "ic": _f((allm.loc[ok, "ic"] * w).sum() / w.sum()) if w.sum() else None, "n_months": int(ok.sum()),
        "note": "包含頭尾不滿 H 個世代的月份,依世代數加權;只是敏感度,不是主要指標"}
    return out


def _ic_summary(ser: pd.DataFrame, H: int) -> dict:
    nw = newey_west(ser["ic"].to_numpy(), H)
    out = {"n_months": int(len(ser)), "nw_lag": H, "mean_cohorts": _f(ser["n_cohorts"].mean()) if len(ser) else None,
           "first_month": str(ser.index[0].date()) if len(ser) else None,
           "last_month": str(ser.index[-1].date()) if len(ser) else None}
    if nw:
        out.update({"ic": nw["mean"], "se_nw": nw["se"], "t_nw": nw["t"], "p": nw["p"], "ci95": nw["ci"], "se_iid": nw["se_iid"]})
    else:
        out.update({"ic": _f(ser["ic"].mean()) if len(ser) else None, "se_nw": None, "t_nw": None, "p": None, "ci95": None})
    sp = ser["spread"].dropna()
    nws = newey_west(sp.to_numpy(), H)
    out["tercile_spread_monthly"] = {"mean": nws["mean"], "se_nw": nws["se"], "ci95": nws["ci"], "n_months": nws["n"],
                                     "annualized": nws["mean"] * 12} if nws else None
    if "spread_w" in ser:      # Red-team L1:極端報酬截尾後的價差(敏感度;主要指標仍是 IC)
        nww = newey_west(ser["spread_w"].dropna().to_numpy(), H)
        out["tercile_spread_monthly_winsor"] = {"mean": nww["mean"], "ci95": nww["ci"], "winsor": WINSOR,
                                                "note": "每月橫斷面報酬截尾 1% / 99% 後的價差,看結果是不是被少數極端報酬帶動"} if nww else None
    return out


# ---------------- 完整期間的前瞻報酬、命中率、評等分組 ----------------

def _excess(ctx: _Ctx, hz: str) -> pd.DataFrame:
    """每列的完整期間前瞻報酬(t+1 成交)與相對同日股票池中位數的超額報酬。股票池包含沒有評等的列。"""
    fr = forward_returns(ctx.tr, ctx.dates, hz, lag=ctx.lag, calendar=ctx.cal)
    d = ctx.rows[["date", "symbol"]].copy()
    stacked = fr.stack(future_stack=True)
    stacked.index.names = ["date", "symbol"]
    d = d.join(stacked.rename("r"), on=["date", "symbol"])
    d["x"] = d["r"] - d.groupby("date")["r"].transform("median")
    return d


def _hit_counts(g: pd.DataFrame, bull, bear) -> tuple[int, int, int]:
    x = g["x"]
    right = int(((bull) & (x > 0)).sum() + ((bear) & (x < 0)).sum())
    wrong = int(((bull) & (x < 0)).sum() + ((bear) & (x > 0)).sum())
    ties = int(((bull | bear) & (x == 0)).sum())
    return right, wrong, ties


def fixed_coverage_flags(d: pd.DataFrame, seed_name: str) -> pd.DataFrame:
    """每個 date,在 _score 不是缺值的列裡排名(平手用固定 seed 的亂數打散),前 FIXED_COVERAGE 標 _fbull、後 FIXED_COVERAGE 標 _fbear。
    回傳依 (date, symbol) 排序、重設索引的新表。"""
    d = d.sort_values(["date", "symbol"]).reset_index(drop=True)
    tie = pd.Series(_rng(seed_name).random(len(d)), index=d.index)
    rk = d.assign(_tie=tie).sort_values(["date", "_score", "_tie"]).groupby("date")["_score"].cumcount() + 1
    cnt = d.groupby("date")["_score"].transform("count")
    rk = rk.reindex(d.index).where(d["_score"].notna())
    k = np.floor(cnt * FIXED_COVERAGE + 1e-9)       # 看多、看空名額一樣多(Verifier39 問題 1;n 太小時兩邊都是 0)
    return d.assign(_fbull=rk > cnt - k, _fbear=rk <= k)


def _hit_rates(ctx: _Ctx, rows: pd.DataFrame, hz: str, split: str, market: str, kind: str) -> dict:
    """一般命中率(附覆蓋率)與固定覆蓋率命中率。kind = 'lt'(lt_level / lt_score)或 'st'(st_code / st_score)。"""
    H = HORIZON_MONTHS[hz]
    ex = _excess(ctx, hz)
    # Red-team M4:固定覆蓋率的分位要在「當天所有有判斷的列」裡排,不能先丟掉之後沒有報酬的列
    # (哪些列之後沒有報酬是未來的資訊;先丟會改變切點)。所以先 left merge、排名,最後才丟 x 缺值。
    d = rows[["date", "symbol", "lt_level", "lt_score", "st_code", "st_score"]].merge(ex, on=["date", "symbol"], how="left")
    d = d[d["date"].isin(ctx.dates_in(H, split))]
    if d.empty or not d["x"].notna().any():
        return {"n": 0}
    if kind == "lt":
        rated = d["lt_level"].notna()
        bull, bear = d["lt_level"] >= 3, d["lt_level"] <= 1
        score = d["lt_score"].where(rated)
    else:
        rated = d["st_code"].notna()
        bull, bear = d["st_code"] > 0, d["st_code"] < 0
        score = d["st_score"].where(rated)
    d = d.assign(_bull=bull, _bear=bear, _score=score)
    base = f"{market}|{split}|{hz}|{kind}"
    # 固定覆蓋率:每個判斷日,有判斷的列中分數前 20% 看多、後 20% 看空。
    # 平手用固定 seed 的亂數打散(Red-team M4):平均名次會讓一整群同分的列同時進出切點,覆蓋率不再固定。
    d = fixed_coverage_flags(d, f"{base}|tie")
    rated = d["lt_level" if kind == "lt" else "st_code"].notna()   # 重新對齊 reset 後的索引
    d = d[d["x"].notna()]
    per = []
    for _, g in d.groupby("date"):
        per.append(_hit_counts(g, g["_bull"], g["_bear"]) + _hit_counts(g, g["_fbull"], g["_fbear"]))
    P = np.array(per, dtype=float)
    R_, W_, T_ = P[:, 0].sum(), P[:, 1].sum(), P[:, 2].sum()
    FR, FW, FT = P[:, 3].sum(), P[:, 4].sum(), P[:, 5].sum()
    n_dates = len(P)

    def hit_of(c0, c1):
        def f(idx):
            r, w = P[idx, c0].sum(), P[idx, c1].sum()
            return r / (r + w) if r + w else np.nan
        return f

    ci = block_bootstrap(n_dates, hit_of(0, 1), H, f"{base}|hit")
    fci = block_bootstrap(n_dates, hit_of(3, 4), H, f"{base}|hit_fixed20")
    n_valid = int(len(d))
    n_unrated = int((~rated.reindex(d.index).fillna(False)).sum())
    return {
        "n_rows_with_return": n_valid, "n_dates": n_dates, "independent_periods": round(n_dates / H, 1),
        "general": {"right": int(R_), "wrong": int(W_), "ties": int(T_), "hit": _f(R_ / (R_ + W_)) if R_ + W_ else None,
                    "coverage": _f((d["_bull"] | d["_bear"]).sum() / n_valid), "n_bull": int(d["_bull"].sum()),
                    "n_bear": int(d["_bear"].sum()), "ci95_block": _strip(ci)},
        "fixed_coverage_20": {"right": int(FR), "wrong": int(FW), "ties": int(FT),
                              "hit": _f(FR / (FR + FW)) if FR + FW else None,
                              "coverage": _f((d["_fbull"] | d["_fbear"]).sum() / n_valid), "ci95_block": _strip(fci)},
        "unrated": {"n": n_unrated, "share": _f(n_unrated / n_valid),
                    "mean_x": _f(d.loc[~rated.reindex(d.index).fillna(False), "x"].mean()) if n_unrated else None},
    }


LT_NAMES = ["賣出", "減碼", "持有", "買進", "強力買進"]


def _rating_buckets(ctx: _Ctx, rows: pd.DataFrame, hz: str, split: str) -> dict:
    H = HORIZON_MONTHS[hz]
    ex = _excess(ctx, hz)
    d = rows[["date", "symbol", "lt_level"]].merge(ex, on=["date", "symbol"])
    d = d[d["date"].isin(ctx.dates_in(H, split)) & d["x"].notna()]
    out = []
    for lvl in (4, 3, 2, 1, 0, None):
        g = d[d["lt_level"].isna()] if lvl is None else d[d["lt_level"] == lvl]
        if not len(g):
            continue
        nz = g["x"] != 0
        out.append({"rating": "沒有評等" if lvl is None else LT_NAMES[lvl], "level": lvl, "n": int(len(g)),
                    "win": _f((g.loc[nz, "x"] > 0).mean()) if nz.any() else None, "ties": int((~nz).sum()),
                    "mean_x": _f(g["x"].mean()), "median_x": _f(g["x"].median())})
    means = [b["mean_x"] for b in out if b["level"] is not None]
    inversions = int(sum(1 for a, b in zip(means, means[1:]) if a is not None and b is not None and a < b))
    # 單調性檢定:評等(離散 0–4)本身的重疊世代月 IC + Newey–West(只用有評等的列)
    mono = _ic_block(ctx, rows, "lt_level", H, split, judge_col="lt_level")
    return {"horizon": hz, "buckets": out, "adjacent_inversions": inversions, "n_levels": len(means),
            "monotonic_ic": {k: mono.get(k) for k in ("ic", "se_nw", "t_nw", "p", "ci95", "n_months")}}


# ---------------- 投組模擬 ----------------

def _equity_stats(r: np.ndarray) -> dict:
    r = np.asarray(r, dtype=float)
    if not len(r):
        return {}
    eq = np.cumprod(1 + r)
    yrs = len(r) / 12
    dd = eq / np.maximum.accumulate(eq) - 1
    sd = r.std(ddof=1) if len(r) > 1 else np.nan
    return {"months": int(len(r)), "cagr": _f(eq[-1] ** (1 / yrs) - 1), "total": _f(eq[-1] - 1), "max_dd": _f(dd.min()),
            "ann_vol": _f(sd * math.sqrt(12)), "sharpe_rf0": _f(r.mean() / sd * math.sqrt(12)) if sd and sd > 0 else None}


def _max_dd(r: np.ndarray) -> float:
    eq = np.cumprod(1 + np.asarray(r, dtype=float))
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def _portfolio_paths(ctx: _Ctx, rows: pd.DataFrame, market: str, split: str) -> dict:
    """每月等權投組,判斷日的下一個交易日收盤成交,持有到下一個判斷日的下一個交易日。
    成本 = 買進金額 × 買進費率 + 賣出金額 × 賣出費率;權重先依上個月報酬漂移,再拉回目標(含每月拉回等權的再平衡)。
    策略:universe(全部等權)、model(只買「買進以上」)、ex_sell(避開「減碼 / 賣出」;沒有評等的照樣持有)。
    持有中某檔缺報酬(停牌、資料缺)時當月報酬記 0 並計數;選股時不偷看下個月有沒有報酬。"""
    c = COSTS[market]
    allowed = ctx.dates_in(1, split)
    months = [d for d in ctx.dates if d in allowed]
    sym_i = {s: i for i, s in enumerate(ctx.symbols)}
    rows = rows.copy()
    sel = {
        "universe": lambda g: list(g["symbol"]),
        "model": lambda g: list(g.loc[g["lt_level"] >= 3, "symbol"]),
        "ex_sell": lambda g: list(g.loc[~(g["lt_level"] <= 1), "symbol"]),
    }
    by_date = {d: g for d, g in rows.groupby("date")}
    res = {k: {"net": [], "gross": [], "turnover": [], "cost": [], "fallback": 0, "missing": 0} for k in sel}
    bench, month_end = [], []
    prev_j = {k: None for k in sel}
    w_prev = {k: None for k in sel}
    for d in months:
        j = ctx.gpos[d]
        if j + 1 >= len(ctx.entries) or pd.isna(ctx.entries[j + 1]):
            continue
        r_next = ctx.R[j + 1]
        g = by_date.get(d)
        if g is None:
            continue
        month_end.append(ctx.entries[j + 1])
        if ctx.bench_tr is not None:
            bv = _values_at(ctx.bench_tr, pd.DatetimeIndex([ctx.entries[j], ctx.entries[j + 1]]))
            bench.append(bv[1] / bv[0] - 1)
        for k, f in sel.items():
            picks = [s for s in f(g) if s in sym_i]
            if not picks:
                picks = [s for s in g["symbol"] if s in sym_i]
                res[k]["fallback"] += 1
            w_t = np.zeros(len(ctx.symbols))
            w_t[[sym_i[s] for s in picks]] = 1 / len(picks)
            w_d = w_prev[k] if (w_prev[k] is not None and prev_j[k] == j - 1) else np.zeros(len(ctx.symbols))
            buys = float(np.clip(w_t - w_d, 0, None).sum())
            sells = float(np.clip(w_d - w_t, 0, None).sum())
            cost = buys * c["buy"] + sells * c["sell"]
            rr = np.where(np.isfinite(r_next), r_next, 0.0)
            res[k]["missing"] += int((~np.isfinite(r_next[w_t > 0])).sum())
            gross = float(w_t @ rr)
            net = (1 + gross) * (1 - cost) - 1
            res[k]["gross"].append(gross)
            res[k]["net"].append(net)
            res[k]["turnover"].append((buys + sells) / 2)
            res[k]["cost"].append(cost)
            w_prev[k] = w_t * (1 + rr) / (1 + gross) if 1 + gross > 0 else w_t
            prev_j[k] = j
    return {"months": month_end, "bench": np.asarray(bench, dtype=float) if bench else None, "paths": res}


def _portfolio_summary(paths: dict, market: str, split: str, bench_kind: str | None) -> dict:
    P = paths["paths"]
    T = len(paths["months"])
    out = {"months": T, "costs": COSTS[market], "strategies": {}, "diffs": [],
           "first_month": str(paths["months"][0].date()) if T else None,
           "last_month": str(paths["months"][-1].date()) if T else None,
           "note": "月頻淨值;最後一個月不扣出場成本。" + ("大盤是價格指數(不含股利),和個股含息報酬比較偏向個股。"
                                                    if bench_kind == "price" else "")}
    if not T:
        return out
    for k, v in P.items():
        st = _equity_stats(np.array(v["net"]))
        st.update({"gross_cagr": _equity_stats(np.array(v["gross"])).get("cagr"), "avg_turnover_oneway": _f(np.mean(v["turnover"])),
                   "annual_cost": _f(np.mean(v["cost"]) * 12), "fallback_months": v["fallback"], "missing_returns": v["missing"]})
        if paths["bench"] is not None:
            rel = (1 + np.array(v["net"])) / (1 + paths["bench"]) - 1
            st["rel_max_dd_vs_bench"] = _f(_max_dd(rel))
        out["strategies"][k] = st
    if paths["bench"] is not None:
        out["strategies"]["bench"] = _equity_stats(paths["bench"]) | {"kind": bench_kind}
    pairs = [("model", "universe"), ("ex_sell", "universe")]
    if paths["bench"] is not None:
        pairs += [("universe", "bench"), ("ex_sell", "bench")]
    for a, b in pairs:
        ra = np.array(P[a]["net"])
        rb = paths["bench"] if b == "bench" else np.array(P[b]["net"])
        dlt = ra - rb
        nw = newey_west(dlt, 3)
        name = f"{market}|{split}|port|{a}-{b}"
        bs = block_bootstrap(len(dlt), lambda ix, dlt=dlt: float(dlt[ix].mean() * 12), 3, name)
        out["diffs"].append({"a": a, "b": b, "ann_mean_diff": _f(dlt.mean() * 12),
                             "ci95_nw": [nw["ci"][0] * 12, nw["ci"][1] * 12] if nw else None,
                             "ci95_block3": _strip(bs)["ci"] if bs else None,
                             "verdict": _verdict(nw["ci"] if nw else None)})
    return out


def _verdict(ci) -> str:
    if not ci:
        return "樣本不足"
    if ci[0] > 0:
        return "顯著為正"
    if ci[1] < 0:
        return "顯著為負"
    return "沒有證實有效"


# ---------------- holdout 防護 ----------------

_TICKETS: set[str] = set()
_HOLDOUT_SCRIPT = (config.ROOT / "scripts" / "holdout.py").resolve()


def holdout_guard() -> None:
    """worktree 或 agent 模式(INVEST_LAB_AGENT 不是 user)一律不准算 holdout。"""
    agent = os.environ.get("INVEST_LAB_AGENT", "user") or "user"
    if config.IS_WORKTREE:
        raise PermissionError("holdout 不能在 git worktree 裡計算(只有主 checkout 的 scripts/holdout.py 可以)")
    if agent != "user":
        raise PermissionError(f"holdout 不能在 agent 模式下計算(INVEST_LAB_AGENT={agent})")


def _issue_holdout_ticket() -> str:
    """只有 scripts/holdout.py 能拿到 holdout 授權。"""
    caller = Path(inspect.stack()[1].filename).resolve()
    if caller != _HOLDOUT_SCRIPT:
        raise PermissionError(f"holdout 授權只發給 scripts/holdout.py(呼叫者:{caller.name})")
    holdout_guard()
    t = secrets.token_hex(16)
    _TICKETS.add(t)
    return t


_MODEL_EVAL = (config.ROOT / "lab" / "research" / "model_eval.py").resolve()


def _issue_model_holdout_ticket() -> str:
    """使用者模型的 holdout(模型工作台,規格 §4.2)只發給 model_eval.py;一樣不准 agent 模式與 worktree。
    model_eval 自己負責:不收出廠模型、每個模型族的紀錄、要確認。"""
    frame = inspect.stack()[1]
    caller = Path(frame.filename).resolve()
    # 打包的 App 裡模組沒有原始檔路徑,改認模組名稱(一樣只有 model_eval 拿得到)
    frozen_ok = runtime.FROZEN and frame.frame.f_globals.get("__name__") == "lab.research.model_eval"
    if caller != _MODEL_EVAL and not frozen_ok:
        raise PermissionError(f"使用者模型的 holdout 授權只發給 lab/research/model_eval.py(呼叫者:{caller.name})")
    holdout_guard()
    t = secrets.token_hex(16)
    _TICKETS.add(t)
    return t


def _check_split(split: str, ticket) -> None:
    if split == "dev":
        return
    if split not in ("holdout", "all"):
        raise ValueError("split 只能是 'dev'(預設)、'holdout'、'all'")
    if not ticket or ticket not in _TICKETS:
        raise PermissionError(f"split='{split}' 會看到 holdout,只能由 scripts/holdout.py 觸發(預設只回傳開發集)")
    holdout_guard()


# ---------------- 階段 5:holdout 上的配對比較(P3-H01 這類「只能用 holdout 檢驗」的假設) ----------------
HOLDOUT_PAIR_M = {"P3-H01": 16}     # 預先登記 §7:Holm 的 m 至少取 16(診斷時看過的版本數)
# 預先登記 §7:「PIT 前 50 為主,網站 40 為對照,兩者方向要一致」;股票池必須剛好是這兩個(Verifier40 問題 2)
HOLDOUT_PAIR_POOLS = {"P3-H01": {"main": "TW_PIT50", "control": "TW_LARGE40"}}


def holdout_paired(base: pd.DataFrame, new: pd.DataFrame, tr_index: dict, *, family: str, primary: str = "st_1m",
                   calendar=None, bench_tr=None, holdout_ticket: str | None = None, lag: int = 1) -> dict:
    """同一個 holdout 設定裡,新(假設打開)− 基準(最終設定)的主要指標月 IC 差。只能由 scripts/holdout.py 觸發(要 ticket)。
    只比雙方都有判斷的「日期 × 股票」交集;Newey–West(lag = H)95% 區間與 fixed-b p 值;Holm 用預先登記的 m(單一假設 → p × m)。"""
    _check_split("holdout", holdout_ticket)
    if family not in HOLDOUT_PAIR_M:
        raise ValueError(f"假設族「{family}」沒有登記成 holdout 配對檢驗(HOLDOUT_PAIR_M)")
    score, judge, H = PRIMARY[primary]
    rb, rn, cov = _pair_rows(base, new, score, judge)
    ctx = _Ctx(pd.concat([rb, rn]).drop_duplicates(["date", "symbol"]), tr_index, lag=lag, calendar=calendar,
               bench_tr=bench_tr, holdout_ticket=holdout_ticket)
    sb = _ic_series(ctx, rb, score, H, "holdout", judge_col=judge)
    sn = _ic_series(ctx, rn, score, H, "holdout", judge_col=judge)
    d = pd.DataFrame({"base": sb["ic"], "new": sn["ic"]}).dropna()
    d["diff"] = d["new"] - d["base"]
    nw = newey_west(d["diff"].to_numpy(), H)
    m = HOLDOUT_PAIR_M[family]
    p = nw["p"] if nw else None
    return {"family": family, "primary": primary, "n_months": int(len(d)), "ic_base": _f(d["base"].mean()),
            "ic_new": _f(d["new"].mean()), "diff": nw["mean"] if nw else None, "ci95": nw["ci"] if nw else None,
            "p": p, "holm_m": m, "p_holm": min(1.0, p * m) if p is not None else None, "coverage": cov,
            "ar1": ar1(d["diff"].to_numpy())}


def holdout_pair_verdict(results: dict, family: str) -> dict:
    """預先登記 §7 的判準(執行前依專案決定寫定):主要股票池(TW_PIT50)95% 區間下界 > 0、Holm 校正後 p < 0.05,
    而且對照股票池(TW_LARGE40)點估計同向。都成立才寫「證實」;否則「沒有證實有效」(不論點估計多大)。
    股票池必須剛好是登記的兩個,否則丟例外(不判定)。"""
    roles = HOLDOUT_PAIR_POOLS[family]
    if set(results) != set(roles.values()):
        raise ValueError(f"{family} 的股票池必須剛好是 {sorted(roles.values())},收到 {sorted(results)}")
    main, ctrl = results[roles["main"]], results[roles["control"]]
    if main.get("diff") is None or ctrl.get("diff") is None:
        return {"proven": False, "reason": "有股票池樣本不足,不判定 → 沒有證實有效"}
    same_dir = (main["diff"] > 0) == (ctrl["diff"] > 0) and main["diff"] != 0 and ctrl["diff"] != 0
    sig = bool(main.get("ci95") and main["ci95"][0] > 0 and main.get("p_holm") is not None and main["p_holm"] < 0.05)
    proven = bool(sig and same_dir)
    return {"proven": proven, "main_significant": sig, "same_direction": same_dir,
            "reason": f"{roles['main']} 95% 下界 > 0、Holm 校正後 p < 0.05,{roles['control']} 同向 → 證實" if proven
            else f"沒有同時滿足「{roles['main']} 95% 下界 > 0、Holm 校正後 p < 0.05、{roles['control']} 同向」→ 沒有證實有效"}


# ---------------- P2-H09:機率校準的 Brier 比較 ----------------

def calibration_brier(new_rows: dict, base_rows: dict, prices: dict, *, family: str = "explore", cfg: dict | None = None,
                      data_vintage: str = "", registry_path=None, lag: int = 1, min_train: int | None = None) -> dict:
    """開發集上比較兩種「12 個月跑贏股票池中位數」的機率(預先登記 P2-H09 的判準)。
    新:`calibration.walk_forward`,輸入 = new_rows 的 lt_score 當日百分位,兩市共用 a、b,判斷日只用出場日 ≤ d 的樣本。
    對照:base_rows 的 lt_level(v1.1 評等)在開發集的實際跑贏頻率(兩市合併;樣本內,對新方法較嚴格)。
    另附 walk-forward 版對照(每個判斷日只用當時已知的各評等頻率),只報告。
    判準:每月 Brier 差(新 − 對照)的 Newey–West(lag 12)95% 區間上界 < 0。只用開發集,寫登記簿。"""
    from ..analysis import calibration as CAL

    if family in prereg.HOLDOUT_ONLY:
        raise ValueError(f"假設族「{family}」只能用 holdout 或前瞻資料檢驗,不得在開發集評估")
    H, hz = 12, "12m"
    frames = []
    for m in new_rows:
        px = prices[m]
        ctx = _Ctx(pd.concat([new_rows[m], base_rows[m]]).drop_duplicates(["date", "symbol"]), px["tr_index"], lag=lag,
                   calendar=px.get("calendar"), bench_tr=px.get("bench_tr"))
        ex = _excess(ctx, hz)
        _, exit_ = _entry_exit(ctx.dates, HORIZON_DAYS[hz], lag, ctx.cal)
        exits = pd.Series(pd.DatetimeIndex(exit_), index=ctx.dates)
        nr = new_rows[m][["date", "symbol", "lt_level", "lt_score"]].copy()
        br = base_rows[m][["date", "symbol", "lt_level"]].rename(columns={"lt_level": "base_level"}).copy()
        for f in (nr, br):
            f["date"] = pd.to_datetime(f["date"])
        d = nr.merge(br, on=["date", "symbol"], how="left").merge(ex[["date", "symbol", "x"]], on=["date", "symbol"], how="left")
        d = d[d["date"].isin(ctx.dates_in(H, "dev"))]
        ok = d["lt_level"].notna() & d["lt_score"].notna()
        d["rank"] = d["lt_score"].where(ok).groupby(d["date"]).rank(pct=True)
        d["y"] = np.where(d["x"] > 0, 1.0, np.where(d["x"] < 0, 0.0, np.nan))
        d["exit"] = d["date"].map(exits)
        d["market"] = m
        frames.append(d)
    allr = pd.concat(frames, ignore_index=True)
    wf = CAL.walk_forward(allr[["date", "exit", "rank", "y"]], min_train or CAL.MIN_TRAIN)
    allr["p_new"] = wf["p"].to_numpy()
    lab = allr[allr["y"].notna() & allr["base_level"].notna()]
    freq = lab.groupby("base_level")["y"].mean()
    allr["p_base"] = allr["base_level"].map(freq)
    # walk-forward 對照:判斷日 d 只用出場日 ≤ d 的各評等頻率(只報告)
    pwf = pd.Series(np.nan, index=allr.index)
    for dd in sorted(allr["date"].unique()):
        tr = lab[lab["exit"] <= dd]
        if len(tr) >= (min_train or CAL.MIN_TRAIN):
            msk = allr["date"] == dd
            pwf[msk] = allr.loc[msk, "base_level"].map(tr.groupby("base_level")["y"].mean())
    allr["p_base_wf"] = pwf
    use = allr[allr["y"].notna() & allr["p_new"].notna() & allr["p_base"].notna()].copy()
    use["b_new"] = (use["p_new"] - use["y"]) ** 2
    use["b_base"] = (use["p_base"] - use["y"]) ** 2
    use["b_base_wf"] = (use["p_base_wf"] - use["y"]) ** 2
    use["month"] = use["date"].dt.to_period("M")
    out: dict = {"version": EVAL_VERSION, "family": family, "horizon": hz, "nw_lag": H, "markets": {},
                 "level_freq_dev": {str(k): _f(v) for k, v in freq.items()}, "min_train": min_train or CAL.MIN_TRAIN}
    series = {}
    for m, g in use.groupby("market"):
        per = g.groupby("month").agg(new=("b_new", "mean"), base=("b_base", "mean"), base_wf=("b_base_wf", "mean"))
        diff = (per["new"] - per["base"]).sort_index()
        nw = newey_west(diff.to_numpy(), H)
        series[m] = diff
        out["markets"][m] = {"n_rows": int(len(g)), "n_months": int(len(per)), "brier_new": _f(g["b_new"].mean()),
                             "brier_base": _f(g["b_base"].mean()), "brier_base_wf": _f(g["b_base_wf"].mean()),
                             "diff": nw["mean"] if nw else None, "ci95": nw["ci"] if nw else None, "p": nw["p"] if nw else None}
    pooled = pd.concat(series, axis=1).mean(axis=1).sort_index() if series else pd.Series(dtype=float)
    nwp = newey_west(pooled.to_numpy(), H)
    ab = wf.dropna(subset=["a"]).drop_duplicates("date").sort_values("date")
    out["pooled"] = {"n_months": int(len(pooled)), "diff": nwp["mean"] if nwp else None, "ci95": nwp["ci"] if nwp else None,
                     "p": nwp["p"] if nwp else None}
    out["last_fit"] = {"date": str(ab["date"].iloc[-1].date()), "a": _f(ab["a"].iloc[-1]), "b": _f(ab["b"].iloc[-1])} if len(ab) else None
    out["passed"] = bool(nwp and nwp["ci"][1] < 0)
    out["reason"] = ("Brier 差的 95% 區間上界 < 0:機率比對照好" if out["passed"]
                     else "Brier 差的 95% 區間上界 ≥ 0:沒有證實比對照好")
    full_cfg = {"eval_version": EVAL_VERSION, "kind": "calibration_brier", "family": family,
                "new": {m: rows_fingerprint(v) for m, v in new_rows.items()},
                "new_rank": {m: rank_fingerprint(v) for m, v in new_rows.items()},
                "new_rank_parts": {m: rank_parts(v) for m, v in new_rows.items()},
                "base": {m: rows_fingerprint(v) for m, v in base_rows.items()},
                "prices": {m: prices_fingerprint(v["tr_index"]) for m, v in prices.items()}, "user_cfg": cfg or {}}
    registry.log("dev", full_cfg, {"brier_diff": out["pooled"]["diff"], "ci95": out["pooled"]["ci95"], "passed": out["passed"]},
                 market="+".join(sorted(new_rows)), data_vintage=data_vintage, family=family,
                 note=out["reason"], path=registry_path)
    return out


# ---------------- 主入口 ----------------

def evaluate(rows: pd.DataFrame, tr_index: dict, *, market: str, bench_tr: pd.Series | None = None,
             bench_kind: str | None = None, calendar=None, split: str = "dev", holdout_ticket: str | None = None,
             lag: int = 1, cfg: dict | None = None, universe: str = "", data_vintage: str = "", family: str = "explore",
             registry_path=None, note: str = "", _log: bool = True) -> dict:
    """新模式評估(預設只算開發集)。rows 欄位:date, symbol, lt_level, lt_score, st_code, st_score。
    tr_index:{代號: 含息報酬指數(日)};bench_tr:大盤含息報酬指數(沒有就跳過大盤比較)。"""
    _check_split(split, holdout_ticket)
    if market not in COSTS:
        raise ValueError("market 必須是 TW 或 US")
    ctx = _Ctx(rows, tr_index, lag=lag, calendar=calendar, bench_tr=bench_tr, holdout_ticket=holdout_ticket)
    r = ctx.rows
    res: dict = {"version": EVAL_VERSION, "mode": "new", "market": market, "split": split, "lag": lag,
                 "n_rows": int(len(r)), "first_date": str(ctx.dates[0].date()), "last_date": str(ctx.dates[-1].date()),
                 "unrated_rows": int(r["lt_level"].isna().sum()),
                 "split_dates": {hz: ctx._labels(H).value_counts().to_dict() for hz, H in HORIZON_MONTHS.items()},
                 "primary": {}, "secondary": {}}
    for name, (score, judge, H) in PRIMARY.items():
        res["primary"][name] = _ic_block(ctx, r, score, H, split, judge_col=judge) | {"score": score, "judged_by": judge}
    res["secondary"]["st_3m_ic"] = _ic_block(ctx, r, "st_score", 3, split, judge_col="st_code")
    res["secondary"]["hit"] = {
        "lt_12m": _hit_rates(ctx, r, "12m", split, market, "lt"), "lt_6m": _hit_rates(ctx, r, "6m", split, market, "lt"),
        "st_3m": _hit_rates(ctx, r, "3m", split, market, "st"), "st_1m": _hit_rates(ctx, r, "1m", split, market, "st")}
    res["secondary"]["rating_12m"] = _rating_buckets(ctx, r, "12m", split)
    res["secondary"]["rating_6m"] = _rating_buckets(ctx, r, "6m", split)
    res["secondary"]["portfolio"] = _portfolio_summary(_portfolio_paths(ctx, r, market, split), market, split, bench_kind)
    res["notes"] = [
        "超額報酬 = 個股含息報酬 − 同日股票池中位數;平手(= 0)不算對也不算錯。",
        "長期指標只用有評等的列;沒有評等的列另外報告(不當中性)。",
        "主要指標是重疊世代月 IC + Newey–West(fixed-b),只用湊滿 H 個世代的月份;12 個月命中率的獨立期數少,只供溝通。",
    ]
    if split == "dev":   # 開發集一律寫登記簿(Verifier30 問題 3:_log=False 不能用來不計次試跑;_log 只對非開發集有意義)
        if family in prereg.HOLDOUT_ONLY:
            raise ValueError(f"假設族「{family}」只能用 holdout 或前瞻資料檢驗,不得在開發集評估")
        full_cfg = {"eval_version": EVAL_VERSION, "mode": "new", "market": market, "split": split, "lag": lag,
                    "rows": rows_fingerprint(rows), "rows_rank": rank_fingerprint(rows), "rows_rank_parts": rank_parts(rows), "prices": prices_fingerprint(tr_index),
                    "user_cfg": cfg or {}}
        registry.log("dev", full_cfg, {k: {"ic": v.get("ic"), "se_nw": v.get("se_nw")} for k, v in res["primary"].items()},
                     market=market, universe=universe, data_vintage=data_vintage, family=family, note=note, path=registry_path)
    return res


# ---------------- 合併閘門 ----------------

def _holm_threshold(p: float, other_ps: list[float], m: int, alpha: float) -> tuple[bool, float, int]:
    """Holm 逐步法。other_ps:同一族其他嘗試已知的 p 值;不知道 p 值的嘗試保守地當作 p = 1(排在後面)。
    回傳 (是否拒絕虛無假設, 這個檢定適用的門檻, 名次)。"""
    m = max(m, len(other_ps) + 1, 1)
    ps = sorted([q for q in other_ps if q is not None and np.isfinite(q)] + [p])
    rank = ps.index(p) + 1
    for i in range(rank):
        if ps[i] > alpha / (m - i):
            return False, alpha / (m - rank + 1), rank
    return True, alpha / (m - rank + 1), rank


def _pair_rows(base: pd.DataFrame, new: pd.DataFrame, score: str, judge: str):
    b = base.copy()
    n = new.copy()
    b["date"], n["date"] = pd.to_datetime(b["date"]), pd.to_datetime(n["date"])
    jb = b[score].notna() & (b[judge].notna() if judge in b else True)
    jn = n[score].notna() & (n[judge].notna() if judge in n else True)
    kb = set(zip(b.loc[jb, "date"], b.loc[jb, "symbol"]))
    kn = set(zip(n.loc[jn, "date"], n.loc[jn, "symbol"]))
    allk = set(zip(b["date"], b["symbol"])) | set(zip(n["date"], n["symbol"]))
    both = kb & kn
    keyb = pd.Series(list(zip(b["date"], b["symbol"])), index=b.index).isin(both)
    keyn = pd.Series(list(zip(n["date"], n["symbol"])), index=n.index).isin(both)
    cov = {"n_rows_union": len(allk), "n_base_judged": len(kb), "n_new_judged": len(kn), "n_both": len(both),
           "coverage_base": _f(len(kb) / len(allk)) if allk else None, "coverage_new": _f(len(kn) / len(allk)) if allk else None,
           "coverage_diff": _f((len(kn) - len(kb)) / len(allk)) if allk else None}
    return b[keyb], n[keyn], cov


def _paired_primary(ctx_b: _Ctx, rb, rn, score, judge, H) -> pd.DataFrame:
    """配對的主要指標月序列(只用雙方都湊滿 H 個世代的月份)。"""
    sb = _ic_series(ctx_b, rb, score, H, "dev", judge_col=judge)
    sn = _ic_series(ctx_b, rn, score, H, "dev", judge_col=judge)
    d = pd.DataFrame({"base": sb["ic"], "new": sn["ic"]}).dropna()
    d["diff"] = d["new"] - d["base"]
    return d


def _dd_pair(a: np.ndarray, b: np.ndarray, name: str, label: str, market: str) -> dict:
    """最大回撤差(新 − 舊;負 = 更深),新舊共用同一組區塊重抽索引;獨立期數不足時不判定。"""
    T = len(a)
    p_worse = ci = None
    if T and T / 3 >= MIN_INDEPENDENT:
        idx = draw_blocks(T, 3, name)
        vals = np.array([_max_dd(a[ix]) - _max_dd(b[ix]) for ix in idx])
        p_worse = float((vals >= 0).mean()) if len(vals) else None
        ci = [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]
    return {"metric": label, "market": market, "diff": _f(_max_dd(a) - _max_dd(b)) if T else None, "ci95": ci,
            "p_worse": p_worse, "decides": p_worse is not None}


def _secondary_pairs(ctx: _Ctx, rb, rn, market: str, H: int) -> list[dict]:
    """次要指標(固定清單):成本後投組差(model、ex_sell)、最大回撤、相對大盤的最大回撤、評等單調性;換手率只報告。
    每一項給「新版本比較差」的單尾 p 值,之後一起做 Holm。"""
    out = []
    pb, pn = _portfolio_paths(ctx, rb, market, "dev"), _portfolio_paths(ctx, rn, market, "dev")
    T = len(pb["months"])
    for k in ("model", "ex_sell"):
        a, b = np.array(pn["paths"][k]["net"]), np.array(pb["paths"][k]["net"])
        nw = newey_west(a - b, 3)
        out.append({"metric": f"{k} 成本後月報酬差(年化)", "market": market, "diff": _f((a - b).mean() * 12) if T else None,
                    "ci95": [nw["ci"][0] * 12, nw["ci"][1] * 12] if nw else None, "p_worse": one_sided_p(nw, "negative"),
                    "decides": True})
    a, b = np.array(pn["paths"]["ex_sell"]["net"]), np.array(pb["paths"]["ex_sell"]["net"])
    out.append(_dd_pair(a, b, f"{market}|gate|maxdd_ex_sell", "ex_sell 最大回撤差(新 − 舊,負 = 更深)", market))
    if pb["bench"] is not None and T:
        ra, rb_ = (1 + a) / (1 + pn["bench"]) - 1, (1 + b) / (1 + pb["bench"]) - 1
        out.append(_dd_pair(ra, rb_, f"{market}|gate|rel_maxdd_ex_sell", "ex_sell 相對大盤最大回撤差(新 − 舊,負 = 更深)", market))
    tb, tn = np.array(pb["paths"]["model"]["turnover"]), np.array(pn["paths"]["model"]["turnover"])
    nw = newey_west(tn - tb, 3)
    out.append({"metric": "model 月單邊換手率差", "market": market, "diff": _f((tn - tb).mean()) if T else None,
                "ci95": nw["ci"] if nw else None, "p_worse": one_sided_p(nw, "positive"), "decides": False})
    if "lt_level" in rb and rb["lt_level"].notna().any():
        mb = _ic_series(ctx, rb, "lt_level", 12, "dev", judge_col="lt_level")
        mn = _ic_series(ctx, rn, "lt_level", 12, "dev", judge_col="lt_level")
        dd = (mn["ic"] - mb["ic"]).dropna()
        nw = newey_west(dd.to_numpy(), 12)
        out.append({"metric": "評等單調性(評等的 12 個月重疊世代 IC)差", "market": market, "diff": _f(dd.mean()) if len(dd) else None,
                    "ci95": nw["ci"] if nw else None, "p_worse": one_sided_p(nw, "negative"), "decides": True})
    return out


def _holm_family(ps: list[float], alpha: float) -> list[bool]:
    """標準 Holm:回傳每個 p 值是否顯著。"""
    m = len(ps)
    order = np.argsort(ps)
    sig = [False] * m
    for i, k in enumerate(order):
        if ps[k] <= alpha / (m - i):
            sig[k] = True
        else:
            break
    return sig


def _gate_cfg(pairs: dict, spec: dict) -> dict:
    return {"eval_version": EVAL_VERSION, "change_type": spec.get("change_type"), "primary": spec.get("primary", "lt_12m"),
            "family": spec.get("family", "explore"),
            "base": {m: rows_fingerprint(v[0]) for m, v in pairs.items()},
            "new": {m: rows_fingerprint(v[1]) for m, v in pairs.items()},
            "prices": {m: prices_fingerprint(v[2]["tr_index"]) for m, v in pairs.items()}, "user_cfg": spec.get("cfg", {})}


_PAIRED = object()   # paired_gate 專用的通行證:正式閘門只能經由 paired_gate(Verifier21 問題 2)

# RedTeam2 問題 2:閘門校準不能變成「不計次的試跑」。gate_core(_calibration=True) 只能在 calibration_session 裡呼叫,
# session 結束時把每一次校準評估過的新分數指紋寫進登記簿(kind = replay、family = calibration);
# paired_gate 算 m 時,同一份新分數在 explore 或校準跑過的次數一併計入(見 pretrial_count)。
_CALIB: dict | None = None


@contextlib.contextmanager
def calibration_session(label: str, *, registry_path=None, note: str = ""):
    """閘門校準的唯一入口(scripts/gate.py calibrate 使用)。離開時寫一筆登記簿,含評估次數與新分數指紋。"""
    global _CALIB
    if _CALIB is not None:
        raise ValueError("calibration_session 不能巢狀使用")
    _CALIB = {"label": label, "n_evals": 0, "new_fps": {}, "new_parts": {}}
    try:
        yield _CALIB
    finally:
        sess, _CALIB = _CALIB, None
        fps = {m: sorted(v) for m, v in sess["new_fps"].items()}
        parts = {m: {k: sorted(v) for k, v in d.items()} for m, d in sess["new_parts"].items()}
        registry.log("replay", {"calibration_session": label, "eval_version": EVAL_VERSION, "new_rank_fps": fps,
                                "new_rank_parts": parts},
                     {"n_evals": sess["n_evals"], "n_unique_new": {m: len(v) for m, v in fps.items()}},
                     market="+".join(sorted(fps)), family="calibration",
                     note=note or "閘門校準 session(新分數指紋計入同指紋閘門的 m)", path=registry_path)


def pretrial_count(new_rows: dict, family: str, path=None, primary: str = "lt_12m") -> dict:
    """同一份新分數在正式閘門之前,在其他地方試跑過幾次(RedTeam2 問題 2、Verifier30 問題 1、2、4、5 與複驗的低問題)。
    - 比對用主要指標那一組(判斷欄 + 分數排名)的排名指紋:單調轉換、或只改主要指標用不到的欄,都仍算同一份。
      舊紀錄沒有分組指紋時,退回整份排名指紋,再退回精確指紋。
    - 開發集紀錄:目標族以外的**任何**族都算;依市場分別數不重複設定,取各市場最大值(和 family_trials 一致)。
    - 校準 session:含這份分數的 session 每個算一次。
    回傳 {"other_dev": 不重複設定數(各市場最大值), "calibration": session 數}。"""
    part = "lt" if PRIMARY[primary][0] == "lt_score" else "st"
    want = {m: (rank_fingerprint(v, part), rank_fingerprint(v), rows_fingerprint(v)) for m, v in new_rows.items()}

    def hit(m, part_fp=None, rank_fp=None, exact_fp=None):
        w = want.get(m)
        return bool(w) and ((part_fp is not None and part_fp == w[0]) or (rank_fp is not None and rank_fp == w[1])
                            or (exact_fp is not None and exact_fp == w[2]))

    dev = {m: set() for m in want}
    calib = 0
    for rec in registry.read(path):
        c = rec.get("config") or {}
        if rec.get("kind") == "dev" and rec.get("family", "explore") != family:
            if c.get("rows"):
                m = rec.get("market")
                if hit(m, (c.get("rows_rank_parts") or {}).get(part), c.get("rows_rank"), c.get("rows")):
                    dev[m].add(rec["config_hash"])
            elif isinstance(c.get("new"), dict):
                ranks, parts = c.get("new_rank") or {}, c.get("new_rank_parts") or {}
                for m, fp in c["new"].items():
                    if hit(m, (parts.get(m) or {}).get(part), ranks.get(m), fp):
                        dev[m].add(rec["config_hash"])
        elif rec.get("kind") == "replay" and rec.get("family") == "calibration":
            parts = c.get("new_rank_parts") or {}
            fps = c.get("new_rank_fps") or {}
            old = c.get("new_fps") or {}
            if (any(hit(m, part_fp=fp) for m, d in parts.items() for fp in (d.get(part) or []))
                    or any(hit(m, rank_fp=fp) for m, v in fps.items() for fp in v)
                    or any(hit(m, exact_fp=fp) for m, v in old.items() for fp in v)):
                calib += 1
    return {"other_dev": max([len(v) for v in dev.values()] or [0]), "calibration": calib}


def gate_core(pairs: dict, spec: dict, *, ctxs: dict | None = None, secondary: bool = True, _calibration: bool = False,
              _token=None, _trials: dict | None = None) -> dict:
    """閘門的計算核心。pairs:{市場: (base_rows, new_rows, prices dict)}。
    只能由 paired_gate(帶 _token)或閘門校準(_calibration=True,必須在 calibration_session 裡,每次評估的新分數指紋
    由 session 寫進登記簿)呼叫;直接呼叫會繞過預先登記與次數檢查,所以報錯。正式閘門每次都寫登記簿(kind = gate)。"""
    if not _calibration and _token is not _PAIRED:
        raise ValueError("gate_core 不能直接呼叫:正式閘門一律用 paired_gate(會檢查預先登記、實際 m 與次數上限)")
    if _calibration:
        if _CALIB is None:
            raise ValueError("閘門校準必須在 calibration_session 裡呼叫(RedTeam2 問題 2:校準也要寫登記簿)")
        _CALIB["n_evals"] += 1
        for mkt, (_, new_rows, _) in pairs.items():
            _CALIB["new_fps"].setdefault(mkt, set()).add(rank_fingerprint(new_rows))
            for k, fp in rank_parts(new_rows).items():
                _CALIB["new_parts"].setdefault(mkt, {}).setdefault(k, set()).add(fp)
    ct = spec.get("change_type")
    if ct not in ("add", "fix"):
        raise ValueError("spec['change_type'] 必須是 'add'(增加特徵 / 參數)或 'fix'(簡化 / 修錯)")
    prim = spec.get("primary", "lt_12m")
    score, judge, H = PRIMARY[prim]
    alpha = spec.get("alpha", 0.05)
    margin = spec.get("margin", FIX_MARGIN[prim])
    family = spec.get("family", "explore")
    n_trials = max(1, int(spec.get("n_trials", 1)))
    other_ps = list(spec.get("family_pvalues", []))
    min_months = MIN_GATE_MONTHS.get(H, 24)
    reasons, warnings, per_mkt, diffs, cov, sec = [], [], {}, {}, {}, []
    for mkt, (base, new, px) in pairs.items():
        rb, rn, cv = _pair_rows(base, new, score, judge)
        cov[mkt] = cv
        if cv["coverage_diff"] is not None and cv["coverage_diff"] < -COVERAGE_DROP_WARN:
            warnings.append(f"{mkt} 覆蓋率大降 {cv['coverage_diff'] * 100:.1f} 個百分點(舊 {cv['coverage_base'] * 100:.1f}% → "
                            f"新 {cv['coverage_new'] * 100:.1f}%):閘門只比交集,少判斷的列不在比較裡")
        if ctxs and mkt in ctxs:
            ctx = ctxs[mkt]
        else:
            ctx = _Ctx(pd.concat([rb, rn]).drop_duplicates(["date", "symbol"]), px["tr_index"], lag=spec.get("lag", 1),
                       calendar=px.get("calendar"), bench_tr=px.get("bench_tr"))
        d = _paired_primary(ctx, rb, rn, score, judge, H)
        nw = newey_west(d["diff"].to_numpy(), H)
        per_mkt[mkt] = {"n_months": int(len(d)), "ic_base": _f(d["base"].mean()), "ic_new": _f(d["new"].mean()),
                        "diff": _f(d["diff"].mean()), "se_nw": nw["se"] if nw else None, "p": nw["p"] if nw else None,
                        "ci95": nw["ci"] if nw else None}
        diffs[mkt] = d["diff"].set_axis(ctx.decision_months(d.index))  # 判斷日月份(見 _Ctx.decision_months)
        if secondary:
            sec += _secondary_pairs(ctx, rb, rn, mkt, H)
    pooled_ser = pd.concat(diffs, axis=1).dropna().mean(axis=1) if diffs else pd.Series(dtype=float)
    nwp = newey_west(pooled_ser.to_numpy(), H, alpha)
    phi = ar1(pooled_ser.to_numpy())
    if phi is not None and phi > AR1_WARN:          # Red-team M5:差異序列高度自相關時,NW 標準誤可能仍偏低
        warnings.append(f"差異序列的一階自相關 φ̂ = {phi:.2f} > {AR1_WARN}:標準誤可能低估,區間要保守解讀")
    out = {"change_type": ct, "primary": prim, "family": family, "nw_lag": H, "markets": per_mkt, "coverage": cov,
           "pooled": {"n_months": int(len(pooled_ser)), "diff": nwp["mean"] if nwp else None, "se_nw": nwp["se"] if nwp else None,
                      "p": nwp["p"] if nwp else None, "ci95": nwp["ci"] if nwp else None, "ar1": phi},
           "note": "主要指標只用新舊雙方都湊滿 H 個世代的月份"}
    if _trials:
        out["trials"] = dict(_trials)   # 報告顯示實際 m(Red-team M2)
    half = len(pooled_ser) // 2
    sub = [_f(pooled_ser.iloc[:half].mean()), _f(pooled_ser.iloc[half:].mean())] if half else [None, None]
    out["subperiods"] = {"first": {"from": str(pooled_ser.index[0]) if half else None, "diff": sub[0]},
                         "second": {"to": str(pooled_ser.index[-1]) if half else None, "diff": sub[1]}}
    passed = True
    if not nwp or len(pooled_ser) < min_months:
        passed = False
        reasons.append(f"配對後的開發集滿世代月數 {len(pooled_ser)} < {min_months},樣本不足,不能算通過")
    if ct == "add":
        rej, thr, rank = _holm_threshold(nwp["p"] if nwp and nwp["p"] is not None else 1.0, other_ps, n_trials, alpha) \
            if nwp else (False, alpha / n_trials, 1)
        out["holm"] = {"family": family, "n_trials": n_trials, "known_family_p": len(other_ps), "rank": rank, "alpha_adjusted": thr,
                       "m_effective": max(n_trials, len(other_ps) + 1, 1)}   # RedTeam2 問題 6:實際用的 m
        if nwp and nwp["se"] > 0:
            q = crit_value(nwp["n"], nwp["lag"], thr)
            out["pooled"]["ci_holm"] = [nwp["mean"] - q * nwp["se"], nwp["mean"] + q * nwp["se"]]
        if family in ("explore", "baseline"):
            passed = False
            reasons.append(f"假設族是「{family}」:增加特徵 / 參數必須屬於預先登記的假設族(spec['family'],例如 P2-H03)")
        if not (nwp and nwp["mean"] > 0 and rej):
            passed = False
            reasons.append(f"主要指標差異沒有在 Holm 校正後顯著為正(p = {out['pooled']['p']},門檻 {thr:.4g})")
        if len(per_mkt) < 2 and not spec.get("single_market_ok"):
            passed = False
            reasons.append("只有一個市場,無法檢查台股 / 美股方向一致(需要 spec['single_market_ok'] 並說明理由)")
        bad = [m for m, v in per_mkt.items() if not (v["diff"] is not None and v["diff"] > 0)]
        if bad:
            passed = False
            reasons.append(f"市場方向不一致:{', '.join(bad)} 的差異不是正的")
        if not all(s is not None and s > 0 for s in sub):
            passed = False
            reasons.append(f"兩個子期間方向不一致(前半 {sub[0]},後半 {sub[1]})")
    else:
        lo = nwp["ci"][0] if nwp else None
        out["noninferiority"] = {"margin": margin, "pooled_ci95_lower": lo}
        if lo is None or lo < -margin:
            passed = False
            reasons.append(f"合併差異 95% 下界 {lo} < −{margin}(可能更差)")
        bad = [m for m, v in per_mkt.items() if v["diff"] is None or v["diff"] < -margin]
        if bad:
            passed = False
            reasons.append(f"{', '.join(bad)} 的差異點估計低於 −{margin}")
    if secondary and sec:
        dec = [s for s in sec if s["decides"] and s["p_worse"] is not None]
        sig = _holm_family([s["p_worse"] for s in dec], alpha) if dec else []
        for s, g in zip(dec, sig):
            s["significantly_worse_holm"] = bool(g)
            if g:
                passed = False
                reasons.append(f"次要指標顯著變差(Holm):{s['market']} {s['metric']}")
    out["secondary"] = sec
    out["warnings"] = warnings
    out["passed"] = bool(passed)
    if passed:
        reasons.append("通過" + ("(證明更好)" if ct == "add" else "(不更差)"))
    elif ct == "add" and nwp and nwp["ci"][0] <= 0 <= nwp["ci"][1]:
        reasons.append("改善落在信賴區間內:沒有證實有效")
    out["reasons"] = reasons
    out["version"] = EVAL_VERSION
    if not _calibration:
        cfg = _gate_cfg(pairs, spec)
        out["config_hash"] = registry.config_hash(cfg)
        registry.log("gate", cfg, {"passed": out["passed"], "p_primary": out["pooled"]["p"], "diff": out["pooled"]["diff"],
                                   "change_type": ct, "primary": prim},
                     market="+".join(sorted(pairs)), data_vintage=spec.get("data_vintage", ""), family=family,
                     note="; ".join(reasons)[:500], path=spec.get("registry_path"))
    return out


def family_trials(markets, family: str, since: str | None = None, path=None) -> int:
    """這個假設族在這些市場已經有幾個不重複的開發集設定(取各市場的最大值)。"""
    return max([registry.n_trials(market=m, family=family, kind="dev", since=since, path=path) for m in markets] or [0])


def paired_gate(base_rows, new_rows, spec: dict) -> dict:
    """合併閘門(docs/架構.md §3.2)。base_rows / new_rows:{市場: DataFrame}(或單一 DataFrame 搭配 spec['market'])。
    spec:change_type('add' / 'fix')、prices({市場: v11_prices() 格式的 dict})、primary(預設 lt_12m)、family(假設族,
    預設 explore)、alpha、margin、n_trials(預設 = 登記簿裡該族的開發集設定數;手動指定不得更少)、trials_since、
    single_market_ok、cfg、registry_path、data_vintage。
    只用開發集;只比雙方都有判斷的「日期 × 股票」交集,覆蓋率差異另報。"""
    if isinstance(base_rows, pd.DataFrame):
        mk = spec["market"]
        base_rows, new_rows = {mk: base_rows}, {mk: new_rows}
    prices = spec["prices"]
    pairs = {m: (base_rows[m], new_rows[m], prices[m]) for m in base_rows}
    rp = spec.get("registry_path")
    spec = dict(spec)
    if spec.get("change_type") == "add":
        feature_keys = spec.get("feature_keys")
        if not isinstance(feature_keys, list) or not feature_keys or not all(isinstance(k, str) and k.strip() for k in feature_keys):
            raise ValueError("add 閘門必須提供非空的 feature_keys 清單")
        if spec.get("tamper_passed") is not True:
            raise ValueError("add 閘門必須提供 tamper_passed=True 的竄改測試通過紀錄")
    family = spec.setdefault("family", "explore")
    prim = spec.get("primary", "lt_12m")
    # Red-team M2:族名必須預先登記且已核准;trials_since 只限維護者;每族最多 3 個不同設定
    reg_h = prereg.check(family, spec.get("change_type"), prim, pairs)
    if family in prereg.HOLDOUT_ONLY:
        raise ValueError(f"假設族「{family}」是看過開發集才登記的,只能用 holdout 或前瞻資料檢驗,開發集閘門不算證據")
    if reg_h is not None:
        # Verifier21 問題 1:預先登記的族不得放寬統計門檻(這些參數不在設定 hash 裡,換了也會被當成同一個設定)
        if spec.get("alpha", GATE_ALPHA) != GATE_ALPHA:
            raise ValueError(f"預先登記的假設族只能用 alpha = {GATE_ALPHA}")
        if "margin" in spec and spec["margin"] != FIX_MARGIN[prim]:
            raise ValueError(f"預先登記的假設族只能用預設非劣性門檻 {FIX_MARGIN[prim]}(evaluate.FIX_MARGIN)")
        if "family_pvalues" in spec:
            raise ValueError("預先登記的假設族不得手動指定 family_pvalues(一律讀登記簿)")
        if spec.get("single_market_ok") and len(reg_h["markets"]) > 1:
            raise ValueError(f"假設族「{family}」登記為兩市,不得用 single_market_ok")
    since = spec.get("trials_since")
    if since:
        agent = os.environ.get("INVEST_LAB_AGENT", "user")
        if agent != prereg.CHIEF_AGENT:
            raise ValueError(f"trials_since 只限維護者使用(INVEST_LAB_AGENT = {prereg.CHIEF_AGENT});目前是「{agent}」")
        if not str(spec.get("trials_since_reason", "")).strip():
            raise ValueError("使用 trials_since 必須附 trials_since_reason(寫進閘門報告)")
    actual = family_trials(sorted(pairs), family, since, rp)
    pre = pretrial_count({m: v[1] for m, v in pairs.items()}, family, rp, prim)
    # RedTeam2 問題 2:同一份新分數在別處的試跑也算嘗試;正式閘門本身至少算 1 次(Verifier30 問題 5)
    floor = max(1, actual) + pre["other_dev"] + pre["calibration"]
    manual = "n_trials" in spec
    if manual:
        if int(spec["n_trials"]) < floor:
            raise ValueError(f"n_trials = {spec['n_trials']} 小於登記簿裡的實際嘗試次數 {floor}"
                             f"(假設族「{family}」max(1, {actual}) + 同一份分數的其他開發集評估 {pre['other_dev']} + 校準 {pre['calibration']})")
    else:
        spec["n_trials"] = max(1, floor)
    me_hash = registry.config_hash(_gate_cfg(pairs, spec))
    prior = {rec["config_hash"] for rec in registry.read(rp)
             if rec.get("kind") == "gate" and rec.get("family", "explore") == family} - {me_hash}
    if family not in prereg.UNREGISTERED and len(prior) >= prereg.MAX_GATE_CONFIGS:
        raise ValueError(f"假設族「{family}」已經送過 {len(prior)} 個不同設定的正式閘門,上限 {prereg.MAX_GATE_CONFIGS} 次")
    trials = {"family": family, "actual_dev_configs": actual, "pretrials": pre, "m_floor": floor,
              "m_used": int(spec["n_trials"]), "manual": manual,
                       "since": since, "since_reason": spec.get("trials_since_reason") if since else None,
                       "gate_configs_before": len(prior), "gate_configs_max": prereg.MAX_GATE_CONFIGS}
    # Verifier21 問題 3:標出用的是哪一個登記簿;不是主登記簿就不是正式閘門
    reg_path = Path(rp or config.REGISTRY_FILE).resolve()
    trials["registry"] = str(reg_path)
    # RedTeam2 問題 3:預先登記清單被替換(測試的 monkeypatch)時也不是正式閘門
    trials["registry_official"] = reg_path == OFFICIAL_REGISTRY and prereg.PATH is None
    trials["prereg_source"] = "main:" + prereg.REL if prereg.PATH is None else str(prereg.PATH)
    if "family_pvalues" not in spec:
        me = registry.config_hash(_gate_cfg(pairs, spec))
        prim = spec.get("primary", "lt_12m")
        seen, ps = {me}, []
        for rec in registry.read(rp):
            m = rec.get("metrics") or {}
            c = rec.get("config") or {}
            if (rec.get("kind") != "gate" or rec["config_hash"] in seen or m.get("p_primary") is None
                    or rec.get("family", "explore") != family or c.get("change_type") != spec["change_type"]
                    or c.get("primary", "lt_12m") != prim or (since and rec.get("time", "") < since)):
                continue
            seen.add(rec["config_hash"])
            ps.append(m["p_primary"])
        spec["family_pvalues"] = ps
    return gate_core(pairs, spec, _token=_PAIRED, _trials=trials)


# ---------------- 舊模式(v1.1 定義,只用來重現基準) ----------------

def _legacy_fwd(adj: pd.Series, d, n: int) -> float:
    i = adj.index.searchsorted(d, side="right") - 1
    if i < 0 or i + n >= len(adj):
        return np.nan
    a, b = adj.iloc[i], adj.iloc[i + n]
    return float(b / a - 1) if a and b and not math.isnan(a) and not math.isnan(b) else np.nan


def _legacy_block_ci(n: int, stat_on_idx, L: int, rng) -> tuple | None:
    if n < 2 * L or n < 3:
        return None
    vals = []
    for _ in range(BOOT_DRAWS):
        starts = rng.integers(0, n, size=math.ceil(n / L))
        idx = np.concatenate([(np.arange(s, s + L) % n) for s in starts])[:n]
        v = stat_on_idx(idx)
        if v is not None and not np.isnan(v):
            vals.append(v)
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))) if vals else None


def _legacy_naive_ci(r: int, w: int) -> tuple | None:
    n = r + w
    if not n:
        return None
    p = r / n
    se = math.sqrt(p * (1 - p) / n)
    return (p - 1.96 * se, p + 1.96 * se)


def _legacy_split(d: pd.DataFrame, split: str, months: int) -> pd.DataFrame:
    if split == "dev":
        return d[d["date"] + pd.DateOffset(months=months) <= splits.HOLDOUT_START]
    if split == "holdout":
        return d[d["date"] >= splits.HOLDOUT_START]
    return d


def _rows_file(rows_dir: Path, market: str) -> Path:
    for name in (f"backtest_{market}_rows.csv", f"backtest_{market}_rows.csv.gz"):
        if (rows_dir / name).exists():
            return rows_dir / name
    raise FileNotFoundError(rows_dir / f"backtest_{market}_rows.csv")


def _legacy_analyze(market: str, rows_dir: Path, cache: Path, rng, full: bool = True) -> dict:
    """full=False(不是凍結基準的逐筆檔):只算開發集,不算全期 / holdout、全期評等分組與投組(那些會看到 holdout)。"""
    syms, bench, allsyms = _universe(market)
    px = load_bt_px(cache, allsyms)
    df = pd.read_csv(_rows_file(rows_dir, market))
    df["date"] = pd.to_datetime(df["date"])
    pil = pd.DataFrame([ast.literal_eval(p) for p in df["pillars"]]).add_prefix("p_")
    df = pd.concat([df.reset_index(drop=True), pil], axis=1)
    adj = {s: (px[s]["Adj Close"] if "Adj Close" in px[s] else px[s]["Close"]).dropna() for s in syms if s in px}
    for k, n in HORIZON_DAYS.items():
        df[f"r_{k}"] = [_legacy_fwd(adj[s], d, n) if s in adj else np.nan for s, d in zip(df["symbol"], df["date"])]
        df[f"x_{k}"] = df[f"r_{k}"] - df.groupby("date")[f"r_{k}"].transform("median")

    out: dict = {"n_rows": int(len(df)), "first_date": str(df["date"].min().date()), "last_date": str(df["date"].max().date())}
    for split in (("all", "dev", "holdout") if full else ("dev",)):
        res = {}
        for hz in ("12m", "6m", "3m", "1m"):
            L = HORIZON_MONTHS[hz]
            d = _legacy_split(df[df[f"x_{hz}"].notna()], split, L)
            if d.empty:
                continue
            by = {k: g for k, g in d.groupby("date")}
            dates = sorted(by)
            lt = hz in ("12m", "6m")
            col = "lt_level" if lt else "st_code"
            score = "lt_score" if lt else "st_score"
            per = []
            for dd in dates:
                g = by[dd]
                bull = g[g[col] >= 3] if lt else g[g[col] > 0]
                bear = g[g[col] <= 1] if lt else g[g[col] < 0]
                r = int((bull[f"x_{hz}"] > 0).sum() + (bear[f"x_{hz}"] < 0).sum())
                w = int((bull[f"x_{hz}"] <= 0).sum() + (bear[f"x_{hz}"] >= 0).sum())
                x = g[[score, f"x_{hz}"]].dropna()
                ic = x[score].rank().corr(x[f"x_{hz}"].rank()) if len(x) >= 10 and x[score].nunique() > 1 else np.nan
                spr = np.nan
                if len(x) >= 12:
                    q = x[score].rank(pct=True)
                    spr = float(x[q > 2 / 3][f"x_{hz}"].mean() - x[q <= 1 / 3][f"x_{hz}"].mean())
                per.append((r, w, ic, spr))
            P = np.array(per, dtype=float)
            n = len(P)
            R, W = int(P[:, 0].sum()), int(P[:, 1].sum())

            def hit(idx, P=P):
                rr, ww = P[idx, 0].sum(), P[idx, 1].sum()
                return rr / (rr + ww) if rr + ww else np.nan

            def mean_col(c, P=P):
                return lambda idx: float(np.nanmean(P[idx, c])) if np.isfinite(P[idx, c]).any() else np.nan

            ics = P[:, 2][np.isfinite(P[:, 2])]
            res[hz] = {
                "signal": col, "right": R, "wrong": W, "hit": R / (R + W) if R + W else None,
                "hit_ci95_block": _legacy_block_ci(n, hit, L, rng), "hit_ci95_naive_iid": _legacy_naive_ci(R, W),
                "n_months": n, "independent_periods": round(n / L, 1), "first": str(dates[0].date()), "last": str(dates[-1].date()),
                "ic_score": score, "ic_mean": float(ics.mean()) if len(ics) else None,
                "ic_ci95_block": _legacy_block_ci(n, mean_col(2), L, rng),
                "ic_t_naive": float(ics.mean() / ics.std() * math.sqrt(len(ics))) if len(ics) > 2 and ics.std() > 0 else None,
                "tercile_spread": float(np.nanmean(P[:, 3])) if np.isfinite(P[:, 3]).any() else None,
                "tercile_spread_ci95_block": _legacy_block_ci(n, mean_col(3), L, rng),
            }
        out[split] = res
    if not full:
        for c in [c for c in df.columns if c.startswith("p_")] + ["upside", "lt_score", "st_score"]:
            for hz in ("3m", "12m"):
                d = _legacy_split(df[df[f"x_{hz}"].notna()], "dev", HORIZON_MONTHS[hz])
                ics = [g[[c, f"x_{hz}"]].dropna() for _, g in d.groupby("date")]
                ics = [x[c].rank().corr(x[f"x_{hz}"].rank()) for x in ics if len(x) >= 10 and x[c].nunique() > 1]
                if len(ics) >= 6:
                    a = np.array(ics)
                    out.setdefault("pillar_ic", []).append({"key": c.replace("p_", ""), "horizon": hz, "split": "dev",
                                                            "ic": float(a.mean()), "n_months": len(a)})
        out["restricted"] = "不是凍結的 v1.1 基準逐筆檔:只回傳開發集(全期、holdout、全期評等分組、投組都不算)"
        return out

    lv = LT_NAMES
    buckets = []
    for k in (4, 3, 2, 1, 0):
        g = df[(df["lt_level"] == k) & df["x_12m"].notna()]
        if len(g):
            buckets.append({"rating": lv[k], "n": int(len(g)), "win": float((g["x_12m"] > 0).mean()),
                            "mean_x12": float(g["x_12m"].mean()), "median_x12": float(g["x_12m"].median())})
    out["rating_buckets_12m"] = buckets

    pil_out = []
    for c in [c for c in df.columns if c.startswith("p_")] + ["upside", "lt_score", "st_score"]:
        for hz in ("3m", "12m"):
            for split in ("all", "dev"):
                d = _legacy_split(df[df[f"x_{hz}"].notna()], split, HORIZON_MONTHS[hz])
                ics = []
                for _, g in d.groupby("date"):
                    x = g[[c, f"x_{hz}"]].dropna()
                    if len(x) >= 10 and x[c].nunique() > 1:
                        ics.append(x[c].rank().corr(x[f"x_{hz}"].rank()))
                if len(ics) >= 6:
                    a = np.array(ics)
                    pil_out.append({"key": c.replace("p_", ""), "horizon": hz, "split": split, "ic": float(a.mean()),
                                    "n_months": len(a), "t_naive": float(a.mean() / a.std() * math.sqrt(len(a))) if a.std() > 0 else None,
                                    "ci95_block": _legacy_block_ci(len(a), lambda idx, a=a: float(a[idx].mean()), HORIZON_MONTHS[hz], rng)})
    out["pillar_ic"] = pil_out

    bt = json.loads((rows_dir / f"backtest_{market}.json").read_text(encoding="utf-8"))
    curve = pd.DataFrame(bt["portfolio"]["curve"]).set_index("date")
    rets = curve.pct_change()
    rets.iloc[0] = curve.iloc[0] - 1
    yrs = len(curve) / 12
    stats = {}
    for k in curve.columns:
        s = curve[k]
        r = rets[k]
        stats[k] = {"cagr": float(s.iloc[-1] ** (1 / yrs) - 1), "max_dd": float((s / s.cummax() - 1).min()),
                    "sharpe": float(r.mean() / r.std() * math.sqrt(12)) if r.std() > 0 else None}
    diffs = []
    for a, b in (("ex_sell", "universe"), ("model", "universe"), ("model_st", "universe"), ("ex_sell", "bench"), ("universe", "bench")):
        dlt = (rets[a] - rets[b]).to_numpy()
        hold = (rets[a] - rets[b])[rets.index >= "2024-11"].to_numpy()
        diffs.append({"a": a, "b": b, "ann_mean_diff": float(dlt.mean() * 12),
                      "ci95_block3": _legacy_block_ci(len(dlt), lambda idx, dlt=dlt: float(dlt[idx].mean() * 12), 3, rng),
                      "holdout_ann_mean_diff": float(hold.mean() * 12) if len(hold) else None, "holdout_months": int(len(hold))})
    out["portfolio"] = {"stats": stats, "diffs": diffs, "avg_turnover_model": bt["portfolio"].get("avg_turnover"),
                        "cost_per_turnover": bt["portfolio"].get("cost"),
                        "note": f"大盤 {bench} 是價格指數(不含股利);個股用還原權值(含股利)報酬,對大盤的比較偏向個股"}
    out["brier"] = {"value": None, "reference_coin_flip": 0.25, "note": "v1.1 只輸出評等、沒有機率,無法計算 Brier"}
    return out


LEGACY_SEED = 20261015


BASELINE_MANIFEST = config.ROOT / "tests" / "baseline" / "data_manifest.json"


def _rows_sha256(path: Path) -> str:
    import gzip
    b = path.read_bytes()
    if path.suffix == ".gz":
        b = gzip.decompress(b)
    return hashlib.sha256(b).hexdigest()


def is_v11_baseline(rows_dir) -> bool:
    """逐筆檔(解壓後)的 SHA-256 是否等於 tests/baseline/data_manifest.json 記的 v1.1 基準值。"""
    try:
        man = json.loads(BASELINE_MANIFEST.read_text(encoding="utf-8"))["rows_csv_sha256"]
        return all(_rows_sha256(_rows_file(Path(rows_dir), m)) == man[m] for m in ("TW", "US"))
    except (OSError, KeyError, ValueError):
        return False


def legacy_metrics(rows_dir, cache_dir=None, *, registry_path=None, _log: bool = True) -> dict:
    """舊模式:v1.1 的定義(同日成交、平手算錯、日曆月切分、單一亂數產生器依序使用),
    結果必須和 tests/baseline/metrics_v1.1.json 完全一致。
    只有逐筆檔正是凍結的 v1.1 基準(SHA-256 對得上 data_manifest.json)才算全期與 holdout(那是已凍結、已記在
    holdout 日誌的基準數字,不是新的嘗試);其他逐筆檔一律只回傳開發集。"""
    rows_dir = Path(rows_dir).resolve()
    cache = Path(cache_dir or config.CACHE_DIR).resolve()
    full = is_v11_baseline(rows_dir)
    rng = np.random.default_rng(LEGACY_SEED)
    res = {"holdout_start": str(splits.HOLDOUT_START.date()), "bootstrap_draws": BOOT_DRAWS, "seed": LEGACY_SEED,
           "markets": {m: _legacy_analyze(m, rows_dir, cache, rng, full) for m in ("TW", "US")}}
    res = json.loads(json.dumps(res, ensure_ascii=False))  # tuple → list,與基準檔格式一致
    if _log:
        cfg = {"eval_version": EVAL_VERSION, "mode": "legacy", "rows": {m: hashlib.sha256(_rows_file(rows_dir, m).read_bytes()).hexdigest()[:16]
                                                                         for m in ("TW", "US")}}
        registry.log("replay", cfg, {m: {"dev_12m_hit": v["dev"]["12m"]["hit"]} for m, v in res["markets"].items()},
                     market="TW+US", family="baseline", note="舊模式重現 v1.1 基準" if full else "舊模式(非基準檔,只算開發集)",
                     path=registry_path)
    return res
