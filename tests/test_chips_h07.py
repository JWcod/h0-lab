"""P2-H07:台股籌碼歷史的 PIT(資料日的下一個營業日才可用)、還原成 FinMind 格式、竄改測試。"""
import numpy as np
import pandas as pd

from lab.analysis import chips as CH
from lab.pit import PITStore
from lab.pit import build_chips as BC
from lab.pit.tamper import assert_pit_invariant


def _store():
    days = pd.bdate_range("2019-01-01", "2020-12-31")
    rng = np.random.default_rng(0)
    rows = []
    for i, d in enumerate(days):
        for name in ("Foreign_Investor", "Investment_Trust", "Dealer_self"):
            rows.append(("inst_net:" + name, d, float(rng.normal(0, 1e6))))
        rows += [("MarginPurchaseTodayBalance", d, 1000.0 + i), ("ShortSaleTodayBalance", d, 50.0 + i % 7),
                 ("MarginPurchaseLimit", d, 1e6), ("ForeignInvestmentSharesRatio", d, 40.0 + 0.01 * i)]
    f = pd.DataFrame(rows, columns=["field", "period_end", "value"])
    f["available_at"] = f["period_end"] + pd.offsets.BDay(1)
    f["entity"], f["period"], f["source"], f["filed"] = "9999", "D", "test", pd.NaT
    px = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0, "cash_div": 0.0}, index=days)
    return PITStore(facts=f, prices={"9999": px})


def test_same_day_chips_not_available():
    v = _store().as_of(pd.Timestamp("2020-06-30"))
    fm = BC.fm_asof(v, "9999")
    assert fm["margin"]["date"].max() == "2020-06-29"          # 6/30 的資料要到 7/1 才可用
    assert fm["institutional"]["date"].max() == "2020-06-29"
    assert set(fm) == {"institutional", "margin", "shareholding"}
    out = CH.tw(fm, 1e9, None, None)
    assert out["margin"]["margin_balance"] == 1000.0 + list(pd.bdate_range("2019-01-01", "2020-06-29")).__len__() - 1
    # 買賣超還原:net = buy − sell
    inst = fm["institutional"]
    assert ((inst["buy"] >= 0) & (inst["sell"] >= 0) & ((inst["buy"] == 0) | (inst["sell"] == 0))).all()


def test_fm_asof_tamper():
    st = _store()
    for d in ("2019-09-30", "2020-03-31", "2020-06-30"):
        assert_pit_invariant(lambda v: CH.tw(BC.fm_asof(v, "9999"), 1e9, None, None), st, pd.Timestamp(d))
