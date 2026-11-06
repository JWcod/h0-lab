"""離線重跑 v1.1 歷史回測:只讀凍結資料、封鎖網路、凍結日期,確保基準可以逐位元重現。

用法(在專案根目錄):
  .venv/bin/python tests/baseline/tools/replay.py data/frozen/v1.1_2026-10-15 <輸出資料夾> TW|US

做法:設定 INVEST_LAB_OFFLINE=1、INVEST_LAB_DATA_DIR=<凍結資料夾>、INVEST_LAB_OUT_DIR=<輸出> 後才 import lab,
由 lab/offline.py 封鎖連線、lab/cache.py 讓快取永不過期(沒有就記 cache miss),「今天」= 凍結資料夾的 freeze_date.txt。
模型參數讀凍結資料夾裡的 model_params.json(不是 lab/model_params.json)。
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
FROZEN = pathlib.Path(sys.argv[1]).resolve()
OUT = pathlib.Path(sys.argv[2]).resolve()
MKT = sys.argv[3]
os.environ.update({"INVEST_LAB_OFFLINE": "1", "INVEST_LAB_DATA_DIR": str(FROZEN), "INVEST_LAB_OUT_DIR": str(OUT),
                   "INVEST_LAB_PRIVATE_DIR": str(OUT / "private_fake")})
sys.path.insert(0, str(ROOT))

import lab.cache as CA  # noqa: E402
import lab.config as C  # noqa: E402

C.PARAMS_PATH = FROZEN / "model_params.json"
import lab.research.backtest as BT  # noqa: E402

res = BT.run(MKT)
print(json.dumps({"market": MKT, "n": res["n_judgments"],
                  "lt": {h: (v["right"], v["wrong"], round(v["hit"], 4)) for h, v in res["long_term"].items()},
                  "st": {h: (v["right"], v["wrong"], round(v["hit"], 4)) for h, v in res["short_term"].items()},
                  "skipped": res["n_skipped"], "cache_misses": len(CA.misses), "miss_sample": CA.misses[:10]},
                 ensure_ascii=False))
