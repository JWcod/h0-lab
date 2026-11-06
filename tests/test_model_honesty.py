"""M5:試了幾次、使用者登記簿、開發集回測(和出廠模型、隨機對照比較)、holdout 每個模型族一次、竄改測試。
全部用合成資料(合成價格、合成判斷),不碰真實資料的 holdout。"""
from __future__ import annotations

import copy
import random

import numpy as np
import pandas as pd
import pytest

from lab import config
from lab.analysis import decision as DE
from lab.analysis import scoring as SC
from lab.models import schema, store
from lab.research import backtest_pit as BP
from lab.research import evaluate as EV
from lab.research import model_eval as ME
from tests.test_model_engine import make_ctx

SYMS = [f"S{i:02d}" for i in range(24)]


def _world(seed=11):
    r = random.Random(seed)
    params = config.load_params()
    cal = pd.bdate_range("2017-01-02", "2026-09-30")
    dates = pd.DatetimeIndex(sorted({cal[cal.to_period("M") == p][-1] for p in cal.to_period("M").unique()}))
    dates = dates[(dates >= "2017-06-01") & (dates <= "2025-09-30")]
    rng = np.random.default_rng(seed)
    tr = {s: pd.Series(np.exp(np.cumsum(rng.normal(0, 0.01, len(cal)))), index=cal) for s in SYMS}
    ctxs = [make_ctx(r) for _ in range(40)]
    envs = [SC.base_vars(c) for c in ctxs]
    pils = [SC.build(copy.deepcopy(c)) for c in ctxs]
    out = []
    for i, (d, s) in enumerate((d, s) for d in dates for s in SYMS):
        k = r.randrange(len(ctxs))
        p = pils[k]
        lt = SC.composite(p, params["pillar_weights"]["long_term"])
        st = SC.composite(p, SC.short_term_weights(params, "TW"))
        out.append({"date": d.strftime("%Y-%m-%d"), "symbol": s, "lt_level": 2.0, "lt_score": lt.get("score"),
                    "st_code": DE.short_term(st, {}, params)["code"], "st_score": st.get("score"),
                    "pillars": str({a: b.get("score") for a, b in p.items()}), "market": "TW", "_env": envs[k]})
    return pd.DataFrame(out), params, {"tr_index": tr, "bench_tr": None, "bench_kind": None, "calendar": cal}


@pytest.fixture(scope="module")
def world():
    return _world()


@pytest.fixture()
def lab_dirs(tmp_path, monkeypatch, world):
    allr, params, px = world
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(BP, "OUT", out)
    monkeypatch.setattr(config, "PRIVATE_DIR", tmp_path / "priv")
    monkeypatch.setattr(config, "REGISTRY_FILE", tmp_path / "main_registry.jsonl")
    monkeypatch.setattr(ME, "_prices", lambda market, symbols: px)
    monkeypatch.setattr(ME, "RANDOM_DRAWS", 3)                                          # 測試只抽 3 次,省時間
    ME._env_cache.clear()
    BP.write_raw_env(allr, params)
    allr.drop(columns=["_env", "market"]).to_csv(out / "rows_tw_pit50.csv", index=False)
    return tmp_path, params


def _my_model(mid="mine-a", fam=None, w=30):
    if mid not in {m["id"] for m in store.list_models()}:
        store.fork("jw-mf-2.0", mid, "我的")
    m = store.get(mid)
    m["pillar_weights"]["long_term"]["quality"] = w
    if fam:
        m["family_id"] = fam
    store.save(m)
    return store.get(mid)


def test_backtest_compares_and_counts_trials(lab_dirs):
    tmp, params = lab_dirs
    r = ME.backtest(_my_model(w=30), "tw_pit50", params)
    assert r["status"] == "開發集" and r["n_trials"] == 1 and "37" in r["shipped_note"]
    blk = r["metrics"]["lt_12m"]
    assert {"mine", "shipped", "random", "mine_vs_shipped"} <= set(blk)
    assert blk["random"]["draws"] == ME.RANDOM_DRAWS and "verdict" not in blk["random"]       # 隨機組只給分布
    assert blk["mine"]["n_months"] > 0 and blk["mine"]["last_month"] < "2024-10-01"      # 只有開發集
    assert ME.backtest(_my_model(w=30), "tw_pit50", params)["n_trials"] == 1            # 同一個雜湊重跑不加
    r3 = ME.backtest(_my_model(w=55), "tw_pit50", params)
    assert r3["n_trials"] == 2 and ME.trial_count("mine-a") == 2
    p = r3["metrics"]["st_1m"]["mine"]
    assert p["p_bonferroni"] == ME.bonferroni(p["p"], 2)
    assert (tmp / "priv" / "models" / "experiments.jsonl").exists()
    assert not (tmp / "main_registry.jsonl").exists()                                    # 不寫主登記簿


