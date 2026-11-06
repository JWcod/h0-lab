"""M4:用回測的基礎變數快取(bt_pit/raw_{tw,us}.sqlite)與任一模型重算逐筆判斷的分數,不重跑判斷。

規格:docs/模型客製化規格.md §5。出廠模型重算的 lt_score / st_score / pillars / lt_level / st_code 必須和
`backtest_pit judge` 寫出的 rows_*.csv 逐筆相同(tests/test_model_eval.py、M4 驗收)。

- 只換「打分」:基礎變數(含模型估值差距、regime 分數)是判斷時算好的,所以估值方法權重(L4)不能在這裡改。
- cross_sectional_scoring / tw_industry_neutral_valuation 開著時拒絕:那兩個開關會在股票池層級改分數,快取重算不等價。
- lt_level / st_code 只給內部評估(evaluate.py 需要);我的模型不對外顯示等級(規格 §4.4)。
"""
from __future__ import annotations

import ast
import datetime as dt
import fcntl
import json
import os
import re
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config
from ..analysis import decision as DE
from ..analysis import scoring as SC
from ..models import engine, schema, store
from . import backtest_pit as BP
from . import evaluate as EV
from . import registry, splits

_env_cache: dict[tuple, dict] = {}


class ModelEvalError(RuntimeError):
    """無法用快取重算(訊息是繁體中文)。"""


def load_env(market: str) -> tuple[dict, dict]:
    """回傳 ({(date, symbol): (env, 當時的短期分數)}, meta)。同一個檔案(路徑 + 修改時間 + 大小)只讀一次。"""
    p = BP.raw_env_path(market)
    if not p.exists():
        raise ModelEvalError(f"沒有 {p.name}:先跑 `python -m lab.research.backtest_pit judge` 產生基礎變數快取")
    stt = p.stat()
    key = (str(p), stt.st_mtime_ns, stt.st_size)
    if key not in _env_cache:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            meta = dict(con.execute("SELECT k, v FROM meta"))
            cols = {r[1] for r in con.execute("PRAGMA table_info(env)")}
            if "st" not in cols:
                raise ModelEvalError(f"{p.name} 是舊格式(沒有逐筆核對欄),請重跑 judge")
            env = {(d, s): (json.loads(e), st) for d, s, e, st in con.execute("SELECT date, symbol, env, st FROM env")}
        finally:
            con.close()
        _env_cache.clear()
        _env_cache[key] = (env, meta)
    return _env_cache[key]


def _params_for(model: dict, params: dict, shipped: bool) -> dict:
    """把模型的權重與門檻套進參數;P3-H01 開關(tw_st_drop_momentum)只對出廠模型生效。"""
    p = dict(params)
    p["pillar_weights"] = model["pillar_weights"]
    p["short_term_thresholds"] = model["short_term_thresholds"]
    if not shipped:
        p["tw_st_drop_momentum"] = False
    return p


def _same(a, b) -> bool:
    na, nb = a is None or pd.isna(a), b is None or pd.isna(b)
    return na and nb or (not na and not nb and float(a) == float(b))


# 只有某個市場才有的籌碼變數(另一個市場的快取本來就沒有這些鍵)
MARKET_ONLY_VARS = {
    "TW": {"big1000_chg", "foreign_d20_pct", "foreign_hold_chg_60d", "foreign_streak", "holders_chg",
           "margin_chg_20d", "margin_to_shares", "trust_d20_pct"},
    "US": {"insider_buy_count_6m", "insider_present", "insider_sell_value_6m", "inst_net_adding",
           "short_chg_vs_prior", "short_pct_float"},
}


def _check_env_vars(env: dict, market: str) -> None:
    """快取是舊版(產生後 BASE_VARS 又新增變數)時直接報錯;不然引用新變數的模型會默默拿到 None。
    只看前 200 筆的鍵(值是 None 也有鍵);另一個市場專屬的籌碼變數不檢查。"""
    seen: set = set()
    for i, (e, _) in enumerate(env.values()):
        seen |= set(e)
        if i >= 200:
            break
    other = set().union(*(v for k, v in MARKET_ONLY_VARS.items() if k != market.upper()))
    missing = sorted(set(SC.BASE_VARS) - seen - other) if seen else []
    if missing:
        raise ModelEvalError(f"基礎變數快取 raw_{market.lower()}.sqlite 是舊版,缺 {', '.join(missing)};"
                             "請重跑 `python -m lab.research.backtest_pit judge`")


