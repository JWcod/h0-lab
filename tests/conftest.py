"""測試一律不碰真實的個人資料與前瞻紀錄:import lab 之前先把私人資料夾、輸出指到暫存區、關掉前瞻紀錄。"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="invest_lab_test_")
os.environ.setdefault("INVEST_LAB_PRIVATE_DIR", os.path.join(_tmp, "private"))
os.environ.setdefault("INVEST_LAB_OUT_DIR", os.path.join(_tmp, "out"))
os.environ["INVEST_LAB_RECORD"] = "0"
os.environ.setdefault("INVEST_LAB_REGISTRY", os.path.join(_tmp, "experiments.jsonl"))  # 測試不寫主登記簿
