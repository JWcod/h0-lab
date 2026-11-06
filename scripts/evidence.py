"""產生網站「證據」頁用的摘要(階段 3 D3):只含開發集,所有數字由評估框架計算。

用法(離線):
  INVEST_LAB_OFFLINE=1 INVEST_LAB_AGENT=claude INVEST_LAB_DATA_DIR=$PWD/data/frozen/v2.2_2026-10-21 \\
    .venv/bin/python scripts/evidence.py --rows TW=<rows_tw_pit50.csv>,US=<rows_us_large40.csv> \\
    --gates .agent_out --model "JW-MF v2.0-dev" --out data/research/evidence.json

內容:
- 主要指標:目前模型在開發集的重疊世代月 IC 與 Newey–West 95% 區間(lt_12m、lt_6m、st_1m),evaluate() 會寫登記簿(family baseline)。
- 閘門:登記簿裡每個假設族最後一次正式閘門,讀 --gates 資料夾下的 gate_<hash>.json(只有開發集)。
- 預先登記狀態、holdout 使用次數(只有次數,不含任何 holdout 成績)。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from lab import config  # noqa: E402
from lab.research import evaluate as E  # noqa: E402
from lab.research import prereg, registry, splits  # noqa: E402

PRIMARY_KEYS = ("lt_12m", "lt_6m", "st_1m")


def gate_results(search_dirs: list[Path], path=None) -> list[dict]:
    files = {}
    for d in search_dirs:
        for f in Path(d).rglob("gate_*.json"):
            files[f.stem.removeprefix("gate_")] = f
    latest = {}
    for rec in registry.read(path):
        if rec.get("kind") == "gate" and rec.get("family", "explore") not in prereg.UNREGISTERED + ("calibration",):
            latest[(rec["family"], rec["config_hash"])] = rec             # 每個不同設定各留一筆(例:P2-H07 兩個台股股票池)
    out = []
    for (fam, _h), rec in sorted(latest.items(), key=lambda kv: (kv[0][0], kv[1].get("time", ""))):
        mkt = rec.get("market", "")
        f = files.get(rec["config_hash"])
        g = json.loads(f.read_text(encoding="utf-8")) if f else {}
        pooled = g.get("pooled") or {}
        out.append({"family": fam, "markets": mkt, "config_hash": rec["config_hash"], "change_type": (rec.get("metrics") or {}).get("change_type"),
                    "primary": (rec.get("metrics") or {}).get("primary"), "passed": (rec.get("metrics") or {}).get("passed"),
                    "diff": pooled.get("diff", (rec.get("metrics") or {}).get("diff")), "ci95": pooled.get("ci95"),
                    "n_months": pooled.get("n_months"), "reasons": g.get("reasons") or [rec.get("note", "")],
                    "noninferiority": g.get("noninferiority"), "user_cfg": (g.get("trials") or {}).get("cfg") or (rec.get("config") or {}).get("user_cfg")})
    return out


def build(rows_files: dict, gate_dirs: list[Path], model: str) -> dict:
    import gate as G
    rows = {m: E.load_rows(p) for m, p in rows_files.items()}
    prices = G._load_prices("pit", {m: [r] for m, r in rows.items()})
    markets = {}
    for m, r in rows.items():
        px = prices[m]
        res = E.evaluate(r, px["tr_index"], market=m, bench_tr=px["bench_tr"], bench_kind=px["bench_kind"],
                         cfg={"label": "evidence_page"}, data_vintage=config.DATA_DIR.name, family="baseline", note="證據頁摘要")
        assert res["split"] == "dev"
        markets[m] = {k: {x: res["primary"][k].get(x) for x in ("ic", "ci95", "p", "n_months")} for k in PRIMARY_KEYS}
    reg = prereg.load()
    return {
        "split": "dev", "holdout_start": str(splits.HOLDOUT_START.date()), "model": model, "data_vintage": config.DATA_DIR.name,
        "generated": dt.datetime.now().isoformat(timespec="seconds"), "eval_version": E.EVAL_VERSION,
        "primary": markets, "gates": gate_results(gate_dirs),
        "prereg": [{"family": k, **{x: (sorted(v[x]) if x == "markets" else v[x]) for x in ("markets", "change_type", "primary", "status")}}
                   for k, v in reg.items()],
        "holdout_used": registry.holdout_count(),
        "notes": ["所有數字只用開發集(結果視窗在 holdout 起點之前結束)。",
                  "95% 區間是重疊世代月序列 + Newey–West;區間含 0 就是沒有證實有效。"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True, help="TW=路徑,US=路徑")
    ap.add_argument("--gates", default=".agent_out", help="gate_<hash>.json 所在資料夾(可用逗號分隔多個)")
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", default=str(config.OUT_DIR / "research" / "evidence.json"))
    a = ap.parse_args(argv)
    rows = dict(kv.split("=", 1) for kv in a.rows.split(","))
    res = build(rows, [Path(p) for p in a.gates.split(",")], a.model)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"已寫入 {out}:{len(res['gates'])} 個閘門、holdout 用了 {res['holdout_used']} 次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
