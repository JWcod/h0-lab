"""診斷(預先登記「不列為假設、只做診斷」第 1 項;family = explore,不據以修改模型):
美股各面向分數的 1 個月 IC,和同一個月的 Fama–French 因子報酬(Ken French 公開資料)有沒有關係;2015–2020 vs 2021 起。

只用開發集(結果視窗在 2024-10-01 前結束)。用法(離線、凍結資料,OUT_DIR 指到含 bt_pit/ store 與逐筆檔的回測輸出):
  INVEST_LAB_OFFLINE=1 INVEST_LAB_DATA_DIR=data/frozen/v2.2_2026-10-21 INVEST_LAB_OUT_DIR=<輸出> \
  .venv/bin/python scripts/diag_us_reversal.py <rows_us_large40.csv> <Ken French 資料夾> <輸出 md>
"""
from __future__ import annotations

import ast
import io
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lab import config  # noqa: E402
from lab.research import backtest_pit as BP  # noqa: E402
from lab.research import evaluate as E  # noqa: E402
from lab.research import registry  # noqa: E402

PILLARS = ("valuation", "quality", "growth", "health", "momentum")
FACTORS = ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom")


def _kf(path: Path, first_col_name: str) -> pd.DataFrame:
    """Ken French CSV:前幾行是說明,月資料表到第一個空白行(之後是年資料)為止;單位 %。"""
    lines = path.read_text(encoding="latin-1").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(",") and first_col_name in ln)
    body = []
    for ln in lines[start + 1:]:
        if not ln.strip() or not ln.strip()[0].isdigit():
            break
        body.append(ln)
    df = pd.read_csv(io.StringIO("\n".join([lines[start]] + body)))
    df = df.rename(columns={df.columns[0]: "ym"})
    df["ym"] = pd.PeriodIndex(df["ym"].astype(str).str.strip(), freq="M")
    df.columns = [c.strip() for c in df.columns]
    return df.set_index("ym").astype(float) / 100


def main(rows_path: str, kf_dir: str, out_md: str) -> int:
    rows = pd.read_csv(rows_path)
    for p in PILLARS:
        rows[p] = rows["pillars"].map(lambda s, p=p: ast.literal_eval(s).get(p) if isinstance(s, str) else None)
    px = BP.pit_prices("US", sorted(rows["symbol"].unique()))
    ctx = E._Ctx(rows, px["tr_index"], calendar=px.get("calendar"), bench_tr=px.get("bench_tr"))
    ex = E._excess(ctx, "1m")
    d = rows.assign(date=pd.to_datetime(rows["date"])).merge(ex[["date", "symbol", "x"]], on=["date", "symbol"])
    d = d[d["date"].isin(ctx.dates_in(1, "dev")) & d["x"].notna()]
    cols = ["lt_score", *PILLARS]
    ic = d.groupby("date").apply(lambda g: pd.Series({c: g[c].corr(g["x"], method="spearman") for c in cols}))
    # 判斷日 d(月底)的 1 個月報酬落在下一個月 → 對齊下一個月的因子報酬
    ic.index = pd.PeriodIndex(ic.index, freq="M") + 1
    kf = Path(kf_dir)
    fac = _kf(kf / "F-F_Research_Data_5_Factors_2x3.csv", "Mkt-RF").join(_kf(kf / "F-F_Momentum_Factor.csv", "Mom"), how="inner")
    j = ic.join(fac, how="inner")
    split = pd.Period("2021-01", freq="M")
    res = {"n_months": int(len(j)), "first": str(j.index.min()), "last": str(j.index.max()), "corr": {}, "subperiods": {}}
    for c in cols:
        res["corr"][c] = {f: round(float(j[c].corr(j[f])), 3) for f in FACTORS}
    for name, m in (("2015–2020", j.index < split), ("2021 起", j.index >= split)):
        g = j[m]
        res["subperiods"][name] = {"n": int(len(g)), **{f"ic_{c}": round(float(g[c].mean()), 4) for c in cols},
                                   **{f"{f}_月均": round(float(g[f].mean()), 4) for f in ("HML", "RMW", "Mom", "Mkt-RF")}}
    registry.log("dev", {"kind": "diag_us_reversal", "rows": E.rows_fingerprint(rows), "kf": sorted(p.name for p in kf.glob("*.csv"))},
                 {"n_months": res["n_months"]}, market="US", data_vintage=config.DATA_DIR.name, family="explore",
                 note="美股反向診斷(Ken French 因子)")
    L = ["# 美股反向診斷(開發集,只寫報告、不改模型)", "", "> 研究用途,非投資建議。", "",
         f"- 月數 {res['n_months']}({res['first']} ~ {res['last']});IC = 判斷日分數與下一個月超額報酬的 Spearman 相關。", "",
         "## 每月 IC 和同月因子報酬的相關", "", "| 分數 | " + " | ".join(FACTORS) + " |", "|---|" + "---|" * len(FACTORS)]
    for c in cols:
        L.append(f"| {c} | " + " | ".join(f"{res['corr'][c][f]:+.2f}" for f in FACTORS) + " |")
    L += ["", "## 兩個子期間", "", "| 期間 | 月數 | " + " | ".join(f"IC {c}" for c in cols) + " | HML 月均 | RMW 月均 | Mom 月均 |",
          "|---|---|" + "---|" * (len(cols) + 3)]
    for name, v in res["subperiods"].items():
        L.append(f"| {name} | {v['n']} | " + " | ".join(f"{v[f'ic_{c}']:+.3f}" for c in cols)
                 + f" | {v['HML_月均']:+.4f} | {v['RMW_月均']:+.4f} | {v['Mom_月均']:+.4f} |")
    Path(out_md).write_text("\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
