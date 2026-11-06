"""磁碟快取。

- key 一律經過 sha1 雜湊成檔名 → 使用者輸入不會變成路徑(防路徑穿越)。
- 用 pickle 存(只讀自己寫的檔案;快取目錄在專案內)。
- 寫入:跨程序檔案鎖(fcntl)+ 唯一暫存檔名 + 原子替換,多個程序同時寫同一筆不會互相覆蓋成壞檔。
- 抓取失敗時,如果有舊快取就回傳舊資料,並記一筆「過期資料」事件(stale_events),讓呼叫端可以標示資料可能不是最新。
- 離線模式(config.OFFLINE):快取永不過期;沒有快取就記一筆 cache miss 並丟 CacheMiss,絕不連網。
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import pickle
import threading
import time
import uuid
from typing import Any, Callable

from . import config

_locks: dict[str, threading.Lock] = {}
_global_lock = threading.Lock()
stale_events: list[dict] = []  # 抓取失敗、改用舊快取的紀錄(只留最近 200 筆)
misses: list[tuple[str, str]] = []  # 離線模式下找不到的快取


class CacheMiss(RuntimeError):
    pass


def _path(namespace: str, key: str):
    h = hashlib.sha1(f"{namespace}|{key}".encode()).hexdigest()
    ns = "".join(c for c in namespace if c.isalnum() or c in "_-")[:40] or "misc"
    return config.CACHE_DIR / ns / f"{h}.pkl"


def _lock_for(p) -> threading.Lock:
    with _global_lock:
        return _locks.setdefault(str(p), threading.Lock())


def _read(p):
    try:
        with open(p, "rb") as f:
            return pickle.load(f)
    except Exception:  # noqa: BLE001
        return None


def age(namespace: str, key: str) -> float | None:
    """快取存在多久了(秒);沒有快取回傳 None。"""
    p = _path(namespace, key)
    if not p.exists():
        return None
    old = _read(p)
    return time.time() - old["t"] if old else None


def _write(p, v) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    lockfile = p.with_suffix(".lock")
    with open(lockfile, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            tmp = p.with_name(f"{p.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
            with open(tmp, "wb") as f:
                pickle.dump({"t": time.time(), "v": v}, f)
            tmp.replace(p)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def cached(namespace: str, key: str, ttl: int, fn: Callable[[], Any], refresh: bool = False) -> Any:
    """回傳 fn() 的結果並快取。fn 丟例外時,若有舊快取則回傳舊資料(並記錄 stale 事件)。"""
    p = _path(namespace, key)
    if config.OFFLINE:
        old = _read(p) if p.exists() else None
        if old is None:
            misses.append((namespace, key[:120]))
            raise CacheMiss(f"離線模式找不到快取:{namespace} | {key[:80]}")
        return old["v"]
    with _lock_for(p):
        old = _read(p) if p.exists() else None
        if old is not None and not refresh and time.time() - old["t"] < ttl:
            return old["v"]
        try:
            v = fn()
        except Exception as e:
            if old is not None:
                stale_events.append({"namespace": namespace, "key": key[:120], "age_sec": round(time.time() - old["t"]),
                                     "error": f"{type(e).__name__}: {e}"[:200]})
                del stale_events[:-200]
                return old["v"]
            raise
        try:
            _write(p, v)
        except Exception:  # noqa: BLE001
            pass
        return v
