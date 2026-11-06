"""命令列掃描(適合排程,例如每天收盤後跑一次):
    .venv/bin/python scripts/scan_cli.py tw_large
    .venv/bin/python scripts/scan_cli.py us_large watchlist
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lab import scan  # noqa: E402

for key in sys.argv[1:] or ["tw_large", "us_large"]:
    t = time.time()
    label, syms = scan._symbols(key)
    print(f"== {label}:{len(syms)} 檔", flush=True)
    scan._job.update({"running": True, "key": key, "label": label, "total": len(syms), "done": 0, "errors": []})
    scan._run(key, label, syms, False)
    res = scan.latest(key)
    print(f"   完成 {len(res['rows'])} 檔,失敗 {len(res['errors'])},{time.time() - t:.0f} 秒", flush=True)
    for e in res["errors"]:
        print("   ✗", e["symbol"], e["error"][:120])