def test_bonferroni():
    assert ME.bonferroni(0.03, 1) == 0.03 and ME.bonferroni(0.03, 4) == pytest.approx(0.12)
    assert ME.bonferroni(0.4, 5) == 1.0 and ME.bonferroni(None, 3) is None


def test_shipped_model_refused(lab_dirs, user_mode):
    _, params = lab_dirs
    with pytest.raises(ME.ModelEvalError, match="出廠模型"):
        ME.backtest(schema.load_shipped("jw-mf-2.0"), "tw_pit50", params)
    store.fork("jw-mf-2.0", "same-as-shipped", "只複製")
    with pytest.raises(ME.ModelEvalError, match="出廠模型"):                              # 分數定義和出廠相同也不行
        ME.holdout(store.get("same-as-shipped"), "tw_pit50", params, confirm=True)


def test_random_control_keeps_each_date_distribution(world):
    allr = world[0].drop(columns=["_env"])
    allr.loc[::5, "lt_level"] = np.nan
    rc = ME.random_control(allr, "t")
    assert rc["lt_level"].isna().equals(allr["lt_level"].isna())                       # 有判斷的列不變
    for d, g in allr.groupby("date"):
        assert sorted(g["lt_score"].round(9)) == sorted(rc.loc[g.index, "lt_score"].round(9))
    assert not np.array_equal(rc["lt_score"].to_numpy(), allr["lt_score"].to_numpy())


def test_holdout_needs_opt_in_confirm_and_user_mode(lab_dirs, monkeypatch):
    _, params = lab_dirs
    m = _my_model()
    monkeypatch.setenv("INVEST_LAB_AGENT", "user")
    monkeypatch.setattr(config, "IS_WORKTREE", False)
    monkeypatch.delenv(ME.USER_HOLDOUT_ENV, raising=False)
    with pytest.raises(ME.ModelEvalError, match="沒有開啟"):                          # 沒設開關就不算
        ME.holdout(m, "tw_pit50", params, confirm=True)
    monkeypatch.setenv(ME.USER_HOLDOUT_ENV, "1")
    with pytest.raises(ME.ModelEvalError, match="確認"):
        ME.holdout(m, "tw_pit50", params)
    monkeypatch.setenv("INVEST_LAB_AGENT", "claude")
    with pytest.raises(PermissionError, match="agent"):
        ME.holdout(m, "tw_pit50", params, confirm=True)


@pytest.fixture()
def user_mode(monkeypatch):
    monkeypatch.setenv("INVEST_LAB_AGENT", "user")
    monkeypatch.setenv(ME.USER_HOLDOUT_ENV, "1")
    monkeypatch.setattr(config, "IS_WORKTREE", False)


def test_holdout_once_per_family(lab_dirs, user_mode):
    _, params = lab_dirs
    m = _my_model(w=40)
    assert ME.holdout_status(m, params)["clean"]
    n_tickets = len(EV._TICKETS)
    r = ME.holdout(m, "tw_pit50", params, confirm=True)
    assert r["clean"] and r["label"] == "乾淨的樣本外" and r["metrics"]["st_1m"]["mine"]["first_month"] >= "2024-09"
    assert "shipped" not in r["metrics"]["st_1m"]                                      # 不和出廠模型比
    assert len(EV._TICKETS) == n_tickets                                               # 授權用完就收回
    again = ME.holdout(m, "tw_pit50", params, confirm=True)
    assert again["repeat"] and again["metrics"] == r["metrics"]
    v2 = _my_model(w=41)                                                               # 同一個模型族的新版本
    r2 = ME.holdout(v2, "tw_pit50", params, confirm=True)
    assert not r2["clean"] and r2["label"] == ME.HOLDOUT_DIRTY
    store.fork("mine-a", "copy-of-a", "複製")                                          # 複製不能洗掉「已看過」
    c = store.get("copy-of-a")
    c["pillar_weights"]["long_term"]["quality"] = 77
    store.save(c)
    assert not ME.holdout_status(store.get("copy-of-a"), params)["clean"]
    assert ME.backtest(v2, "tw_pit50", params)["holdout"]["family_viewed"]


