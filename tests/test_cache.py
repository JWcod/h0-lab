"""快取:多程序同時寫同一筆不能寫壞;抓取失敗回傳舊資料時要記 stale 事件。"""
import os
import pickle
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_concurrent_writes_stay_valid(tmp_path):
    code = ("import lab.cache as c\n"
            "for i in range(40):\n"
            "    c.cached('t', 'same', 0, lambda: list(range(20000)), refresh=True)\n")
    env = {k: v for k, v in os.environ.items() if k != "INVEST_LAB_OFFLINE"}  # 這個測試要測連網模式的寫入
    env.update(INVEST_LAB_DATA_DIR=str(tmp_path), INVEST_LAB_OUT_DIR=str(tmp_path / "out"),
               INVEST_LAB_PRIVATE_DIR=str(tmp_path / "p"), INVEST_LAB_RECORD="0")
    procs = [subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env) for _ in range(4)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    files = list((tmp_path / "cache" / "t").glob("*.pkl"))
    assert len(files) == 1
    with open(files[0], "rb") as f:
        assert pickle.load(f)["v"] == list(range(20000))
    assert not list((tmp_path / "cache" / "t").glob("*.tmp"))


def test_stale_fallback_is_recorded(tmp_path, monkeypatch):
    from lab import cache, config
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "OFFLINE", False)
    assert cache.cached("s", "k", 3600, lambda: 1) == 1
    n = len(cache.stale_events)

    def boom():
        raise RuntimeError("source down")
    assert cache.cached("s", "k", 3600, boom, refresh=True) == 1
    assert len(cache.stale_events) == n + 1 and "source down" in cache.stale_events[-1]["error"]
