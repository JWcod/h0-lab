"""消息面:Google News RSS(中英文)+ Yahoo 新聞。

情緒用詞典法(v1,免費、可解釋);之後可換成 LLM 分類(需付費 API,待維護者同意)。
外部內容一律當資料處理:只取標題、來源、時間、連結,前端顯示時做 HTML 跳脫。
"""
from __future__ import annotations

import datetime as dt
import email.utils
import re
import urllib.parse

import feedparser
import requests

from ..cache import cached
from ..config import TTL

POS_EN = {
    "beat", "beats", "surge", "surges", "soar", "soars", "record", "upgrade", "upgraded", "upgrades", "raises", "raise",
    "raised", "strong", "stronger", "outperform", "rally", "rallies", "jump", "jumps", "gain", "gains", "exceed",
    "exceeds", "boost", "boosts", "bullish", "expands", "expansion", "approval", "approved", "partnership", "wins",
    "win", "tops", "topped", "higher", "growth", "accelerat", "buyback", "upside", "optimistic", "breakthrough",
    "rebound", "rebounds", "climbs", "climb", "positive", "overweight", "best",
}
NEG_EN = {
    "miss", "misses", "missed", "plunge", "plunges", "fall", "falls", "fell", "drop", "drops", "downgrade",
    "downgraded", "downgrades", "cut", "cuts", "weak", "weaker", "lawsuit", "sued", "probe", "investigation", "recall",
    "loss", "losses", "decline", "declines", "slump", "slumps", "warning", "warns", "bearish", "tariff", "tariffs",
    "ban", "bans", "sanction", "sanctions", "layoff", "layoffs", "delay", "delays", "fraud", "halt", "selloff",
    "sell-off", "lowers", "lowered", "slashes", "concern", "concerns", "risk", "risks", "underperform", "tumble",
    "tumbles", "sink", "sinks", "slide", "slides", "lower", "worst", "crash", "fears", "underweight", "shortfall",
}
POS_ZH = ["創新高", "大漲", "漲停", "上修", "調升", "買進", "看好", "成長", "優於預期", "獲利", "強勁", "利多", "突破", "加碼",
          "樂觀", "擴產", "接單", "爆發", "回升", "旺季", "轉盈", "新高", "受惠", "飆", "噴", "上調", "超標", "勁揚", "大單", "回購", "庫藏股"]
NEG_ZH = ["大跌", "跌停", "下修", "調降", "賣出", "看壞", "衰退", "低於預期", "虧損", "疲弱", "利空", "跌破", "減碼", "悲觀",
          "砍單", "警訊", "翻空", "下滑", "淡季", "轉虧", "新低", "衝擊", "關稅", "制裁", "調查", "訴訟", "裁員", "延後", "重挫",
          "崩", "失守", "賣壓", "逃命", "示警", "風險", "疑慮", "看衰", "下調", "罰"]

EVENTS = {
    "財報/法說": ["earnings", "results", "quarter", "財報", "法說", "季報", "eps", "營收", "revenue"],
    "財測/指引": ["guidance", "outlook", "forecast", "財測", "展望", "指引"],
    "分析師": ["analyst", "price target", "upgrade", "downgrade", "目標價", "評等", "外資報告", "券商"],
    "併購/合作": ["acquire", "acquisition", "merger", "deal", "partnership", "併購", "收購", "合作", "結盟"],
    "法規/政策": ["tariff", "regulator", "sec ", "ftc", "doj", "export", "ban", "sanction", "關稅", "管制", "禁令", "制裁", "政策", "川普", "trump"],
    "訴訟/調查": ["lawsuit", "probe", "investigation", "court", "訴訟", "調查", "官司"],
    "股東回饋": ["dividend", "buyback", "repurchase", "股利", "配息", "庫藏股", "回購"],
    "人事": ["ceo", "cfo", "resign", "appoint", "執行長", "董事長", "財務長", "辭任", "接任"],
    "產品/技術": ["launch", "product", "chip", "ai ", "new ", "推出", "新品", "製程", "技術", "量產"],
    "產能/供應鏈": ["capacity", "supply", "order", "factory", "fab", "產能", "擴產", "砍單", "訂單", "供應鏈", "廠"],
}


