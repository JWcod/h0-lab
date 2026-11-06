"""判斷快照與復盤。

每次產生報告就存一份精簡快照(價格、八面向分數、公平價、等級)。
之後回來看:當時的判斷,後來實際報酬如何?相對大盤如何?方向對不對?
樣本累積夠多後,用來校正權重和門檻(這就是系統「會學習」的資料來源)。
"""
from __future__ import annotations

import datetime as dt
import json
import re

import pandas as pd

from .analysis.decision import neutral_label
from .config import RECORD_FORWARD, SNAPSHOT_DIR, git_sha
from .sources import yahoo

SAFE = re.compile(r"^[A-Z0-9\-\.]{1,20}$")


def _dir(symbol: str):
    if not SAFE.fullmatch(symbol):
        raise ValueError("bad symbol")
    d = SNAPSHOT_DIR / symbol
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(report: dict) -> None:
    snap = make(report)
    p = _dir(snap["symbol"]) / f"{snap['date']}.json"
    p.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")


def make(report: dict) -> dict:
    m, v = report["meta"], report["verdict"]
    snap = {
        "symbol": m["symbol"], "name": m["name"], "market": m["market"], "date": dt.date.today().isoformat(),
        "time": m["generated_at"], "model_version": m["model_version"], "price": report["quote"]["price"],
        "currency": m["currency"], "fair_value": v.get("fair_value"), "upside": v.get("upside"),
        "uncertainty": v.get("uncertainty"), "lt_rating": (v.get("long_term_model") or v["long_term"]).get("rating"),
        "lt_level": (v.get("long_term_model") or v["long_term"]).get("level"),
        "st_signal": (v.get("short_term_model") or v["short_term"]).get("signal"),
        "st_code": (v.get("short_term_model") or v["short_term"]).get("code"),
        "lt_score": v["lt_score"].get("score"), "st_score": v["st_score"].get("score"),
        "info_only": v["long_term"].get("method") == "us_info_only",   # Q7:畫面不顯示這筆的等級(lt_rating 是模型內部等級)
        "pillars": {k: p.get("score") for k, p in report["pillars"].items()},
        "benchmark": "^TWII" if m["market"] == "TW" else "^GSPC",
        "git_sha": git_sha(), "forward_record": RECORD_FORWARD,
    }
    return snap


def previous(symbol: str, before: str) -> dict | None:
    """這檔股票在 before(YYYY-MM-DD)之前最近的一份快照。"""
    try:
        d = _dir(symbol)
    except ValueError:
        return None
    files = sorted(f for f in d.glob("*.json") if f.stem < before)
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def diff(prev: dict, cur: dict) -> dict:
    """和上次分析相比,哪些東西變了。"""
    out = {"prev_date": prev.get("date"), "changes": []}

    def add(label, a, b, fmt="{:.0f}", th=0.0):
        if a is None or b is None:
            return
        if abs(b - a) > th:
            out["changes"].append({"label": label, "from": fmt.format(a), "to": fmt.format(b), "delta": b - a})

    add("股價", prev.get("price"), cur["price"], "{:,.2f}", 0)
    add("公平價", prev.get("fair_value"), cur.get("fair_value"), "{:,.2f}", 0)
    add("長期分數", prev.get("lt_score"), cur.get("lt_score"), "{:.0f}", 2)
    add("短期分數", prev.get("st_score"), cur.get("st_score"), "{:.0f}", 2)
    names = {"quality": "品質", "growth": "成長", "valuation": "估值", "health": "財務安全", "momentum": "技術動能",
             "flows": "籌碼", "revisions": "預期修正", "sentiment": "消息情緒"}
    for k, n in names.items():
        add(n, (prev.get("pillars") or {}).get(k), (cur.get("pillars") or {}).get(k), "{:.0f}", 5)
    if cur.get("info_only") or prev.get("info_only") or (cur.get("market") == "US" and "info_only" not in prev):
        return out                    # Q7:任一筆是美股不評等(含沒有標記的舊美股快照)就不顯示等級變化,避免帶出內部等級
    a_lt, b_lt, a_st, b_st = (neutral_label(x) for x in (prev.get("lt_rating"), cur.get("lt_rating"), prev.get("st_signal"), cur.get("st_signal")))
    if a_lt != b_lt:   # 舊快照的字眼先換成現在的名稱,才不會把改名誤報成等級變動
        out["rating_change"] = {"from": a_lt, "to": b_lt}
    if a_st != b_st:
        out["signal_change"] = {"from": a_st, "to": b_st}
    return out


