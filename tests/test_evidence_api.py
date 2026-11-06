"""證據頁 API(階段 3 D3):只放行開發集版本的摘要檔。"""
from __future__ import annotations

import json

from lab import config
from lab.research import splits


def _call(monkeypatch, tmp_path, payload):
    from app import server
    (tmp_path / "research").mkdir(exist_ok=True)
    if payload is not None:
        (tmp_path / "research" / "evidence.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(config, "OUT_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "nothing")
    return server.api_research_evidence()


def test_missing_file(monkeypatch, tmp_path):
    r = _call(monkeypatch, tmp_path, None)
    assert r["evidence"] is None and "還沒有驗證摘要" in r["message"]


def test_rejects_non_dev(monkeypatch, tmp_path):
    for bad in ({"split": "holdout", "holdout_start": str(splits.HOLDOUT_START.date())}, {"split": "dev", "holdout_start": "2030-01-01"}):
        r = _call(monkeypatch, tmp_path, bad)
        assert r["evidence"] is None and "不是開發期" in r["message"]


def test_accepts_dev(monkeypatch, tmp_path):
    ok = {"split": "dev", "holdout_start": str(splits.HOLDOUT_START.date()), "primary": {}, "gates": []}
    assert _call(monkeypatch, tmp_path, ok)["evidence"] == ok
