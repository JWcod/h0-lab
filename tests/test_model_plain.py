"""S3:過去表現白話版(docs/上手精靈規格.md §5)。沿用 test_model_honesty 的假世界。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from lab.research import model_eval as ME
from lab.research import splits
from tests.test_model_honesty import _my_model, lab_dirs, world  # noqa: F401  (pytest fixtures)


def test_top_levels_marks_top_fifth():
    rows = pd.DataFrame({"date": ["2020-01-31"] * 10 + ["2020-02-28"] * 6,
                         "symbol": [f"S{i}" for i in range(10)] + [f"S{i}" for i in range(6)],
                         "lt_score": list(range(10)) + [5, None, 3, 9, 1, 2]})
    out = ME._top_levels(rows)
    jan = out[out["date"] == "2020-01-31"]
    assert set(jan.loc[jan["lt_level"] == 3, "symbol"]) == {"S9", "S8"}           # ceil(10 × 0.2) = 2
    feb = out[out["date"] == "2020-02-28"]
    assert set(feb.loc[feb["lt_level"] == 3, "symbol"]) == {"S3"}                   # 5 檔有分數 → 1 檔
    assert (out["lt_level"].isin([2.0, 3.0])).all()


def test_backtest_has_plain_summary_dev_only(lab_dirs):  # noqa: F811
    _, params = lab_dirs
    r = ME.backtest(_my_model(w=30), "tw_pit50", params)
    p = r["plain"]
    assert p["status"] == "ok" and p["split"] == "dev"
    assert p["last_month"] < str(splits.HOLDOUT_START.date())
    n = len(p["months"])
    assert n > 12 and len(p["curves"]["top"]) == n and len(p["curves"]["universe"]) == n
    assert 0 <= p["win_share_vs_universe"] <= 1
    assert p["random_cagr"]["draws"] == ME.RANDOM_DRAWS                                   # 只給分布
    assert "verdict" not in p["random_cagr"]
    assert "不代表未來" in p["note"]
    assert set(p["stats"]["top"]) >= {"cagr", "max_dd"}


def test_plain_ignores_prices_after_holdout(lab_dirs, world, monkeypatch):  # noqa: F811
    """竄改 holdout 以後的價格,開發集的白話版數字不得改變。"""
    _, params = lab_dirs
    m = _my_model(w=30)
    a = ME.backtest(m, "tw_pit50", params)["plain"]
    _, _, px = world
    cut = pd.Timestamp(splits.HOLDOUT_START)
    tr2 = {s: v.where(v.index < cut, v * np.linspace(0.5, 3.0, len(v))) for s, v in px["tr_index"].items()}
    monkeypatch.setattr(ME, "_prices", lambda market, symbols: px | {"tr_index": tr2})
    b = ME.backtest(m, "tw_pit50", params)["plain"]
    assert a["curves"] == b["curves"] and a["stats"] == b["stats"] and a["random_cagr"] == b["random_cagr"]


def test_plain_note_and_start(lab_dirs):  # noqa: F811
    _, params = lab_dirs
    r = ME.backtest(_my_model(w=30), "tw_pit50", params)
    p = r["plain"]
    assert p["start"] < p["months"][0]
    if r["metrics"]["lt_12m"]["mine"]["verdict"] != "顯著為正":
        assert p["note"].startswith("長期分數的 IC 沒有證實有效")


def test_top_levels_skips_unrated_rows():
    rows = pd.DataFrame({"date": ["2020-01-31"] * 5, "symbol": list("ABCDE"),
                         "lt_score": [90, 10, 20, 30, 40], "lt_level": [None, 2, 2, 2, 2]})
    out = ME._top_levels(rows)
    assert list(out.loc[out["lt_level"] == 3, "symbol"]) == ["E"]          # A 沒有評等,不參加排名


def test_stale_raw_cache_refused():
    import pytest
    env = {("2020-01-31", "A"): ({"roe": 0.1}, None)}
    with pytest.raises(ME.ModelEvalError, match="舊版"):
        ME._check_env_vars(env, "TW")
