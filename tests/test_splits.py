import pandas as pd

from lab.research import splits as S


def test_purge_uses_trading_days_not_calendar_months():
    cal = pd.bdate_range("2022-01-03", "2026-10-15")
    d = pd.Series(pd.to_datetime(["2023-10-20", "2023-06-30", "2024-10-31", "2026-09-30"]))
    a = S.assign(d, "12m", cal)
    # 2023-10-20 + 252 個交易日落在 2024-10-01 之後 → 剔除(Verifier 發現日曆月規則漏掉這種)
    assert list(a) == ["purged", "dev", "holdout", "no_label"]
    ends = S.label_end(d, "12m", cal)
    assert (ends[a == "dev"] < S.HOLDOUT_START).all()
