"""個人投資筆記(每家公司一份):投資理由、賣出條件、想追蹤的事。存在 private/notes/(不進 git)。"""
from __future__ import annotations

import datetime as dt
import json

from .config import PRIVATE_DIR
from .resolver import valid_symbol

DIR = PRIVATE_DIR / "notes"
FIELDS = ("thesis", "sell_rules", "watch", "free")


def _path(symbol: str):
    symbol = symbol.upper()
    if not valid_symbol(symbol):
        raise ValueError("代號格式不正確")
    DIR.mkdir(parents=True, exist_ok=True)
    return DIR / f"{symbol}.json"


def load(symbol: str) -> dict | None:
    p = _path(symbol)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save(symbol: str, data: dict) -> dict:
    p = _path(symbol)
    note = {k: str(data.get(k, ""))[:5000] for k in FIELDS}
    note["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(note, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return note