def test_model_ticket_only_for_model_eval():
    with pytest.raises(PermissionError, match="model_eval"):
        EV._issue_model_holdout_ticket()


def test_custom_metric_tamper(lab_dirs):
    """竄改測試:把某一天之後的基礎變數全部改掉,之前每一筆的分數不變(自訂指標只讀同一筆判斷的基礎變數)。"""
    _, params = lab_dirs
    m = _my_model(w=30)
    m["metrics"].append({"key": "custom_roe_x", "pillar": "quality", "applies": "all", "market": "all",
                         "label": "自訂 ROE 加 ROIC", "value": {"expr": "coalesce(roe, 0) + coalesce(roic, 0)"},
                         "score": {"type": "linear", "bad": 0.0, "good": 0.4}, "weight": 5, "fmt": "pct", "note": "",
                         "peer_blend": None, "requires": None, "gate": None, "enabled": True})
    schema.validate(m)
    rows = pd.read_csv(BP.OUT / "rows_tw_pit50.csv")
    before = ME.rescore_rows(rows, m, "TW", params)
    env, meta = ME.load_env("TW")
    cut = "2021-01-01"
    bad = {k: (({x: (v * 3 + 7 if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                 for x, v in e.items()}, st) if k[0] > cut else (e, st)) for k, (e, st) in env.items()}
    ME._env_cache[next(iter(ME._env_cache))] = (bad, meta)
    after = ME.rescore_rows(rows, m, "TW", params)
    early = pd.to_datetime(rows["date"]) <= cut
    for c in ("lt_score", "st_score"):
        assert np.array_equal(before.loc[early, c].to_numpy(float), after.loc[early, c].to_numpy(float), equal_nan=True)
    assert list(before.loc[early, "pillars"]) == list(after.loc[early, "pillars"])
    assert not np.array_equal(before.loc[~early, "lt_score"].to_numpy(float), after.loc[~early, "lt_score"].to_numpy(float),
                              equal_nan=True)


def test_holdout_cannot_be_washed(lab_dirs, user_mode):
    """M5 Verifier:同雜湊換模型族、改 family_id、兄弟複製、封存父模型,都不能把「已看過」洗掉。"""
    _, params = lab_dirs
    p = _my_model("parent", w=45)
    store.fork("parent", "kid-b", "b")
    store.fork("parent", "kid-c", "c")
    b = store.get("kid-b")
    b["pillar_weights"]["long_term"]["quality"] = 46
    store.save(b)
    assert ME.holdout(store.get("kid-b"), "tw_pit50", params, confirm=True)["clean"]
    assert not ME.holdout_status(store.get("kid-c"), params)["clean"]                 # 兄弟
    assert not ME.holdout_status(p, params)["clean"]                                    # 父
    store.archive("parent")
    assert not ME.holdout_status(store.get("kid-c"), params)["clean"]                 # 封存父模型也不行
    store.fork("jw-mf-2.0", "fresh", "全新")                                            # 雜湊相同、模型族不同
    f = store.get("fresh")
    f["pillar_weights"] = store.get("kid-b")["pillar_weights"]
    store.save(f)
    st = ME.holdout_status(store.get("fresh"), params)
    assert st["viewed"] and not st["clean"]
    assert ME.holdout(store.get("fresh"), "tw_pit50", params, confirm=True)["repeat"]
    c = store.get("kid-c") | {"family_id": "brand-new", "based_on": None}               # 改 family_id 存檔:被忽略
    store.save(c)
    assert store.get("kid-c")["family_id"] == "kid-c" and store.get("kid-c")["based_on"].startswith("parent@")


def test_trials_cannot_be_reset_by_family_edit(lab_dirs):
    _, params = lab_dirs
    ME.backtest(_my_model(w=30), "tw_pit50", params)
    ME.backtest(_my_model(w=31), "tw_pit50", params)
    m = store.get("mine-a") | {"family_id": "other"}
    store.save(m)
    assert store.get("mine-a")["family_id"] == "mine-a" and ME.trial_count("mine-a") == 2


def test_score_hash_includes_params(lab_dirs):
    _, params = lab_dirs
    m = _my_model(w=30)
    assert ME._check_user_model(m, params) != ME._check_user_model(m, params | {"tw_st_drop_momentum": True})
