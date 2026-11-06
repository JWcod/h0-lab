"""公司名稱 → 股票代號。

支援:台股代號(2330)、中文名稱(台積電)、英文名稱(Apple / TSMC)、美股代號(AAPL)、
常見中文俗稱(輝達、蘋果)。來源:FinMind 台股清單 + SEC 美股清單 + Yahoo 搜尋(備援)。
"""
from __future__ import annotations

import difflib
import re

from .sources import finmind, sec, yahoo

# 常用別名(英文/俗稱 → 代號)。之後可以自己加。
ALIASES = {
    # 台股
    "tsmc": "2330", "台積": "2330", "mediatek": "2454", "聯發": "2454", "foxconn": "2317", "hon hai": "2317",
    "鴻海精密": "2317", "ase": "3711", "日月光": "3711", "umc": "2303", "delta": "2308", "quanta": "2382",
    "largan": "3008", "asus": "2357", "華碩電腦": "2357", "acer": "2353", "wistron": "3231", "wiwynn": "6669",
    "realtek": "2379", "novatek": "3034", "chunghwa telecom": "2412", "中華電信": "2412", "fubon": "2881",
    "cathay": "2882", "ctbc": "2891", "nanya": "1303", "formosa plastics": "1301", "evergreen": "2603",
    "yang ming": "2609", "世芯": "3661", "alchip": "3661", "創意電子": "3443", "gudeng": "3680", "aspeed": "5274",
    "信驊科技": "5274", "力積電": "6770", "南亞科技": "2408", "nanya tech": "2408", "華邦": "2344", "winbond": "2344",
    "macronix": "2337", "旺宏電子": "2337", "台達": "2308", "廣達電腦": "2382", "緯創資通": "3231",
    # 美股
    "蘋果": "AAPL", "輝達": "NVDA", "英偉達": "NVDA", "微軟": "MSFT", "谷歌": "GOOGL", "google": "GOOGL",
    "alphabet": "GOOGL", "亞馬遜": "AMZN", "特斯拉": "TSLA", "美光": "MU", "超微": "AMD", "英特爾": "INTC",
    "博通": "AVGO", "高通": "QCOM", "臉書": "META", "facebook": "META", "網飛": "NFLX", "波克夏": "BRK-B",
    "berkshire": "BRK-B", "艾司摩爾": "ASML", "應用材料": "AMAT", "科林研發": "LRCX", "甲骨文": "ORCL",
    "超微電腦": "SMCI", "安謀": "ARM", "德州儀器": "TXN", "德儀": "TXN", "可口可樂": "KO", "摩根大通": "JPM",
    "台積電adr": "TSM", "邁威爾": "MRVL", "科磊": "KLAC", "新思": "SNPS", "益華": "CDNS", "思科": "CSCO",
    "賽富時": "CRM", "奧多比": "ADBE", "帕蘭泰爾": "PLTR", "palantir": "PLTR", "威騰": "WDC", "希捷": "STX",
    "沃爾瑪": "WMT", "好市多": "COST", "嬌生": "JNJ", "禮來": "LLY", "輝瑞": "PFE", "迪士尼": "DIS",
    "麥當勞": "MCD", "星巴克": "SBUX", "耐吉": "NKE", "波音": "BA", "高盛": "GS", "摩根士丹利": "MS",
    "visa": "V", "萬事達": "MA", "超級微電腦": "SMCI", "戴爾": "DELL", "惠普": "HPQ", "ibm": "IBM",
    "康寧": "GLW", "微芯": "MCHP", "安森美": "ON", "恩智浦": "NXPI", "亞德諾": "ADI", "格芯": "GFS",
    "三星": "005930.KS", "samsung": "005930.KS", "sk海力士": "000660.KS", "sk hynix": "000660.KS",
}


