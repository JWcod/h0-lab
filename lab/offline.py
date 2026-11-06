"""離線模式(INVEST_LAB_OFFLINE=1):開發工作階段、Verifier、回測重跑都在這個模式下工作。

封鎖對外連線的四道關卡:
1. HTTP(S) proxy 環境變數指向不存在的位址(擋住 libcurl / curl_cffi 這類不經過 Python socket 的連線)
2. socket 層:connect / create_connection / getaddrinfo 只允許本機位址
3. requests、curl_cffi 的 Session.request 直接丟例外
4. yfinance 的 download / Ticker / Search / Industry / Sector 直接丟例外

快取在離線模式下永不過期;找不到就記一筆 cache miss(lab/cache.py),不會連網補抓。
"""
from __future__ import annotations

import os
import socket

_installed = False
_LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


class NetworkBlocked(RuntimeError):
    pass


def _blocked(*_a, **_k):
    raise NetworkBlocked("離線模式:禁止對外連線")


def _host_of(address) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ[k] = "http://127.0.0.1:9"
    for k in ("NO_PROXY", "no_proxy"):
        os.environ.pop(k, None)

    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_gai = socket.getaddrinfo
    orig_cc = socket.create_connection

    def connect(self, address):
        if self.family == socket.AF_UNIX or _host_of(address) in _LOCAL:
            return orig_connect(self, address)
        raise NetworkBlocked(f"離線模式:禁止連線到 {_host_of(address)}")

    def connect_ex(self, address):
        if self.family == socket.AF_UNIX or _host_of(address) in _LOCAL:
            return orig_connect_ex(self, address)
        raise NetworkBlocked(f"離線模式:禁止連線到 {_host_of(address)}")

    def getaddrinfo(host, *a, **k):
        if host is None or str(host) in _LOCAL:
            return orig_gai(host, *a, **k)
        raise NetworkBlocked(f"離線模式:禁止查詢 {host}")

    def create_connection(address, *a, **k):
        if _host_of(address) in _LOCAL:
            return orig_cc(address, *a, **k)
        raise NetworkBlocked(f"離線模式:禁止連線到 {_host_of(address)}")

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
    socket.create_connection = create_connection

    try:
        import requests

        requests.sessions.Session.request = _blocked
        requests.get = requests.post = _blocked
    except ImportError:
        pass
    try:
        from curl_cffi import requests as cr

        cr.Session.request = _blocked
        cr.get = cr.post = _blocked
    except ImportError:
        pass
    try:
        import yfinance as yf

        class _NoNet:
            def __init__(self, *_a, **_k):
                raise NetworkBlocked("離線模式:yfinance 已停用")

        yf.download = _blocked
        for name in ("Ticker", "Tickers", "Search", "Industry", "Sector", "Lookup"):
            if hasattr(yf, name):
                setattr(yf, name, _NoNet)
    except ImportError:
        pass
