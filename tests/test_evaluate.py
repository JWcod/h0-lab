"""評估框架(lab/research/evaluate.py)、閘門(scripts/gate.py)、holdout 入口(scripts/holdout.py)的測試。

全部用合成資料與暫存路徑;登記簿一律寫到 tmp_path,不汙染主登記簿。舊模式重現基準需要凍結資料(slow)。
"""
import importlib.util
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

from lab import config
from lab.research import evaluate as E
from lab.research import prereg, registry

ROOT = Path(__file__).resolve().parents[1]


_TEST_PREREG = """# 測試用預先登記

```prereg
P1-T01 | TW,US | add | lt_12m | 核准
P1-T02 | TW,US | add | lt_12m | 核准
P1-F01 | TW    | fix | lt_12m | 核准
P1-T03 | TW    | add | lt_12m | 核准
P1-D01 | TW,US | add | lt_12m | 草稿
P3-H01 | TW    | add | st_1m  | 核准
```
"""


@pytest.fixture(autouse=True)
def _test_prereg(tmp_path, monkeypatch):
    """閘門只接受預先登記且核准的族名:測試用自己的清單,不讀 docs/預先登記.md。"""
    p = tmp_path / "prereg.md"
    p.write_text(_TEST_PREREG, encoding="utf-8")
    monkeypatch.setattr(prereg, "PATH", p)
    monkeypatch.delenv("INVEST_LAB_AGENT", raising=False)
    return p


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"_script_{name}", ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------- Newey–West ----------------

def _ar1(rng, T, phi):
    e = rng.standard_normal(T + 200)
    x = np.zeros_like(e)
    for t in range(1, len(e)):
        x[t] = phi * x[t - 1] + e[t]
    return x[200:]


def test_newey_west_ar1_standard_error_is_reasonable():
    rng = np.random.default_rng(1)
    phi, T, sims, L = 0.5, 1000, 300, 12
    lrv_hat, iid, cover = [], [], 0
    for _ in range(sims):
        x = _ar1(rng, T, phi)
        nw = E.newey_west(x, L)
        lrv_hat.append(nw["se"] ** 2 * T)
        iid.append(nw["se_iid"] ** 2 * T)
        cover += nw["ci"][0] <= 0 <= nw["ci"][1]
    g0 = 1 / (1 - phi ** 2)
    bartlett = g0 * (1 + 2 * sum((1 - k / (L + 1)) * phi ** k for k in range(1, L + 1)))  # Bartlett 核的期望值
    true_lrv = 1 / (1 - phi) ** 2                                                         # 真正的長期變異數 = 4
    assert np.mean(lrv_hat) == pytest.approx(bartlett, rel=0.06)   # 實作和理論的 Bartlett 估計量一致
    assert 0.85 < np.mean(lrv_hat) / true_lrv < 1.0                 # 略偏低(Bartlett 核的已知偏誤),但接近
    assert np.mean(iid) / true_lrv < 0.40                           # 天真 iid 變異數嚴重低估(理論 (1−φ)/(1+φ) = 1/3)
    assert 0.88 < cover / sims < 0.98


def test_fixed_b_controls_size_in_short_samples():
    """43 個月、lag 12(閘門的實際情況):t(T−1) 臨界值過度拒絕,fixed-b 臨界值把錯誤拒絕率拉回約 5%。"""
    rng = np.random.default_rng(5)
    rej_fb = rej_t = 0
    sims = 1500
    for _ in range(sims):
        nw = E.newey_west(_ar1(rng, 43, 0.5), 12)
        rej_fb += nw["p"] < 0.05
        rej_t += nw["p_t"] < 0.05
    assert rej_t / sims > 0.10
    assert 0.02 < rej_fb / sims < 0.09
    assert E.crit_value(43, 12, 0.05) > 2.5 > E.crit_value(1000, 2, 0.05) > 1.9


def test_newey_west_white_noise_matches_iid_and_lag0():
    rng = np.random.default_rng(2)
    x = rng.standard_normal(500)
    nw0 = E.newey_west(x, 0)
    assert nw0["se"] == pytest.approx(x.std(ddof=0) / math.sqrt(500), rel=1e-12)
    nw = E.newey_west(x, 5)
    assert nw["se"] == pytest.approx(nw["se_iid"], rel=0.15)
    assert E.newey_west([1.0, 2.0], 1) is None


# ---------------- 前瞻報酬:t+1 成交 ----------------

def test_forward_returns_enter_next_trading_day():
    cal = pd.bdate_range("2020-01-01", periods=400)
    tr = {"A": pd.Series(np.arange(1, 401, dtype=float), index=cal)}  # 第 k 個交易日的值 = k + 1
    d_trading = cal[5]
    fri = next(i for i, d in enumerate(cal) if d.dayofweek == 4)
    d_weekend = cal[fri] + pd.Timedelta(days=1)  # 週六:最後一個收盤是週五 cal[fri]
    fr = E.forward_returns(tr, [d_trading, d_weekend], 21, lag=1)
    assert fr.loc[d_trading, "A"] == pytest.approx((6 + 1 + 21) / (6 + 1) - 1)   # 進場 cal[6]、出場 cal[27]
    assert fr.loc[d_weekend, "A"] == pytest.approx((fri + 2 + 21) / (fri + 2) - 1)  # 進場下週一 cal[fri+1]
    assert fr.attrs["entry"][d_trading] == cal[6]
    fr0 = E.forward_returns(tr, [d_trading], 21, lag=0)
    assert fr0.loc[d_trading, "A"] == pytest.approx((5 + 1 + 21) / (5 + 1) - 1)  # lag=0 = v1.1 的同日成交
    late = E.forward_returns(tr, [cal[390]], "1m")
    assert np.isnan(late.loc[cal[390], "A"])                                       # 資料不夠 → NaN
    with pytest.raises(TypeError):
        E.forward_returns([1, 2], [d_trading], 21)


# ---------------- 重疊世代 IC ----------------

def _reference_cohort_ic(S, R, H, ok, min_n=10):
    J = S.shape[0]
    out = np.full(J, np.nan)
    cnt = np.zeros(J)
    for t in range(J):
        vals = []
        for j in range(max(0, t - H), t):
            if not ok[j]:
                continue
            m = np.isfinite(S[j]) & np.isfinite(R[t])
            if np.isfinite(S[j]).sum() < min_n or m.sum() < min_n:
                continue
            if np.ptp(S[j][m]) == 0 or np.ptp(R[t][m]) == 0:
                continue
            vals.append(spearmanr(S[j][m], R[t][m]).statistic)
        if vals:
            out[t] = np.mean(vals)
            cnt[t] = len(vals)
    return out, cnt