def rescore_rows(rows: pd.DataFrame, model: dict, market: str, params: dict, shipped: bool = False,
                 holdout_ticket: str | None = None) -> pd.DataFrame:
    """rows = 某股票池的 rows_*.csv(DataFrame);回傳同欄位的新 DataFrame,分數換成 model 算的。
    holdout 鎖:判斷日在 HOLDOUT_START 以後的列,分數一律遮成 NaN(等級、短期分數等級也清掉);
    只有帶 holdout 授權(scripts/holdout.py 或 M5 的 holdout 路徑)才會算。"""
    if params.get("cross_sectional_scoring") or params.get("tw_industry_neutral_valuation"):
        raise ModelEvalError("參數開了橫斷面打分(cross_sectional_scoring / tw_industry_neutral_valuation),快取重算不等價")
    cuts = (params.get("decision_rules") or {}).get("fixed_quantile_levels")
    if not cuts:
        raise ModelEvalError("參數沒有固定分位等級(decision_rules.fixed_quantile_levels),快取重算不支援舊的等級規則")
    if holdout_ticket is not None:
        EV._check_split("holdout", holdout_ticket)
    env, meta = load_env(market)
    if meta.get("data_dir") and meta["data_dir"] != config.DATA_DIR.name:
        raise ModelEvalError(f"快取是用資料夾 {meta['data_dir']} 產生的,現在用的是 {config.DATA_DIR.name};請重跑 judge")
    if meta.get("cross_sectional_scoring") == "True" or meta.get("tw_industry_neutral_valuation") == "True":
        raise ModelEvalError("這份快取是在橫斷面打分開著時產生的,重算不等價;請用目前的參數重跑 judge")
    _check_env_vars(env, market)
    p = _params_for(model, params, shipped)
    lt_w = p["pillar_weights"]["long_term"]
    st_w = SC.short_term_weights(p, market)
    dts = pd.to_datetime(rows["date"])
    dates = dts.dt.strftime("%Y-%m-%d")
    hide = (dts >= pd.Timestamp(splits.HOLDOUT_START)).to_numpy() if holdout_ticket is None else None
    lt_s, st_s, st_c, pil = [], [], [], []
    missing = stale = 0
    for i, (d, s, st0) in enumerate(zip(dates, rows["symbol"], rows["st_score"])):
        hit = env.get((d, s))
        if hit is None or not _same(hit[1], st0):
            missing += hit is None
            stale += hit is not None
            lt_s.append(None)
            st_s.append(None)
            st_c.append(0)
            pil.append(None)
            continue
        if hide is not None and hide[i]:
            lt_s.append(None)
            st_s.append(None)
            st_c.append(0)
            pil.append(None)
            continue
        e = hit[0]
        pillars = engine.score_pillars(model, e)
        pillars["regime"] = {"score": e.get("regime")}
        lt = SC.composite(pillars, lt_w)
        st = SC.composite(pillars, st_w)
        lt_s.append(lt.get("score"))
        st_s.append(st.get("score"))
        st_c.append(DE.short_term(st, {}, p).get("code"))
        pil.append(str({k: v.get("score") for k, v in pillars.items()}))
    if missing or stale:
        raise ModelEvalError(f"快取少了 {missing} 筆、對不上 {stale} 筆判斷:快取和逐筆檔不是同一次 judge 產生的,請重跑 judge")
    out = rows.copy()
    out["lt_score"] = pd.array(lt_s, dtype="Float64").astype(float)
    out["st_score"] = pd.array(st_s, dtype="Float64").astype(float)
    out["st_code"] = st_c
    out["pillars"] = pil
    # 和 judge() 一樣:原本有評等的列(有公平價)才排名;holdout 遮掉的列沒有分數,不參與排名(按日排名,不影響開發集)
    r = out.assign(date=pd.to_datetime(out["date"]))
    out["lt_level"] = BP.fixed_quantile_levels(r, cuts).to_numpy()
    if hide is not None:
        out.loc[hide, "lt_level"] = float("nan")
        out.loc[hide, "st_code"] = None
    return out


