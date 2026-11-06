"""P2-H10 網站版:單檔報告的長期評等用正式股票池的固定分位切點,和回測 `fixed_quantile_levels` 一致。"""
import json

import numpy as np
import pandas as pd

from lab import config
from lab import report as RP
from lab.analysis import decision as DE
from lab.research import backtest_pit as BP

CUTS = [0.1, 0.3, 0.7, 0.9]
VAL = {"price": 100.0, "fair_value": 120.0, "upside": 0.2, "uncertainty": "中", "margin_of_safety": 0.25,
       "zones": {"well_below": 90.0, "below": 105.0, "above": 135.0, "well_above": 150.0}}


def _pool(seed=0, n=45):
    rng = np.random.default_rng(seed)
    s = np.round(rng.uniform(20, 90, n), 1)
    s[:6] = s[6]                                   # 故意放同分
    return [float(x) for x in s]


def test_thresholds_reproduce_backtest_levels():
    for seed in range(5):
        scores = _pool(seed)
        r = pd.DataFrame({"date": ["2026-09-30"] * len(scores), "lt_score": scores, "lt_level": [2.0] * len(scores)})
        bt = BP.fixed_quantile_levels(r, CUTS).astype(int).tolist()
        th = DE.quantile_thresholds(scores, CUTS)
        assert th == sorted(th)
        assert [DE.level_from_thresholds(s, th) for s in scores] == bt


def test_thresholds_none_when_pool_too_small():
    assert DE.quantile_thresholds([50.0, 60.0], CUTS) is None
    assert DE.quantile_thresholds([], CUTS) is None


def test_write_cutpoints_latest_full_date_and_only_rated_rows(tmp_path, monkeypatch):
    params = config.load_params()
    monkeypatch.setattr(config, "load_params", lambda: params)
    old, new = _pool(1), _pool(2, 40)
    r = pd.DataFrame({"date": ["2026-08-31"] * len(old) + ["2026-09-30"] * len(new) + ["2026-10-31"] * 3,
                      "lt_score": old + new + [10.0, 20.0, 30.0], "lt_level": 2.0})
    r.loc[len(old), "lt_level"] = np.nan          # 沒有公平價的列不參與
    out = BP.write_cutpoints({"tw_pit50": r}, tmp_path / DE.CUTPOINTS_FILE)
    m = out["markets"]["TW"]
    assert m["date"] == "2026-09-30" and m["n"] == len(new) - 1      # 最後一天只有 3 檔(< MIN_POOL),退到前一天
    assert m["thresholds"] == DE.quantile_thresholds(new[1:], CUTS)
    assert "US" not in out["markets"]
    monkeypatch.setattr(config, "OUT_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "nope")
    cp, why = DE.load_cutpoints("TW", params)
    assert why is None and cp["thresholds"] == m["thresholds"]
    assert DE.load_cutpoints("US", params)[0] is None
    other = json.loads(json.dumps(params))
    other["pillar_weights"]["long_term"]["quality"] = 99
    assert DE.load_cutpoints("TW", other)[0] is None                  # 權重不同 = 切點過期,不用


def test_write_cutpoints_skipped_with_v11_params(tmp_path, monkeypatch):
    v11 = json.loads((config.ROOT / "tests" / "baseline" / "model_params_v1.1.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(config, "load_params", lambda: v11)
    r = pd.DataFrame({"date": ["2026-09-30"] * 45, "lt_score": _pool(), "lt_level": 2.0})
    assert BP.write_cutpoints({"tw_pit50": r}, tmp_path / "x.json") is None
    assert not (tmp_path / "x.json").exists()


def test_report_decision_paths(tmp_path, monkeypatch):
    params = config.load_params()
    monkeypatch.setattr(config, "OUT_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "nope")
    lt = {"score": 80.0}
    # 1. v1.1 參數(沒有分位鍵)= 舊行為,輸出完全不變
    v11 = json.loads((config.ROOT / "tests" / "baseline" / "model_params_v1.1.json").read_text(encoding="utf-8"))
    assert RP._long_term_decision(VAL, lt, {}, {}, "TW", v11) == DE.long_term(VAL, lt, {}, {}, "TW", v11["decision_rules"])
    # 2. 有分位參數但沒有切點檔:退回舊規則並標示
    fb = RP._long_term_decision(VAL, lt, {}, {}, "TW", params)
    assert fb["method"] == "legacy_fallback" and "不一致" in fb["reasons"][-1]
    # 3. 有切點檔:固定分位,估值不決定評等
    r = pd.DataFrame({"date": ["2026-09-30"] * 45, "lt_score": _pool(), "lt_level": 2.0})
    th = BP.write_cutpoints({"tw_pit50": r}, tmp_path / DE.CUTPOINTS_FILE)["markets"]["TW"]["thresholds"]
    q = RP._long_term_decision(VAL, {"score": th[3] + 0.1}, {}, {}, "TW", params)
    assert q["method"] == "fixed_quantile" and q["rating"] == "模型等級 A"
    assert RP._long_term_decision(VAL, {"score": th[0] - 0.1}, {}, {}, "TW", params)["rating"] == "模型等級 E"
    flagged = RP._long_term_decision(VAL, {"score": th[3] + 0.1}, {"health": {"altman_z": 1.0}}, {}, "TW", params)
    assert flagged["level"] == 4 and flagged["red_flags"]          # 紅旗照列,但和回測一樣不改評等
    # 4. 沒有公平價 / 沒有長期分數:不評等
    nofv = RP._long_term_decision(dict(VAL, fair_value=None), lt, {}, {}, "TW", params)
    assert nofv["level"] is None and nofv["rating"] == "不評等(估值不可得)"
    assert RP._long_term_decision(VAL, {"score": None}, {}, {}, "TW", params)["level"] is None
    # 5. 美股沒有切點:退回舊規則
    assert RP._long_term_decision(VAL, lt, {}, {}, "US", params)["method"] == "legacy_fallback"
