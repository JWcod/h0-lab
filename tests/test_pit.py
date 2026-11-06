"""PIT 資料層:as_of 只看得到當時的資料;分割只往回套用已發生的;竄改測試抓得到偷看未來的特徵。"""
import numpy as np
import pandas as pd
import pytest

from lab.pit import PITStore
from lab.pit.tamper import assert_pit_invariant, tamper

T = pd.Timestamp


def _store():
    facts = pd.DataFrame([
        # FY2021 營收:2022-02-15 首次公布 100,2023-02-10 重編成 90
        ["X", "revenue", T("2021-12-31"), "FY", 100.0, T("2022-02-16"), "sec", T("2022-02-15")],
        ["X", "revenue", T("2021-12-31"), "FY", 90.0, T("2023-02-13"), "sec", T("2023-02-10")],
        ["X", "revenue", T("2022-12-31"), "FY", 120.0, T("2023-02-13"), "sec", T("2023-02-10")],
        ["X", "eps", T("2021-12-31"), "FY", 4.0, T("2022-02-16"), "sec", T("2022-02-15")],
    ], columns=["entity", "field", "period_end", "period", "value", "available_at", "source", "filed"])
    idx = pd.bdate_range("2021-01-04", "2023-12-29")
    close = np.linspace(100, 160, len(idx))
    close[idx >= T("2022-06-01")] /= 4  # 2022-06-01 一拆四
    px = pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                       "volume": 1000.0, "cash_div": 0.0}, index=idx)
    px.loc[T("2022-09-01"), "cash_div"] = 1.0
    ev = pd.DataFrame([["X", T("2022-06-01"), "split", 4.0]], columns=["entity", "date", "kind", "ratio"])
    return PITStore(facts, {"X": px}, ev, "test")


def test_as_of_uses_version_known_at_the_time():
    s = _store()
    assert s.as_of("2022-02-15").facts("X").empty  # 公布當天收盤前還不能用(available_at = 公布日 +1 交易日)
    assert s.as_of("2022-06-30").facts("X", ["revenue"]).loc[T("2021-12-31"), "revenue"] == 100.0
    w = s.as_of("2023-03-01").facts("X", ["revenue"])
    assert w.loc[T("2021-12-31"), "revenue"] == 90.0 and w.loc[T("2022-12-31"), "revenue"] == 120.0


def test_split_adjustment_only_uses_past_events():
    s = _store()
    before = s.as_of("2022-05-31").prices("X")
    raw = s.as_of("2022-05-31").prices("X", adjust="none")
    pd.testing.assert_frame_equal(before, raw)  # 分割還沒發生 → 不調整
    after = s.as_of("2022-07-01").prices("X")
    jump = after["close"].loc["2022-05-31"] / after["close"].loc["2022-06-01"]
    assert jump == pytest.approx(1.0, abs=0.01)  # 分割後看,價格是連續的


def test_total_return_includes_dividend():
    s = _store()
    tr = s.as_of("2022-09-30").prices("X", adjust="total")["close"]
    sp = s.as_of("2022-09-30").prices("X", adjust="split")["close"]
    assert tr.loc["2022-08-31"] < sp.loc["2022-08-31"] and tr.loc["2022-09-01"] == sp.loc["2022-09-01"]


def test_as_of_view_only_holds_truncated_data():
    s = _store()
    v = s.as_of("2022-06-30")
    assert not hasattr(v, "_store")
    assert not any(isinstance(value, PITStore) for value in vars(v).values())
    assert (v._facts["available_at"] <= v.d).all()
    assert (v._events["date"] <= v.d).all()
    assert all((frame.index <= v.d).all() for frame in v._prices.values())


