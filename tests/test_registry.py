import os
import subprocess
import sys
from pathlib import Path

from lab.research import registry as R

ROOT = Path(__file__).resolve().parents[1]


def test_hash_is_stable_and_order_free():
    assert R.config_hash({"a": 1, "b": [1, 2]}) == R.config_hash({"b": [1, 2], "a": 1})
    assert R.config_hash({"a": 1}) != R.config_hash({"a": 2})


def test_concurrent_logging_counts_unique_configs(tmp_path):
    reg = tmp_path / "exp.jsonl"
    code = ("import sys; from lab.research import registry as R\n"
            "for i in range(30): R.log('dev', {'w': i % 7, 'p': int(sys.argv[1])}, {'ic': 0.01}, market='TW')\n")
    env = dict(os.environ, INVEST_LAB_REGISTRY=str(reg), INVEST_LAB_RECORD="0")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(k % 2)], cwd=ROOT, env=env) for k in range(4)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    rows = R.read(reg)
    assert len(rows) == 120  # 沒有因為同時寫而遺失或寫壞
    assert R.n_trials("TW", path=reg) == 14  # 7 種 w × 2 種 p


def test_family_limits_trial_count(tmp_path):
    reg = tmp_path / "e.jsonl"
    for i in range(5):
        R.log("dev", {"x": i}, {}, market="TW", family="explore", path=reg)
    R.log("dev", {"h": 1}, {}, market="TW", family="P2-H01", path=reg)
    R.log("dev", {"h": 2}, {}, market="TW", family="P2-H01", path=reg)
    assert R.n_trials("TW", path=reg, family="P2-H01") == 2
    assert R.n_trials("TW", path=reg) == 7
