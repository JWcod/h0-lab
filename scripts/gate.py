"""合併閘門命令列(只用開發集;規則見 docs/架構.md §3.2,門檻見 docs/閘門校準.md)。

用法(在專案根目錄;開發工作階段先設好 INVEST_LAB_OFFLINE 等環境變數):
  # 比較基準與新版本的逐筆判斷檔(欄位同 tests/baseline/outputs/backtest_*_rows.csv.gz)
  .venv/bin/python scripts/gate.py compare --change-type fix \
      --base TW=tests/baseline/outputs/backtest_TW_rows.csv.gz --base US=tests/baseline/outputs/backtest_US_rows.csv.gz \
      --new TW=<新 TW 逐筆> --new US=<新 US 逐筆> --out <輸出資料夾>
  # 閘門校準(安慰劑與植入訊號;只用開發集)
  .venv/bin/python scripts/gate.py calibrate --reps 200 --out <輸出資料夾>
  # 一個版本的開發集指標(新模式)
  .venv/bin/python scripts/gate.py dev-metrics --rows TW=... --rows US=... --out <輸出資料夾>

價格(compare / dev-metrics 的 `--prices`):
  bt_px(預設):凍結快取的 bt_px(個股 Adj Close 當含息報酬;大盤是價格指數),配 v1.1 的逐筆檔。
  pit:PIT store 的含息報酬指數與含息大盤(`backtest_pit.pit_prices`),配 PIT 逐筆檔;INVEST_LAB_OUT_DIR 要指到含
       bt_pit/ store(pit_tw.sqlite 等)的回測輸出。逐筆檔超過 5% 代號沒有價格時會報錯,不會悄悄少評。
輸出:<out>/gate_<hash>.md(繁體中文報告)與 .json。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lab import config  # noqa: E402
from lab.research import evaluate as E  # noqa: E402
from lab.research import registry  # noqa: E402

DISCLAIMER = "> 研究用途,非投資建議。回測與閘門結果不代表未來報酬,也不保證獲利。"


def _kv(items: list[str]) -> dict[str, Path]:
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        out[k.strip().upper()] = Path(v).expanduser().resolve()
    return out


def _prices(markets) -> dict:
    return {m: E.v11_prices(m) for m in markets}


def _pit_prices(rows_by_market: dict) -> dict:
    """PIT store 的價格(需要 INVEST_LAB_OUT_DIR 指到含 bt_pit store 的回測輸出)。"""
    from lab.research import backtest_pit as BP
    P = BP.paths()
    need = [P["macro"]] + [P[m] for m in rows_by_market]
    lost = [str(x) for x in need if not Path(x).exists()]
    if lost:
        raise FileNotFoundError("找不到 PIT store(INVEST_LAB_OUT_DIR 要指到 backtest_pit 的回測輸出):" + "、".join(lost))
    out = {}
    for m, frames in rows_by_market.items():
        syms = sorted(set().union(*(set(f["symbol"]) for f in frames)))
        out[m] = BP.pit_prices(m, syms)
    return out


def _load_prices(kind: str, rows_by_market: dict) -> dict:
    """rows_by_market:{市場: [逐筆 DataFrame, ...]}。bt_px 用 _prices()(測試會替換它),pit 用 _pit_prices()。"""
    return _pit_prices(rows_by_market) if kind == "pit" else _prices(list(rows_by_market))


def _fmt(x, nd=3, pct=False, sign=True):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    sg = "+" if sign else ""
    return f"{x * 100:{sg}.{max(nd - 2, 1)}f}%" if pct else f"{x:{sg}.{nd}f}"


def _pc(x):
    return _fmt(x, pct=True, sign=False)


def _cip(c):
    return "—(樣本不足)" if not c else f"{_pc(c[0])} ~ {_pc(c[1])}"


def _ci(c, nd=3, pct=False):
    return "—(樣本不足)" if not c else f"{_fmt(c[0], nd, pct)} ~ {_fmt(c[1], nd, pct)}"


# ---------------- compare ----------------

def gate_markdown(res: dict, base_files: dict, new_files: dict) -> str:
    L = [f"# 合併閘門報告({'增加特徵 / 參數' if res['change_type'] == 'add' else '簡化 / 修錯'})", "", DISCLAIMER, "",
         f"- 結果:**{'通過' if res['passed'] else '不通過'}**",
         f"- 主要指標:`{res['primary']}`(重疊世代月 IC,Newey–West lag = {res['nw_lag']})",
         f"- 設定 hash:`{res.get('config_hash', '')}`;評估版本:{res.get('version', '')};產生時間:{dt.datetime.now():%Y-%m-%d %H:%M}",
         "- 基準:" + "、".join(f"{m} `{p.name}`" for m, p in base_files.items()),
         "- 新版本:" + "、".join(f"{m} `{p.name}`" for m, p in new_files.items()),
         f"- 假設族:`{res.get('family', 'explore')}`;主要指標只用新舊雙方都湊滿 H 個世代的月份", ""]
    t = res.get("trials")
    if t:
        L += [f"- 嘗試次數:登記簿裡假設族 `{t['family']}` 的開發集設定數 **{t['actual_dev_configs']}**;Holm 的 m 下限 = **{t['m_used']}**(add 的實際 m 見下方 Holm 校正一行)"
              f"({'手動指定' if t['manual'] else '自動讀登記簿'});這是本族第 {t['gate_configs_before'] + 1} 個正式閘門設定"
              f"(上限 {t['gate_configs_max']})"]
        if not t.get("registry_official", True):
            L += [f"- **警告:非正式閘門**:用的登記簿是 `{t.get('registry')}`,不是主登記簿,嘗試次數與上限不可信;維護者只採信主登記簿的結果"]
        if t.get("since"):
            L += [f"- **注意:嘗試次數從 {t['since']} 起算**(維護者指定),理由:{t.get('since_reason')}"]
        L += [""]
    for w in res.get("warnings", []):
        L += [f"> **警告:{w}**", ""]
    L += ["## 理由", ""]
    L += [f"- {r}" for r in res["reasons"]]
    L += ["", "## 主要指標差異(新 − 舊,只用雙方都有判斷的交集,開發集)", "",
          "| 市場 | 月數 | 舊 IC | 新 IC | 差異 | NW 標準誤 | 95% 區間 | p |", "|---|---|---|---|---|---|---|---|"]
    for m, v in res["markets"].items():
        L.append(f"| {m} | {v['n_months']} | {_fmt(v['ic_base'])} | {_fmt(v['ic_new'])} | {_fmt(v['diff'])} | "
                 f"{_fmt(v['se_nw'])} | {_ci(v['ci95'])} | {v['p'] if v['p'] is None else round(v['p'], 4)} |")
    p = res["pooled"]
    L.append(f"| 合併 | {p['n_months']} | | | {_fmt(p['diff'])} | {_fmt(p['se_nw'])} | {_ci(p['ci95'])} | "
             f"{p['p'] if p['p'] is None else round(p['p'], 4)} |")
    if "holm" in res:
        h = res["holm"]
        L += ["", f"Holm 校正:實際 m = {h.get('m_effective', h['n_trials'])}(登記簿設定數 {h['n_trials']}、已知 p 值 {h['known_family_p']} 個),本檢定名次 {h['rank']},"
                  f"門檻 α = {h['alpha_adjusted']:.4g};校正後區間 {_ci(res['pooled'].get('ci_holm'))}"]
    if "noninferiority" in res:
        n = res["noninferiority"]
        L += ["", f"非劣性:合併差異 95% 下界 {_fmt(n['pooled_ci95_lower'])},門檻 −{n['margin']}"]
    s = res["subperiods"]
    L += ["", f"子期間:前半(自 {s['first']['from']})差異 {_fmt(s['first']['diff'])};後半(至 {s['second']['to']})差異 {_fmt(s['second']['diff'])}",
          "", "## 覆蓋率(有判斷的列 / 兩版聯集)", "", "| 市場 | 聯集列數 | 舊 | 新 | 差 | 交集 |", "|---|---|---|---|---|---|"]
    for m, c in res["coverage"].items():
        L.append(f"| {m} | {c['n_rows_union']} | {_pc(c['coverage_base'])} | {_pc(c['coverage_new'])} | "
                 f"{_fmt(c['coverage_diff'], pct=True)} | {c['n_both']} |")
    L += ["", "## 次要指標(固定清單,Holm 校正;「比較差」單尾檢定)", "", "| 市場 | 指標 | 差異 | 95% 區間 | p(變差) | 判定用 | Holm 後顯著變差 |",
          "|---|---|---|---|---|---|---|"]
    for v in res["secondary"]:
        L.append(f"| {v['market']} | {v['metric']} | {_fmt(v['diff'], 4)} | {_ci(v['ci95'], 4)} | "
                 f"{'—' if v['p_worse'] is None else round(v['p_worse'], 4)} | {'是' if v['decides'] else '只報告'} | "
                 f"{'是' if v.get('significantly_worse_holm') else '否'} |")
    L += ["", "改善落在信賴區間內一律視為「沒有證實有效」。資料改動只用正確性判斷,不看這份報告。", ""]
    return "\n".join(L)


def cmd_compare(a) -> int:
    base_f, new_f = _kv(a.base), _kv(a.new)
    if set(base_f) != set(new_f):
        print("基準與新版本的市場不一致", file=sys.stderr)
        return 2
    base = {m: E.load_rows(p) for m, p in base_f.items()}
    new = {m: E.load_rows(p) for m, p in new_f.items()}
    try:
        prices = _load_prices(a.prices, {m: [base[m], new[m]] for m in base})
    except FileNotFoundError as e:
        print(f"錯誤:{e}", file=sys.stderr)
        return 2
    spec = {"change_type": a.change_type, "prices": prices, "primary": a.primary, "alpha": a.alpha,
            "single_market_ok": a.single_market_ok, "cfg": {"label": a.label}, "data_vintage": config.DATA_DIR.name,
            "family": a.family, "feature_keys": a.feature_keys, "tamper_passed": a.tamper_passed}
    if a.prices != "bt_px":
        spec["cfg"]["prices"] = a.prices
    if a.margin is not None:
        spec["margin"] = a.margin
    if a.n_trials is not None:
        spec["n_trials"] = a.n_trials
    if a.trials_since:
        spec["trials_since"] = a.trials_since
        spec["trials_since_reason"] = a.trials_since_reason or ""
    try:
        res = E.paired_gate(base, new, spec)
    except ValueError as e:
        print(f"錯誤:{e}", file=sys.stderr)
        return 2
    out = Path(a.out or (config.OUT_DIR / "gate")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    stem = f"gate_{res['config_hash']}"
    (out / f"{stem}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / f"{stem}.md").write_text(gate_markdown(res, base_f, new_f), encoding="utf-8")
    print(f"{'通過' if res['passed'] else '不通過'}:{'; '.join(res['reasons'])}")
    print(f"報告:{out / (stem + '.md')}")
    return 0 if res["passed"] else 1


# ---------------- calibration(P2-H09 的 Brier 判準) ----------------

def cmd_calibration(a) -> int:
    from lab.research import prereg
    base_f, new_f = _kv(a.base), _kv(a.new)
    if set(base_f) != set(new_f):
        print("基準與新版本的市場不一致", file=sys.stderr)
        return 2
    try:
        prereg.check(a.family, "fix", "lt_12m", base_f)   # 預先登記:P2-H09 是 fix、lt_12m
    except ValueError as e:
        print(f"錯誤:{e}", file=sys.stderr)
        return 2
    base = {m: E.load_rows(p) for m, p in base_f.items()}
    new = {m: E.load_rows(p) for m, p in new_f.items()}
    prices = _load_prices("pit", {m: [base[m], new[m]] for m in base})
    res = E.calibration_brier(new, base, prices, family=a.family, cfg={"label": a.label}, data_vintage=config.DATA_DIR.name)
    out = Path(a.out or (config.OUT_DIR / "gate")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "calibration_brier.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    L = ["# 機率校準 Brier 比較(P2-H09,開發集)", "", DISCLAIMER, "", f"- 結果:**{'通過' if res['passed'] else '不通過'}**:{res['reason']}",
         f"- 對照(v1.1 評等的開發集實際頻率):{res['level_freq_dev']}", f"- 最後一次擬合:{res['last_fit']}", "",
         "| 市場 | 月數 | 列數 | Brier 新 | Brier 對照 | Brier 對照(walk-forward,只報告) | 差異 | 95% 區間 | p |", "|---|---|---|---|---|---|---|---|---|"]
    for m, v in res["markets"].items():
        L.append(f"| {m} | {v['n_months']} | {v['n_rows']} | {_fmt(v['brier_new'], 4, sign=False)} | {_fmt(v['brier_base'], 4, sign=False)} | "
                 f"{_fmt(v['brier_base_wf'], 4, sign=False)} | {_fmt(v['diff'], 4)} | {_ci(v['ci95'], 4)} | {v['p'] if v['p'] is None else round(v['p'], 4)} |")
    pl = res["pooled"]
    L.append(f"| 合併 | {pl['n_months']} | | | | | {_fmt(pl['diff'], 4)} | {_ci(pl['ci95'], 4)} | {pl['p'] if pl['p'] is None else round(pl['p'], 4)} |")
    (out / "calibration_brier.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"{'通過' if res['passed'] else '不通過'}:{res['reason']}")
    print(f"報告:{out / 'calibration_brier.md'}")
    return 0 if res["passed"] else 1


# ---------------- dev-metrics ----------------

def dev_markdown(all_res: dict) -> str:
    L = ["# 開發集指標(新模式)", "", DISCLAIMER, "", "主要指標只用湊滿 H 個世代的月份;「加權敏感度」包含頭尾不滿的月份、依世代數加權。", "",
         "| 市場 | 指標 | IC | NW 95% 區間 | 滿世代月數 | 加權敏感度 IC(月數) | 三分位價差(月) | 價差 95% 區間 |",
         "|---|---|---|---|---|---|---|---|"]
    for m, r in all_res.items():
        for k, v in list(r["primary"].items()) + [("st_3m(次要)", r["secondary"]["st_3m_ic"])]:
            sp = v.get("tercile_spread_monthly") or {}
            sw = v.get("sensitivity_cohort_weighted") or {}
            L.append(f"| {m} | {k} | {_fmt(v.get('ic'))} | {_ci(v.get('ci95'))} | {v.get('n_months')} | "
                     f"{_fmt(sw.get('ic'))}({sw.get('n_months')}) | {_fmt(sp.get('mean'), pct=True)} | {_ci(sp.get('ci95'), pct=True)} |")
    L += ["", "| 市場 | 命中率 | 對 / 錯 / 平手 | 命中率 | 覆蓋率 | 區塊 CI | 固定 20% 命中率 | 固定 CI | 沒有評等 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for m, r in all_res.items():
        for k, v in r["secondary"]["hit"].items():
            if "general" not in v:
                L.append(f"| {m} | {k} | 開發集沒有資料 | — | — | — | — | — | — |")
                continue
            g, f = v["general"], v["fixed_coverage_20"]
            L.append(f"| {m} | {k} | {g['right']} / {g['wrong']} / {g['ties']} | {_pc(g['hit'])} | "
                     f"{_pc(g['coverage'])} | {_cip((g['ci95_block'] or {}).get('ci'))} | "
                     f"{_pc(f['hit'])} | {_cip((f['ci95_block'] or {}).get('ci'))} | {v['unrated']['n']} |")
    L += ["", "| 市場 | 投組比較(成本後) | 年化差 | NW 95% 區間 | 區塊 CI | 結論 |", "|---|---|---|---|---|---|"]
    for m, r in all_res.items():
        for d in r["secondary"]["portfolio"]["diffs"]:
            L.append(f"| {m} | {d['a']} − {d['b']} | {_fmt(d['ann_mean_diff'], pct=True)} | {_ci(d['ci95_nw'], pct=True)} | "
                     f"{_ci(d['ci95_block3'], pct=True)} | {d['verdict']} |")
    return "\n".join(L) + "\n"


def cmd_dev_metrics(a) -> int:
    files = _kv(a.rows)
    rows = {m: E.load_rows(p) for m, p in files.items()}
    try:
        prices = _load_prices(a.prices, {m: [r] for m, r in rows.items()})
    except FileNotFoundError as e:
        print(f"錯誤:{e}", file=sys.stderr)
        return 2
    all_res = {}
    for m in files:
        px = prices[m]
        all_res[m] = E.evaluate(rows[m], px["tr_index"], market=m, bench_tr=px["bench_tr"], bench_kind=px["bench_kind"],
                                cfg={"label": a.label, **({"prices": a.prices} if a.prices != "bt_px" else {})},
                                calendar=px.get("calendar"), data_vintage=config.DATA_DIR.name, note=f"dev-metrics {a.label}")
    out = Path(a.out or (config.OUT_DIR / "gate")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / f"dev_metrics_{a.label}.json").write_text(json.dumps(all_res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    md = dev_markdown(all_res)
    (out / f"dev_metrics_{a.label}.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


# ---------------- calibrate ----------------

def _zscore_by_date(df: pd.DataFrame, col: str) -> pd.Series:
    g = df.groupby("date")[col]
    return (df[col] - g.transform("mean")) / g.transform("std").replace(0, np.nan)


def _rank_normal_by_date(df: pd.DataFrame, col: str) -> pd.Series:
    from scipy.stats import norm
    r = df.groupby("date")[col].rank()
    n = df.groupby("date")[col].transform("count")
    return pd.Series(norm.ppf((r - 0.5) / n), index=df.index)


def _planted_component(judged: pd.DataFrame, x12: pd.Series, target_ic: float, rng) -> np.ndarray:
    """與 12 個月前瞻超額報酬的橫斷面 Spearman IC ≈ target_ic 的合成成分(單位變異數)。沒有前瞻報酬的列是純雜訊。"""
    rho = target_ic / 0.955  # 常態分數下 Spearman ≈ (6/π)·asin(ρ/2) ≈ 0.955ρ
    u = _rank_normal_by_date(judged.assign(_x=x12), "_x").fillna(0.0).to_numpy()
    has = x12.notna().to_numpy()
    eps = rng.standard_normal(len(judged))
    return np.where(has, rho * u + math.sqrt(1 - rho ** 2) * eps, eps)


def cmd_calibrate(a) -> int:
    t0 = time.time()
    reps = a.reps
    markets = ("TW", "US")
    score, judge, H = E.PRIMARY[a.primary]
    hz = E.MONTHS_TO_HORIZON[H]
    prices = _prices(markets)
    base, ctxs, judged, x12, zs = {}, {}, {}, {}, {}
    for m in markets:
        rows = E.load_rows(Path(a.rows_dir) / f"backtest_{m}_rows.csv.gz")
        base[m] = rows
        ctxs[m] = E._Ctx(rows, prices[m]["tr_index"], lag=1, bench_tr=prices[m]["bench_tr"])
        jm = rows[judge].notna() & rows[score].notna()
        judged[m] = rows[jm].copy()
        ex = E._excess(ctxs[m], hz)  # 主要指標預測期的前瞻超額報酬(t+1 成交)
        x12[m] = judged[m][["date", "symbol"]].merge(ex, on=["date", "symbol"], how="left")["x"].set_axis(judged[m].index)
        zs[m] = _zscore_by_date(judged[m], score)
    margins = [0.0, 0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05]

    def make_new(kind: str, rng) -> dict:
        out = {}
        for m in markets:
            j = judged[m]
            new = base[m].copy()
            if kind == "placebo_shuffle":
                s = j.groupby("date")[score].transform(lambda v: rng.permutation(v.to_numpy()))
            elif kind.startswith("placebo_noise"):
                w = float(kind.split("_w")[1])
                s = zs[m] + w * rng.standard_normal(len(j))
            elif kind.startswith("equiv"):
                w = float(kind.split("_w")[1])
                s = zs[m] + w * rng.standard_normal(len(j))
            elif kind.startswith("plant"):
                # plant12_ic0.03_w1.0:與預測期(12 個月)超額報酬 IC 0.03 的成分;
                # plantM_…:目標換算成主要指標的月 IC 0.03(預測期 IC ≈ 0.03·√H)
                _, ic, w = kind.split("_")
                tgt = float(ic[2:]) * (math.sqrt(H) if kind.startswith("plantM") else 1.0)
                s = zs[m] + float(w[1:]) * _planted_component(j, x12[m], tgt, rng)
            else:
                raise ValueError(kind)
            new.loc[j.index, score] = np.asarray(s, dtype=float)
            out[m] = new
        return out

    kinds = ["placebo_shuffle", "placebo_noise_w1.0", "plant12_ic0.03_w0.5", "plant12_ic0.03_w1.0",
             "plantM_ic0.03_w0.5", "plantM_ic0.03_w1.0", "equiv_w0.25", "equiv_w0.5"]
    summary = {}
    spec = {"change_type": "add", "primary": a.primary, "n_trials": 1, "family_pvalues": [], "alpha": 0.05,
            "family": "calibration"}
    with E.calibration_session(f"calibrate|{a.primary}|reps={reps}"):   # 每次評估的新分數指紋寫進登記簿(RedTeam2 問題 2)
        for kind in kinds:
            rng = np.random.default_rng(E.metric_seed(f"calibrate|{kind}"))
            recs = []
            for _ in range(reps):
                new = make_new(kind, rng)
                pairs = {m: (base[m], new[m], prices[m]) for m in markets}
                g = E.gate_core(pairs, spec, ctxs=ctxs, secondary=False, _calibration=True)
                recs.append({"add": g["passed"], "diff": g["pooled"]["diff"], "lo": (g["pooled"]["ci95"] or [None])[0],
                             "p": g["pooled"]["p"], "TW": g["markets"]["TW"]["diff"], "US": g["markets"]["US"]["diff"],
                             "ic_new_TW": g["markets"]["TW"]["ic_new"], "ic_new_US": g["markets"]["US"]["ic_new"],
                             "ic_base_TW": g["markets"]["TW"]["ic_base"], "ic_base_US": g["markets"]["US"]["ic_base"],
                             "sig_pos": bool(g["pooled"]["p"] is not None and g["pooled"]["p"] < 0.05 and g["pooled"]["diff"] > 0),
                             "consistent": bool(g["markets"]["TW"]["diff"] > 0 and g["markets"]["US"]["diff"] > 0
                                                and all(x["diff"] is not None and x["diff"] > 0 for x in g["subperiods"].values())
                                                and g["pooled"]["n_months"] >= E.MIN_GATE_MONTHS.get(H, 24))})
            df = pd.DataFrame(recs)

            def fix_pass(row, mg):
                return row["lo"] is not None and row["lo"] >= -mg and row["TW"] >= -mg and row["US"] >= -mg
            summary[kind] = {
                "reps": reps, "add_pass_rate": float(df["add"].mean()), "pooled_sig_pos_rate": float(df["sig_pos"].mean()),
                "mean_diff": float(df["diff"].mean()), "sd_diff": float(df["diff"].std()), "mean_lower": float(df["lo"].mean()),
                "mean_diff_TW": float(df["TW"].mean()), "mean_diff_US": float(df["US"].mean()),
                "mean_ic_new_TW": float(df["ic_new_TW"].mean()), "mean_ic_new_US": float(df["ic_new_US"].mean()),
                "ic_base_TW": float(df["ic_base_TW"].iloc[0]), "ic_base_US": float(df["ic_base_US"].iloc[0]),
                "fix_pass_rate": {str(mg): float(df.apply(lambda r, mg=mg: fix_pass(r, mg), axis=1).mean()) for mg in margins},
                # Holm 第一步(其他嘗試的 p 值未知 → 門檻 α/m):嘗試次數 m 變多時 add 的通過率
                "add_pass_rate_by_trials": {str(mt): float((df["consistent"] & (df["diff"] > 0) & (df["p"].fillna(1) <= 0.05 / mt)).mean())
                                            for mt in (1, 3, 10, 30)},
            }
            print(kind, json.dumps({k: v for k, v in summary[kind].items() if k != "fix_pass_rate"}, ensure_ascii=False),
                  json.dumps(summary[kind]["fix_pass_rate"]), f"{time.time() - t0:.0f}s", flush=True)
    # 合成成分本身的月 IC(只算一次,說明「IC 0.03」換算到主要指標的大小)
    comp = {}
    for kind, tgt in (("plant12", 0.03), ("plantM", 0.03 * math.sqrt(H))):
        rng = np.random.default_rng(E.metric_seed(f"calibrate|component|{kind}"))
        vals = {m: [] for m in markets}
        for _ in range(20):
            for m in markets:
                r = base[m].copy()
                r.loc[judged[m].index, score] = _planted_component(judged[m], x12[m], tgt, rng)
                ser = E._ic_series(ctxs[m], r, score, H, "dev", judge_col=judge)
                vals[m].append(float(ser["ic"].mean()))
        comp[kind] = {"target_horizon_ic": tgt, **{f"monthly_jt_ic_{m}": float(np.mean(v)) for m, v in vals.items()}}
    res = {"version": E.EVAL_VERSION, "primary": a.primary, "reps": reps, "markets": list(markets), "summary": summary,
           "component_ic": comp, "elapsed_sec": round(time.time() - t0), "note": "只用開發集;閘門核心不含次要指標(只改分數,評等不變)"}
    out = Path(a.out or (config.OUT_DIR / "gate")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / f"calibration_{a.primary}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    registry.log("replay", {"calibration": a.primary, "reps": reps, "eval_version": E.EVAL_VERSION},
                 {k: v["add_pass_rate"] for k, v in summary.items()}, market="TW+US", family="calibration",
                 note="閘門校準(不算模型嘗試)")
    print(json.dumps(comp, ensure_ascii=False))
    print(f"完成,{time.time() - t0:.0f} 秒;{out / f'calibration_{a.primary}.json'}")
    return 0


PRICES_HELP = "價格來源:bt_px(預設,v1.1 凍結快取)或 pit(PIT store 含息報酬;INVEST_LAB_OUT_DIR 要有 bt_pit store)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="合併閘門(只用開發集)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare", help="比較基準與新版本")
    c.add_argument("--base", action="append", required=True, help="市場=逐筆判斷檔,例如 TW=path.csv.gz")
    c.add_argument("--new", action="append", required=True)
    c.add_argument("--change-type", choices=["add", "fix"], required=True)
    c.add_argument("--feature-keys", nargs="+", default=None, help="add 必填：本次新增或調整的特徵 key")
    c.add_argument("--tamper-passed", action="store_true", help="add 必填：相關竄改測試已通過")
    c.add_argument("--primary", default="lt_12m", choices=list(E.PRIMARY))
    c.add_argument("--alpha", type=float, default=0.05)
    c.add_argument("--margin", type=float, default=None, help="'fix' 的非劣性門檻(預設見 evaluate.FIX_MARGIN)")
    c.add_argument("--n-trials", type=int, default=None, help="預設讀實驗登記簿(該假設族的開發集設定數);不得小於實際次數")
    c.add_argument("--family", default="explore", help="假設族:docs/預先登記.md 裡狀態為「核准」的編號(例如 P2-H03);explore 只能用在 fix,add 不會通過")
    c.add_argument("--trials-since", default=None, help="只限維護者(INVEST_LAB_AGENT=claude),必須同時給 --trials-since-reason")
    c.add_argument("--trials-since-reason", default=None)
    c.add_argument("--single-market-ok", action="store_true")
    c.add_argument("--label", default="")
    c.add_argument("--prices", choices=["bt_px", "pit"], default="bt_px", help=PRICES_HELP)
    c.add_argument("--out", default=None)
    k = sub.add_parser("calibrate", help="閘門校準(安慰劑與植入訊號)")
    k.add_argument("--reps", type=int, default=200)
    k.add_argument("--primary", default="lt_12m", choices=list(E.PRIMARY))
    k.add_argument("--rows-dir", default=str(config.ROOT / "tests" / "baseline" / "outputs"))
    k.add_argument("--out", default=None)
    cb = sub.add_parser("calibration", help="P2-H09:機率校準的 Brier 比較(只用開發集、PIT 價格)")
    cb.add_argument("--base", action="append", required=True, help="對照的逐筆檔(用它的 lt_level),市場=路徑")
    cb.add_argument("--new", action="append", required=True, help="新模型的逐筆檔(用它的 lt_score),市場=路徑")
    cb.add_argument("--family", required=True)
    cb.add_argument("--label", default="")
    cb.add_argument("--out", default=None)
    d = sub.add_parser("dev-metrics", help="一個版本的開發集指標")
    d.add_argument("--rows", action="append", required=True)
    d.add_argument("--label", default="v1.1")
    d.add_argument("--prices", choices=["bt_px", "pit"], default="bt_px", help=PRICES_HELP)
    d.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    return {"compare": cmd_compare, "calibrate": cmd_calibrate, "dev-metrics": cmd_dev_metrics,
            "calibration": cmd_calibration}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
