"""復盤(/api/review):價格序列最後一天是 NaN(Yahoo 當天還沒收盤)時,不得產生 NaN,
否則 FastAPI 轉 JSON 會 500,預測追蹤頁顯示「讀取失敗」。"""
from __future__ import annotations

import json

import pandas as pd

from lab import snapshots


def test_review_ignores_trailing_nan_close(monkeypatch):
    idx = pd.to_datetime(["2026-10-20", "2026-10-21", "2026-10-30", "2026-10-31"])
    stock = pd.DataFrame({"Close": [100.0, 101.0, 110.0, float("nan")]}, index=idx)
    bench = pd.DataFrame({"Close": [50.0, 50.0, 51.0, float("nan")]}, index=idx)
    monkeypatch.setattr(snapshots, "list_all", lambda: [
        {"symbol": "2330.TW", "benchmark": "^TWII", "date": "2026-10-20", "price": 100.0, "lt_level": 3}])
    monkeypatch.setattr(snapshots.yahoo, "index_history", lambda sym, period: stock if sym == "2330.TW" else bench)
    r = snapshots.review()
    json.dumps(r, allow_nan=False)                      # 不得有 NaN / inf
    row = r["rows"][0]
    assert row["price_now"] == 110.0                    # 用最後一個有效收盤價
    assert abs(row["ret"] - 0.10) < 1e-12
    assert abs(row["bench_ret"] - 0.02) < 1e-12
