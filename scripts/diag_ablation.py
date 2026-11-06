"""診斷(預先登記「不列為假設、只做診斷」第 2 項;family = explore,所有假設都跑完閘門之後才做,不據以修改模型):
面向層級消融:每次拿掉一個面向(其他面向依目前權重重新加權),看開發集主要指標(長期 lt_12m、短期 st_1m 的重疊世代月 IC)。

回測裡有分數的面向:長期 = 品質、估值、成長、財務安全;短期 = 技術動能、估值、大盤環境(台股另有籌碼,開關關閉時沒有)。
用法(離線、凍結資料,OUT_DIR 指到含 bt_pit/ store 的回測輸出):
  .venv/bin/python scripts/diag_ablation.py <rows 資料夾> <輸出 md>
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lab import config  # noqa: E402
from lab.research import backtest_pit as BP  # noqa: E402
from lab.research import evaluate as E  # noqa: E402

POOLS = {"TW": "tw_pit50", "US": "us_large40"}


def _composite(pillars: pd.DataFrame, w: dict) -> pd.Series:
    num = sum(pillars[k].fillna(0) * v for k, v in w.items() if v and k in pillars)
    den = sum(pillars[k].notna() * v for k, v in w.items() if v and k in pillars)
    return (num / den.where(den > 0)).round(1)


def main(rows_dir: str, out_md: str) -> int:
    params = config.load_params()
    pw = params["pillar_weights"]
    out = {}
    for m, u in POOLS.items():
        rows = pd.read_csv(Path(rows_dir) / f"rows_{u}.csv")
        P = pd.DataFrame([ast.literal_eval(s) for s in rows["pillars"]], index=rows.index)
        px = BP.pit_prices(m, sorted(rows["symbol"].unique()))
        wl, ws = pw["long_term"], pw["short_term_tw" if m == "TW" else "short_term_us"]
        lt_p = [k for k, v in wl.items() if v and P.get(k) is not None and P[k].notna().any()]
        st_p = [k for k, v in ws.items() if v and P.get(k) is not None and P[k].notna().any()]
        variants = {"全部": (wl, ws)}
        variants |= {f"長期拿掉 {k}": ({**wl, k: 0}, ws) for k in lt_p}
        variants |= {f"短期拿掉 {k}": (wl, {**ws, k: 0}) for k in st_p}
        res = {}
        for name, (a, b) in variants.items():
            r = rows.copy()
            r["lt_score"], r["st_score"] = _composite(P, a), _composite(P, b)
            ev = E.evaluate(r, px["tr_index"], market=m, bench_tr=px.get("bench_tr"), calendar=px.get("calendar"),
                            cfg={"diag": "ablation", "variant": name}, universe=u, data_vintage=config.DATA_DIR.name,
                            family="explore", note=f"面向消融:{name}")
            res[name] = {k: {"ic": ev["primary"][k].get("ic"), "ci": ev["primary"][k].get("ci95")} for k in ("lt_12m", "st_1m")}
        out[m] = res
    L = ["# 面向層級消融(開發集,只寫報告、不改模型)", "", "> 研究用途,非投資建議。所有區間含 0 的差異都是「沒有證實有效」。", ""]
    for m, res in out.items():
        L += [f"## {m}({POOLS[m]})", "", "| 版本 | 長期 12 個月 IC | 95% 區間 | 短期 1 個月 IC | 95% 區間 |", "|---|---|---|---|---|"]
        for name, v in res.items():
            def f(k):
                ic, ci = v[k]["ic"], v[k]["ci"]
                return (f"{ic:+.3f}" if ic is not None else "—"), (f"{ci[0]:+.3f} ~ {ci[1]:+.3f}" if ci else "—")
            a, b = f("lt_12m"), f("st_1m")
            L.append(f"| {name} | {a[0]} | {a[1]} | {b[0]} | {b[1]} |")
        L.append("")
    Path(out_md).write_text("\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, default=str)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