def test_cohort_ic_matches_bruteforce_reference():
    rng = np.random.default_rng(3)
    J, N = 40, 25
    S = rng.standard_normal((J, N))
    R = rng.standard_normal((J, N)) * 0.05
    S[rng.random((J, N)) < 0.1] = np.nan
    R[rng.random((J, N)) < 0.1] = np.nan
    R[0] = np.nan
    ok = rng.random(J) < 0.8
    for H in (1, 6, 12):
        got = E.cohort_series(S, R, H, ok, spread=False)
        ref, cnt = _reference_cohort_ic(S, R, H, ok)
        np.testing.assert_allclose(got["ic"].to_numpy(), ref, atol=1e-12, equal_nan=True)
        np.testing.assert_array_equal(got["n_cohorts"].to_numpy(), cnt)


def test_cohort_ic_recovers_known_signal():
    rng = np.random.default_rng(4)
    J, N, H = 30, 20, 12
    perms = np.array([rng.permutation(N).astype(float) for _ in range(J)])
    R = np.full((J, N), np.nan)
    R[1:] = perms[:-1] / 100  # 第 t 個月的報酬排名 = 世代 t−1 的分數排名
    ok = np.ones(J, dtype=bool)
    one = E.cohort_series(perms, R, 1, ok)
    assert np.allclose(one["ic"].to_numpy()[1:], 1.0)
    assert np.isnan(one["ic"].iloc[0])
    twelve = E.cohort_series(perms, R, H, ok)
    ref, _ = _reference_cohort_ic(perms, R, H, ok)
    np.testing.assert_allclose(twelve["ic"].to_numpy(), ref, atol=1e-12, equal_nan=True)
    # 每個月只有最新世代完全正確,其他 11 個世代約為 0 → 平均約 1/12
    assert abs(np.nanmean(twelve["ic"].to_numpy()[H:]) - 1 / H) < 0.05


# ---------------- 合成市場:平手、沒有評等、投組 ----------------