NEG_ZH += ["趨緩", "放緩", "減緩", "不如預期", "低迷", "保守", "降溫", "失速", "利空", "下殺", "跳水"]
POS_ZH += ["優於", "回溫", "暢旺", "看俏", "樂觀", "轉強", "強勢", "噴發", "上攻"]
# 片語優先:先把這些片語整段判斷,避免「成長趨緩」被算成正面
PHRASE_NEG = ["成長趨緩", "成長放緩", "獲利衰退", "營收衰退", "需求疲弱", "不如預期", "低於預期", "下修財測", "砍單"]
PHRASE_POS = ["優於預期", "超乎預期", "上修財測", "營收創新高", "獲利創新高", "接單暢旺"]
NEG_EN_PHRASES = ["slows", "slowing", "slowdown", "weaker-than-expected", "below expectations", "cuts guidance", "lowers guidance"]
POS_EN_PHRASES = ["better-than-expected", "above expectations", "raises guidance", "record revenue", "record high"]


def score_title(title: str) -> tuple[float, list[str], list[str]]:
    """回傳 (情緒分數 -1~1, 正面詞, 負面詞)。片語優先,剩下的再用單詞。"""
    t = title.lower()
    rest = title
    pos, neg = [], []
    for ph in PHRASE_NEG:
        if ph in rest:
            neg.append(ph)
            rest = rest.replace(ph, " ")
    for ph in PHRASE_POS:
        if ph in rest:
            pos.append(ph)
            rest = rest.replace(ph, " ")
    pos += [p for p in POS_EN_PHRASES if p in t]
    neg += [p for p in NEG_EN_PHRASES if p in t]
    words = re.findall(r"[a-z][a-z\-]+", t)
    pos += [w for w in words if w in POS_EN or any(w.startswith(p) for p in ("accelerat",))]
    neg += [w for w in words if w in NEG_EN and w not in ("risk", "risks")]  # risk 太常出現在中性語句
    pos += [k for k in POS_ZH if k in rest]
    neg += [k for k in NEG_ZH if k in rest]
    n = len(pos) + len(neg)
    s = 0.0 if n == 0 else (len(pos) - len(neg)) / n
    return s, pos, neg


def tag_events(title: str) -> list[str]:
    t = title.lower()
    return [k for k, kws in EVENTS.items() if any(kw in t for kw in kws)]


def _parse_time(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return email.utils.parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError):
        return None


def google_news(query: str, lang: str = "en", days: int = 14) -> list[dict]:
    hl = "en-US&gl=US&ceid=US:en" if lang == "en" else "zh-TW&gl=TW&ceid=TW:zh-Hant"
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}+when:{days}d&hl={hl}"

    def fetch():
        r = requests.get(url, timeout=30, headers={"User-Agent": "H0-Lab (open-source research tool)"})
        d = feedparser.parse(r.content)
        out = []
        for e in d.entries[:80]:
            title = e.get("title", "")
            src = (e.get("source") or {}).get("title") or ""
            if src and title.endswith(" - " + src):
                title = title[: -len(" - " + src)]
            out.append({"title": title, "link": e.get("link"), "publisher": src, "time": _parse_time(e.get("published")),
                        "origin": "Google News"})
        return out

    try:
        return cached("gnews", f"{lang}|{query}|{days}", TTL["news"], fetch)
    except Exception:  # noqa: BLE001
        return []


def enrich(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        key = re.sub(r"\W+", "", title.lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        link = it.get("link") or ""
        if not link.startswith(("http://", "https://")):
            link = ""
        s, pos, neg = score_title(title)
        out.append({**it, "title": title, "link": link, "sentiment": round(s, 2), "pos_words": pos[:5],
                    "neg_words": neg[:5], "events": tag_events(title)})
    out.sort(key=lambda x: x.get("time") or 0, reverse=True)
    return out


def summarize(items: list[dict]) -> dict:
    now = dt.datetime.now().timestamp()

    def window(days):
        xs = [x for x in items if x.get("time") and now - x["time"] <= days * 86400]
        if not xs:
            return {"n": 0, "avg": None, "pos": 0, "neg": 0}
        return {"n": len(xs), "avg": round(sum(x["sentiment"] for x in xs) / len(xs), 3),
                "pos": sum(1 for x in xs if x["sentiment"] > 0), "neg": sum(1 for x in xs if x["sentiment"] < 0)}

    ev: dict[str, int] = {}
    for x in items:
        if x.get("time") and now - x["time"] <= 14 * 86400:
            for e in x["events"]:
                ev[e] = ev.get(e, 0) + 1
    daily: dict[str, list[float]] = {}
    for x in items:
        if x.get("time"):
            d = dt.date.fromtimestamp(x["time"]).isoformat()
            daily.setdefault(d, []).append(x["sentiment"])
    timeline = [{"date": d, "n": len(v), "avg": round(sum(v) / len(v), 3)} for d, v in sorted(daily.items())][-30:]
    return {"d3": window(3), "d7": window(7), "d14": window(14), "d30": window(30), "events": ev, "timeline": timeline}
