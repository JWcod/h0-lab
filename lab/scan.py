"""市場掃描:對一整組股票跑模型,存成一張比較表(也會順便存快照,累積復盤資料)。

在背景執行緒跑;一次只允許一個掃描。台股之間稍微停頓,避免打爆 FinMind 免費額度。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
import traceback

from . import report, watchlist
from .analysis.decision import neutral_label
from .config import OUT_DIR, PRIVATE_DIR, RECORD_FORWARD
from .universe import UNIVERSES

SCAN_DIR = OUT_DIR / "scan"
SCAN_DIR.mkdir(parents=True, exist_ok=True)
PRIVATE_SCAN_DIR = PRIVATE_DIR / "scan"  # 觀察清單是個人資料,掃描結果也只放 private/


def _dir_for(key: str):
    return PRIVATE_SCAN_DIR if key == "watchlist" else SCAN_DIR

_job: dict = {"running": False}
_lock = threading.Lock()


def _lt(v: dict) -> dict:
    """掃描與模擬帳戶用模型內部的等級(Q7 讓網站不顯示美股等級,但模擬帳戶的行為在階段 4 之前不改)。"""
    return v.get("long_term_model") or v["long_term"]


def _st(v: dict) -> dict:
    return v.get("short_term_model") or v["short_term"]


def summarize(r: dict) -> dict:
    v, q, m, val = r["verdict"], r["quote"], r["meta"], r["valuation"]
    mu = val.get("multiples") or {}
    return {
        "symbol": m["symbol"], "name": m["name"], "market": m["market"], "industry": m.get("industry_tw") or m.get("industry"),
        "currency": m["currency"], "price": q["price"], "change_pct": q.get("change_pct"), "market_cap": q.get("market_cap"),
        "fair_value": v.get("fair_value"), "upside": v.get("upside"), "uncertainty": v.get("uncertainty"),
        "lt_rating": _lt(v).get("rating"), "lt_level": _lt(v).get("level"),
        "info_only": v["long_term"].get("method") == "us_info_only",   # Q7:畫面顯示「不評等」,lt_rating 只給模擬帳戶
        "st_signal": _st(v).get("signal"), "st_code": _st(v).get("code"),
        "lt_score": v["lt_score"].get("score"), "st_score": v["st_score"].get("score"), "confidence": v["confidence"].get("score"),
        "pillars": {k: p.get("score") for k, p in r["pillars"].items()},
        "pe": mu.get("pe_ttm"), "forward_pe": mu.get("forward_pe"), "dividend_yield": mu.get("dividend_yield"),
        "company_type": val.get("company_type_label"), "cyclical": bool((val.get("cyclical") or {}).get("cyclical")),
        "red_flags": v["long_term"].get("red_flags") or [],
        "mom_12_1": r["technical"].get("mom_12_1"), "vol_1y": r["technical"].get("vol_1y"),
        "data_fails": [f"{s['source']}:{s['item']}" for s in r.get("data_status", []) if not s["ok"]],
        "max_position_pct": v.get("sim_max_position_pct"),
        "missing_core": v["confidence"].get("missing_core") or [],
        # 基礎變數快照:市場掃描頁用使用者自己的模型重算排名(app/server.py /api/scan/latest?model=),不重抓資料
        "model_env": r.get("model_env"),
    }


def _symbols(key: str) -> tuple[str, list[str]]:
    if key == "watchlist":
        return "我的觀察清單", [x["symbol"] for x in watchlist.load()]
    label, lst = UNIVERSES[key]
    return label, [s for s, _ in lst]


def status() -> dict:
    with _lock:
        return {k: v for k, v in _job.items() if k != "thread"}


def start(key: str, refresh: bool = False) -> dict:
    if key not in UNIVERSES and key != "watchlist":
        raise ValueError("未知的股票池")
    with _lock:
        if _job.get("running"):
            return {k: v for k, v in _job.items() if k != "thread"}
        label, syms = _symbols(key)
        if not syms:
            raise ValueError("清單是空的")
        _job.clear()
        _job.update({"running": True, "key": key, "label": label, "total": len(syms), "done": 0, "current": None,
                     "errors": [], "recent": [], "started": dt.datetime.now().isoformat(timespec="seconds")})
        t = threading.Thread(target=_run, args=(key, label, syms, refresh), daemon=True)
        _job["thread"] = t
        t.start()
        return {k: v for k, v in _job.items() if k != "thread"}


def _wait_finmind(s: str) -> None:
    """台股每檔約用 8 次 FinMind;額度不夠就等(背景掃描可以慢,但資料要完整)。最多等 65 分鐘。"""
    from .sources import finmind
    waited = 0
    while waited < 65 * 60:
        b = finmind.budget_status()
        if not b["cooldown_remaining"] and b["used_last_hour"] <= b["budget"] - 10:
            return
        wait = max(30, min(b["cooldown_remaining"] or 60, 300))
        with _lock:
            _job["current"] = f"{s}(等待 FinMind 額度,約 {max(1, (b['cooldown_remaining'] or 60) // 60)} 分鐘)"
        time.sleep(wait)
        waited += wait


def _run(key: str, label: str, syms: list[str], refresh: bool):
    rows = []
    for s in syms:
        if s.endswith((".TW", ".TWO")):
            from .sources import finmind
            if refresh or finmind.needs_calls(s.split(".")[0]):
                _wait_finmind(s)
        with _lock:
            _job["current"] = s
        try:
            r = report.build(s, refresh=refresh)
            sm = summarize(r)
            rows.append(sm)
            with _lock:
                _job.setdefault("recent", []).insert(0, {k: sm.get(k) for k in ("symbol", "name", "price", "upside", "lt_rating", "info_only",
                                                                                  "lt_level", "st_signal", "st_code", "lt_score")})
                _job["recent"] = _job["recent"][:15]
        except Exception as e:  # noqa: BLE001
            with _lock:
                _job["errors"].append({"symbol": s, "error": f"{type(e).__name__}: {e}"[:200]})
            traceback.print_exc()
        with _lock:
            _job["done"] += 1
        if s.endswith((".TW", ".TWO")):
            time.sleep(1.0)
    out = {"key": key, "label": label, "time": dt.datetime.now().isoformat(timespec="seconds"), "rows": rows,
           "errors": _job.get("errors", [])}
    d = _dir_for(key)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{key}-{dt.date.today().isoformat()}.json"
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    # 模擬交易:只用固定股票池(觀察清單會變動,不適合當策略範圍);資料不完整的股票不交易
    if key in ("tw_large", "us_large") and RECORD_FORWARD:
        try:
            from . import paper
            from .sources import yahoo
            mkt = "TW" if key == "tw_large" else "US"
            bh = yahoo.index_history("^TWII" if mkt == "TW" else "^GSPC", "1mo")
            bench = float(bh["Close"].iloc[-1]) if bh is not None and len(bh) else None
            ok_rows = [r for r in rows if not r.get("missing_core")]
            out["paper"] = paper.update_all(mkt, ok_rows, bench)
            # 策略 C:含息大盤(還原股價,一年序列足夠接上上次更新的日期)
            th = yahoo.index_history(paper.TR_BENCH[mkt][0], "1y")
            out["paper"]["C"] = paper.update_risk(mkt, ok_rows, th["Close"] if th is not None and len(th) else None)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    with _lock:
        _job.update({"running": False, "current": None, "finished": out["time"]})


def latest(key: str) -> dict | None:
    if not re.fullmatch(r"[a-z_]{2,20}", key or ""):
        return None
    files = sorted(_dir_for(key).glob(f"{key}-*.json"))
    if not files:
        return None
    out = json.loads(files[-1].read_text(encoding="utf-8"))
    for r in out.get("rows") or []:   # 舊掃描檔的等級字眼換成現在的名稱、拿掉舊的行動建議欄位(資料檔不改)
        r.pop("action", None)
        for k in ("lt_rating", "st_signal"):
            if k in r:
                r[k] = neutral_label(r[k])
    return out