def _norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[\s\.,'’\-]+", " ", s)
    for suf in (" inc", " corp", " corporation", " co", " ltd", " limited", " plc", " holdings", " company", " group",
                " class a", " class b", " the"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s.strip()


def _tw_universe() -> list[dict]:
    df = finmind.stock_info()
    if df is None or df.empty:
        return []
    # 同一檔股票可能有多個產業分類(例:台積電 = 半導體業 + 電子工業),取最具體的那個
    generic = {"電子工業", "其他", "所有證券", "創新板股票", "其他電子類", "數位雲端類", "居家生活類"}
    skip = {"ETF", "ETN", "受益證券", "存託憑證", "Index", "大盤", "上櫃指數股票型基金(ETF)", "上櫃ETF"}
    out = []
    for sid, g in df.groupby("stock_id"):
        sid = str(sid)
        if not re.fullmatch(r"\d{4}[A-Z]?", sid):  # 排除權證等
            continue
        cats = [str(c) for c in g["industry_category"].dropna().unique()]
        if any(c in skip for c in cats):
            continue
        typ = str(g["type"].iloc[-1])
        if typ not in ("twse", "tpex"):
            continue
        specific = [c for c in cats if c not in generic]
        cat = specific[0] if specific else (cats[0] if cats else "")
        out.append({"symbol": f"{sid}.TW" if typ == "twse" else f"{sid}.TWO", "code": sid,
                    "name": g["stock_name"].iloc[-1], "market": "TW", "exchange": "上市" if typ == "twse" else "上櫃",
                    "industry": cat, "categories": cats})
    return out


def _us_universe() -> list[dict]:
    try:
        df = sec.tickers()
    except Exception:  # noqa: BLE001
        return []
    return [{"symbol": r.ticker.replace(".", "-"), "code": r.ticker, "name": r.title, "market": "US", "exchange": "US",
             "industry": ""} for r in df.itertuples()]


_cache: dict = {}


def universe() -> tuple[list[dict], list[dict]]:
    if "tw" not in _cache or not _cache["tw"]:
        _cache["tw"] = _tw_universe()
    if "us" not in _cache or not _cache["us"]:
        _cache["us"] = _us_universe()
    return _cache["tw"], _cache["us"]


def tw_lookup(code: str) -> dict | None:
    tw, _ = universe()
    for x in tw:
        if x["code"] == code:
            return x
    return None


def search(q: str, limit: int = 10) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    q = q[:60]
    tw, us = universe()
    results: list[tuple[float, dict]] = []
    ql = q.lower()
    nq = _norm(q)

    alias = ALIASES.get(ql) or ALIASES.get(nq)
    if alias:
        if re.fullmatch(r"\d{4}[A-Z]?", alias):
            x = tw_lookup(alias)
            if x:
                results.append((100, x))
        else:
            hit = next((x for x in us if x["symbol"].upper() == alias.upper()), None)
            results.append((100, hit or {"symbol": alias, "code": alias, "name": alias, "market": "US" if "." not in alias else "OTHER",
                                         "exchange": "", "industry": ""}))

    # 台股代號
    m = re.fullmatch(r"(\d{4}[A-Z]?)(\.TWO?|)", q.upper())
    if m:
        x = tw_lookup(m.group(1))
        if x:
            results.append((99, x))

    for x in tw:
        name = x["name"]
        if q == name:
            results.append((98, x))
        elif name.startswith(q):
            results.append((90 - len(name) * 0.1, x))
        elif q in name:
            results.append((80 - len(name) * 0.1, x))

    qu = q.upper().replace(".", "-")
    for x in us:
        if x["symbol"].upper() == qu:
            results.append((97, x))
            continue
        nn = _norm(x["name"])
        if nn == nq:
            results.append((95, x))
        elif len(nq) >= 3 and nn.startswith(nq):
            results.append((85 - len(nn) * 0.05, x))
        elif len(nq) >= 4 and f" {nq}" in f" {nn}":
            results.append((70 - len(nn) * 0.05, x))

    if not results and len(q) >= 2:
        # 模糊比對(打錯字)
        names = {x["name"]: x for x in tw}
        for n in difflib.get_close_matches(q, list(names), n=5, cutoff=0.5):
            results.append((60, names[n]))
        if re.search(r"[a-zA-Z]", q):
            for y in yahoo.search(q):
                sym = y.get("symbol") or ""
                if y.get("type") not in ("EQUITY", None):
                    continue
                if sym.endswith((".TW", ".TWO")):
                    x = tw_lookup(sym.split(".")[0])
                    if x:
                        results.append((65, x))
                        continue
                results.append((55, {"symbol": sym, "code": sym, "name": y.get("name") or sym,
                                     "market": "US" if "." not in sym else "OTHER", "exchange": y.get("exchange") or "",
                                     "industry": ""}))

    seen, out = set(), []
    for _, x in sorted(results, key=lambda t: -t[0]):
        if x["symbol"] in seen:
            continue
        seen.add(x["symbol"])
        out.append(x)
        if len(out) >= limit:
            break
    return out


SYMBOL_RE = re.compile(r"^[A-Z0-9\-\^=]{1,15}(\.[A-Z]{1,4})?$")


def valid_symbol(sym: str) -> bool:
    return bool(SYMBOL_RE.fullmatch(sym or ""))
