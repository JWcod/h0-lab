"""網站評等政策(階段 3 D3):Q2 未驗證面向不進評等、Q7 美股不給評等、資料完整度改名、不說沒校準過的機率。

用離線模式 + 凍結資料 v2.2 真的跑一次 report.build(子程序,假 private、輸出到暫存、不寫前瞻紀錄)。
凍結資料不在(例如別台電腦)就跳過。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FROZEN = ROOT / "data" / "frozen" / "v2.2_2026-10-21"
SYMS = ("2330.TW", "INTC")

_SCRIPT = r"""
import json, sys
from lab import report
out = {}
for sym in sys.argv[1:]:
    r = report.build(sym)
    v = r["verdict"]
    out[sym] = {
        "lt": v["long_term"], "st": v["short_term"], "has_action": "action" in v,
        "lt_model": v.get("long_term_model"), "st_model": v.get("short_term_model"),
        "st_used": v["st_score"].get("used"), "lt_used": v["lt_score"].get("used"),
        "st_full_used": (v.get("st_score_full") or {}).get("used"),
        "unvalidated": v.get("unvalidated_pillars"), "conf": v["confidence"],
        "narrative": r.get("narrative"),
    }
    if sym == "INTC":
        from lab import scan, snapshots
        out["us_since"] = snapshots.diff({"lt_rating": "買進", "st_signal": "中性"}, snapshots.make(r))
        sm = scan.summarize(r)
        out["us_scan"] = {"info_only": sm["info_only"], "lt_rating": sm["lt_rating"], "lt_level": sm["lt_level"]}
# v1.1 參數(沒有 validated_pillars / us_ratings 鍵):評等計算與舊版相同,美股照樣有評等
base = report.load_params()
legacy = {k: v for k, v in base.items() if k not in ("validated_pillars", "us_ratings")}
report.load_params = lambda: legacy
r = report.build("INTC")
v = r["verdict"]
out["legacy_INTC"] = {"lt": v["long_term"], "unvalidated": v.get("unvalidated_pillars"), "st_used": v["st_score"].get("used"),
                      "st_full_used": (v.get("st_score_full") or {}).get("used")}
print("JSON" + json.dumps(out, ensure_ascii=False, default=str))
"""


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    if not (FROZEN / "cache").exists():
        pytest.skip("沒有凍結資料 v2.2")
    tmp = tmp_path_factory.mktemp("rating")
    (tmp / "private").mkdir()
    env = dict(os.environ, INVEST_LAB_OFFLINE="1", INVEST_LAB_DATA_DIR=str(FROZEN), INVEST_LAB_OUT_DIR=str(tmp / "out"),
               INVEST_LAB_PRIVATE_DIR=str(tmp / "private"), INVEST_LAB_RECORD="0", INVEST_LAB_AGENT="pytest")
    r = subprocess.run([sys.executable, "-c", _SCRIPT, "2330.TW", "INTC"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    line = next(x for x in r.stdout.splitlines() if x.startswith("JSON"))
    return json.loads(line[4:])


def test_q2_ratings_use_only_validated_pillars(reports):
    params = json.loads((ROOT / "lab" / "model_params.json").read_text(encoding="utf-8"))
    ok = set(params["validated_pillars"])
    for sym, r in ((k, reports[k]) for k in SYMS):
        assert set(r["st_used"]) <= ok and set(r["lt_used"]) <= ok, (sym, r["st_used"], r["lt_used"])
        assert r["unvalidated"] and not (set(r["unvalidated"]) & ok)
        # 未驗證面向照樣算(參考分數)
        assert set(r["st_full_used"]) - ok, r["st_full_used"]


def test_q7_us_is_info_only_but_model_rating_kept(reports):
    us = reports["INTC"]
    assert us["lt"]["level"] is None and us["lt"]["rating"] == "不評等" and us["lt"]["method"] == "us_info_only"
    assert us["st"]["signal"] == "不評等" and not us["has_action"]
    assert us["lt_model"] is not None and "rating" in us["lt_model"]            # 掃描 / 模擬帳戶用的內部評等還在
    assert any("不給等級" in p for p in us["narrative"])
    tw = reports["2330.TW"]
    assert tw["lt"].get("method") != "us_info_only" and tw["lt"] == tw["lt_model"]


def test_wording(reports):
    for sym, r in ((k, reports[k]) for k in SYMS):
        assert r["conf"]["title"] == "資料完整度"
        text = "".join(r["narrative"])
        assert "機率落在" not in text and "68%" not in text, sym
    tw = "".join(reports["2330.TW"]["narrative"])
    assert "沒有證實的預測力" in tw                                              # 價位前有提醒


def test_us_internal_rating_never_shown(reports):
    # since_last 不顯示美股評等變化;掃描列標 info_only(畫面顯示「不評等」),內部評等仍在給模擬帳戶
    assert "rating_change" not in reports["us_since"] and "signal_change" not in reports["us_since"]
    sc = reports["us_scan"]
    assert sc["info_only"] is True and sc["lt_rating"] not in (None, "不評等")
    us_text = "".join(reports["INTC"]["narrative"])
    assert "強力買進價" not in us_text and "停損" not in us_text


def test_legacy_params_unchanged(reports):
    lg = reports["legacy_INTC"]
    assert lg["unvalidated"] == [] and lg["st_used"] == lg["st_full_used"]
    assert lg["lt"].get("method") != "us_info_only" and lg["lt"].get("rating") != "不評等"
