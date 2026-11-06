"""模擬交易(紙上交易):依模型訊號在「虛擬帳戶」買賣,驗證策略。只做研究與模擬,不接任何券商或交易所、不下真單,完全不碰真錢。

規則 v1(都寫在 RULES,改規則要記錄):
- 模擬買入:未持有、長期模型等級 B 以上、短期分數不是偏低、資料完整度 ≥ 60、沒有財務紅旗 → 依模擬規則的權重(上限 10%)買入
- 模擬賣出:持有中、長期模型等級降到 D 以下,或有財務紅旗 → 全部賣出
- 最多同時 10 檔;每次掃描完成時更新一次(訊號沒變就不動,避免過度交易)
- 成本:台股手續費 0.1425% ×2 + 證交稅 0.3%(賣出);美股滑價 0.05%
台股、美股分開兩個帳戶(各自幣別),避免匯率干擾。
帳戶存在 private/paper.json(個人資料,不進 git)。

策略 C(階段 4.2,專案決定 2026-10-26):和 A 同樣的選股條件,加上投組風控(lab/portfolio.py:波動目標、單檔與產業上限、
每月再平衡、偏離小不交易)。新增帳本前自動備份 paper.json;A / B 的規則與歷史不動。
C 的大盤對照用含息報酬(台股 0050、美股 SPY 的還原股價),以連乘方式累積,避免除息後整段價格被重算而失真。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import shutil
import threading

import pandas as pd

from . import portfolio
from .analysis.decision import neutral_text
from .config import PRIVATE_DIR

PATH = PRIVATE_DIR / "paper.json"
_lock = threading.Lock()

RULES = {
    "initial": {"TW": 1_000_000.0, "US": 30_000.0},
    "max_positions": 10, "max_weight": 0.10, "min_confidence": 60,
    "fee_buy": {"TW": 0.001425, "US": 0.0005}, "fee_sell": {"TW": 0.001425 + 0.003, "US": 0.0005},
    "lot": {"TW": 1, "US": 1},  # 台股用零股(盤中零股交易),高價股小帳戶也能分散
}
RULES_TEXT = [
    "模擬買入條件:長期模型等級 B 以上、短期分數不是偏低、資料完整度 ≥ 60、沒有財務紅旗",
    "模擬權重:依波動換算的上限(2.5 倍 ATR 的跌幅最多影響模擬資金 1%),單檔不超過 10%;最多同時 10 檔",
    "模擬賣出條件:長期模型等級降到 D 以下,或出現財務紅旗",
    "模擬成本:台股手續費 0.1425% ×2 + 證交稅 0.3%;美股滑價 0.05%;台股用零股、美股用零碎股",
    "每次選股掃描完成時更新一次;條件沒變就不動。這些是模擬規則,不是對個股的建議",
]


STRATEGIES = {
    "TW": ("A 精選:模擬持有模型等級 B 以上", "TW"), "TW_ex": ("B 排除法:排除模型等級 D、E,其餘等權", "TW"),
    "US": ("A 精選:模擬持有模型等級 B 以上", "US"), "US_ex": ("B 排除法:排除模型等級 D、E,其餘等權", "US"),
    "TW_risk": ("C 風控版:A 的選股 + 投組風控", "TW"), "US_risk": ("C 風控版:A 的選股 + 投組風控", "US"),
}
TR_BENCH = {"TW": ("0050.TW", "0050 含息"), "US": ("SPY", "SPY 含息")}
RULES_TEXT_RISK = [
    "模擬選股:和策略 A 相同(長期模型等級 B 以上、短期分數不是偏低、資料完整度 ≥ 60、沒有財務紅旗);已持有的在等級降到 D 以下或出現紅旗前都算候選",
    f"模擬權重:候選依分數取前 {portfolio.RISK['max_positions']} 檔等權,單檔上限 {portfolio.RISK['max_weight']:.0%}、"
    f"同產業合計上限 {portfolio.RISK['max_sector']:.0%},超過的部分留現金",
    f"波動目標:用個股一年波動與固定相關係數 {portfolio.RISK['rho']} 估算投組年化波動,高於 {portfolio.RISK['vol_target']:.0%} 就整體等比例縮小(不加槓桿)",
    f"再平衡:約每月一次(距上次至少 {portfolio.RISK['rebalance_days']} 天);權重偏離 ≤ {portfolio.RISK['rebalance_band']:.0%} 不交易"
    f"(超過單檔上限的一律減回);兩次再平衡之間漲多的持股可能暫時超過 {portfolio.RISK['max_weight']:.0%};"
    "等級降到 D 以下或出現紅旗則隨時模擬賣出",
    "成本:台股手續費 0.1425% ×2 + 證交稅 0.3%;美股滑價 0.05%",
    "大盤對照:含息報酬(台股 0050、美股 SPY 還原股價)。這些是風控規則,沒有經過回測驗證,不代表能提高報酬",
]
RULES_TEXT_EX = [
    "策略 B(排除法):股票池裡模型等級 C 以上、沒有財務紅旗、資料完整度 ≥ 50 的全部等權模擬持有;降到 D 以下就模擬賣出",
    "依據:開發期重算,台股「排除模型等級 D、E」與全部等權的差距 +1.6%/年,95% 區間 −2.0% ~ +5.5%,"
    "沒有證實有效;並行觀察用(美股差距 −0.1%/年,沒有差別)",
    "只在名單變動時模擬交易(新進名單的買入、退出名單的賣出),不定期再平衡,減少模擬成本",
]


def _new_book(market: str) -> dict:
    return {"cash": RULES["initial"][market], "positions": {}, "trades": [], "equity": [],
            "start": dt.date.today().isoformat(), "bench_start": None}


def _backup_once() -> None:
    """新增帳本前備份既有的 paper.json(同一天只備份一次,不覆蓋已有的備份)。"""
    dst = PATH.with_name(f"paper.backup-{dt.date.today().isoformat()}.json")
    if PATH.exists() and not dst.exists():
        shutil.copy2(PATH, dst)


def load() -> dict:
    try:
        st = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        st = {"created": dt.date.today().isoformat(), "books": {}}
    if PATH.exists() and any(k not in st["books"] for k in STRATEGIES):
        _backup_once()
    for k, (_, m) in STRATEGIES.items():
        st["books"].setdefault(k, _new_book(m))
    return st


def _save(st: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PATH)


def _value(book: dict, prices: dict) -> float:
    v = book["cash"]
    for s, p in book["positions"].items():
        v += p["shares"] * prices.get(s, p.get("last_price", p["cost"]))
    return v


def update(market: str, rows: list[dict], bench_price: float | None = None) -> dict:
    """用一次掃描的結果更新該市場帳戶。rows 來自 scan.summarize。"""
    if market not in ("TW", "US"):
        return {}
    with _lock:
        st = load()
        book = st["books"][market]
        today = dt.date.today().isoformat()
        prices = {r["symbol"]: r["price"] for r in rows if r.get("price")}
        by_sym = {r["symbol"]: r for r in rows}
        actions = []
        # 1) 賣出
        for s in list(book["positions"]):
            r = by_sym.get(s)
            if not r:
                continue
            if (r.get("lt_level") is not None and r["lt_level"] <= 1) or r.get("red_flags"):
                p = book["positions"].pop(s)
                px = r["price"]
                gross = p["shares"] * px
                fee = gross * RULES["fee_sell"][market]
                book["cash"] += gross - fee
                pnl = gross - fee - p["shares"] * p["cost"]
                t = {"date": today, "symbol": s, "name": r.get("name"), "side": "賣出", "shares": p["shares"], "price": px,
                     "fee": fee, "pnl": pnl, "reason": f"長期模型等級 {r.get('lt_rating')}" + ("、財務紅旗" if r.get("red_flags") else "")}
                book["trades"].append(t)
                actions.append(t)
        # 2) 買進
        equity = _value(book, prices)
        cands = [r for r in rows if r["symbol"] not in book["positions"] and (r.get("lt_level") or 0) >= 3
                 and (r.get("st_code") or 0) >= 0 and (r.get("confidence") or 0) >= RULES["min_confidence"]
                 and not r.get("red_flags") and r.get("price")]
        cands.sort(key=lambda r: -((r.get("lt_score") or 0) + 0.5 * (r.get("st_score") or 0)))
        for r in cands:
            if len(book["positions"]) >= RULES["max_positions"]:
                break
            w = min(RULES["max_weight"], r.get("max_position_pct") or RULES["max_weight"])
            budget = min(equity * w, book["cash"])
            lot = RULES["lot"][market]
            px = r["price"]
            raw = budget / (px * (1 + RULES["fee_buy"][market]))
            shares = int(raw // lot * lot) if market == "TW" else math.floor(raw * 10000) / 10000
            if shares <= 0:
                continue
            cost = shares * px
            fee = cost * RULES["fee_buy"][market]
            book["cash"] -= cost + fee
            book["positions"][r["symbol"]] = {"shares": shares, "cost": (cost + fee) / shares, "entry_date": today,
                                              "name": r.get("name"), "last_price": px}
            t = {"date": today, "symbol": r["symbol"], "name": r.get("name"), "side": "買進", "shares": shares, "price": px,
                 "fee": fee, "pnl": None, "reason": f"長期 {r.get('lt_rating')}、短期 {r.get('st_signal')}、資料完整度 {r.get('confidence')}"}
            book["trades"].append(t)
            actions.append(t)
        # 3) 估值
        for s, p in book["positions"].items():
            if s in prices:
                p["last_price"] = prices[s]
        val = _value(book, prices)
        if bench_price and not book.get("bench_start"):
            book["bench_start"] = bench_price
        eq = [e for e in book["equity"] if e["date"] != today]
        eq.append({"date": today, "value": val, "bench": bench_price})
        book["equity"] = eq[-2000:]
        book["trades"] = book["trades"][-2000:]
        _save(st)
        return {"market": market, "actions": actions, "value": val}


def update_all(market: str, rows: list[dict], bench_price: float | None = None) -> dict:
    a = update(market, rows, bench_price)
    b = update_ex(market, rows, bench_price)
    return {"A": a, "B": b}


def update_ex(market: str, rows: list[dict], bench_price: float | None = None) -> dict:
    """策略 B:排除法(避開減碼 / 賣出,其餘等權)。"""
    key = f"{market}_ex"
    with _lock:
        st = load()
        book = st["books"][key]
        today = dt.date.today().isoformat()
        prices = {r["symbol"]: r["price"] for r in rows if r.get("price")}
        target = [r for r in rows if (r.get("lt_level") is not None and r["lt_level"] >= 2) and not r.get("red_flags")
                  and (r.get("confidence") or 0) >= 50 and r.get("price")]
        tset = {r["symbol"] for r in target}
        by_sym = {r["symbol"]: r for r in rows}
        actions = []
        for s in list(book["positions"]):
            r = by_sym.get(s)
            if r is None or s in tset:
                continue
            p = book["positions"].pop(s)
            px = r["price"]
            gross = p["shares"] * px
            fee = gross * RULES["fee_sell"][market]
            book["cash"] += gross - fee
            t = {"date": today, "symbol": s, "name": r.get("name"), "side": "賣出", "shares": p["shares"], "price": px,
                 "fee": fee, "pnl": gross - fee - p["shares"] * p["cost"], "reason": f"等級降到「{r.get('lt_rating')}」或出現紅旗"}
            book["trades"].append(t)
            actions.append(t)
        equity = _value(book, prices)
        each = equity / max(1, len(tset))
        for r in sorted(target, key=lambda r: -(r.get("lt_score") or 0)):
            if r["symbol"] in book["positions"]:
                continue
            px = r["price"]
            budget = min(each, book["cash"])
            raw = budget / (px * (1 + RULES["fee_buy"][market]))
            shares = int(raw) if market == "TW" else math.floor(raw * 10000) / 10000  # 美股可用零碎股
            if shares <= 0:
                continue
            cost = shares * px
            fee = cost * RULES["fee_buy"][market]
            book["cash"] -= cost + fee
            book["positions"][r["symbol"]] = {"shares": shares, "cost": (cost + fee) / shares, "entry_date": today,
                                              "name": r.get("name"), "last_price": px}
            t = {"date": today, "symbol": r["symbol"], "name": r.get("name"), "side": "買進", "shares": shares, "price": px,
                 "fee": fee, "pnl": None, "reason": f"進入名單(等級「{r.get('lt_rating')}」)"}
            book["trades"].append(t)
            actions.append(t)
        for s, p in book["positions"].items():
            if s in prices:
                p["last_price"] = prices[s]
        val = _value(book, prices)
        if bench_price and not book.get("bench_start"):
            book["bench_start"] = bench_price
        eq = [e for e in book["equity"] if e["date"] != today]
        eq.append({"date": today, "value": val, "bench": bench_price})
        book["equity"] = eq[-2000:]
        book["trades"] = book["trades"][-2000:]
        _save(st)
        return {"market": key, "actions": actions, "value": val}


def tr_level(book: dict, adj_close, today: str) -> float | None:
    """含息大盤的累積指數(起點 1.0)。adj_close:還原股價序列(pandas Series,索引是日期)。
    每次只用同一份序列裡「上次日期 → 今天」的比值往上乘,所以除息後整段還原價被重算也不會失真。"""
    try:
        s = adj_close.dropna()
        s = s[s > 0]
    except AttributeError:
        return None
    if s is None or not len(s):
        return None
    last_px = float(s.iloc[-1])
    last_day = pd.Timestamp(s.index[-1]).date().isoformat()   # 記序列最後一筆價格的日期,不記呼叫當天(資料延遲時才不會漏掉一段)
    tr = book.get("bench_tr")
    if not tr:
        book["bench_tr"] = {"date": last_day, "level": 1.0}
        book.setdefault("bench_since", today)
        return 1.0
    prev = s[s.index <= pd.Timestamp(tr["date"])]
    if not len(prev):                      # 接不上上次的日期(例如停了一年以上):從這裡重新接,中斷的那段不算,畫面會標示
        book["bench_tr"] = {"date": last_day, "level": tr["level"]}
        book["bench_gap"] = {"from": tr["date"], "to": last_day}
        return tr["level"]
    level = tr["level"] * last_px / float(prev.iloc[-1])
    book["bench_tr"] = {"date": last_day, "level": level}
    return level


def _held_weights(book: dict, prices: dict, equity: float) -> dict:
    return {s: p["shares"] * prices.get(s, p.get("last_price", p["cost"])) / equity
            for s, p in book["positions"].items()} if equity > 0 else {}


def update_risk(market: str, rows: list[dict], bench_adj=None, today: str | None = None) -> dict:
    """策略 C:A 的選股 + 投組風控。bench_adj:含息大盤的還原股價序列(可以是 None)。"""
    if market not in ("TW", "US"):
        return {}
    key = f"{market}_risk"
    R = portfolio.RISK
    with _lock:
        st = load()
        book = st["books"][key]
        today = today or dt.date.today().isoformat()
        prices = {r["symbol"]: r["price"] for r in rows if r.get("price")}
        by_sym = {r["symbol"]: r for r in rows}
        actions = []

        def trade(sym, side, shares, px, reason):
            r = by_sym.get(sym, {})
            notional = shares * px
            fee = portfolio.trade_cost(market, "sell" if side == "賣出" else "buy", notional)
            pnl = None
            if side == "賣出":
                p = book["positions"][sym]
                book["cash"] += notional - fee
                pnl = notional - fee - shares * p["cost"]
                p["shares"] = round(p["shares"] - shares, 4)
                if p["shares"] <= 0:
                    book["positions"].pop(sym)
            else:
                book["cash"] -= notional + fee
                p = book["positions"].get(sym)
                if p:
                    p["cost"] = (p["cost"] * p["shares"] + notional + fee) / (p["shares"] + shares)
                    p["shares"] = round(p["shares"] + shares, 4)
                else:
                    book["positions"][sym] = {"shares": shares, "cost": (notional + fee) / shares, "entry_date": today,
                                              "name": r.get("name"), "last_price": px}
            t = {"date": today, "symbol": sym, "name": r.get("name") or book["positions"].get(sym, {}).get("name"),
                 "side": side, "shares": shares, "price": px, "fee": fee, "pnl": pnl, "reason": reason}
            book["trades"].append(t)
            actions.append(t)

        # 1) 賣出條件(隨時執行):評等降到減碼以下或財務紅旗
        for s in list(book["positions"]):
            r = by_sym.get(s)
            if r and r.get("price") and ((r.get("lt_level") is not None and r["lt_level"] <= 1) or r.get("red_flags")):
                trade(s, "賣出", book["positions"][s]["shares"], r["price"],
                      f"長期模型等級 {r.get('lt_rating')}" + ("、財務紅旗" if r.get("red_flags") else ""))
        # 2) 再平衡(約每月一次)
        last = book.get("last_rebalance")
        due = not last or (dt.date.fromisoformat(today) - dt.date.fromisoformat(last)).days >= R["rebalance_days"]
        if due:
            def ok_new(r):
                return ((r.get("lt_level") or 0) >= 3 and (r.get("st_code") or 0) >= 0
                        and (r.get("confidence") or 0) >= RULES["min_confidence"] and not r.get("red_flags") and r.get("price"))
            elig = [r for r in rows if r.get("price") and (ok_new(r) or (r["symbol"] in book["positions"]))]
            elig.sort(key=lambda r: -((r.get("lt_score") or 0) + 0.5 * (r.get("st_score") or 0)))
            plan = portfolio.target_weights([r["symbol"] for r in elig], {r["symbol"]: r.get("vol_1y") for r in elig},
                                            {r["symbol"]: r.get("industry") for r in elig})
            equity = _value(book, prices)
            cur = _held_weights(book, prices, equity)
            # 沒有今天價格的持股不動(不知道價格就不交易)
            orders = {s: d for s, d in portfolio.rebalance_orders(cur, plan["weights"], R["rebalance_band"], cap=R["max_weight"]).items()
                      if s in prices}
            for s, d in sorted(orders.items(), key=lambda kv: kv[1]):          # 先賣後買
                px = prices[s]
                if d < 0:
                    held = book["positions"][s]["shares"]
                    sh = held if plan["weights"].get(s, 0) == 0 else min(held, _shares(market, -d * equity / px))
                    if sh > 0:
                        trade(s, "賣出", sh, px, "再平衡:移出名單" if plan["weights"].get(s, 0) == 0 else "再平衡:調降到目標權重")
                else:
                    budget = min(d * equity, book["cash"] / (1 + portfolio.FEE["buy"][market]))
                    sh = _shares(market, budget / px)
                    if sh > 0:
                        r = by_sym[s]
                        trade(s, "買進", sh, px, f"再平衡:目標權重 {plan['weights'][s]:.1%}(長期 {r.get('lt_rating')}、短期 {r.get('st_signal')})")
            book["last_rebalance"] = today
            book["risk_note"] = {"date": today, "est_vol": plan["est_vol"], "gross": plan["gross"], "scale": plan["scale"], "notes": plan["notes"]}
        # 3) 估值與含息大盤
        for s, p in book["positions"].items():
            if s in prices:
                p["last_price"] = prices[s]
        val = _value(book, prices)
        level = tr_level(book, bench_adj, today) if bench_adj is not None else None
        if level is not None and not book.get("bench_start"):
            book["bench_start"] = 1.0
        eq = [e for e in book["equity"] if e["date"] != today]
        eq.append({"date": today, "value": val, "bench": level})
        book["equity"] = eq[-2000:]
        book["trades"] = book["trades"][-2000:]
        _save(st)
        return {"market": key, "actions": actions, "value": val}


def _shares(market: str, raw: float) -> float:
    """台股零股取整數股;美股零碎股取到小數 4 位。一律無條件捨去,不超買。"""
    if raw <= 0:
        return 0
    return int(raw) if market == "TW" else math.floor(raw * 10000) / 10000


def summary() -> dict:
    st = load()
    out = {"rules": RULES_TEXT, "rules_ex": RULES_TEXT_EX, "rules_risk": RULES_TEXT_RISK, "books": {}}
    for m, b in st["books"].items():
        mk = STRATEGIES.get(m, ("", m))[1]
        init = RULES["initial"][mk]
        last = b["equity"][-1] if b["equity"] else {"value": init, "bench": None}
        bench_ret = (last["bench"] / b["bench_start"] - 1) if b.get("bench_start") and last.get("bench") else None
        bench_since = b.get("bench_since") if m.endswith("_risk") else None
        out["books"][m] = {
            "strategy": STRATEGIES.get(m, ("", m))[0], "market": mk,
            "currency": "TWD" if mk == "TW" else "USD", "initial": init, "value": last["value"], "cash": b["cash"],
            "return": last["value"] / init - 1, "bench_return": bench_ret, "start": b.get("start"),
            "positions": [{"symbol": s, **p, "value": p["shares"] * p.get("last_price", p["cost"]),
                           "pnl_pct": p.get("last_price", p["cost"]) / p["cost"] - 1} for s, p in b["positions"].items()],
            "trades": [{**t, "reason": neutral_text(t.get("reason"))} for t in reversed(b["trades"][-50:])], "equity": b["equity"],
            "bench_name": TR_BENCH[mk][1] if m.endswith("_risk") else None, "risk_note": b.get("risk_note"),
            # C 的含息大盤從第一次抓到序列那天起算;和帳戶起始日不同時畫面要標出來(期間不完全相同)
            "bench_since": bench_since if bench_since and bench_since != b.get("start") else None,
            "bench_gap": b.get("bench_gap") if m.endswith("_risk") else None,
        }
    return out
