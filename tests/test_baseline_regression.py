"""基準回歸:在凍結資料上離線重跑 v1.1,逐筆判斷必須和 tests/baseline 的指紋一模一樣。"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from lab.config import MAIN_ROOT  # noqa: E402  worktree 裡沒有 data/,凍結資料在主資料夾

FROZEN = MAIN_ROOT / "data" / "frozen" / "v1.1_2026-10-15"
MANIFEST = json.loads((ROOT / "tests" / "baseline" / "data_manifest.json").read_text(encoding="utf-8"))


@pytest.mark.slow
@pytest.mark.parametrize("market", ["TW", "US"])
def test_v11_replay_is_bit_identical(market, tmp_path):
    if not FROZEN.exists():
        pytest.skip("沒有凍結資料(data/ 不進 git)")
    r = subprocess.run([sys.executable, "tests/baseline/tools/replay.py", str(FROZEN), str(tmp_path), market],
                       cwd=ROOT, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stderr[-2000:]
    info = json.loads(r.stdout.strip().splitlines()[-1])
    assert info["cache_misses"] == 0
    rows = tmp_path / "research" / f"backtest_{market}_rows.csv"
    assert hashlib.sha256(rows.read_bytes()).hexdigest() == MANIFEST["rows_csv_sha256"][market]
