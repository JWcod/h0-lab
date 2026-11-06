"""竄改測試工具:把判斷日 d「之後」的資料全部亂改,當下的決策必須完全不變。

用法(每條特徵管線、模型擬合、門檻選擇都要有一個這種測試):

    from lab.pit.tamper import assert_pit_invariant
    assert_pit_invariant(lambda view: my_feature(view, "2330"), store, "2022-06-30")

會被竄改的東西(只動 d 之後):
- facts:刪除未來列、改寫未來數值、加入假的重編與假的未來期間
- prices:刪除未來列、改寫未來價格 / 成交量 / 股利、加入假的未來交易日
- events:刪除未來公司行動、加入假的未來分割 / 配股
如果特徵偷看未來(讀磁碟快取、用了今天的參數，或取得未截斷的資料…),結果就會變,測試就會失敗。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import PITStore


def tamper(store: PITStore, d, seed: int = 0) -> PITStore:
    d = pd.Timestamp(d)
    rng = np.random.default_rng(seed)
    f = store.facts.copy()
    fut = f["available_at"] > d
    f.loc[fut, "value"] = f.loc[fut, "value"] * rng.uniform(0.2, 5.0, fut.sum()) * rng.choice([-1, 1], fut.sum())
    if fut.any():
        # 刪除部分未來財報，避免測試只涵蓋「改值」而漏掉未來列是否存在的洩漏。
        f = f.drop(index=f.index[fut][::2])
    if len(f):
        # 假的重編:對 d 以前已公布的期間,在 d 之後再「公布」一個完全不同的數字
        past = f[f["available_at"] <= d]
        if len(past):
            fake = past.sample(min(len(past), 50), random_state=seed).copy()
            fake["available_at"] = d + pd.Timedelta(days=int(rng.integers(1, 400)))
            fake["value"] = fake["value"] * rng.uniform(-3, 3, len(fake))
            fake["source"] = "tamper"
            f = pd.concat([f, fake], ignore_index=True)
            # 假的未來期間，抓得到直接查看未來 period_end 的特徵。
            future_period = past.iloc[[0]].copy()
            future_period["period_end"] = max(pd.Timestamp(d), pd.to_datetime(f["period_end"]).max()) + pd.DateOffset(years=1)
            future_period["available_at"] = pd.Timestamp(d) + pd.Timedelta(days=int(rng.integers(1, 400)))
            future_period["filed"] = future_period["available_at"]
            future_period["basis_at"] = future_period["available_at"]
            future_period["value"] = future_period["value"] * rng.uniform(-3, 3)
            future_period["source"] = "tamper"
            f = pd.concat([f, future_period], ignore_index=True)
    prices = {}
    for ent, p in store.prices.items():
        q = p.copy()
        m = q.index > d
        if m.any():
            walk = np.exp(np.cumsum(rng.normal(0, 0.05, m.sum())))
            for c in ("open", "high", "low", "close"):
                if c in q:
                    q.loc[m, c] = q.loc[m, c] * walk
            if "volume" in q:
                q.loc[m, "volume"] = rng.permutation(q.loc[m, "volume"].to_numpy())
            if "cash_div" in q:
                q.loc[m, "cash_div"] = np.where(rng.random(m.sum()) < 0.02, q.loc[m, "close"] * 0.05, 0.0)
            q = q.drop(index=q.index[m][::2])
        # 加一個不存在的未來交易日，抓得到直接查看日期或列數的洩漏。
        fake_day = d + pd.Timedelta(days=1)
        while fake_day in q.index:
            fake_day += pd.Timedelta(days=1)
        if len(q):
            fake_row = q.iloc[[-1]].copy()
            fake_row.index = pd.DatetimeIndex([fake_day]).astype(q.index.dtype)
            fake_row["close"] = fake_row["close"] * rng.uniform(0.2, 5.0)
            q = pd.concat([q, fake_row]).sort_index()
        prices[ent] = q
    e = store.events.copy()
    future_events = e["date"] > d
    if future_events.any():
        e = e.drop(index=e.index[future_events][::2])
    fake_ev = [{"entity": ent, "date": d + pd.Timedelta(days=int(rng.integers(1, 300))), "kind": "split",
                "ratio": float(rng.choice([2.0, 4.0, 1.1, 0.7]))} for ent in store.prices]
    if fake_ev:
        e = pd.concat([e, pd.DataFrame(fake_ev)], ignore_index=True)
    return PITStore(f, prices, e, store.vintage + "+tamper")


def _equal(a, b, path="") -> str | None:
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return f"{path}: 鍵不同 {set(a) ^ set(b)}"
        for k in a:
            r = _equal(a[k], b[k], f"{path}.{k}")
            if r:
                return r
        return None
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return f"{path}: 長度不同"
        for i, (x, y) in enumerate(zip(a, b)):
            r = _equal(x, y, f"{path}[{i}]")
            if r:
                return r
        return None
    if isinstance(a, (pd.DataFrame, pd.Series)):
        try:
            if isinstance(a, pd.DataFrame):
                pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12)
            else:
                pd.testing.assert_series_equal(a, b, check_exact=False, rtol=1e-12)
        except AssertionError as e:
            return f"{path}: {str(e)[:300]}"
        return None
    if isinstance(a, (float, np.floating)) or isinstance(b, (float, np.floating)):
        if a is None or b is None:
            return None if a is b else f"{path}: {a} != {b}"
        fa, fb = float(a), float(b)
        if math.isnan(fa) and math.isnan(fb):
            return None
        return None if math.isclose(fa, fb, rel_tol=1e-12, abs_tol=1e-12) else f"{path}: {a} != {b}"
    return None if a == b else f"{path}: {a!r} != {b!r}"


def assert_pit_invariant(fn, store: PITStore, d, seeds=(1, 2, 3)) -> None:
    """fn(view) 在原始資料與竄改後資料上的結果必須一模一樣。"""
    base = fn(store.as_of(d))
    for s in seeds:
        got = fn(tamper(store, d, s).as_of(d))
        diff = _equal(base, got)
        if diff:
            raise AssertionError(f"偷看未來資料(seed={s}, d={pd.Timestamp(d).date()}):{diff}")