def test_tamper_passes_honest_feature_and_changes_future_rows():
    s = _store()

    def honest(view):
        return {"rev": view.facts("X", ["revenue"]).iloc[-1, 0], "px": float(view.prices("X")["close"].iloc[-1])}

    assert_pit_invariant(honest, s, "2022-06-30")
    t = tamper(s, "2022-06-30", seed=1)

    def future_peeker(store):  # 模擬錯誤地持有完整 store 時，會看到未來列是否存在與未來日期。
        return {
            "facts": tuple(pd.to_datetime(store.facts.loc[store.facts["available_at"] > T("2022-06-30"), "period_end"])),
            "prices": tuple(store.prices["X"].index[store.prices["X"].index > T("2022-06-30")]),
            "events": tuple(pd.to_datetime(store.events.loc[store.events["date"] > T("2022-06-30"), "date"])),
        }

    assert future_peeker(s) != future_peeker(t)
    assert_pit_invariant(honest, t, "2022-06-30")


def test_sqlite_roundtrip(tmp_path):
    s = _store()
    s.save(tmp_path / "pit.sqlite")
    t = PITStore.load(tmp_path / "pit.sqlite")
    assert t.vintage == "test"
    pd.testing.assert_frame_equal(s.as_of("2023-03-01").facts("X"), t.as_of("2023-03-01").facts("X"))
    pd.testing.assert_frame_equal(s.as_of("2023-03-01").prices("X"), t.as_of("2023-03-01").prices("X"), check_freq=False)


def test_split_factor_converts_per_share_to_decision_basis():
    s = _store()
    assert s.as_of("2022-05-31").split_factor("X", "2022-02-16") == 1.0
    assert s.as_of("2022-07-01").split_factor("X", "2022-02-16") == 4.0  # 2022-02 公布的 EPS 4.0 → 換成分割後基準 1.0
    assert s.as_of("2022-07-01").split_factor("X", "2022-06-02") == 1.0  # 分割後才公布的數字不再調整


def test_spinoff_only_affects_total_return():
    s = _store()
    s.events = pd.concat([s.events, pd.DataFrame([["X", T("2023-03-01"), "spinoff", 0.9]],
                                                   columns=["entity", "date", "kind", "ratio"])], ignore_index=True)
    v = s.as_of("2023-06-30")
    assert v.split_factor("X", "2022-07-01") == 1.0  # 分拆不改股數
    sp, tr = v.prices("X", "split")["close"], v.prices("X", "total")["close"]
    assert sp.loc["2023-02-28"] == pytest.approx(s.as_of("2023-02-28").prices("X", "split")["close"].iloc[-1])
    assert tr.loc["2023-02-28"] / tr.loc["2023-03-01"] == pytest.approx(0.9 * sp.loc["2023-02-28"] / sp.loc["2023-03-01"])


def test_basis_at_defaults_and_roundtrip(tmp_path):
    s = _store()
    assert (s.facts["basis_at"] == s.facts["filed"]).all()  # 沒填 → 預設申報日
    f = s.facts.copy()
    f["filed"] = pd.NaT
    f = f.drop(columns="basis_at")
    s2 = PITStore(f, s.prices, s.events, "t2")
    assert (s2.facts["basis_at"] == s2.facts["available_at"]).all()  # 沒申報日 → 可用日
    s.save(tmp_path / "b.sqlite")
    assert (PITStore.load(tmp_path / "b.sqlite").facts["basis_at"] == s.facts["basis_at"]).all()
    assert "basis_at" in s.as_of("2023-03-01").facts_long("X").columns


def test_total_return_uses_last_valid_close_and_ignores_bad_dividend():
    s = _store()
    px = s.prices["X"].copy()
    px.loc[T("2022-08-31"), "close"] = np.nan  # 除息日前一天收盤缺值
    px.loc[T("2023-05-02"), "cash_div"] = 1e6  # 異常:股利大於收盤
    s.prices["X"] = px
    v = s.as_of("2023-06-30")
    tr, sp = v.prices("X", "total")["close"], v.prices("X", "split")["close"]
    assert tr.loc["2022-08-30"] < sp.loc["2022-08-30"]  # 股利沒被漏掉
    assert (tr.dropna() > 0).all() and v.bad_dividends == 1