def _det_market(n=11, months=36, start="2018-01-01"):
    """沒有雜訊的合成市場:第 i 檔每天報酬固定且隨 i 遞增 → 每個月、每個期間排名都一樣,中位數剛好是第 5 檔。"""
    cal = pd.bdate_range(start, periods=months * 22 + 320)
    mu = np.linspace(-0.001, 0.001, n)
    tr = {f"S{i}": pd.Series(np.exp(np.arange(len(cal)) * mu[i]), index=cal) for i in range(n)}
    me = pd.Series(cal, index=cal).groupby(cal.to_period("M")).max().iloc[:months]
    rows = []
    for d in me:
        for i in range(n):
            rows.append({"date": d, "symbol": f"S{i}", "lt_score": float(i), "lt_level": float(min(4, i // 2)),
                         "st_score": float(i), "st_code": 1 if i >= 6 else (-1 if i <= 2 else 0)})
    df = pd.DataFrame(rows)
    df.loc[df["symbol"] == "S0", ["lt_level"]] = np.nan          # 最差的一檔沒有評等
    df.loc[df["symbol"] == "S0", ["lt_score"]] = 1000.0          # 分數故意放最高:若被算進 IC 就會拉低 IC
    df.loc[df["symbol"] == "S5", "lt_level"] = 4.0               # 中位數那檔看多 → 超額報酬剛好 0 → 平手
    df.loc[df["symbol"] == "S1", "lt_level"] = 0.0               # 賣出
    return df, tr


def test_ties_and_unrated_rows(tmp_path):
    rows, tr = _det_market()
    res = E.evaluate(rows, tr, market="TW", registry_path=tmp_path / "reg.jsonl")
    h = res["secondary"]["hit"]["lt_12m"]
    g = h["general"]
    n_dates = h["n_dates"]
    assert g["ties"] == n_dates                    # S5 每期都平手:不算對也不算錯
    assert g["right"] + g["wrong"] + g["ties"] == g["n_bull"] + g["n_bear"]
    assert g["wrong"] == 0                         # 確定性市場:看多的(i≥6)都跑贏,看空的(S1)都跑輸
    assert h["unrated"]["n"] == n_dates            # S0 單獨計數
    assert h["unrated"]["mean_x"] < 0
    lt = res["primary"]["lt_12m"]
    assert lt["ic"] == pytest.approx(1.0)          # 沒有評等的 S0 沒被算進長期 IC
    assert res["unrated_rows"] == int(rows["lt_level"].isna().sum())
    buckets = {b["rating"]: b for b in res["secondary"]["rating_12m"]["buckets"]}
    assert "沒有評等" in buckets and buckets["沒有評等"]["n"] == n_dates
    recs = registry.read(tmp_path / "reg.jsonl")
    assert len(recs) == 1 and recs[0]["kind"] == "dev" and recs[0]["caller"]


def test_portfolio_keeps_unrated_in_ex_sell_and_charges_costs():
    rows, tr = _det_market()
    ctx = E._Ctx(rows, tr)
    paths = E._portfolio_paths(ctx, rows, "TW", "dev")
    j0 = ctx.gpos[ctx.dates[0]]
    r1 = ctx.R[j0 + 1]
    syms = ctx.symbols
    g0 = rows[rows["date"] == ctx.dates[0]].set_index("symbol")["lt_level"]
    held = [s for s in syms if not (g0[s] <= 1)]   # ex_sell 只避開減碼 / 賣出;S0 沒有評等照樣持有
    assert "S0" in held and "S1" not in held
    exp = np.mean([r1[syms.index(s)] for s in held])
    assert paths["paths"]["ex_sell"]["gross"][0] == pytest.approx(exp)
    first_cost = paths["paths"]["universe"]["cost"][0]
    assert first_cost == pytest.approx(E.COSTS["TW"]["buy"])  # 第一個月從現金全部買進
    # 之後每月拉回等權:有漂移就有週轉與成本
    assert all(c > 0 for c in paths["paths"]["universe"]["cost"][1:])
    assert max(paths["paths"]["universe"]["turnover"][1:]) < 0.05


# ---------------- holdout 防護 ----------------

def test_holdout_requires_authorization(tmp_path):
    rows, tr = _det_market()
    for split in ("holdout", "all"):
        with pytest.raises(PermissionError):
            E.evaluate(rows, tr, market="TW", split=split, registry_path=tmp_path / "r.jsonl")
        with pytest.raises(PermissionError):
            E.evaluate(rows, tr, market="TW", split=split, holdout_ticket="偽造的授權", registry_path=tmp_path / "r.jsonl")
    with pytest.raises(ValueError):
        E.evaluate(rows, tr, market="TW", split="everything", registry_path=tmp_path / "r.jsonl")
    with pytest.raises(PermissionError):
        E._issue_holdout_ticket()                   # 不是 scripts/holdout.py 呼叫的
    assert not (tmp_path / "r.jsonl").exists()      # 被拒絕的呼叫不寫登記簿


def test_holdout_guard_blocks_worktree_and_agents(monkeypatch):
    monkeypatch.setattr(config, "IS_WORKTREE", True)
    monkeypatch.delenv("INVEST_LAB_AGENT", raising=False)
    with pytest.raises(PermissionError):
        E.holdout_guard()
    monkeypatch.setattr(config, "IS_WORKTREE", False)
    monkeypatch.setenv("INVEST_LAB_AGENT", "C1")
    with pytest.raises(PermissionError):
        E.holdout_guard()
    ho = _load_script("holdout")
    with pytest.raises(PermissionError):
        ho.main(["--rows", "TW=x.csv", "--stage", "s", "--label", "l", "--yes"])
    monkeypatch.setenv("INVEST_LAB_AGENT", "user")
    E.holdout_guard()  # 主 checkout + user 才放行


def test_holdout_pending_then_result_and_counts_once(tmp_path):
    ho = _load_script("holdout")
    md = tmp_path / "日誌.md"
    shutil.copy(ROOT / "docs" / "holdout 評估日誌.md", md)
    before = md.read_text(encoding="utf-8")
    n_rows_before = sum(1 for ln in before.splitlines() if ln.startswith("| ") and ln.split("|")[1].strip().isdigit())
    reg = tmp_path / "reg.jsonl"
    cfg = {"eval_version": "t", "rows_sha256": {"TW": "abc"}, "prices": "bt_px", "data_vintage": "v"}
    with pytest.raises(ValueError):
        ho.record_pending(cfg, stage="階段 1 末", label="測試", note="n", md_path=md, registry_path=reg)
    assert not reg.exists() and md.read_text(encoding="utf-8") == before
    # 1) 計算前先記:日誌多一列「計算中」、登記簿一筆 pending、次數 = 1
    p1 = ho.record_pending(cfg, stage="階段1", label="測試", note="n", md_path=md, registry_path=reg, date="2026-10-15")
    assert not p1["counted_before"] and p1["log_row"] == n_rows_before and p1["holdout_count"] == 1
    rows = [ln for ln in md.read_text(encoding="utf-8").splitlines() if ln.startswith(f"| {p1['log_row']} |")]
    assert len(rows) == 1 and ho.PENDING in rows[0] and p1["config_hash"] in rows[0]
    # 2) 算完補結果
    d1 = ho.record_result(cfg, {"TW": {}}, "摘要 | 含直線", stage="階段1", label="測試", md_path=md, registry_path=reg)
    assert d1["log_filled"] and d1["holdout_count"] == 1
    after = md.read_text(encoding="utf-8").splitlines()
    row = [ln for ln in after if ln.startswith(f"| {p1['log_row']} |")][0]
    assert ho.PENDING not in row and "摘要 / 含直線" in row
    assert len(after) == len(before.splitlines()) + 1
    # 3) 同一個設定重算:不加列、不另計次
    p2 = ho.record_pending(cfg, stage="階段1", label="測試", note="n", md_path=md, registry_path=reg)
    assert p2["counted_before"] and p2["log_row"] is None and p2["holdout_count"] == 1
    assert len(md.read_text(encoding="utf-8").splitlines()) == len(after)
    kinds = [(r["kind"], r["metrics"]["status"]) for r in registry.read(reg)]
    assert kinds == [("holdout", "pending"), ("holdout", "done"), ("holdout", "pending")]
    assert ho.stage_hashes("階段1", reg) == {p1["config_hash"]}
    assert ho.MAIN_REGISTRY == config.MAIN_ROOT / "data" / "research" / "experiments.jsonl"
    assert (ROOT / "docs" / "holdout 評估日誌.md").read_text(encoding="utf-8") == before  # 原檔不動(寫的是副本)


def test_public_helpers_cannot_reach_holdout_without_ticket():
    rows, tr = _det_market()
    ctx = E._Ctx(rows, tr)
    with pytest.raises(PermissionError):
        ctx.dates_in(12, "holdout")
    with pytest.raises(PermissionError):
        ctx._cohort_ok(12, "all")
    with pytest.raises(PermissionError):
        E._hit_rates(ctx, ctx.rows, "12m", "holdout", "TW", "lt")
    with pytest.raises(PermissionError):
        E._rating_buckets(ctx, ctx.rows, "12m", "holdout")
    with pytest.raises(PermissionError):
        E._portfolio_paths(ctx, ctx.rows, "TW", "holdout")
    with pytest.raises(PermissionError):
        E._ic_series(ctx, ctx.rows, "lt_score", 12, "holdout")


def test_non_monthly_dates_are_rejected(tmp_path):
    cal = pd.bdate_range("2018-01-01", periods=600)
    tr = {f"S{i}": pd.Series(np.linspace(1, 2, 600) * (1 + i / 100), index=cal) for i in range(12)}
    weekly = cal[::5][:40]
    rows = pd.DataFrame([{"date": d, "symbol": f"S{i}", "lt_score": float(i), "lt_level": 2.0, "st_score": 1.0, "st_code": 0}
                         for d in weekly for i in range(12)])
    with pytest.raises(ValueError, match="月頻"):
        E.evaluate(rows, tr, market="TW", registry_path=tmp_path / "r.jsonl")


def test_full_cohort_months_only(tmp_path):
    rows, tr = _det_market()
    ctx = E._Ctx(rows, tr)
    full = E._ic_series(ctx, ctx.rows, "lt_score", 12, "dev", judge_col="lt_level")
    allm = E._ic_series(ctx, ctx.rows, "lt_score", 12, "dev", judge_col="lt_level", full_only=False)
    assert (full["n_cohorts"] == 12).all() and len(allm) > len(full) and (allm["n_cohorts"] < 12).any()
    res = E.evaluate(rows, tr, market="TW", registry_path=tmp_path / "r.jsonl")
    lt = res["primary"]["lt_12m"]
    assert lt["n_months"] == len(full) and lt["sensitivity_cohort_weighted"]["n_months"] == len(allm)


def test_legacy_baseline_detection(tmp_path):
    if not (ROOT / "tests" / "baseline" / "outputs").exists():
        pytest.skip("沒有基準輸出(公開版不附含第三方資料衍生值的檔案)")
    assert E.is_v11_baseline(ROOT / "tests" / "baseline" / "outputs")
    for m in ("TW", "US"):
        df = pd.read_csv(ROOT / "tests" / "baseline" / "outputs" / f"backtest_{m}_rows.csv.gz")
        if m == "TW":
            df.loc[0, "lt_score"] += 1
        df.to_csv(tmp_path / f"backtest_{m}_rows.csv", index=False)
    assert not E.is_v11_baseline(tmp_path)


# ---------------- 配對閘門 ----------------

def _synth_market(seed, n=30, months=40, signal=1.0, start="2017-01-01"):
    rng = np.random.default_rng(seed)
    cal = pd.bdate_range(start, periods=months * 22 + 320)
    f = rng.standard_normal(n)
    daily = 0.0003 + 0.01 * rng.standard_normal((len(cal), n)) + signal * 0.001 * f
    tr = {f"S{i}": pd.Series(np.exp(np.cumsum(daily[:, i])), index=cal) for i in range(n)}
    me = pd.Series(cal, index=cal).groupby(cal.to_period("M")).max().iloc[:months]
    base, new = [], []
    for d in me:
        sb = rng.standard_normal(n)
        sn = f + 0.3 * rng.standard_normal(n)
        for i in range(n):
            lvl_b = float(np.searchsorted(np.quantile(sb, [0.2, 0.4, 0.6, 0.8]), sb[i]))
            lvl_n = float(np.searchsorted(np.quantile(sn, [0.2, 0.4, 0.6, 0.8]), sn[i]))
            base.append({"date": d, "symbol": f"S{i}", "lt_score": sb[i], "lt_level": lvl_b, "st_score": sb[i], "st_code": 0})
            new.append({"date": d, "symbol": f"S{i}", "lt_score": sn[i], "lt_level": lvl_n, "st_score": sn[i], "st_code": 0})
    return pd.DataFrame(base), pd.DataFrame(new), {"tr_index": tr, "bench_tr": None}


def test_paired_gate_add_and_fix(tmp_path):
    reg = tmp_path / "reg.jsonl"
    mk = {m: _synth_market(s) for m, s in (("TW", 11), ("US", 12))}
    base = {m: v[0] for m, v in mk.items()}
    new = {m: v[1] for m, v in mk.items()}
    prices = {m: v[2] for m, v in mk.items()}
    add_provenance = {"feature_keys": ["test_feature"], "tamper_passed": True}
    # 同一個模型:'fix' 通過、'add' 不通過(沒有證明更好)
    same_fix = E.paired_gate(base, base, {"change_type": "fix", "prices": prices, "registry_path": reg})
    assert same_fix["passed"], same_fix["reasons"]
    same_add = E.paired_gate(base, base, {"change_type": "add", "prices": prices, "registry_path": reg, "n_trials": 1,
                                           **add_provenance})
    assert not same_add["passed"]
    # 新分數有強訊號:'add' 通過(n_trials = 1),兩市場、兩個子期間方向一致
    explore = E.paired_gate(base, new, {"change_type": "add", "prices": prices, "registry_path": reg, "n_trials": 1,
                                         **add_provenance})
    assert not explore["passed"] and any("假設族" in r for r in explore["reasons"])  # 探索性的 add 不算數
    good = E.paired_gate(base, new, {"change_type": "add", "prices": prices, "registry_path": reg, "n_trials": 1,
                                     "family": "P1-T01", **add_provenance})
    assert good["passed"], good["reasons"]
    assert good["markets"]["TW"]["diff"] > 0 and good["markets"]["US"]["diff"] > 0
    assert good["subperiods"]["first"]["diff"] > 0 and good["subperiods"]["second"]["diff"] > 0
    # 反過來(拿掉訊號)當成修錯:不通過
    worse = E.paired_gate(new, base, {"change_type": "fix", "prices": prices, "registry_path": reg})
    assert not worse["passed"]
    # 只有一個市場的 'add' 預設不通過
    single = E.paired_gate(base["TW"], new["TW"], {"change_type": "add", "market": "TW", "prices": prices,
                                                   "registry_path": reg, "n_trials": 1, "family": "P1-T01", **add_provenance})
    assert not single["passed"] and any("一個市場" in r for r in single["reasons"])
    # 每次閘門都寫登記簿(kind = gate),p 值留給之後的 Holm
    recs = [r for r in registry.read(reg) if r["kind"] == "gate"]
    assert len(recs) == 6 and all("p_primary" in r["metrics"] for r in recs)
    # Holm 族群:同族、同 change_type、同主要指標的其他設定才算;自己重跑不算
    again = E.paired_gate(base, new, {"change_type": "add", "prices": prices, "registry_path": reg, "family": "P1-T01",
                                       **add_provenance})
    assert again["holm"]["known_family_p"] == 1   # 只有 single(同族 add);good 是同一個設定,排除
    other = E.paired_gate(base, new, {"change_type": "add", "prices": prices, "registry_path": reg, "family": "P1-T02",
                                       **add_provenance})
    assert other["holm"]["known_family_p"] == 0
    # n_trials 預設 = 該族開發集設定數;手動指定不得更少
    for i in range(3):
        registry.log("dev", {"k": i}, {}, market="TW", family="P1-T01", path=reg)
    fam = E.paired_gate(base, new, {"change_type": "add", "prices": prices, "registry_path": reg, "family": "P1-T01",
                                     **add_provenance})
    assert fam["holm"]["n_trials"] == 3
    with pytest.raises(ValueError):
        E.paired_gate(base, new, {"change_type": "add", "prices": prices, "registry_path": reg, "family": "P1-T01", "n_trials": 2,
                                   **add_provenance})


def test_paired_gate_add_requires_tamper_provenance(tmp_path):
    base, new, prices = _synth_market(13, months=30)
    common = {"change_type": "add", "market": "TW", "prices": {"TW": prices}, "registry_path": tmp_path / "reg.jsonl"}
    with pytest.raises(ValueError, match="feature_keys"):
        E.paired_gate(base, new, common)
    with pytest.raises(ValueError, match="tamper_passed"):
        E.paired_gate(base, new, {**common, "feature_keys": ["test_feature"]})


def test_paired_gate_reports_coverage_and_uses_intersection(tmp_path):
    base, new, px = _synth_market(21, months=30)
    new = new.copy()
    drop = new.sample(frac=0.2, random_state=0).index
    new.loc[drop, "lt_level"] = np.nan             # 新版本少判斷 20%
    reg = tmp_path / "reg.jsonl"
    with pytest.raises(ValueError, match="不能直接呼叫"):       # 正式閘門只能經由 paired_gate(M2)
        E.gate_core({"TW": (base, new, px)}, {"change_type": "fix", "registry_path": reg}, secondary=False)
    res = E.gate_core({"TW": (base, new, px)}, {"change_type": "fix", "registry_path": reg}, secondary=False, _token=E._PAIRED)
    c = res["coverage"]["TW"]
    assert c["n_both"] == c["n_new_judged"] < c["n_base_judged"]
    assert c["coverage_diff"] == pytest.approx(-len(drop) / len(base))
    assert res["warnings"] and "覆蓋率大降" in res["warnings"][0]
    assert [r["kind"] for r in registry.read(reg)] == ["gate"]          # 非校準的 gate_core 也寫登記簿
    with pytest.raises(ValueError, match="calibration_session"):     # 校準不能在 session 外試跑(RedTeam2 問題 2)
        E.gate_core({"TW": (base, new, px)}, {"change_type": "fix", "registry_path": reg}, secondary=False, _calibration=True)
    with E.calibration_session("測試", registry_path=reg):
        E.gate_core({"TW": (base, new, px)}, {"change_type": "fix", "registry_path": reg}, secondary=False, _calibration=True)
    recs = registry.read(reg)
    assert [r["kind"] for r in recs] == ["gate", "replay"]               # 校準本身不寫 gate,session 寫一筆 replay
    assert recs[1]["family"] == "calibration" and recs[1]["metrics"]["n_evals"] == 1
    assert recs[1]["config"]["new_rank_fps"] == {"TW": [E.rank_fingerprint(new)]}


def test_holm_threshold():
    rej, thr, rank = E._holm_threshold(0.004, [], 10, 0.05)
    assert rej and thr == pytest.approx(0.005) and rank == 1
    rej, thr, rank = E._holm_threshold(0.006, [], 10, 0.05)
    assert not rej
    # 已知另一個更小的 p(先被拒絕)→ 這個檢定的門檻放寬到 α/(m−1)
    rej, thr, rank = E._holm_threshold(0.0055, [0.001], 10, 0.05)
    assert rej and rank == 2 and thr == pytest.approx(0.05 / 9)
    assert E._holm_family([0.01, 0.04, 0.03], 0.05) == [True, False, False]


def test_metric_seed_is_stable():
    assert E.metric_seed("TW|dev|3m|st|hit") == E.metric_seed("TW|dev|3m|st|hit")
    assert E.metric_seed("a") != E.metric_seed("b")
    # 固定值:跨程序、跨機器都一樣(不能用 Python 內建 hash)
    assert E.metric_seed("a") == int("ca978112", 16)


def test_gate_cli_compare_writes_report(tmp_path, monkeypatch):
    gate = _load_script("gate")
    base, new, px = _synth_market(31, months=30)
    b, n = tmp_path / "b.csv", tmp_path / "n.csv"
    base.to_csv(b, index=False)
    new.to_csv(n, index=False)
    monkeypatch.setattr(gate, "_prices", lambda markets: {m: px for m in markets})
    monkeypatch.setattr(config, "REGISTRY_FILE", tmp_path / "reg.jsonl")
    add_args = ["--feature-keys", "test_feature", "--tamper-passed"]
    assert gate.main(["compare", "--change-type", "add", *add_args, "--base", f"TW={b}", "--new", f"TW={n}", "--family", "P1-T03",
                      "--n-trials", "0", "--out", str(tmp_path / "bad")]) == 2      # 正式閘門本身至少算 1 次(Verifier30 問題 5)
    registry.log("dev", {"x": 1}, {}, market="TW", family="P1-T03")
    registry.log("dev", {"x": 2}, {}, market="TW", family="P1-T03")
    assert gate.main(["compare", "--change-type", "add", *add_args, "--base", f"TW={b}", "--new", f"TW={n}", "--family", "P1-T03",
                      "--n-trials", "1", "--out", str(tmp_path / "bad")]) == 2      # 少報嘗試次數 → 錯誤
    rc = gate.main(["compare", "--change-type", "add", *add_args, "--base", f"TW={b}", "--new", f"TW={n}", "--single-market-ok",
                    "--family", "P1-T03", "--n-trials", "2", "--out", str(tmp_path / "out")])
    md = list((tmp_path / "out").glob("gate_*.md"))
    js = list((tmp_path / "out").glob("gate_*.json"))
    assert rc == 0 and len(md) == 1 and len(js) == 1
    text = md[0].read_text(encoding="utf-8")
    assert "非投資建議" in text and "通過" in text
    assert json.loads(js[0].read_text(encoding="utf-8"))["passed"] is True


def _saturday_market(seed, n=20, months=40, saturdays=("2017-09-30", "2018-03-31")):
    """日曆含週六補班日(仿 TWSE_TR);判斷日 = 每月最後一個平日,所以 9/29 判斷的 t+1 成交日是週六 9/30。"""
    rng = np.random.default_rng(seed)
    wk = pd.bdate_range("2016-01-01", periods=months * 22 + 320)
    cal = wk.union(pd.DatetimeIndex(pd.to_datetime(list(saturdays))))
    tr = {f"S{i}": pd.Series(np.exp(np.cumsum(0.0003 + 0.01 * rng.standard_normal(len(cal)))), index=cal) for i in range(n)}
    me = pd.Series(wk, index=wk).groupby(wk.to_period("M")).max().iloc[:months]
    rows = []
    for d in me:
        sc = rng.standard_normal(n)
        for i in range(n):
            rows.append({"date": d, "symbol": f"S{i}", "lt_score": sc[i], "lt_level": float(i % 5), "st_score": sc[i], "st_code": 0})
    return pd.DataFrame(rows), {"tr_index": tr, "bench_tr": None, "calendar": cal}


def test_gate_saturday_trading_day_does_not_crash(tmp_path):
    tw, px_tw = _saturday_market(41)
    us, _, px_us = _synth_market(42, months=40, start="2016-01-01")
    ctx = E._Ctx(tw, px_tw["tr_index"], calendar=px_tw["calendar"])
    j = ctx.gpos[pd.Timestamp("2017-09-29")]
    assert ctx.entries[j] == pd.Timestamp("2017-09-30")                  # 週六補班日當成交日
    assert ctx.entries[j - 1].to_period("M") == ctx.entries[j].to_period("M")  # 成交日月份重複(舊鍵會當機)
    keys = ctx.decision_months(ctx.entries[[j - 1, j]])
    assert [str(k) for k in keys] == ["2017-08", "2017-09"]              # 判斷日月份不重複
    res = E.gate_core({"TW": (tw, tw, px_tw), "US": (us, us, px_us)}, {"change_type": "fix", "registry_path": tmp_path / "r.jsonl"},
                      secondary=False, _token=E._PAIRED)
    assert res["passed"], res["reasons"]
    assert res["pooled"]["n_months"] > 0 and res["pooled"]["diff"] == pytest.approx(0.0)


def test_ctx_rejects_missing_prices(tmp_path):
    base, _, px = _synth_market(51, n=30, months=24)
    tr = dict(px["tr_index"])
    tr.pop("S0")                                                         # 1 / 30 ≈ 3%:允許
    E._Ctx(base, tr)
    tr.pop("S1")                                                         # 2 / 30 ≈ 7% > 5%:報錯並列出代號
    with pytest.raises(ValueError, match="沒有價格") as ei:
        E._Ctx(base, tr)
    assert "S0" in str(ei.value) and "S1" in str(ei.value)
    with pytest.raises(ValueError, match="沒有價格"), E.calibration_session("測試", registry_path=tmp_path / "r.jsonl"):
        E.gate_core({"TW": (base, base, {"tr_index": tr, "bench_tr": None})}, {"change_type": "fix"}, secondary=False,
                    _calibration=True)


def test_gate_cli_prices_pit(tmp_path, monkeypatch):
    gate = _load_script("gate")
    from lab.research import backtest_pit as BP
    monkeypatch.setattr(config, "REGISTRY_FILE", tmp_path / "reg.jsonl")
    rows = BP.OUT / "rows_tw_pit50.csv"
    have = all(Path(p).exists() for k, p in BP.paths().items() if k in ("TW", "macro")) and rows.exists()
    if not have:
        # 沒有 store:清楚報錯(rc = 2),不會退回 bt_px
        base, _, _ = _synth_market(61, months=24)
        b = tmp_path / "b.csv"
        base.to_csv(b, index=False)
        monkeypatch.setattr(BP, "OUT", tmp_path / "no_store")
        assert gate.main(["compare", "--change-type", "fix", "--prices", "pit", "--base", f"TW={b}", "--new", f"TW={b}",
                          "--out", str(tmp_path / "out")]) == 2
        pytest.skip("沒有 bt_pit store(INVEST_LAB_OUT_DIR 要指到 backtest_pit 的回測輸出)")
    rc = gate.main(["compare", "--change-type", "fix", "--prices", "pit", "--base", f"TW={rows}", "--new", f"TW={rows}",
                    "--out", str(tmp_path / "out")])
    js = list((tmp_path / "out").glob("gate_*.json"))
    assert rc == 0 and len(js) == 1
    res = json.loads(js[0].read_text(encoding="utf-8"))
    assert res["passed"] and res["markets"]["TW"]["diff"] == pytest.approx(0.0)


def test_dev_markdown_handles_empty_horizon(tmp_path):
    gate = _load_script("gate")
    rows, tr = _det_market()
    res = E.evaluate(rows, tr, market="TW", registry_path=tmp_path / "r.jsonl")
    res["secondary"]["hit"]["lt_12m"] = {"n": 0}
    md = gate.dev_markdown({"TW": res})
    assert "開發集沒有資料" in md


# ---------------- 舊模式重現 v1.1 基準(需要凍結資料) ----------------

FROZEN = config.MAIN_ROOT / "data" / "frozen" / "v1.1_2026-10-15"


@pytest.mark.slow
def test_legacy_mode_reproduces_v11_baseline(tmp_path):
    if not (FROZEN / "cache" / "bt_px").exists():
        pytest.skip("沒有凍結資料(data/ 不進 git)")
    res = E.legacy_metrics(ROOT / "tests" / "baseline" / "outputs", FROZEN / "cache", registry_path=tmp_path / "reg.jsonl")
    base = json.loads((ROOT / "tests" / "baseline" / "metrics_v1.1.json").read_text(encoding="utf-8"))
    assert res == base
    assert registry.read(tmp_path / "reg.jsonl")[0]["kind"] == "replay"


@pytest.mark.slow
def test_legacy_mode_on_other_rows_returns_dev_only(tmp_path):
    if not (FROZEN / "cache" / "bt_px").exists():
        pytest.skip("沒有凍結資料(data/ 不進 git)")
    src = ROOT / "tests" / "baseline" / "outputs"
    for m in ("TW", "US"):
        df = pd.read_csv(src / f"backtest_{m}_rows.csv.gz")
        df["lt_score"] = df["lt_score"] * 1.01
        df.to_csv(tmp_path / f"backtest_{m}_rows.csv", index=False)
        shutil.copy(src / f"backtest_{m}.json", tmp_path / f"backtest_{m}.json")
    res = E.legacy_metrics(tmp_path, FROZEN / "cache", registry_path=tmp_path / "reg.jsonl")
    for m in ("TW", "US"):
        r = res["markets"][m]
        assert "dev" in r and "holdout" not in r and "all" not in r and "portfolio" not in r and "restricted" in r
        assert all(x["split"] == "dev" for x in r["pillar_ic"])


@pytest.mark.slow
def test_new_mode_on_v11_dev_runs(tmp_path):
    if not (FROZEN / "cache" / "bt_px").exists():
        pytest.skip("沒有凍結資料(data/ 不進 git)")
    for m in ("TW", "US"):
        px = E.v11_prices(m, FROZEN / "cache")
        rows = E.load_rows(ROOT / "tests" / "baseline" / "outputs" / f"backtest_{m}_rows.csv.gz")
        res = E.evaluate(rows, px["tr_index"], market=m, bench_tr=px["bench_tr"], bench_kind="price",
                         registry_path=tmp_path / "reg.jsonl")
        lt = res["primary"]["lt_12m"]
        assert lt["n_months"] == 21 and lt["last_month"] < "2024-10-01"   # 32 個開發集世代 → 21 個滿 12 世代的月份
        assert res["split_dates"]["12m"]["dev"] == 32   # 交易日切分 + t+1:比舊模式的 33 個月少 1 個


# ---------------- Red-team M2:預先登記、實際 m、trials_since、次數上限 ----------------

def _two_markets():
    mk = {m: _synth_market(s, months=30) for m, s in (("TW", 41), ("US", 42))}
    return ({m: v[0] for m, v in mk.items()}, {m: v[1] for m, v in mk.items()}, {m: v[2] for m, v in mk.items()})


def test_prereg_parses_repo_file():
    reg = prereg.load(ROOT / "docs" / "預先登記.md")
    assert len(reg) == 12 and reg["P3-H01"]["primary"] == "st_1m" and reg["P2-H07"]["markets"] == {"TW"} and reg["P2-H08"]["primary"] == "lt_6m"


def test_gate_rejects_unregistered_or_unapproved_family(tmp_path):
    base, new, prices = _two_markets()
    common = {"prices": prices, "registry_path": tmp_path / "reg.jsonl", "feature_keys": ["f"], "tamper_passed": True}
    with pytest.raises(ValueError, match="不在"):
        E.paired_gate(base, new, {**common, "change_type": "add", "family": "P9-H99"})
    with pytest.raises(ValueError, match="草稿"):
        E.paired_gate(base, new, {**common, "change_type": "add", "family": "P1-D01"})
    with pytest.raises(ValueError, match="類型"):
        E.paired_gate(base, new, {**common, "change_type": "fix", "family": "P1-T01"})
    with pytest.raises(ValueError, match="主要指標"):
        E.paired_gate(base, new, {**common, "change_type": "add", "family": "P1-T01", "primary": "lt_6m"})
    with pytest.raises(ValueError, match="只登記了"):
        E.paired_gate(base, new, {**common, "change_type": "fix", "family": "P1-F01"})
    # explore 的 fix 照常可以跑(不用登記)
    assert E.paired_gate(base, base, {**common, "change_type": "fix"})["trials"]["family"] == "explore"


def test_trials_since_only_for_chief(tmp_path, monkeypatch):
    base, new, prices = _two_markets()
    spec = {"change_type": "add", "prices": prices, "registry_path": tmp_path / "reg.jsonl", "family": "P1-T01",
            "feature_keys": ["f"], "tamper_passed": True, "trials_since": "2026-01-01"}
    monkeypatch.setenv("INVEST_LAB_AGENT", "B1")
    with pytest.raises(ValueError, match="維護者"):
        E.paired_gate(base, new, {**spec, "trials_since_reason": "x"})
    monkeypatch.setenv("INVEST_LAB_AGENT", "claude")
    with pytest.raises(ValueError, match="reason"):
        E.paired_gate(base, new, spec)
    res = E.paired_gate(base, new, {**spec, "trials_since_reason": "測試"})
    assert res["trials"]["since"] == "2026-01-01" and res["trials"]["since_reason"] == "測試"


def test_gate_reports_actual_m_and_caps_configs(tmp_path):
    base, new, prices = _two_markets()
    reg = tmp_path / "reg.jsonl"
    for i in range(4):
        registry.log("dev", {"k": i}, {}, market="TW", family="P1-T02", path=reg)
    spec = {"change_type": "add", "prices": prices, "registry_path": reg, "family": "P1-T02",
            "feature_keys": ["f"], "tamper_passed": True}
    res = E.paired_gate(base, new, spec)
    t = res["trials"]
    assert t["actual_dev_configs"] == 4 and t["m_used"] == 4 and not t["manual"] and t["gate_configs_before"] == 0
    for lbl in ("b", "c"):
        E.paired_gate(base, new, {**spec, "cfg": {"label": lbl}})
    assert E.paired_gate(base, new, spec)["trials"]["gate_configs_before"] == 2   # 同一個設定重跑不另計
    with pytest.raises(ValueError, match="上限"):
        E.paired_gate(base, new, {**spec, "cfg": {"label": "d"}})               # 第 4 個不同設定
    gate = _load_script("gate")
    md = gate.gate_markdown(res, {"TW": Path("b.csv")}, {"TW": Path("n.csv")})
    assert "Holm 的 m 下限 = **4**" in md and "實際 m = 4" in md and "第 1 個正式閘門設定" in md


def test_holdout_only_family_rejected_on_dev(tmp_path):
    base, new, prices = _synth_market(61, months=30)
    with pytest.raises(ValueError, match="開發集閘門不算證據"):
        E.paired_gate(base, new, {"change_type": "add", "market": "TW", "primary": "st_1m", "family": "P3-H01",
                                  "prices": {"TW": prices}, "registry_path": tmp_path / "r.jsonl",
                                  "feature_keys": ["f"], "tamper_passed": True})


def test_pretrials_of_same_scores_raise_m(tmp_path):
    """RedTeam2 問題 2、Verifier30 問題 1、2、4、5:同一份新分數在別處試跑過,正式閘門的 m 要算進去。"""
    base, new, prices = _two_markets()
    reg = tmp_path / "reg.jsonl"
    spec = {"change_type": "add", "prices": prices, "registry_path": reg, "family": "P1-T02",
            "feature_keys": ["f"], "tamper_passed": True}
    t0 = E.paired_gate(base, new, spec)["trials"]
    assert t0["pretrials"] == {"other_dev": 0, "calibration": 0} and t0["m_used"] == 1
    tr = {m: prices[m]["tr_index"] for m in prices}
    E.evaluate(base["TW"], tr["TW"], market="TW", registry_path=reg)                      # 另一份分數:不算
    E.evaluate(new["TW"], tr["TW"], market="TW", cfg={"label": "a"}, registry_path=reg)    # explore
    E.evaluate(new["TW"], tr["TW"], market="TW", family="baseline", registry_path=reg)     # 換族名也算
    E.evaluate(new["TW"], tr["TW"], market="TW", family="P1-T01", registry_path=reg)       # 別的族也算
    mono = new["TW"].assign(lt_score=new["TW"]["lt_score"] * 2 + 1, st_score=new["TW"]["st_score"] + 1e-9)
    E.evaluate(mono, tr["TW"], market="TW", cfg={"label": "mono"}, registry_path=reg)      # 單調轉換:同一份
    E.evaluate(new["US"], tr["US"], market="US", cfg={"label": "a"}, registry_path=reg)    # 兩市場各跑:取最大值
    noisy = new["TW"].assign(st_score=np.random.default_rng(0).standard_normal(len(new["TW"])))
    E.evaluate(noisy, tr["TW"], market="TW", cfg={"label": "st 雜訊"}, registry_path=reg)  # 只改 lt_12m 用不到的欄:同一份
    with E.calibration_session("試跑", registry_path=reg):
        E.gate_core({m: (base[m], new[m], prices[m]) for m in base}, {"change_type": "add", "family": "calibration"},
                    secondary=False, _calibration=True)
    t1 = E.paired_gate(base, new, spec)["trials"]
    assert t1["pretrials"] == {"other_dev": 4, "calibration": 1}   # baseline 與 P1-T01 兩次設定完全相同,算一次
    assert t1["m_floor"] == 6 and t1["m_used"] == 6   # max(1, 0) + 4 + 1
    with pytest.raises(ValueError, match="實際嘗試次數 6"):
        E.paired_gate(base, new, {**spec, "n_trials": 5})
    # _log=False 不能讓開發集評估不留紀錄
    n = len(registry.read(reg))
    E.evaluate(new["TW"], tr["TW"], market="TW", cfg={"label": "silent"}, registry_path=reg, _log=False)
    assert len(registry.read(reg)) == n + 1


def test_holdout_only_family_blocked_in_dev_evaluations(tmp_path):
    base, new, prices = _two_markets()
    with pytest.raises(ValueError, match="holdout"):
        E.evaluate(new["TW"], prices["TW"]["tr_index"], market="TW", family="P3-H01", registry_path=tmp_path / "r.jsonl")
    assert not (tmp_path / "r.jsonl").exists()


def test_registered_family_cannot_loosen_thresholds(tmp_path):
    """Verifier21 問題 1:alpha、margin、family_pvalues、single_market_ok 不在設定 hash 裡,預先登記的族不得改。"""
    base, new, prices = _two_markets()
    spec = {"change_type": "add", "prices": prices, "registry_path": tmp_path / "reg.jsonl", "family": "P1-T01",
            "feature_keys": ["f"], "tamper_passed": True}
    for extra, msg in (({"alpha": 0.9}, "alpha"), ({"family_pvalues": []}, "family_pvalues"),
                       ({"single_market_ok": True}, "single_market_ok")):
        with pytest.raises(ValueError, match=msg):
            E.paired_gate(base, new, {**spec, **extra})
    fix = {"change_type": "fix", "prices": {"TW": prices["TW"]}, "registry_path": tmp_path / "reg.jsonl", "family": "P1-F01"}
    with pytest.raises(ValueError, match="非劣性門檻"):
        E.paired_gate(base["TW"], new["TW"], {**fix, "market": "TW", "margin": 10})
    ok = E.paired_gate(base["TW"], base["TW"], {**fix, "market": "TW", "margin": E.FIX_MARGIN["lt_12m"]})
    assert ok["passed"]
    # explore 照舊可以自訂(只用來探索,add 不會通過)
    E.paired_gate(base, base, {"change_type": "fix", "prices": prices, "registry_path": tmp_path / "reg.jsonl", "alpha": 0.1})


def test_gate_flags_non_official_registry(tmp_path):
    """Verifier21 問題 3:不是主登記簿就在報告標「非正式閘門」。"""
    base, new, prices = _two_markets()
    res = E.paired_gate(base, base, {"change_type": "fix", "prices": prices, "registry_path": tmp_path / "reg.jsonl"})
    assert res["trials"]["registry_official"] is False
    md = _load_script("gate").gate_markdown(res, {"TW": Path("b")}, {"TW": Path("n")})
    assert "非正式閘門" in md


def test_prereg_default_reads_committed_main(monkeypatch):
    """Verifier21 問題 4:預設讀主 checkout 的 main 分支已 commit 版本,不讀工作目錄。"""
    monkeypatch.setattr(prereg, "PATH", None)
    calls = []
    real = prereg.subprocess.run

    def spy(args, **kw):
        calls.append(args)
        return real(args, **kw)
    monkeypatch.setattr(prereg.subprocess, "run", spy)
    reg = prereg.load()
    assert calls and calls[0][-1] == "main:docs/預先登記.md" and str(config.MAIN_ROOT) in calls[0]
    assert reg["P2-H01"]["status"] in prereg.STATUSES
    monkeypatch.setattr(prereg, "REF", "no-such-branch-xyz")
    with pytest.raises(ValueError, match="git show"):
        prereg.load()


# ---------------- 階段 5:holdout 配對(P3-H01);只用合成資料,不碰真的 holdout ----------------
def test_holdout_paired_needs_ticket():
    rows, tr = _det_market(start="2022-01-01", months=40)
    with pytest.raises(PermissionError):
        E.holdout_paired(rows, rows, tr, family="P3-H01")
    with pytest.raises(PermissionError):
        E.holdout_paired(rows, rows, tr, family="P3-H01", holdout_ticket="偽造的授權")


def test_holdout_paired_on_synthetic(monkeypatch):
    monkeypatch.setattr(config, "IS_WORKTREE", False)
    monkeypatch.setenv("INVEST_LAB_AGENT", "user")
    monkeypatch.setattr(E, "_TICKETS", {"t"})
    rows, tr = _det_market(start="2022-01-01", months=40)
    new = rows.assign(st_score=-rows["st_score"])               # 新版排名完全相反:IC +1 → −1
    r = E.holdout_paired(rows, new, tr, family="P3-H01", holdout_ticket="t")
    assert r["n_months"] > 0 and r["ic_base"] == pytest.approx(1.0) and r["ic_new"] == pytest.approx(-1.0)
    assert r["diff"] == pytest.approx(-2.0) and r["holm_m"] == 16
    with pytest.raises(ValueError):
        E.holdout_paired(rows, new, tr, family="P2-H07", holdout_ticket="t")   # 沒登記成 holdout 配對的族


def test_holdout_pair_verdict():
    ok = {"diff": 0.03, "ci95": [0.01, 0.05], "p_holm": 0.02}
    weak = {"diff": 0.01, "ci95": [-0.02, 0.04], "p_holm": 1.0}
    V = lambda a, b: E.holdout_pair_verdict({"TW_PIT50": a, "TW_LARGE40": b}, "P3-H01")   # noqa: E731
    assert V(ok, ok)["proven"] is True
    assert V(ok, weak)["proven"] is True                       # 對照池只要同向
    assert V(weak, ok)["proven"] is False                      # 主要池不顯著
    assert V({**ok, "p_holm": 0.2}, ok)["proven"] is False     # Holm 校正後不顯著
    assert V(ok, {**weak, "diff": -0.01})["proven"] is False   # 方向不一致
    assert V(ok, {"diff": None})["proven"] is False            # 樣本不足
    with pytest.raises(ValueError):
        E.holdout_pair_verdict({"TW_PIT50": ok}, "P3-H01")       # 股票池不完整不判定
    with pytest.raises(ValueError):
        E.holdout_pair_verdict({"TW_PIT50": ok, "US_LARGE40": ok}, "P3-H01")
