"""預先登記清單(docs/預先登記.md 的 ```prereg 區塊)的讀取與檢查(Red-team M2)。

區塊每一行:`編號 | 市場(逗號分隔) | change_type | 主要指標 | 狀態`,狀態只能是 草稿 / 核准 / 刪除 / 探索性。
閘門(`evaluate.paired_gate`)只接受狀態為「核准」的假設族;`explore`、`baseline` 不在清單裡,只能當 fix 或診斷用。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .. import config

PATH: Path | None = None   # None = 讀主 checkout 的 main 分支已 commit 版本;測試用 monkeypatch 換成暫存檔(不提供環境變數覆寫)
REL = "docs/預先登記.md"
REF = "main"
STATUSES = ("草稿", "核准", "刪除", "探索性")
UNREGISTERED = ("explore", "baseline")     # 不需預先登記的族:不能用來通過 add
MAX_GATE_CONFIGS = 3                        # 每個假設族最多送正式閘門幾個不同設定(升級計畫 §5.3)
CHIEF_AGENT = "claude"                      # 維護者的 INVEST_LAB_AGENT;只有維護者能用 trials_since
HOLDOUT_ONLY = ("P3-H01",)                  # 看過開發集才登記的假設:開發集閘門不算證據,一律拒絕(預先登記 §7)

_BLOCK = re.compile(r"```prereg\s*\n(.*?)```", re.S)


def _text(path: Path | None) -> str:
    """指定路徑(或測試換掉的 PATH)就讀檔;否則讀主 checkout 裡 main 分支**已 commit** 的版本
    (Verifier21 問題 4:worktree 或工作目錄裡沒 commit 的「核准」不算數)。讀不到就報錯,不當成空清單。"""
    p = path or PATH
    if p is not None:
        return Path(p).read_text(encoding="utf-8") if Path(p).exists() else ""
    if not (config.MAIN_ROOT / ".git").exists():   # 打包的 App、下載 ZIP:沒有 git 歷史,正式閘門本來就不能用
        raise ValueError(f"讀不到 {REF}:{REL}:這份安裝沒有 git 歷史,正式閘門只能在 git clone 裡跑")
    try:
        r = subprocess.run(["git", "-C", str(config.MAIN_ROOT), "show", f"{REF}:{REL}"], capture_output=True)
    except OSError as e:
        raise ValueError(f"讀不到 {REF}:{REL}:找不到 git({e})") from e
    if r.returncode != 0:
        raise ValueError(f"讀不到 {REF}:{REL}(git show 失敗):{r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8")


def load(path: Path | None = None) -> dict[str, dict]:
    """回傳 {編號: {markets, change_type, primary, status}};區塊不存在回傳空 dict。格式錯誤直接報錯。"""
    m = _BLOCK.search(_text(path))
    if not m:
        return {}
    out: dict[str, dict] = {}
    for ln in m.group(1).splitlines():
        if not ln.strip():
            continue
        parts = [x.strip() for x in ln.split("|")]
        if len(parts) != 5:
            raise ValueError(f"預先登記區塊格式錯誤(要 5 欄):{ln!r}")
        fid, mk, ct, prim, st = parts
        if ct not in ("add", "fix") or st not in STATUSES or fid in out:
            raise ValueError(f"預先登記區塊內容錯誤(change_type / 狀態 / 重複編號):{ln!r}")
        out[fid] = {"markets": {x.strip() for x in mk.split(",") if x.strip()}, "change_type": ct, "primary": prim, "status": st}
    return out


def check(family: str, change_type: str, primary: str, markets, path: Path | None = None) -> dict | None:
    """閘門送出前檢查假設族。未登記的 explore / baseline 回傳 None(交給閘門本身處理:add 不會通過);
    其他族必須在清單裡、狀態「核准」、change_type 與主要指標相符、市場在登記範圍內,否則 ValueError。"""
    if family in UNREGISTERED:
        return None
    reg = load(path)
    if family not in reg:
        raise ValueError(f"假設族「{family}」不在 docs/預先登記.md 的清單裡:閘門只接受預先登記的假設(其他用 explore)")
    h = reg[family]
    if h["status"] != "核准":
        raise ValueError(f"假設族「{family}」的狀態是「{h['status']}」,不是「核准」,不能送閘門")
    if h["change_type"] != change_type:
        raise ValueError(f"假設族「{family}」登記的類型是 {h['change_type']},這次送的是 {change_type}")
    if h["primary"] != primary:
        raise ValueError(f"假設族「{family}」登記的主要指標是 {h['primary']},這次用的是 {primary}")
    extra = set(markets) - h["markets"]
    if extra:
        raise ValueError(f"假設族「{family}」只登記了 {sorted(h['markets'])},這次多了 {sorted(extra)}")
    return h
