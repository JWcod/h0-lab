"""原生視窗的連結規則(不需要 pyobjc:只測純函式;視窗本身在打包版實測)。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("mac_window", Path(__file__).resolve().parent.parent / "packaging" / "mac_window.py")
mac_window = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mac_window)


@pytest.mark.parametrize("url,kind", [
    ("http://127.0.0.1:8765/", "internal"),
    ("http://localhost:8765/#/models", "internal"),
    ("about:blank", "internal"),
    ("blob:http://127.0.0.1:8765/abc", "internal"),
    ("https://github.com/x/y/releases", "external"),
    ("http://example.com/", "external"),
    ("http://127.0.0.1:9999/", "external"),                 # 別的埠不是我們
    ("https://127.0.0.1:8765/", "external"),
    ("http://127.0.0.1.evil.example:8765/", "external"),
    ("http://127.0.0.1:8765@evil.example/", "external"),
    ("blob:https://evil.example/abc", "blocked"),
    ("file:///Applications/Calculator.app", "blocked"),
    ("javascript:alert(1)", "blocked"),
    ("x-apple.systempreferences:com.apple.preference", "blocked"),
    ("data:text/html,hi", "blocked"),
    ("http://[bad", "blocked"),
    ("", "blocked"),
])
def test_classify_url(url, kind):
    assert mac_window.classify_url(url, 8765) == kind
