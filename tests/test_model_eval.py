"""M4:基礎變數快取(raw_{tw,us}.sqlite)+ 模型重算。小型合成資料,不依賴凍結資料。
真實資料的逐筆相同由 M4 驗收腳本在凍結 v2.2 上核對(結果已記錄)。"""
from __future__ import annotations

import copy
import math
import random

import numpy as np
import pandas as pd
import pytest

from lab import config
from lab.analysis import decision as DE
from lab.analysis import scoring as SC
from lab.models import schema
from lab.research import backtest_pit as BP
from lab.research import model_eval as ME
from tests.test_model_engine import make_ctx


def _rows(n=300, market="TW", seed=5):
    r = random.Random(seed)
    params = config.load_params()
    out = []
    dates = [f"2020-0{m}-28" for m in range(1, 7)]
    for i in range(n):
        ctx = make_ctx(r)
        if i % 7 == 0:   # 有些 env 帶 NaN 與 numpy 型別,要能存進快取
            ctx["regime"] = {"score": np.float64(55.5)}
            ctx["fund"]["profitability"]["roe"] = float("nan")
        p = SC.build(copy.deepcopy(ctx))
        lt = SC.composite(p, params["pillar_weights"]["long_term"])
        st = SC.composite(p, SC.short_term_weights(params, market))
        out.append({"date": dates[i % len(dates)], "symbol": f"S{i:03d}", "lt_level": 2.0 if i % 5 else None,
                    "lt_score": lt.get("score"), "st_code": DE.short_term(st, {}, params)["code"],
                    "st_score": st.get("score"), "pillars": str({k: v.get("score") for k, v in p.items()}),
                    "market": market, "_env": SC.base_vars(ctx)})
    return pd.DataFrame(out), params


@pytest.fixture()
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(BP, "OUT", tmp_path)
    ME._env_cache.clear()
    return tmp_path


def test_shipped_model_rescore_reproduces_rows(out_dir):
    allr, params = _rows()
    assert BP.write_raw_env(allr, params) == {"TW": len(allr)}
    rows = allr.drop(columns=["_env", "market"])
    cuts = params["decision_rules"]["fixed_quantile_levels"]
    rows["lt_level"] = BP.fixed_quantile_levels(rows.assign(date=pd.to_datetime(rows["date"])), cuts)
    new = ME.rescore_rows(rows, schema.load_shipped("jw-mf-2.0"), "TW", params, shipped=True)
    for c in ("lt_score", "st_score"):
        a, b = rows[c].to_numpy(dtype=float), new[c].to_numpy(dtype=float)
        assert np.array_equal(a, b, equal_nan=True), c
    assert list(new["st_code"]) == list(rows["st_code"])
    assert np.array_equal(new["lt_level"].to_numpy(dtype=float), rows["lt_level"].to_numpy(dtype=float), equal_nan=True)
    # pillars 只有在 env 沒有 NaN 時逐字相同(NaN 存進快取後仍是 NaN,打分走同一條路)
    # pillars 字串:測試注入 np.float64 的列(i % 7 == 0),字串表示會從 np.float64(55.5) 變 55.5,值相同;其他列逐字相同
    for i, (a, b) in enumerate(zip(rows["pillars"], new["pillars"])):
        if i % 7:
            assert a == b, i
        else:
            pa, pb = ME.pillars_of(rows.iloc[[i]] .assign(pillars=[a.replace("np.float64(55.5)", "55.5")]))[0], \
                ME.pillars_of(new.iloc[[i]])[0]
            assert pa.keys() == pb.keys()


def test_user_model_changes_scores_and_is_fast(out_dir):
    allr, params = _rows(n=600)
    BP.write_raw_env(allr, params)
    rows = allr.drop(columns=["_env", "market"])
    m = schema.load_shipped("jw-mf-2.0")
    m["pillar_weights"]["long_term"] = {"quality": 100}
    t0 = __import__("time").time()
    new = ME.rescore_rows(rows, m, "TW", params)
    assert __import__("time").time() - t0 < 5
    q = [ME.pillars_of(new)[i]["quality"] for i in range(len(new))]
    assert all((a is None and math.isnan(b)) or a == b for a, b in zip(q, new["lt_score"]))


def test_refuses_cross_sectional_and_missing_cache(out_dir):
    allr, params = _rows(n=20)
    rows = allr.drop(columns=["_env", "market"])
    with pytest.raises(ME.ModelEvalError, match="沒有 raw_tw"):
        ME.rescore_rows(rows, schema.load_shipped("jw-mf-2.0"), "TW", params)
    BP.write_raw_env(allr, params)
    with pytest.raises(ME.ModelEvalError, match="橫斷面"):
        ME.rescore_rows(rows, schema.load_shipped("jw-mf-2.0"), "TW", params | {"cross_sectional_scoring": True})
    extra = pd.concat([rows, rows.assign(symbol="ZZZ")])
    with pytest.raises(ME.ModelEvalError, match="快取少了"):
        ME.rescore_rows(extra, schema.load_shipped("jw-mf-2.0"), "TW", params)


def test_raw_env_meta(out_dir):
    allr, params = _rows(n=10)
    BP.write_raw_env(allr, params)
    _, meta = ME.load_env("TW")
    assert meta["market"] == "TW" and meta["n"] == "10" and meta["params_version"] == params["version"]


def test_stale_cache_and_holdout_mask(out_dir):
    allr, params = _rows(n=40)
    BP.write_raw_env(allr, params)
    rows = allr.drop(columns=["_env", "market"])
    stale = rows.copy()
    stale.loc[3, "st_score"] = (stale.loc[3, "st_score"] or 0) + 1      # 同鍵、內容不同(不同次 judge)
    with pytest.raises(ME.ModelEvalError, match="對不上 1 筆"):
        ME.rescore_rows(stale, schema.load_shipped("jw-mf-2.0"), "TW", params)
    late = rows.assign(date="2024-11-29")                                # holdout 期的判斷:沒有授權就遮掉
    allr2 = allr.assign(date="2024-11-29")
    BP.write_raw_env(allr2, params)
    ME._env_cache.clear()
    out = ME.rescore_rows(late, schema.load_shipped("jw-mf-2.0"), "TW", params)
    assert out["lt_score"].isna().all() and out["st_score"].isna().all() and out["lt_level"].isna().all()
    with pytest.raises(PermissionError):
        ME.rescore_rows(late, schema.load_shipped("jw-mf-2.0"), "TW", params, holdout_ticket="fake")
    with pytest.raises(ME.ModelEvalError, match="固定分位"):
        ME.rescore_rows(rows, schema.load_shipped("jw-mf-2.0"), "TW", params | {"decision_rules": {}})


def test_cache_made_with_cross_sectional_is_refused(out_dir):
    allr, params = _rows(n=10)
    BP.write_raw_env(allr, params | {"cross_sectional_scoring": True})
    with pytest.raises(ME.ModelEvalError, match="橫斷面打分開著時"):
        ME.rescore_rows(allr.drop(columns=["_env", "market"]), schema.load_shipped("jw-mf-2.0"), "TW", params)


def test_write_failure_leaves_no_tmp(out_dir):
    allr, params = _rows(n=5)
    allr.at[0, "_env"] = dict(allr.at[0, "_env"], bad=object())
    with pytest.raises(TypeError):
        BP.write_raw_env(allr, params)
    assert not list(out_dir.glob("*.tmp")) and not BP.raw_env_path("TW").exists()
