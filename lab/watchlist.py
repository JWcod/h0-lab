"""觀察清單:存在 private/watchlist.json(個人資料,不進 git)。"""
from __future__ import annotations

import json
import threading

from .config import PRIVATE_DIR
from .resolver import valid_symbol

PATH = PRIVATE_DIR / "watchlist.json"
_lock = threading.Lock()


def load() -> list[dict]:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PATH)


def add(symbol: str, name: str = "") -> list[dict]:
    symbol = symbol.upper()
    if not valid_symbol(symbol):
        raise ValueError("代號格式不正確")
    with _lock:
        items = load()
        if not any(x["symbol"] == symbol for x in items):
            items.append({"symbol": symbol, "name": (name or symbol)[:40]})
            _save(items[:200])
        return items


def remove(symbol: str) -> list[dict]:
    with _lock:
        items = [x for x in load() if x["symbol"] != symbol.upper()]
        _save(items)
        return items
