"""離線模式必須擋住所有對外連線,而且快取找不到時要記成 cache miss(不能悄悄連網)。"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = r"""
import json, os, socket
import lab.config  # noqa: F401  觸發離線模式
from lab import cache, offline
out = {}
def attempt(name, fn):
    try:
        fn(); out[name] = "allowed"
    except offline.NetworkBlocked:
        out[name] = "blocked"
    except Exception as e:  # 其他錯誤(例如 proxy 連不上)也算沒連出去,但要分開記
        out[name] = "error:" + type(e).__name__
import requests
attempt("requests", lambda: requests.get("https://example.com", timeout=3))
attempt("socket", lambda: socket.create_connection(("example.com", 443), timeout=3))
attempt("getaddrinfo", lambda: socket.getaddrinfo("example.com", 443))
import yfinance as yf
attempt("yf_ticker", lambda: yf.Ticker("AAPL"))
attempt("yf_download", lambda: yf.download("AAPL"))
from curl_cffi import requests as cr
attempt("curl_cffi", lambda: cr.get("https://example.com", timeout=3))
try:
    cache.cached("nope", "k", 1, lambda: 1)
    out["miss"] = "no-raise"
except cache.CacheMiss:
    out["miss"] = "raised"
out["miss_recorded"] = len(cache.misses)
out["record_forward"] = lab.config.RECORD_FORWARD
print(json.dumps(out))
"""


def test_offline_blocks_network(tmp_path):
    env = dict(os.environ, INVEST_LAB_OFFLINE="1", INVEST_LAB_DATA_DIR=str(tmp_path / "data"),
               INVEST_LAB_OUT_DIR=str(tmp_path / "out"), INVEST_LAB_PRIVATE_DIR=str(tmp_path / "priv"))
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    for k in ("requests", "socket", "getaddrinfo", "yf_ticker", "yf_download", "curl_cffi"):
        assert out[k] == "blocked", (k, out)
    assert out["miss"] == "raised" and out["miss_recorded"] == 1
    assert out["record_forward"] is False
    assert not (tmp_path / "data").exists()  # 離線模式不建立 / 不寫資料夾