_NAN = re.compile(r"\bnan\b")


def pillars_of(rows: pd.DataFrame) -> list[dict]:
    """pillars 欄(dict 的 str)→ dict;nan 讀成 float('nan')。"""
    return [{k: (float("nan") if v is None and f"'{k}': nan" in x else v)
             for k, v in ast.literal_eval(_NAN.sub("None", x)).items()} if isinstance(x, str) else {}
            for x in rows["pillars"]]


def timed_rescore(universe: str, model: dict, params: dict, shipped: bool = False) -> tuple[pd.DataFrame, float]:
    """讀 OUT/bt_pit/rows_{universe}.csv 並重算;回傳 (DataFrame, 秒數)。"""
    t0 = time.time()
    market = BP.UNIVERSES[universe]
    rows = pd.read_csv(BP.OUT / f"rows_{universe}.csv")
    out = rescore_rows(rows, model, market, params, shipped)
    return out, time.time() - t0



# ---------------- M5:誠實驗證(規格 §4) ----------------
# 試了幾次(trials.jsonl)、使用者自己的實驗登記簿、開發集回測、holdout 每個模型族一次。全部只寫私人資料夾 models/。

SHIPPED_TRIALS_NOTE = ("出廠模型在開發時已經試了 37 個設定(v2 實驗登記簿,截至 2026-10-27)。"
                       "這裡的「設定」是登記簿的定義,和你的模型的評分雜湊不同,兩個數字分開看,不加總")
MULTI_NOTE = "試越多次,越容易碰巧找到好看的結果;所以 p 值乘上試過的次數(Bonferroni 校正)"
HOLDOUT_DIRTY = "已看過,不是乾淨的樣本外"
RANDOM_DRAWS = 30   # 隨機排序對照組抽幾次(回報分布,不給判定字眼)
# 使用者模型的 holdout 開關(2026-11-02 專案決定;start.sh 預設設 1,其他啟動方式預設關)
USER_HOLDOUT_ENV = "INVEST_LAB_USER_HOLDOUT"
DISCLAIMER = "回測結果不代表未來報酬,非投資建議"


def _priv(name: str) -> Path:
    return store.models_dir() / name


