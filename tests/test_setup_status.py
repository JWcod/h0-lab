"""第一次執行引導的狀態檢查(lab/setup_status.py;規格 docs/安裝與上手規格.md §3)。

用空的暫存資料夾模擬剛下載的新使用者,再一步步放進假檔案,確認每一步的 done 跟著變;token 只回有沒有填。
"""
from __future__ import annotations

import json

from lab import config, setup_status as S


def _dirs(tmp_path):
    root, data, out, priv, snap = (tmp_path / x for x in ("root", "data", "out", "private", "snapshots"))
    root.mkdir()
    return {"root": root, "data_dir": data, "out_dir": out, "private_dir": priv, "snapshot_dir": snap}


def _step(st, sid):
    return next(s for s in st["steps"] if s["id"] == sid)


def test_new_user_nothing_done(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FINMIND_TOKEN", "")
    st = S.status(**_dirs(tmp_path))
    assert [s["id"] for s in st["steps"]] == ["install", "first_report", "finmind_token", "research"]
    assert not any(s["done"] for s in st["steps"])
    assert st["pending"] == ["install", "first_report", "research"]      # token 是選用,不算待辦
    assert st["all_done"] is False
    assert st["research"]["TW"]["ready"] is False and "raw_tw.sqlite" in st["research"]["TW"]["missing"]
    r = _step(st, "research")
    assert r["minutes"] == S.RESEARCH["minutes"] and "setup_research.py" in r["command"]
    assert _step(st, "first_report")["network"] is True and _step(st, "install")["network"] is False


def test_steps_follow_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FINMIND_TOKEN", "")
    d = _dirs(tmp_path)
    (d["root"] / ".env").write_text("FINMIND_TOKEN=\n", encoding="utf-8")
    d["private_dir"].mkdir()
    assert _step(S.status(**d), "install")["done"] is True
    (d["data_dir"] / "cache").mkdir(parents=True)
    (d["data_dir"] / "cache" / "x.json").write_text("{}", encoding="utf-8")
    assert _step(S.status(**d), "first_report")["done"] is False          # 首頁大盤環境也會寫快取,不算查過公司
    (d["snapshot_dir"] / "2330.TW").mkdir(parents=True)
    assert _step(S.status(**d), "first_report")["done"] is False
    (d["snapshot_dir"] / "2330.TW" / "2026-11-02.json").write_text("{}", encoding="utf-8")
    assert _step(S.status(**d), "first_report")["done"] is True
    bt = d["out_dir"] / "bt_pit"
    bt.mkdir(parents=True)
    for f in S.RESEARCH_FILES["TW"]:
        (bt / f).write_bytes(b"")
    st = S.status(**d)
    assert st["research"]["TW"]["ready"] is False                         # 還沒有任何台股股票池的逐筆檔
    (bt / "rows_tw_large40.csv").write_text("", encoding="utf-8")          # 新使用者的精簡版只有大型股池
    st = S.status(**d)
    assert st["research"]["TW"] == {"ready": True, "missing": [], "universes": ["tw_large40"]}
    assert st["research"]["US"]["ready"] is False and _step(st, "research")["done"] is False
    for f in S.RESEARCH_FILES["US"]:
        (bt / f).write_bytes(b"")
    (bt / "rows_us_large40.csv").write_text("", encoding="utf-8")
    st = S.status(**d)
    assert _step(st, "research")["done"] is True and st["pending"] == [] and st["all_done"] is True


def test_token_is_never_returned(tmp_path, monkeypatch):
    secret = "tok_SHOULD_NOT_LEAK_123"
    monkeypatch.setattr(config, "FINMIND_TOKEN", secret)
    st = S.status(**_dirs(tmp_path))
    assert st["checks"]["finmind_token"] is True and _step(st, "finmind_token")["done"] is True
    assert secret not in json.dumps(st)


def test_research_files_match_wizard_defaults():
    """精靈預設股票池改了,這裡要跟著改(否則引導卡會說資料齊了、精靈卻找不到)。"""
    from lab.models import wizard
    from lab.research import backtest_pit as BP
    for m, uni in wizard.DEFAULT_UNIVERSE.items():
        assert uni in S.RESEARCH_UNIVERSES[m] and BP.raw_env_path(m).name in S.RESEARCH_FILES[m]
        assert {p.name for k, p in BP.paths().items() if k in (m, "macro")} <= set(S.RESEARCH_FILES[m])
        assert all(BP.UNIVERSES[u] == m for u in S.RESEARCH_UNIVERSES[m])


def test_wizard_falls_back_to_large_cap_pool(tmp_path, monkeypatch):
    """新使用者只有 tw_large40 的逐筆檔時,精靈預設改用它;兩個都有時用正式股票池。"""
    from lab.models import wizard
    from lab.research import backtest_pit as BP
    monkeypatch.setattr(BP, "OUT", tmp_path)
    assert wizard.default_universe("TW") == "tw_pit50"                     # 都沒有:回正式股票池(錯誤訊息照常)
    (tmp_path / "rows_tw_large40.csv").write_text("", encoding="utf-8")
    assert wizard.default_universe("TW") == "tw_large40"
    (tmp_path / "rows_tw_pit50.csv").write_text("", encoding="utf-8")
    assert wizard.default_universe("TW") == "tw_pit50"
