"""新使用者一鍵建立回測資料(指令入口 scripts/setup_research.py,App 內由 lab/setup_job.py 觸發;精簡版:台股 TW_LARGE 40 檔 + 美股 US_LARGE 40 檔;規格 docs/安裝與上手規格.md §2.1)。

選風格精靈的「今天的排名」和「過去表現」要讀 data/bt_pit/ 的檔案。這支腳本依序:
  1. 下載(要網路,只抓免費公開資料,全部進快取;可以中斷,重跑時已下載的略過)
     - Yahoo 長歷史:80 檔 + 6 個大盤 / 總經序列(每檔間隔 1 秒)
     - 證交所報酬指數(台股含息大盤):2014 起每月一次(每次間隔約 3 秒,限速見 lab/sources/twse_hist.py)
     - FinMind 台股財報 6 個資料集 × 40 檔(額度快用完就等,不繞過限制)
     - SEC 美股財報(companyfacts)40 檔 + 代號對照表 1 次
  2. 在自己電腦上計算(不用網路):python -m lab.research.backtest_pit build / judge(只算大型股兩個股票池)

用法:
    .venv/bin/python scripts/setup_research.py --plan          只列出要做的事與估計,不連網
    .venv/bin/python scripts/setup_research.py                 全部做完
    .venv/bin/python scripts/setup_research.py --tw 2330,2317 --us AAPL,MSFT   只用幾檔(測試用)
注意:
- 這支腳本是「第一次建立」用:已經有的快取不會重抓(歷史資料不變),所以隔幾個月重跑不會更新最近的股價;
  要更新得清掉 data/cache 後重跑(之後再做更新模式)。
- 這台電腦已經有完整版回測資料(bt_pit/rows_tw_pit50.csv)時預設不執行,避免精簡版蓋掉完整版(--force 才覆寫)。
- 下載和本機計算最好同一個月內做完:台股本益比歷史的快取 key 對齊到月初,跨月會找不到、少掉本益比。
研究用途,非投資建議。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

from . import config, runtime
from .universe import TW_LARGE, US_LARGE

FINMIND_JOBS = [("TaiwanStockFinancialStatements", "2013-01-01", "statements_slow"),
                ("TaiwanStockBalanceSheet", "2013-01-01", "statements_slow"),
                ("TaiwanStockCashFlowsStatement", "2013-01-01", "statements_slow"),
                ("TaiwanStockMonthRevenue", "2013-01-01", "statements"),
                ("TaiwanStockDividend", "2010-01-01", "statements_slow")]
MACRO = ["^TWII", "^VIX", "^TNX", "TWD=X", "^GSPC", "^SP500TR"]   # backtest_pit.MACRO_SYMS + 美股含息大盤
TR_START = "2014-01-01"
UNIVERSES = "tw_large40,us_large40"
SEC_REFUSED = "SEC 拒絕連線"
# 結束碼(lab/setup_job.py EXIT_MESSAGES 對應介面訊息):資料不齊不能算完成
EXIT_FAILED = 1
EXIT_INCOMPLETE = 5
EXIT_SEC_REFUSED = 6
EXIT_PARTIAL = 7   # 檔案都在,但有些公司沒抓到:可以用,但結果會少這些公司


def say(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def per_code_start() -> str:
    from .sources import finmind
    return finmind._ago(365 * 10 + 40)   # 本益比歷史(和 lab/pit/build_tw.py 一樣的起始日)


def todo(tw: list[str], us: list[str]) -> dict:
    """還沒有快取的項目(只看快取在不在,不連網)。"""
    import pandas as pd

    from .cache import age
    jobs = FINMIND_JOBS + [("TaiwanStockPER", per_code_start(), "statements")]
    months = pd.period_range(pd.Timestamp(TR_START), pd.Timestamp(config.today()), freq="M")
    return {
        "yahoo": [s for s in tw + us + MACRO if age("px_since", f"{s}|2013-01-01") is None],
        "twse_tr": [p for p in months if age("twse_tr", f"{p.year}{p.month:02d}") is None],
        "finmind": [(c.split(".")[0], ds, st, ttl) for c in tw for ds, st, ttl in jobs
                    if age("finmind", f"{ds}|{c.split('.')[0]}|{st}") is None],
        "sec": us,   # CIK 要先查對照表才知道快取 key;已有快取的在下載時會直接略過
    }


def n_sec(t: dict) -> int:
    return len(t["sec"]) + 1 if t["sec"] else 0   # 每檔 companyfacts + 代號對照表 1 次


def estimate_minutes(t: dict) -> dict:
    """各來源預估分鐘數:每次間隔 + 約略回應時間;FinMind 超過一小時額度的部分才加上等額度恢復(和 download() 的等待邏輯一致)。"""
    fm_per_hour = 560 if config.FINMIND_TOKEN else 280
    n_fm = len(t["finmind"])
    fm_wait = 60 * ((n_fm - (fm_per_hour - 5)) // fm_per_hour + 1) if n_fm > fm_per_hour - 5 else 0
    return {"yahoo": len(t["yahoo"]) * 2.0 / 60, "twse": len(t["twse_tr"]) * 3.2 / 60,
            "finmind": n_fm * 2.0 / 60 + fm_wait, "sec": n_sec(t) * 1.0 / 60}


def show_plan(t: dict) -> None:
    m = estimate_minutes(t)
    say(f"要下載:Yahoo {len(t['yahoo'])} 次(約 {m['yahoo']:.0f} 分鐘)、證交所 {len(t['twse_tr'])} 次(約 {m['twse']:.0f} 分鐘)、"
        f"FinMind {len(t['finmind'])} 次(約 {m['finmind']:.0f} 分鐘{'' if config.FINMIND_TOKEN else ',沒有 token'})、"
        f"SEC 最多 {n_sec(t)} 次(約 {m['sec']:.0f} 分鐘);合計約 {sum(m.values()):.0f} 分鐘,之後在本機計算約 10–20 分鐘")


def sec_refusal(e: Exception) -> str | None:
    """SEC 明確拒絕(不是網路問題)時回傳給使用者看的原因;拒絕後逐檔重試也不會成功,還會加重對方負擔。"""
    code = getattr(getattr(e, "response", None), "status_code", None)
    if code in (401, 403, 429):
        return f"{SEC_REFUSED}(HTTP {code}),請到設定確認名字與聯絡信箱是真實的,或稍後再試"
    return None


def sec_access_error() -> str | None:
    """先試 SEC 對照表:被拒絕或連不上就回傳原因,不要逐檔重試,也不要誤報成「對照表找不到」。"""
    from .sources import sec
    try:
        sec.tickers()
    except Exception as e:  # noqa: BLE001  任何失敗都要回報原因
        return sec_refusal(e) or f"SEC 連不上({type(e).__name__})"
    return None


def incomplete_markets(tw: list[str], us: list[str], data_dir: Path) -> list[str]:
    """這次要建的市場裡,回測資料還不齊的(和介面的「第一次使用」狀態同一個判斷)。"""
    from .setup_status import research_ready
    wanted = [m for m, symbols in (("TW", tw), ("US", us)) if symbols]
    return [m for m in wanted if not research_ready(m, data_dir / "bt_pit")["ready"]]


def download(t: dict) -> list[str]:
    from .sources import finmind as F
    from .sources import sec, twse_hist, yahoo
    bad = []
    for i, s in enumerate(t["yahoo"], 1):
        if yahoo.history_since(s) is None:
            bad.append(f"Yahoo {s}")
            if i == len(bad) == 3:   # 前 3 檔全部失敗:多半是沒有網路,不要再花幾小時重試
                raise ConnectionError("Yahoo 前 3 檔都抓不到,請確認網路連線後重跑(已抓到的會保留)")
        say(f"Yahoo {i}/{len(t['yahoo'])} {s}")
        time.sleep(1.0)
    for i, p in enumerate(t["twse_tr"], 1):
        try:
            twse_hist.total_return_month(p.year, p.month)
        except Exception as e:  # noqa: BLE001  單月失敗不中斷;重跑時再補
            bad.append(f"證交所 {p}:{type(e).__name__}")
        if i % 12 == 0 or i == len(t["twse_tr"]):
            say(f"證交所報酬指數 {i}/{len(t['twse_tr'])}")
    for i, (code, ds, start, ttl) in enumerate(t["finmind"], 1):
        while True:   # 額度快用完就等(不繞過限制)
            st = F.budget_status()
            if st["cooldown_remaining"] == 0 and st["used_last_hour"] < st["budget"] - 5:
                break
            say(f"FinMind 這個小時的免費額度快用完了,等 1 分鐘(進度 {i - 1}/{len(t['finmind'])})")
            time.sleep(60)
        if F.get(ds, code, start, ttl) is None:
            bad.append(f"FinMind {code} {ds}")
        if i % 10 == 0 or i == len(t["finmind"]):
            say(f"FinMind {i}/{len(t['finmind'])}")
        time.sleep(1.0)
    sec_error = sec_access_error() if t["sec"] else None
    if sec_error:
        bad.append(sec_error)
        return bad
    for i, s in enumerate(t["sec"], 1):
        cik = sec.cik_for(s, overrides=True)
        if not cik:
            bad.append(f"SEC {s}:對照表找不到")
            continue
        try:
            sec.companyfacts(cik)
        except Exception as e:  # noqa: BLE001
            refused = sec_refusal(e)
            if refused:   # 對照表可能來自快取,真正被拒絕是在這裡才知道
                bad.append(refused)
                break
            bad.append(f"SEC {s}:{type(e).__name__}")
        if i % 10 == 0 or i == len(t["sec"]):
            say(f"SEC {i}/{len(t['sec'])}")
        time.sleep(0.3)
    return bad


def compute(symbols: list[str] | None) -> int:
    """在本機算 bt_pit(離線、子程序:離線模式要在 import lab 之前設定)。"""
    env = dict(os.environ, INVEST_LAB_OFFLINE="1", INVEST_LAB_DATA_DIR=str(config.DATA_DIR),
               INVEST_LAB_OUT_DIR=str(config.DATA_DIR))
    base = runtime.module_argv("lab.research.backtest_pit")   # 打包版沒有 python -m,改走 App 執行檔
    extra = ["--symbols", ",".join(symbols)] if symbols else ["--symbols", ",".join(s for s, _ in TW_LARGE + US_LARGE)]
    for step in (["build"], ["judge", "--universes", UNIVERSES]):
        say(f"本機計算:{step[0]}(不用網路)")
        r = subprocess.run(base + step + extra, cwd=str(config.ROOT), env=env)
        if r.returncode != 0:
            return r.returncode
    return 0


def has_full_research(data_dir: Path) -> bool:
    """已經有完整版回測資料(tw_pit50,開發者用的完整股票池):精簡版會把它蓋掉,所以不跑。"""
    return (data_dir / "bt_pit" / "rows_tw_pit50.csv").exists()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="新使用者一鍵建立回測資料(精簡版)")
    ap.add_argument("--plan", action="store_true", help="只列出要做的事與估計,不連網")
    ap.add_argument("--tw", default="", help="只用這些台股代號(逗號分隔,測試用)")
    ap.add_argument("--us", default="", help="只用這些美股代號(逗號分隔,測試用)")
    ap.add_argument("--force", action="store_true", help="已經有完整版回測資料(tw_pit50)時仍然用精簡版覆寫")
    a = ap.parse_args(argv)
    tw_all = {s.split(".")[0]: s for s, _ in TW_LARGE}
    subset = bool(a.tw or a.us)
    # 測試用:只給其中一個市場時,另一個市場不抓(上櫃股請帶 .TWO,例如 5274.TWO)
    tw = [tw_all.get(c.strip(), c.strip() if "." in c else f"{c.strip()}.TW") for c in a.tw.split(",") if c.strip()]
    us = [x.strip().upper() for x in a.us.split(",") if x.strip()]
    if not subset:
        tw, us = [s for s, _ in TW_LARGE], [s for s, _ in US_LARGE]
    say("== H0 Lab 建立回測資料(研究用途,非投資建議;只抓免費公開資料,存在這台電腦的 data/)==")
    t = todo(tw, us)
    show_plan(t)
    if a.plan:
        return 0
    if has_full_research(config.DATA_DIR) and not a.force:
        say("這台電腦已經有完整版回測資料(bt_pit/rows_tw_pit50.csv),精簡版會把它蓋掉,所以不執行。"
            "確定要覆寫才加 --force。")
        return 3
    if config.OFFLINE:
        say("現在是離線模式(INVEST_LAB_OFFLINE=1),不能下載;拿掉這個設定再跑")
        return 2
    try:
        bad = download(t)
    except ConnectionError as e:
        say(str(e))
        return 4
    if bad:
        say(f"有 {len(bad)} 項沒抓到(稍後重跑這支腳本會再補):" + "、".join(bad[:15]))
    refused = any(x.startswith(SEC_REFUSED) for x in bad)
    rc = compute(tw + us if subset else None)
    if rc != 0:
        say(f"本機計算失敗(結束碼 {rc});把上面的訊息貼給開發者,或重跑一次")
        # 計算的結束碼不直接傳回去:它和這裡的結束碼是兩回事,傳回去介面會顯示錯的訊息
        return EXIT_SEC_REFUSED if refused and us else EXIT_FAILED
    missing = incomplete_markets(tw, us, config.DATA_DIR)
    if missing:
        say(f"沒有完成:{'、'.join(missing)} 的回測資料不齊。修好上面列的問題後再跑一次,已下載的資料會保留。")
        return EXIT_SEC_REFUSED if refused and "US" in missing else EXIT_INCOMPLETE
    if bad:
        say(f"完成,但有 {len(bad)} 項沒抓到,結果會少這些資料;再跑一次可以補。")
        return EXIT_SEC_REFUSED if refused else EXIT_PARTIAL
    say("完成。回到網站重新整理,「選風格建模型」第 3 步就能看到排名與過去表現。")
    return 0


if __name__ == "__main__":   # lab/setup_job.py 在原始碼模式用 python -m 執行
    raise SystemExit(main())
