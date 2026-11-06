"""資料來源不得假裝成瀏覽器(只抓免費公開資料,不得為了繞過限制而規避)。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_browser_impersonation_in_project_code():
    hits = [str(p.relative_to(ROOT)) for d in ("lab", "app", "scripts") for p in (ROOT / d).rglob("*.py")
            if any(s in p.read_text(encoding="utf-8") for s in ("impersonate=", "Mozilla/"))]
    assert hits == []
