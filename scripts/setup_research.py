"""新使用者一鍵建立回測資料的指令入口(邏輯在 lab/setup_research.py,打包的 App 也用那一份)。

用法:.venv/bin/python scripts/setup_research.py [--plan] [--tw 2330,2317] [--us AAPL,MSFT] [--force]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lab.setup_research import config, estimate_minutes, has_full_research, main, n_sec, todo  # noqa: E402,F401

if __name__ == "__main__":
    sys.exit(main())