def user_registry() -> Path:
    """使用者模型呼叫 evaluate 時用的登記簿(不寫進主登記簿 data/research/experiments.jsonl)。"""
    return _priv("experiments.jsonl")


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _append_jsonl(p: Path, rec: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def trial_count(family: str) -> int:
    """這個模型族試過幾個不同的評分雜湊(同一個雜湊重跑不加)。"""
    return len({r.get("score_hash") for r in _read_jsonl(_priv("trials.jsonl")) if r.get("family") == family})


def bonferroni(p, n: int):
    return None if p is None else min(1.0, float(p) * max(int(n), 1))


def _check_user_model(model: dict, params: dict) -> str:
    """回傳評分雜湊(含參數裡影響評分的開關);出廠模型(或分數定義和出廠模型相同)不走這條路。"""
    h = schema.score_hash(model, params)
    shipped = {schema.score_hash(schema.load_shipped(i, readonly=True), params) for i in store.shipped_ids()}
    if model.get("id") in store.shipped_ids() or h in shipped:
        raise ModelEvalError("出廠模型的回測與 holdout 只走 backtest_pit / scripts/holdout.py,不走模型工作台")
    return h


_px_cache: dict = {}


def _prices(market: str, symbols) -> dict:
    p = BP.paths()[market]
    key = (market, str(p), p.stat().st_mtime_ns if p.exists() else 0, tuple(sorted(set(symbols))))
    if key not in _px_cache:
        if not p.exists():
            raise ModelEvalError(f"沒有價格庫 {p.name}:先跑 `python -m lab.research.backtest_pit build`")
        _px_cache.clear()
        _px_cache[key] = BP.pit_prices(market, symbols)
    return _px_cache[key]


def _cal(px: dict):
    """沒有含息大盤時日曆是空的;和 evaluate_all 一樣改用個股價格建日曆。"""
    return px["calendar"] if len(px["calendar"]) else None


def random_control(rows: pd.DataFrame, seed_name: str) -> pd.DataFrame:
    """隨機排序對照組:每個判斷日內,只在「有判斷的列」之間打亂分數(長期:lt_score + lt_level 一組;
    短期:st_score + st_code 一組),固定 seed;判斷的筆數、哪些列有判斷都不變。"""
    rng = EV._rng(seed_name)
    out = rows.copy()
    for cols, judge in ((["lt_score", "lt_level"], "lt_level"), (["st_score", "st_code"], "st_code")):
        cols = [c for c in cols if c in out]
        ok = out[cols[0]].notna() & (out[judge].notna() if judge in out else True)
        for _, idx in out[ok].groupby("date").groups.items():
            perm = rng.permutation(len(idx))
            out.loc[idx, cols] = out.loc[idx, cols].to_numpy()[perm]
    return out


def _summ(ser: pd.DataFrame, H: int, n_trials: int | None) -> dict:
    s = EV._ic_summary(ser, H)
    out = {k: s.get(k) for k in ("ic", "ci95", "p", "n_months", "first_month", "last_month")}
    out["verdict"] = EV._verdict(s.get("ci95"))
    if n_trials is not None:
        out["p_bonferroni"] = pb = bonferroni(s.get("p"), n_trials)
        if n_trials >= 2 and out["verdict"].startswith("顯著") and (pb is None or pb >= 0.05):
            out["verdict"] = f"校正試了 {n_trials} 次之後,沒有證實有效"
    return out


PAIR_LABEL = {"shipped": "出廠模型", "random": "隨機對照"}


def _paired(ctx, base: pd.DataFrame, new: pd.DataFrame, score: str, judge: str, H: int, split: str,
            label: str) -> dict:
    rb, rn, cov = EV._pair_rows(base, new, score, judge)
    sb = EV._ic_series(ctx, rb, score, H, split, judge_col=judge)
    sn = EV._ic_series(ctx, rn, score, H, split, judge_col=judge)
    d = pd.DataFrame({"b": sb["ic"], "n": sn["ic"]}).dropna()
    nw = EV.newey_west((d["n"] - d["b"]).to_numpy(), H)
    ci = nw["ci"] if nw else None
    if not ci:
        v = "樣本不足"
    elif ci[0] <= 0 <= ci[1]:
        v = f"和{label}沒有差別"
    else:
        v = f"比{label}好" if ci[0] > 0 else f"比{label}差"
    return {"diff": nw["mean"] if nw else None, "ci95": ci, "n_months": int(len(d)), "verdict": v, "coverage": cov}


def _random_dist(ctx, mine: pd.DataFrame, score: str, judge: str, H: int, split: str, seed: str, ic_mine) -> dict:
    """隨機排序對照組抽 RANDOM_DRAWS 次的 IC 分布;只回報分布與我的模型排第幾,不給「顯著」之類的判定。"""
    ics = []
    for k in range(RANDOM_DRAWS):
        r = random_control(mine, f"{seed}|{k}")
        v = EV._ic_summary(EV._ic_series(ctx, r, score, H, split, judge_col=judge), H).get("ic")
        if v is not None and np.isfinite(v):
            ics.append(v)
    if not ics:
        return {"draws": 0}
    a = np.asarray(ics)
    return {"draws": len(a), "median": float(np.median(a)), "p2_5": float(np.quantile(a, 0.025)),
            "p97_5": float(np.quantile(a, 0.975)),
            "mine_share_above": None if ic_mine is None else float((a < ic_mine).mean()),
            "note": f"把每天的分數隨機打亂 {len(a)} 次;隨機排序也可能碰巧看起來有效,所以這裡只給分布"}


def _compare(ctx, variants: dict, split: str, n_trials: int, seed: str) -> dict:
    """variants = {名稱: rows};每個主要指標回報各自的 IC、mine 對其他組的配對差異、隨機對照的分布。"""
    out = {}
    for name, (score, judge, H) in EV.PRIMARY.items():
        blk = {}
        for k, r in variants.items():
            blk[k] = _summ(EV._ic_series(ctx, r, score, H, split, judge_col=judge), H, n_trials if k == "mine" else None)
        for k in variants:
            if k != "mine":
                blk[f"mine_vs_{k}"] = _paired(ctx, variants[k], variants["mine"], score, judge, H, split, PAIR_LABEL[k])
        blk["random"] = _random_dist(ctx, variants["mine"], score, judge, H, split, f"{seed}|{name}", blk["mine"]["ic"])
        out[name] = blk
    return out


PLAIN_TOP = 0.2      # 白話版:每月取長期分數前 20%
PLAIN_NOTE = "這是過去的模擬結果,沒有扣稅、沒有考慮流動性,不代表未來"


def _top_levels(rows: pd.DataFrame, frac: float = PLAIN_TOP) -> pd.DataFrame:
    """每個判斷日依長期分數取前 frac(至少 1 檔)標 lt_level = 3,其他列一律 2,讓 EV._portfolio_paths 的
    「model」策略 = 前 20% 等權、「universe」= 股票池等權。
    只在「有長期模型等級」的列之間排名(和 IC 評估的 _judged、random_control 打亂的範圍一致)。
    同分照原本列順序(穩定排序,實際上是代號順序);分數取到 0.1,切點同分的月份會讓結果差到約 0.5 個百分點。"""
    out = rows.copy()
    ok = out["lt_score"].notna() & (out["lt_level"].notna() if "lt_level" in out else True)
    out["lt_level"] = 2.0
    for _, idx in out[ok].groupby("date").groups.items():
        k = max(1, int(np.ceil(len(idx) * frac)))
        top = out.loc[idx, "lt_score"].sort_values(ascending=False, kind="mergesort").index[:k]
        out.loc[top, "lt_level"] = 3.0
    return out


def _curve(r: np.ndarray) -> list:
    """累積報酬;缺值(例如大盤某個成交日沒有價格)回傳 None,JSON 才不會出現 NaN。"""
    return [EV._f(x) for x in np.cumprod(1 + np.asarray(r, dtype=float)) - 1]


def _plain_stats(r: np.ndarray) -> dict:
    s = EV._equity_stats(r)
    return {k: s.get(k) for k in ("cagr", "total", "max_dd", "ann_vol", "months")}


def plain_summary(ctx, rows: pd.DataFrame, market: str, split: str, seed: str) -> dict:
    """過去表現白話版(規格 docs/上手精靈規格.md §5):前 20% 等權月調整 vs 股票池等權 vs 含息大盤,扣交易成本
    (EV.COSTS,和回測同一套);隨機選同樣檔數抽 RANDOM_DRAWS 次只給年化報酬的分布。數字只算 split 那一段。"""
    paths = EV._portfolio_paths(ctx, _top_levels(rows), market, split)
    P = paths["paths"]
    if not paths["months"]:
        return {"status": "樣本不足", "note": PLAIN_NOTE}
    top, uni, bench = np.array(P["model"]["net"]), np.array(P["universe"]["net"]), paths["bench"]
    # 隨機選股每月幾乎全換,扣成本後會被換手拖垮、讓我的模型看起來比較好;所以隨機對照一律和「扣成本前」比
    rnd = []
    for k in range(RANDOM_DRAWS):
        rp = EV._portfolio_paths(ctx, _top_levels(random_control(rows, f"{seed}|{k}")), market, split)
        c = EV._equity_stats(np.array(rp["paths"]["model"]["gross"])).get("cagr")
        if c is not None and np.isfinite(c):
            rnd.append(c)
    a = np.asarray(rnd)
    out = {
        "status": "ok", "split": split, "top_frac": PLAIN_TOP, "costs": EV.COSTS[market],
        "first_month": str(paths["months"][0].date()), "last_month": str(paths["months"][-1].date()),
        "start": str(ctx.entries[ctx.gpos[min(d for d in ctx.dates_in(1, split))]].date()),
        "months": [str(m.date()) for m in paths["months"]],
        "curves": {"top": _curve(top), "universe": _curve(uni), "bench": _curve(bench) if bench is not None else None},
        "stats": {"top": _plain_stats(top), "universe": _plain_stats(uni),
                  "bench": _plain_stats(bench) if bench is not None else None},
        "win_share_vs_universe": float((top > uni).mean()),
        "top_gross_cagr": EV._equity_stats(np.array(P["model"]["gross"])).get("cagr"),
        "avg_turnover": {k: EV._f(np.mean(P[k]["turnover"])) for k in ("model", "universe")},
        "random_cagr": ({"draws": len(a), "median": float(np.median(a)), "p2_5": float(np.quantile(a, 0.025)),
                         "p97_5": float(np.quantile(a, 0.975)), "basis": "扣成本前",
                         "compare_with": "top_gross_cagr"} if len(a) else {"draws": 0}),
        "fallback_months": P["model"]["fallback"],
        "note": PLAIN_NOTE + ";成本照回測設定(台股手續費 0.1425% + 賣出證交稅 0.3%,美股單邊 0.05%)",
    }
    return out


def _unvalidated(rows: pd.DataFrame) -> bool:
    return not len(rows) or rows["lt_score"].notna().sum() + rows["st_score"].notna().sum() == 0


def _family(model: dict) -> str:
    return model.get("family_id") or model["id"]


def _load_rows(universe: str) -> pd.DataFrame:
    if universe not in BP.UNIVERSES:
        raise ModelEvalError(f"沒有這個股票池:{universe}")
    p = BP.OUT / f"rows_{universe}.csv"
    if not p.exists():
        raise ModelEvalError(f"沒有 {p.name}:先跑 `python -m lab.research.backtest_pit judge`")
    return pd.read_csv(p)


def backtest(model: dict, universe: str, params: dict) -> dict:
    """開發集回測:我的模型、出廠模型、隨機排序對照組。寫 trials.jsonl(新雜湊才加計數)與使用者登記簿。"""
    base_rows = _load_rows(universe)
    h = _check_user_model(model, params)
    market = BP.UNIVERSES[universe]
    family = _family(model)
    mine = rescore_rows(base_rows, model, market, params)
    shipped = rescore_rows(base_rows, schema.load_shipped(SC.DEFAULT_MODEL, readonly=True), market, params, shipped=True)
    t0 = time.time()
    px = _prices(market, base_rows["symbol"].unique())
    tr = px["tr_index"]
    keep = lambda r: r[r["symbol"].isin(tr)].assign(date=lambda x: pd.to_datetime(x["date"]))  # noqa: E731
    variants = {"mine": keep(mine), "shipped": keep(shipped)}
    if _unvalidated(variants["mine"]):
        return {"status": "未驗證", "reason": "這個模型在股票池的歷史上算不出分數(指標全部無法回測)", "disclaimer": DISCLAIMER}
    seen = {r.get("score_hash") for r in _read_jsonl(_priv("trials.jsonl")) if r.get("family") == family}
    n = len(seen | {h})
    ctx = EV._Ctx(variants["mine"], tr, calendar=_cal(px), bench_tr=px["bench_tr"])
    res = _compare(ctx, variants, "dev", n, f"random|{universe}|{h}")
    plain = plain_summary(ctx, variants["mine"], market, "dev", f"plain|{universe}|{h}")
    if plain.get("status") == "ok" and res["lt_12m"]["mine"]["verdict"] != "顯著為正":
        plain["note"] = "長期分數的 IC 沒有證實有效;" + plain["note"]
    metrics = {k: {"ic": v["mine"]["ic"], "p": v["mine"]["p"]} for k, v in res.items()}
    if h not in seen:
        _append_jsonl(_priv("trials.jsonl"), {"time": dt.datetime.now().isoformat(timespec="seconds"), "family": family,
                                             "model_id": model["id"], "score_hash": h, "universe": universe,
                                             "metrics": metrics})
    registry.log("dev", {"source": "model_workbench", "model": model["id"], "score_hash": h, "universe": universe,
                         "rows": EV.rows_fingerprint(variants["mine"]), "eval_version": EV.EVAL_VERSION},
                 metrics, market=market, universe=universe, data_vintage=BP.vintage_label(), family=f"user:{family}",
                 path=user_registry())
    return {"status": "開發集", "split": "dev", "model": model["id"], "family": family, "score_hash": h,
            "universe": universe, "n_trials": n, "multi_note": MULTI_NOTE, "shipped_note": SHIPPED_TRIALS_NOTE,
            "metrics": res, "plain": plain, "seconds": round(time.time() - t0, 2), "disclaimer": DISCLAIMER,
            "holdout": holdout_status(model, params)}


def holdout_status(model: dict, params: dict) -> dict:
    """同一個評分雜湊在任何模型族看過、或族譜(store.related_families)裡任何一族看過,都算「已看過」。"""
    h = schema.score_hash(model, params)
    log = _read_jsonl(_priv("holdout_log.jsonl"))
    fams = store.related_families(_family(model))
    same = [r for r in log if r.get("score_hash") == h]
    seen = same or [r for r in log if r.get("family") in fams]
    return {"viewed": bool(same), "family_viewed": bool(seen), "clean": not seen,
            "label": HOLDOUT_DIRTY if seen else "還沒看過(乾淨的樣本外,每個模型族只有一次)"}


def holdout(model: dict, universe: str, params: dict, confirm: bool = False) -> dict:
    """使用者模型的 holdout(2024-10-01 以後):要 confirm;同一個評分雜湊只算一次(之後回傳紀錄);
    族譜看過之後,新版本仍可算但標「已看過,不是乾淨的樣本外」。不和出廠模型比較。
    已知限制:擋 agent 的防線是 INVEST_LAB_AGENT(和 scripts/holdout.py 一樣);把出廠模型權重改一點點也算「我的模型」,
    所以這條路只給使用者自己看,不得拿來調出廠模型(規格 §4.2)。"""
    EV.holdout_guard()
    if os.environ.get(USER_HOLDOUT_ENV) != "1":
        raise ModelEvalError(f"使用者模型的 holdout 沒有開啟:用 ./start.sh 啟動,或啟動時設 {USER_HOLDOUT_ENV}=1")
    if not confirm:
        raise ModelEvalError("看 holdout 之前要確認(confirm: true);每個模型族只有一次乾淨的樣本外")
    base_rows = _load_rows(universe)
    h = _check_user_model(model, params)
    st = holdout_status(model, params)
    for r in _read_jsonl(_priv("holdout_log.jsonl")):
        if r.get("score_hash") == h and r.get("universe") == universe:
            return r["result"] | {"repeat": True}
    market = BP.UNIVERSES[universe]
    ticket = EV._issue_model_holdout_ticket()
    try:
        mine = rescore_rows(base_rows, model, market, params, holdout_ticket=ticket)
        px = _prices(market, base_rows["symbol"].unique())
        tr = px["tr_index"]
        mine = mine[mine["symbol"].isin(tr)].assign(date=lambda x: pd.to_datetime(x["date"]))
        ctx = EV._Ctx(mine, tr, calendar=_cal(px), bench_tr=px["bench_tr"], holdout_ticket=ticket)
        res = _compare(ctx, {"mine": mine}, "holdout", 1, f"random|{universe}|{h}|holdout")
        plain = plain_summary(ctx, mine, market, "holdout", f"plain|{universe}|{h}|holdout")
    finally:
        EV._TICKETS.discard(ticket)
    family = _family(model)
    out = {"status": "holdout", "split": "holdout", "model": model["id"], "family": family, "score_hash": h,
           "universe": universe, "clean": st["clean"], "label": "乾淨的樣本外" if st["clean"] else HOLDOUT_DIRTY,
           "metrics": res, "plain": plain, "disclaimer": DISCLAIMER,
           "note": "holdout 不和出廠模型比較(出廠模型的 holdout 只走 scripts/holdout.py)"}
    _append_jsonl(_priv("holdout_log.jsonl"), {"time": dt.datetime.now().isoformat(timespec="seconds"),
                                               "family": family, "model_id": model["id"], "score_hash": h,
                                               "universe": universe, "clean": st["clean"],
                                               "result": json.loads(json.dumps(out, default=str))})
    return out