def list_all() -> list[dict]:
    out = []
    for d in sorted(SNAPSHOT_DIR.iterdir()) if SNAPSHOT_DIR.exists() else []:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                out.append(_neutral(json.loads(f.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError):
                continue
    return out


def _neutral(s: dict) -> dict:
    """舊快照的等級字眼換成現在的名稱、拿掉舊的行動建議欄位(資料檔不改)。"""
    s.pop("action", None)
    for k in ("lt_rating", "st_signal"):
        if k in s:
            s[k] = neutral_label(s[k])
    return s


def review() -> dict:
    """計算每個快照之後的實際報酬與相對大盤報酬。"""
    snaps = list_all()
    rows = []
    price_cache: dict[str, pd.Series] = {}

    def series(sym):
        if sym not in price_cache:
            h = yahoo.index_history(sym, "2y")
            # 去掉 NaN:Yahoo 最後一天常是還沒收盤的空值,直接拿來當現價會讓報酬變 NaN,/api/review 轉 JSON 時 500
            price_cache[sym] = h["Close"].dropna() if h is not None else pd.Series(dtype=float)
        return price_cache[sym]

    for s in snaps:
        px = series(s["symbol"])
        bx = series(s["benchmark"])
        d0 = pd.Timestamp(s["date"])
        row = dict(s)
        if len(px):
            later = px[px.index >= d0]
            if len(later):
                p_now = float(px.iloc[-1])
                row["price_now"] = p_now
                row["ret"] = p_now / s["price"] - 1 if s.get("price") else None
                row["days"] = (px.index[-1] - d0).days
                if len(bx):
                    b0 = bx[bx.index >= d0]
                    if len(b0):
                        row["bench_ret"] = float(bx.iloc[-1] / b0.iloc[0] - 1)
                        row["excess"] = row["ret"] - row["bench_ret"] if row.get("ret") is not None else None
                lvl = s.get("lt_level")
                if row.get("excess") is not None and lvl is not None and row["days"] >= 20:
                    expect = 1 if lvl >= 3 else -1 if lvl <= 1 else 0
                    row["direction_ok"] = None if expect == 0 else (expect > 0) == (row["excess"] > 0)
        rows.append(row)
    judged = [r for r in rows if r.get("direction_ok") is not None]
    summary = {"n_snapshots": len(rows), "n_judged": len(judged),
               "hit_rate": (sum(1 for r in judged if r["direction_ok"]) / len(judged)) if judged else None}
    rows.sort(key=lambda r: r.get("time") or "", reverse=True)
    return {"summary": summary, "rows": rows, "learning": learning(rows), "forward": forward_stats(snaps, series)}


# ---------------------------------------------------------------- 前瞻樣本外儀表板(階段 4.4)
# 前瞻起點:v2.2 資料凍結(2026-10-21)之後。之前的快照產生在模型開發期間,結果可能已經被看過,不算樣本外。
FORWARD_START = "2026-10-22"
HORIZONS = (("1m", "1 個月", 30), ("3m", "3 個月", 91), ("12m", "12 個月", 365))
FWD_MIN_N = 30          # 少於這個數只標「樣本不足」,不給區間


def wilson(k: int, n: int, z: float = 1.96) -> list | None:
    """命中率的 Wilson 95% 區間(樣本少時比常態近似可靠)。"""
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return [c - h, c + h]


def _px_at(ser: pd.Series, d: pd.Timestamp) -> tuple[float | None, pd.Timestamp | None]:
    later = ser[ser.index >= d]
    return (float(later.iloc[0]), later.index[0]) if len(later) else (None, None)


def forward_stats(snaps: list[dict], series, today=None) -> dict:
    """前瞻樣本外:FORWARD_START 以後、正式記錄(forward_record)的快照,每檔每月只取最早一筆(避免重複計算同一段報酬)。
    每個期間(1 / 3 / 12 個月)到期的才算:超額報酬 = 個股從快照日到期滿的價格報酬 − 同期大盤。
    方向命中率只算台股、且長期模型等級不是「持有」的(美股畫面不評等,Q7;只計檔數,不算命中率)。"""
    today = pd.Timestamp(today or dt.date.today())
    seen, sample = set(), []
    for sn in sorted(snaps, key=lambda x: x.get("date") or ""):
        if not sn.get("forward_record") or (sn.get("date") or "") < FORWARD_START:
            continue
        k = (sn["symbol"], sn["date"][:7])
        if k in seen:
            continue
        seen.add(k)
        sample.append(sn)
    out = {"start": FORWARD_START, "n_snapshots": len(sample), "min_n": FWD_MIN_N, "horizons": []}
    for key, label, days in HORIZONS:
        ex, ex_us, hits, n_us = [], [], [], 0
        for sn in sample:
            d0 = pd.Timestamp(sn["date"])
            d1 = d0 + pd.Timedelta(days=days)
            if d1 > today:
                continue
            px, bx = series(sn["symbol"]), series(sn["benchmark"])
            p0, t0 = _px_at(px, d0)
            p1, t1 = _px_at(px, d1)
            b0, _ = _px_at(bx, d0)
            b1, _ = _px_at(bx, d1)
            if None in (p0, p1, b0, b1) or (t1 - d1).days > 10:      # 期滿附近沒有價格(停牌、下市)就不算
                continue
            e = (p1 / p0 - 1) - (b1 / b0 - 1)
            if sn.get("market") == "US" or sn.get("info_only"):   # 美股對 S&P 500,分開平均,不和台股混在一起
                n_us += 1
                ex_us.append(e)
                continue
            ex.append(e)
            lvl = sn.get("lt_level")
            if lvl is not None and lvl != 2:
                hits.append((lvl >= 3) == (e > 0))
        n, k = len(hits), sum(hits)
        thin = n < FWD_MIN_N
        m = sum(ex) / len(ex) if ex else None
        sd = (sum((x - m) ** 2 for x in ex) / (len(ex) - 1)) ** 0.5 if len(ex) > 1 else None
        out["horizons"].append({
            "key": key, "label": label, "days": days, "n_matured": len(ex) + n_us, "n_us": n_us, "n_dir": n,
            "us_mean_excess": sum(ex_us) / n_us if n_us else None,
            "hit_rate": k / n if n else None, "hit_ci": None if thin else wilson(k, n),
            "mean_excess": m, "excess_ci": None if len(ex) < FWD_MIN_N or sd is None else
            [m - 1.96 * sd / len(ex) ** 0.5, m + 1.96 * sd / len(ex) ** 0.5],
            "thin": thin,
        })
    return out


MIN_DAYS = 20
MIN_N = 15


def learning(rows: list[dict]) -> dict:
    """系統「學習」的核心:每個面向分數,和之後的超額報酬有沒有關係?(Spearman IC)
    同一檔股票只取最早一筆已到期的快照,避免重複計算。樣本夠了才給建議。"""
    seen, sample = set(), []
    for r in sorted(rows, key=lambda r: r.get("date") or ""):
        if r.get("excess") is None or (r.get("days") or 0) < MIN_DAYS or r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        sample.append(r)
    out = {"n": len(sample), "min_n": MIN_N, "min_days": MIN_DAYS, "pillars": [], "buckets": []}
    if len(sample) < 5:
        out["message"] = f"樣本不足:需要至少 {MIN_N} 檔「分析後已過 {MIN_DAYS} 天」的快照才開始計算(目前 {len(sample)})。持續使用或定期掃描就會累積。"
        return out
    ex = pd.Series([r["excess"] for r in sample])
    keys = ["quality", "growth", "valuation", "health", "momentum", "flows", "revisions", "sentiment", "regime"]
    for k in keys + ["lt_score", "st_score"]:
        x = pd.Series([(r["pillars"] or {}).get(k) if k in keys else r.get(k) for r in sample], dtype=float)
        m = x.notna() & ex.notna()
        if m.sum() < 5 or x[m].nunique() < 2:
            continue
        ic = float(x[m].rank().corr(ex[m].rank()))
        n = int(m.sum())
        t = ic * ((n - 2) / max(1e-9, 1 - ic ** 2)) ** 0.5 if abs(ic) < 1 else None
        out["pillars"].append({"key": k, "ic": ic, "n": n, "t": t})
    # 依長期模型等級分組的平均超額報酬
    for lvl, name in ((4, "模型等級 A"), (3, "模型等級 B"), (2, "模型等級 C"), (1, "模型等級 D"), (0, "模型等級 E")):
        xs = [r["excess"] for r in sample if r.get("lt_level") == lvl]
        if xs:
            out["buckets"].append({"rating": name, "n": len(xs), "avg_excess": sum(xs) / len(xs),
                                   "win": sum(1 for x in xs if x > 0) / len(xs)})
    out["ready"] = len(sample) >= MIN_N
    out["message"] = ("樣本已足夠,可以參考下表調整權重(仍需人工判斷)" if out["ready"]
                      else f"樣本還少({len(sample)} < {MIN_N}),下表只能當早期參考")
    return out
