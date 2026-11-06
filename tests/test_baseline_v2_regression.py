"""v1.1@v2.0 基準回歸:在凍結 v2.0 資料上離線重跑 PIT 回測,三個股票池的逐筆判斷必須和 tests/baseline/v1.1_on_v2.0 逐位元一致。"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lab.config import MAIN_ROOT

ROOT = Path(__file__).resolve().parents[1]
FROZEN = MAIN_ROOT / "data" / "frozen" / "v2.0_2026-10-16"
EXPECTED = dict(line.split() for line in (ROOT / "tests" / "baseline" / "v1.1_on_v2.0" / "rows_sha256.txt")
                .read_text(encoding="utf-8").splitlines() if line.strip())


@pytest.mark.slow
def test_v11_on_v20_replay_is_bit_identical(tmp_path):
    if not FROZEN.exists():
        pytest.skip("沒有凍結資料 v2.0(data/ 不進 git)")
    env = {k: v for k, v in os.environ.items() if not k.startswith("INVEST_LAB_")}
    env.update(INVEST_LAB_OFFLINE="1", INVEST_LAB_DATA_DIR=str(FROZEN), INVEST_LAB_OUT_DIR=str(tmp_path),
               INVEST_LAB_PRIVATE_DIR=str(tmp_path / "p"), INVEST_LAB_REGISTRY=str(tmp_path / "exp.jsonl"),
               INVEST_LAB_AGENT="test", INVEST_LAB_PARAMS=str(FROZEN / "model_params.json"))  # v1.1 參數
    r = subprocess.run([sys.executable, "-m", "lab.research.backtest_pit", "all", "--workers", "8"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, r.stderr[-2000:]
    for u, h in EXPECTED.items():
        assert hashlib.sha256((tmp_path / "bt_pit" / f"rows_{u}.csv").read_bytes()).hexdigest() == h, u
