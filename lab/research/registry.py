"""實驗登記簿(共用介面,只有維護者改)。

每一次在開發集上評估一個設定,都要自動記一筆;多重檢定校正用的「試了幾次」從這裡算,所以不能靠人手記。
`lab/research/evaluate.py` 每被呼叫一次就要呼叫 `log()`(C1 實作時接上)。

一筆紀錄:time、kind(dev / gate / holdout / replay)、config_hash、config、git_sha、caller(agent 名稱或 user)、
market、universe、data_vintage、family(假設族:預先登記的假設編號,例 "P2-H03";探索性嘗試記 "explore";
基準重算記 "baseline")、metrics(主要指標的數字)、note。
多重檢定的嘗試次數 m 只算同一個假設族裡、不重複的設定(探索性與基準重算不計入 m,但會在報告裡另列總嘗試次數)。
檔案:config.REGISTRY_FILE(主資料夾 data/research/experiments.jsonl,所有 worktree 共用),寫入時加檔案鎖。
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path

from .. import config

KINDS = {"dev", "gate", "holdout", "replay"}


def config_hash(cfg: dict) -> str:
    """設定的指紋:同一組設定永遠得到同一個 hash(鍵排序、浮點數轉字串)。"""
    s = json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def log(kind: str, cfg: dict, metrics: dict, *, market: str = "", universe: str = "", data_vintage: str = "",
        family: str = "explore", note: str = "", path: Path | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind 必須是 {sorted(KINDS)}")
    rec = {
        "time": dt.datetime.now().isoformat(timespec="seconds"), "kind": kind, "config_hash": config_hash(cfg),
        "config": cfg, "git_sha": config.git_sha(), "caller": os.environ.get("INVEST_LAB_AGENT", "user"),
        "worktree": config.IS_WORKTREE, "market": market, "universe": universe, "data_vintage": data_vintage,
        "family": family, "metrics": metrics, "note": note,
    }
    p = Path(path or config.REGISTRY_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
    with open(p, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return rec


def read(path: Path | None = None) -> list[dict]:
    p = Path(path or config.REGISTRY_FILE)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def n_trials(market: str | None = None, since: str | None = None, kind: str = "dev", path: Path | None = None,
             family: str | None = None) -> int:
    """不重複設定的嘗試次數。family 指定時只算該假設族(多重檢定的 m 用這個);None 則全部算(報告總嘗試次數用)。"""
    hs = {r["config_hash"] for r in read(path) if r.get("kind") == kind
          and (market is None or r.get("market") == market) and (since is None or r.get("time", "") >= since)
          and (family is None or r.get("family", "explore") == family)}
    return len(hs)


def holdout_count(path: Path | None = None) -> int:
    return len({r["config_hash"] for r in read(path) if r.get("kind") == "holdout"})
