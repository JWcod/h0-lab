"""唯一可以算 holdout 的入口(規則見 docs/holdout 評估日誌.md、docs/架構.md §3.1)。

用法(只能在主 checkout、INVEST_LAB_AGENT 沒設或 = user 時執行;每個階段最多 1 個新設定):
  .venv/bin/python scripts/holdout.py --rows TW=<逐筆判斷> --rows US=<逐筆判斷> \
      --stage 階段1 --label "v1.1@資料 v2.0" --note "..." --yes
  只能用 holdout 檢驗的預先登記假設(例 P3-H01)和最終設定一起算,不另外多用一次 holdout:
      --compare P3-H01:TW_PIT50=<假設打開的逐筆判斷>(基準 = 同一個鍵的 --rows),配對檔也算進設定 hash

做的事
1. 拒絕在 git worktree 或 agent 模式下執行(evaluate.holdout_guard)。
2. 設定 hash = 逐筆判斷檔的 SHA-256 + 評估版本 + 資料版本(--label、--note 不影響 hash)。
3. 同一個 hash 只計一次:已經記過的 hash 照樣重算(讓 Verifier 核對),但不在日誌加列、不增加 holdout 次數。
4. 新的 hash:若同一個階段已經有別的 holdout 設定,沒有 --allow-second-in-stage 就拒絕。
5. **先記再算**:計算前先在登記簿與日誌寫一筆「計算中」;算完再補上結果(中途當掉也留得下「看過」的紀錄)。
6. 登記簿固定用主資料夾的 data/research/experiments.jsonl(忽略 INVEST_LAB_REGISTRY),--stage 只接受「階段N」。

防護是「誠實制 + 日誌」:擋得住不小心,擋不住刻意繞過;保障來自日誌與重跑(見 lab/research/evaluate.py 檔頭)。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lab import config  # noqa: E402
from lab.research import evaluate as E  # noqa: E402
from lab.research import registry  # noqa: E402

LOG_MD = config.ROOT / "docs" / "holdout 評估日誌.md"
MAIN_REGISTRY = config.MAIN_ROOT / "data" / "research" / "experiments.jsonl"  # holdout 一律記在主登記簿
STAGE_RE = re.compile(r"^階段\d+$")
PENDING = "計算中(尚未完成)"


def file_sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def holdout_config(files: dict[str, Path], data_vintage: str, prices: str = "bt_px") -> dict:
    return {"eval_version": E.EVAL_VERSION, "rows_sha256": {m: file_sha256(p) for m, p in sorted(files.items())},
            "prices": prices, "data_vintage": data_vintage}


def check_stage(stage: str) -> None:
    if not STAGE_RE.match(stage or ""):
        raise ValueError(f"--stage 只接受「階段N」格式(例如 階段1),收到:{stage!r}")


def already_counted(cfg_hash: str, registry_path) -> bool:
    return any(r.get("kind") == "holdout" and r.get("config_hash") == cfg_hash for r in registry.read(registry_path))


def stage_hashes(stage: str, registry_path) -> set[str]:
    return {r["config_hash"] for r in registry.read(registry_path)
            if r.get("kind") == "holdout" and (r.get("metrics") or {}).get("stage") == stage}


def _cell(s: str) -> str:
    return str(s).replace("|", "/").replace("\n", " ").strip()


def append_log_row(md_path: Path, *, stage: str, label: str, summary: str, note: str, date: str | None = None) -> int:
    """在日誌表格最後加一列,回傳新列的編號(上一列編號 + 1)。"""
    md_path = Path(md_path)
    lines = md_path.read_text(encoding="utf-8").splitlines()
    table = [i for i, ln in enumerate(lines) if ln.startswith("|")]
    if not table:
        raise ValueError(f"{md_path} 找不到表格")
    nums = [int(c) for c in (lines[i].strip("|").split("|")[0].strip() for i in table) if c.isdigit()]
    no = (max(nums) + 1) if nums else 0
    row = f"| {no} | {date or dt.date.today().isoformat()} | {_cell(stage)} | {_cell(label)} | {_cell(summary)} | {_cell(note)} |"
    lines.insert(table[-1] + 1, row)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return no


def fill_log_row(md_path: Path, cfg_hash: str, summary: str) -> bool:
    """把這個設定 hash 那一列的「計算中」換成結果。找不到(或已經填過)回傳 False。"""
    md_path = Path(md_path)
    lines = md_path.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("|") and f"`{cfg_hash}`" in ln and PENDING in ln:
            lines[i] = ln.replace(PENDING, _cell(summary))
            md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


def summarize(res_by_market: dict, paired: dict | None = None) -> str:
    name = {"TW": "台股", "US": "美股"}
    parts = []
    for fam, pr in (paired or {}).items():
        bits = []
        for k, r in pr["pools"].items():
            if r.get("diff") is None:
                bits.append(f"{k} 樣本不足")
            else:
                bits.append(f"{k} 差 {r['diff']:+.3f}(NW CI {r['ci95'][0]:+.3f} ~ {r['ci95'][1]:+.3f},Holm p {r['p_holm']:.3f},"
                            f"{r['n_months']} 個月)")
        parts.append(f"{fam}({pr['primary']},新 − 最終設定):" + ";".join(bits) + f" → {pr['verdict']['reason']}")
    for m, r in res_by_market.items():
        bits = []
        for k in ("lt_12m", "lt_6m", "st_1m"):
            v = r["primary"].get(k) or {}
            ic, ci = v.get("ic"), v.get("ci95")
            if ic is None:
                bits.append(f"{k} 樣本不足")
            else:
                bits.append(f"{k} IC {ic:+.3f}" + (f"(NW CI {ci[0]:+.3f} ~ {ci[1]:+.3f},{v.get('n_months')} 個滿世代月)" if ci else ""))
        h = r["secondary"]["hit"]["lt_12m"].get("general", {})
        if h.get("hit") is not None:
            bits.append(f"12m 命中 {h['right']}/{h['wrong']} = {h['hit'] * 100:.1f}%(平手 {h['ties']})")
        parts.append(f"{name.get(m, m)}:" + ";".join(bits))
    return "。".join(parts)


def record_pending(cfg: dict, *, stage: str, label: str, note: str, md_path: Path, registry_path, date: str | None = None) -> dict:
    """計算前先記:新的 hash → 日誌加一列「計算中」並計次;已記過的 hash → 只記登記簿(不另計次)。"""
    check_stage(stage)
    h = registry.config_hash(cfg)
    counted = already_counted(h, registry_path)
    row = None
    if not counted:
        row = append_log_row(md_path, stage=stage, label=label, summary=PENDING, note=f"設定 `{h}`;{note}".rstrip(";"), date=date)
    registry.log("holdout", cfg, {"status": "pending", "stage": stage, "label": label},
                 market="+".join(sorted(cfg.get("rows_sha256", {}))), data_vintage=cfg.get("data_vintage", ""), family="holdout",
                 note="同一個設定重算,不另計次" if counted else f"holdout 日誌第 {row} 列(計算前先記)", path=registry_path)
    return {"config_hash": h, "counted_before": counted, "log_row": row, "holdout_count": registry.holdout_count(registry_path)}


def record_result(cfg: dict, metrics: dict, summary: str, *, stage: str, label: str, md_path: Path, registry_path) -> dict:
    """算完再補結果:登記簿多一筆 done;日誌裡還是「計算中」的那一列換成結果(重算時通常已經填過)。"""
    h = registry.config_hash(cfg)
    filled = fill_log_row(md_path, h, summary)
    registry.log("holdout", cfg, {**metrics, "status": "done", "stage": stage, "label": label},
                 market="+".join(sorted(cfg.get("rows_sha256", {}))), data_vintage=cfg.get("data_vintage", ""), family="holdout",
                 note="結果已補進日誌" if filled else "結果(日誌列已有結果或是重算)", path=registry_path)
    return {"config_hash": h, "log_filled": filled, "holdout_count": registry.holdout_count(registry_path)}


def main(argv=None) -> int:
    E.holdout_guard()  # worktree 或 agent 模式直接丟 PermissionError
    ap = argparse.ArgumentParser(description="計算 holdout(每次都會記在 holdout 評估日誌)")
    ap.add_argument("--rows", action="append", required=True, help="市場=逐筆判斷檔,例如 TW=path.csv.gz")
    ap.add_argument("--stage", required=True, help="階段,格式「階段N」,例如 階段1")
    ap.add_argument("--label", required=True, help="模型 / 改了什麼")
    ap.add_argument("--note", default="")
    ap.add_argument("--yes", action="store_true", help="確認要看 holdout(沒有就只顯示會做什麼)")
    ap.add_argument("--allow-second-in-stage", action="store_true")
    ap.add_argument("--compare", action="append", default=[],
                    help="假設族:市場鍵=假設打開的逐筆判斷,例如 P3-H01:TW_PIT50=path.csv(基準是同一個鍵的 --rows)")
    ap.add_argument("--prices", choices=["bt_px", "pit"], default="bt_px",
                    help="bt_px = v1.1 凍結基準的價格;pit = backtest_pit 的含息報酬與含息大盤(INVEST_LAB_OUT_DIR 要指到該次回測輸出)")
    a = ap.parse_args(argv)
    check_stage(a.stage)
    reg = MAIN_REGISTRY
    files = {}
    for it in a.rows:
        k, v = it.split("=", 1)
        files[k.strip().upper()] = Path(v).expanduser().resolve()
    cfg = holdout_config(files, config.DATA_DIR.name, a.prices)
    compares = {}   # (假設族, 市場鍵) → 檔案
    for it in a.compare:
        fam_key, v = it.split("=", 1)
        fam, key = fam_key.split(":", 1)
        fam, key = fam.strip(), key.strip().upper()
        if key not in files:
            raise ValueError(f"--compare 的市場鍵 {key} 沒有對應的 --rows(基準)")
        if fam not in E.HOLDOUT_PAIR_M:
            raise ValueError(f"假設族「{fam}」沒有登記成 holdout 配對檢驗")
        compares[(fam, key)] = Path(v).expanduser().resolve()
    for fam in {f for f, _ in compares}:   # 股票池必須剛好是登記的那幾個(加 --yes 之前就檢查)
        need = set(E.HOLDOUT_PAIR_POOLS[fam].values())
        got = {k for f, k in compares if f == fam}
        if got != need:
            raise ValueError(f"{fam} 的 --compare 必須剛好是 {sorted(need)},收到 {sorted(got)}")
    if compares:   # 配對檔也是這次設定的一部分:換任何一個檔就是新的設定
        cfg["compare_sha256"] = {f"{f}:{k}": file_sha256(p) for (f, k), p in sorted(compares.items())}
    loaded = {}  # 市場鍵 → (rows, 價格);PIT 價格的指紋要在算 hash 之前放進設定
    for m, p in files.items():
        rows = E.load_rows(p)
        mk = m.split("_")[0]  # 鍵可以是 TW、US 或 TW_PIT50 這類,市場取底線前面
        if a.prices == "pit":
            from lab.research import backtest_pit as BP
            px = BP.pit_prices(mk, rows["symbol"].unique())
            cfg.setdefault("price_fingerprints", {})[m] = E.prices_fingerprint(px["tr_index"])
        else:
            px = E.v11_prices(mk)
        loaded[m] = (rows, px, mk)
    h = registry.config_hash(cfg)
    counted = already_counted(h, reg)
    others = stage_hashes(a.stage, reg) - {h}
    print(f"設定 hash {h};{'已經記過(重算不另計次)' if counted else '新的設定(會在日誌加一列)'};"
          f"目前 holdout 次數 {registry.holdout_count(reg)};登記簿 {reg}")
    if not counted and others and not a.allow_second_in_stage:
        print(f"拒絕:「{a.stage}」已經看過 holdout(設定 {sorted(others)});每個階段最多 1 次。", file=sys.stderr)
        return 3
    if not a.yes:
        print("沒有加 --yes:不計算。確認要看 holdout 再加 --yes。")
        return 2
    pend = record_pending(cfg, stage=a.stage, label=a.label, note=a.note, md_path=LOG_MD, registry_path=reg)
    ticket = E._issue_holdout_ticket()
    res = {}
    for m, (rows, px, mk) in loaded.items():
        extra = {"calendar": px["calendar"]} if a.prices == "pit" else {}
        if a.prices == "pit":
            rows = rows[rows["symbol"].isin(px["tr_index"])]
        res[m] = E.evaluate(rows, px["tr_index"], market=mk, bench_tr=px["bench_tr"], bench_kind=px["bench_kind"],
                            split="holdout", holdout_ticket=ticket, data_vintage=config.DATA_DIR.name, _log=False, **extra)
    out = config.OUT_DIR / "holdout"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"holdout_{h}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    paired: dict = {}
    for (fam, key), path in compares.items():
        rows_b, px, mk = loaded[key]
        rows_n = E.load_rows(path)
        if a.prices == "pit":
            rows_b, rows_n = rows_b[rows_b["symbol"].isin(px["tr_index"])], rows_n[rows_n["symbol"].isin(px["tr_index"])]
        extra = {"calendar": px["calendar"]} if a.prices == "pit" else {}
        r = E.holdout_paired(rows_b, rows_n, px["tr_index"], family=fam, bench_tr=px["bench_tr"], holdout_ticket=ticket, **extra)
        paired.setdefault(fam, {"primary": r["primary"], "pools": {}})["pools"][key] = r
    for fam, pr in paired.items():
        pr["verdict"] = E.holdout_pair_verdict(pr["pools"], fam)
    if paired:
        (out / f"holdout_{h}_paired.json").write_text(json.dumps(paired, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    metrics = {m: {k: {"ic": v.get("ic"), "ci95": v.get("ci95")} for k, v in r["primary"].items()} for m, r in res.items()}
    if paired:
        metrics["paired"] = {f: {"verdict": pr["verdict"], "pools": {k: {x: v.get(x) for x in ("diff", "ci95", "p_holm", "n_months")}
                                                                    for k, v in pr["pools"].items()}} for f, pr in paired.items()}
    done = record_result(cfg, metrics, summarize(res, paired), stage=a.stage, label=a.label, md_path=LOG_MD, registry_path=reg)
    print(json.dumps({**pend, **done}, ensure_ascii=False))
    print(summarize(res, paired))
    return 0


if __name__ == "__main__":
    sys.exit(main())
