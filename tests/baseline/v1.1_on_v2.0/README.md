# v1.1 在 v2.0 新資料上的基準(比較鏈第二段)

- 資料:`data/frozen/v2.0_2026-10-16`(唯讀,不進 git)
- 程式:`lab/research/backtest_pit.py`(模型 v1.1 的 `judge_at` 完全不變),含 1.9c PIT 截斷、1.9d 時效檢查、含息報酬修正(1773ed9)
- 重現:`INVEST_LAB_OFFLINE=1 INVEST_LAB_DATA_DIR=data/frozen/v2.0_2026-10-16 INVEST_LAB_OUT_DIR=<輸出> .venv/bin/python -m lab.research.backtest_pit all --workers 8`
- 逐筆判斷 SHA-256(解壓後)在 `rows_sha256.txt`;自動檢查:`pytest -q -m slow`(`tests/test_baseline_v2_regression.py`,約 6 分鐘)
- 逐筆判斷與完整評估含第三方資料衍生值,公開版只附 SHA-256 指紋。
- 2026-10-16 維護者在凍結資料上重跑(取代同日稍早、1.9 修正前的版本;差異:2448 晶電 49 筆過期財報判斷改為跳過、2603 長榮 25 筆公平價因含息報酬修正而改變)。
