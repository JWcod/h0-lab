"""集保結算所 股權分散表(每週更新,開放資料只給最新一週)。

每抓一次就把該週原始檔留在 data/tdcc/,時間久了自然累積出歷史,可看大戶比例趨勢。
"""
from __future__ import annotations

import io
import re

import pandas as pd
import requests

from ..cache import cached
from ..config import TDCC_DIR, TTL

URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
# 伺服器會把 requests 預設的 User-Agent 導到別的網站;如實標示工具身分就能正常下載,不假裝成瀏覽器
HEADERS = {"User-Agent": "H0-Lab (open-source research tool)"}

LEVELS = {
    1: "1-999股", 2: "1-5張", 3: "5-10張", 4: "10-15張", 5: "15-20張", 6: "20-30張", 7: "30-40張",
    8: "40-50張", 9: "50-100張", 10: "100-200張", 11: "200-400張", 12: "400-600張", 13: "600-800張",
    14: "800-1000張", 15: "1000張以上",
}


def _download() -> str:
    r = requests.get(URL, headers=HEADERS, timeout=120)
    txt = r.content.decode("utf-8-sig", "replace")
    if "證券代號" not in txt[:200]:
        raise RuntimeError("TDCC unexpected content")
    m = re.search(r"\n(\d{8}),", txt)
    if m:
        p = TDCC_DIR / f"{m.group(1)}.csv"
        if not p.exists():
            p.write_text(txt, encoding="utf-8")
    return txt


def latest_text() -> str:
    return cached("tdcc", "latest", TTL["statements"], _download)


def _parse(txt: str, code: str) -> pd.DataFrame:
    # 先用字串過濾(檔案約 2.4MB,全部丟給 pandas 太慢)
    key = f",{code}"
    lines = [ln for ln in txt.splitlines() if key in ln and ln.split(",")[1].strip() == code]
    df = pd.read_csv(io.StringIO("\n".join(lines)), header=None, dtype=str) if lines else pd.DataFrame()
    if df.empty:
        return df
    df.columns = ["date", "code", "level", "holders", "shares", "pct"]
    df["code"] = df["code"].str.strip()
    for c in ("level", "holders", "shares"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["pct"] = pd.to_numeric(df["pct"], errors="coerce")
    return df


def distribution(code: str) -> dict | None:
    """回傳最新一週分布 + 歷史(本機累積的週數)。"""
    try:
        latest_text()
    except Exception:  # noqa: BLE001
        pass
    files = sorted(TDCC_DIR.glob("*.csv"))
    if not files:
        return None
    history = []
    latest = None
    for f in files[-60:]:
        df = _parse(f.read_text(encoding="utf-8"), code)
        if df.empty:
            continue
        lv = df[df["level"].between(1, 15)]
        big400 = lv[lv["level"] >= 12]["pct"].sum()
        big1000 = lv[lv["level"] == 15]["pct"].sum()
        small50 = lv[lv["level"] <= 8]["pct"].sum()
        total_holders = lv["holders"].sum()
        row = {"date": f.stem, "big400_pct": round(float(big400), 2), "big1000_pct": round(float(big1000), 2),
               "small50_pct": round(float(small50), 2), "holders": int(total_holders)}
        history.append(row)
        latest = [{"level": int(r.level), "label": LEVELS.get(int(r.level), str(r.level)), "holders": int(r.holders),
                   "shares": int(r.shares), "pct": float(r.pct)} for r in lv.itertuples()]
    if not history:
        return None
    return {"latest": latest, "history": history}
