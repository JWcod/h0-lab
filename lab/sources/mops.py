"""公開資訊觀測站「重大訊息」(證交所 OpenAPI,只提供最近一天、上市公司)。

每次抓到就存到 data/mops/<日期>.json,時間久了累積出每家公司的官方公告歷史。
重大訊息是第一手資訊(比新聞早、比新聞準),例如董事會決議、庫藏股、處分資產、財測。
"""
from __future__ import annotations

import json
import re

import requests

from ..cache import cached
from ..config import DATA_DIR, OFFLINE, TTL

DIR = DATA_DIR / "mops"
if not OFFLINE:
    DIR.mkdir(parents=True, exist_ok=True)
URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"

KEYWORDS = {
    "股東回饋": ["庫藏股", "股利", "配息", "減資"],
    "財務/資產": ["取得", "處分", "背書", "資金貸與", "增資", "公司債"],
    "營運": ["財務預測", "營收", "法說", "合約", "訂單", "擴廠", "投資"],
    "人事/治理": ["董事", "經理人", "辭任", "解任", "會計師"],
    "風險事件": ["停工", "火災", "訴訟", "罰", "災害", "資安", "違約", "裁罰"],
    "澄清媒體": ["澄清", "媒體報導", "報載"],
}


def _roc_to_iso(s: str) -> str:
    s = (s or "").strip()
    if len(s) == 7 and s.isdigit():
        return f"{int(s[:3]) + 1911}-{s[3:5]}-{s[5:]}"
    return s


def _fetch() -> list[dict]:
    j = requests.get(URL, timeout=30).json()
    rows = []
    for x in j:
        x = {k.strip(): v for k, v in x.items()}
        rows.append({
            "code": str(x.get("公司代號", "")).strip(), "name": x.get("公司名稱", ""),
            "date": _roc_to_iso(x.get("發言日期", "")), "time": str(x.get("發言時間", "")).zfill(6),
            "subject": re.sub(r"\s+", " ", str(x.get("主旨", ""))).strip()[:300],
            "clause": x.get("符合條款", ""), "fact_date": _roc_to_iso(x.get("事實發生日", "")),
            "desc": str(x.get("說明", ""))[:1500],
        })
    if rows:
        day = max(r["date"] for r in rows)
        p = DIR / f"{day}.json"
        old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        seen = {(r["code"], r["time"], r["subject"]) for r in old}
        merged = old + [r for r in rows if (r["code"], r["time"], r["subject"]) not in seen]
        p.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    return rows


def refresh() -> None:
    try:
        cached("mops", "latest", TTL["news"], _fetch)
    except Exception:  # noqa: BLE001
        pass


def tag(subject: str) -> list[str]:
    return [k for k, kws in KEYWORDS.items() if any(w in subject for w in kws)]


def for_code(code: str, limit: int = 40) -> list[dict]:
    refresh()
    out = []
    for f in sorted(DIR.glob("*.json"), reverse=True)[:400]:
        try:
            rows = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for r in rows:
            if r.get("code") == code:
                out.append({**r, "tags": tag(r.get("subject", ""))})
        if len(out) >= limit:
            break
    out.sort(key=lambda r: (r.get("date", ""), r.get("time", "")), reverse=True)
    return out[:limit]


def days_collected() -> int:
    return len(list(DIR.glob("*.json")))
