"""第一次執行引導:檢查新使用者的設定做到哪一步(規格 docs/安裝與上手規格.md §3)。

只看檔案在不在,不讀資料內容、不連網(唯一讀內容的是自己寫的上次建立結果);token 只回有沒有填,不回值。
文字不在這裡:每一步回傳 id 與數字(請求數、分鐘),前端用 t("setup.step.<id>.*") 顯示,之後英文版只要加字典。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, runtime

# 回測資料(精靈的今天排名與回測):每個市場要有的檔案(lab/research/backtest_pit.py 的輸出)。
# 股票池的逐筆檔有任何一個就算齊:維護者的完整版有 tw_pit50,新使用者的精簡版(scripts/setup_research.py)只有 tw_large40;
# 精靈會自己挑有資料的股票池(lab/models/wizard.py default_universe)。pit_macro 是大盤與總經,兩個市場共用。
RESEARCH_FILES = {"TW": ("raw_tw.sqlite", "pit_tw.sqlite", "pit_macro.sqlite"),
                  "US": ("raw_us.sqlite", "pit_us.sqlite", "pit_macro.sqlite")}
RESEARCH_UNIVERSES = {"TW": ("tw_pit50", "tw_large40"), "US": ("us_large40",)}

FIRST_REPORT = {"requests": 30, "minutes": 1}
# 新使用者的精簡版(scripts/setup_research.py:台股、美股各 40 檔大型股)。分鐘數和 setup_research.estimate_minutes 一致
# (Yahoo 86、證交所 155、FinMind 240、SEC 41 次),2026-11-02 用 2+2 檔實測過整條流程;完整 80 檔的時間是估計。
RESEARCH = {"requests": 520, "minutes": 20, "minutes_with_token": 20, "build_minutes": 20,
            "command": ".venv/bin/python scripts/setup_research.py"}


RESULT_FILE = "setup_research_result.json"
# 上次建立沒有完全成功:就算檔案都在也要留著步驟和按鈕,使用者才能再試(結果名稱同 lab/setup_job.py EXIT_MESSAGES)
RETRY_RESULTS = ("failed", "incomplete", "sec_refused", "partial")


def save_research_result(result: str, out_dir: Path | None = None) -> None:
    d = out_dir if out_dir is not None else config.OUT_DIR
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (RESULT_FILE + ".tmp")
    tmp.write_text(json.dumps({"result": result}), encoding="utf-8")
    tmp.replace(d / RESULT_FILE)


def last_research_result(out_dir: Path | None = None) -> str | None:
    """上次建立要使用者再試的結果;沒有、已成功或檔案壞掉都回 None。"""
    d = out_dir if out_dir is not None else config.OUT_DIR
    try:
        result = json.loads((d / RESULT_FILE).read_text(encoding="utf-8"))["result"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return result if result in RETRY_RESULTS else None


def _nonempty_dir(p: Path) -> bool:
    try:
        return p.is_dir() and any(True for _ in p.iterdir())
    except OSError:
        return False


def research_ready(market: str, bt_dir: Path | None = None) -> dict:
    """某個市場的回測資料齊不齊;缺哪些檔(只回檔名)、哪些股票池有逐筆檔。"""
    d = bt_dir if bt_dir is not None else config.OUT_DIR / "bt_pit"
    missing = [f for f in RESEARCH_FILES[market] if not (d / f).is_file()]
    unis = [u for u in RESEARCH_UNIVERSES[market] if (d / f"rows_{u}.csv").is_file()]
    if not unis:
        missing.append(f"rows_{RESEARCH_UNIVERSES[market][0]}.csv")
    return {"ready": not missing, "missing": missing, "universes": unis}


def _has_snapshot(snap_dir: Path) -> bool:
    """查過至少一家公司:每次產生報告都會存快照(lab/report.py → snapshots.save)。
    不看 data/cache:首頁的大盤環境自己會抓資料寫快取,沒查公司也不是空的。"""
    try:
        return snap_dir.is_dir() and any(True for _ in snap_dir.glob("*/*.json"))
    except OSError:
        return False


def status(root: Path | None = None, data_dir: Path | None = None, out_dir: Path | None = None,
           private_dir: Path | None = None, snapshot_dir: Path | None = None) -> dict:
    env_file = root / ".env" if root is not None else runtime.env_file()
    data_dir = data_dir if data_dir is not None else config.DATA_DIR
    out_dir = out_dir if out_dir is not None else config.OUT_DIR
    private_dir = private_dir if private_dir is not None else config.PRIVATE_DIR
    snapshot_dir = snapshot_dir if snapshot_dir is not None else config.SNAPSHOT_DIR
    research = {m: research_ready(m, out_dir / "bt_pit") for m in RESEARCH_FILES}
    last_result = last_research_result(out_dir)
    has_token = bool(config.FINMIND_TOKEN)
    checks = {
        "env_file": env_file.is_file(),
        "private_dir": private_dir.is_dir(),
        "finmind_token": has_token,
        "sec_user_agent": not config.SEC_UA_IS_DEFAULT,
        "has_cache": _nonempty_dir(data_dir / "cache"),
        "has_report": _has_snapshot(snapshot_dir),
    }
    steps = [
        {"id": "install", "done": checks["env_file"] and checks["private_dir"], "optional": False,
         "network": False, "requests": 0, "minutes": 1},
        {"id": "first_report", "done": checks["has_report"], "optional": False, "network": True,
         **FIRST_REPORT},
        {"id": "finmind_token", "done": has_token, "optional": True, "network": True, "requests": 0, "minutes": 3},
        {"id": "research", "done": all(r["ready"] for r in research.values()) and not last_result, "last_result": last_result,
         "optional": False, "network": True, "requests": RESEARCH["requests"], "minutes": RESEARCH["minutes_with_token"] if has_token else RESEARCH["minutes"],
         "build_minutes": RESEARCH["build_minutes"], "command": RESEARCH["command"]},
    ]
    if runtime.FROZEN:   # App:沒有終端機也沒有 scripts/,改成網頁上的按鈕與設定(web/js/setup.js)
        for s in steps:
            if s["id"] == "research":
                s["command"], s["job"] = None, True
            elif s["id"] == "finmind_token":
                s["settings"] = True
        steps.insert(2, {"id": "sec_user_agent", "done": checks["sec_user_agent"], "optional": False, "network": False,
                         "requests": 0, "minutes": 1, "settings": True})
    pending = [s["id"] for s in steps if not s["done"] and not s["optional"]]
    return {"offline": config.OFFLINE, "checks": checks, "research": research, "steps": steps,
            "pending": pending, "all_done": not pending}
