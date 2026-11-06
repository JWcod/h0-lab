"""全域設定:路徑、環境變數、模型參數載入。

安全原則:
- 所有讀寫都限制在專案資料夾內(ROOT 底下);金鑰只從 .env 讀,不寫死。

路徑(v2 起可用環境變數指定,必須在 import lab 之前設好;規約見 docs/架構.md):
- INVEST_LAB_DATA_DIR    讀資料的地方(快取、凍結版本)。預設 data/;開發工作階段指向唯讀的凍結版本
- INVEST_LAB_OUT_DIR     可寫的輸出(回測結果、掃描結果、非正式快照)。預設 = DATA_DIR;離線模式預設 .agent_out/
- INVEST_LAB_PRIVATE_DIR 個人資料(觀察清單、筆記、模擬帳戶)。預設 private/;開發工作階段一律指向假資料
- INVEST_LAB_OFFLINE=1   離線模式:封鎖所有對外連線,快取永不過期、沒有就算 cache miss(lab/offline.py)
- INVEST_LAB_AS_OF       「今天」(YYYY-MM-DD)。離線模式下預設讀 DATA_DIR/freeze_date.txt
- INVEST_LAB_RECORD=0    不寫前瞻紀錄(快照、模擬帳戶)。離線模式與 git worktree 一律不寫
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """極簡 .env 讀取(避免多裝套件)。只接受 KEY=VALUE,不執行任何東西。"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name)
    return Path(v).expanduser().resolve() if v else default


OFFLINE = os.environ.get("INVEST_LAB_OFFLINE") == "1"
IS_WORKTREE = (ROOT / ".git").is_file()  # git worktree 的 .git 是檔案,主 checkout 是資料夾


def _main_root() -> Path:
    """主 checkout 的位置(worktree 的 .git 檔內容是「gitdir: <主資料夾>/.git/worktrees/<名稱>」)。"""
    if not IS_WORKTREE:
        return ROOT
    try:
        gitdir = Path((ROOT / ".git").read_text(encoding="utf-8").split("gitdir:", 1)[1].strip())
        return gitdir.parents[2]
    except (OSError, IndexError):
        return ROOT


MAIN_ROOT = _main_root()
DATA_DIR = _env_path("INVEST_LAB_DATA_DIR", ROOT / "data")
CACHE_DIR = DATA_DIR / "cache"
OUT_DIR = _env_path("INVEST_LAB_OUT_DIR", ROOT / ".agent_out" if OFFLINE else DATA_DIR)
PRIVATE_DIR = _env_path("INVEST_LAB_PRIVATE_DIR", ROOT / "private")
# 前瞻紀錄(快照、模擬帳戶)是唯一真正乾淨的樣本外資料:只有主 checkout、連網、沒有關掉時才寫
RECORD_FORWARD = not OFFLINE and not IS_WORKTREE and os.environ.get("INVEST_LAB_RECORD", "1") == "1"
SNAPSHOT_DIR = (DATA_DIR if RECORD_FORWARD else OUT_DIR) / "snapshots"
TDCC_DIR = DATA_DIR / "tdcc"
RESEARCH_DIR = OUT_DIR / "research"
WEB_DIR = ROOT / "web"
# 模型參數;INVEST_LAB_PARAMS 可指到舊版參數(例:凍結資料夾的 model_params.json)以逐位元重現舊模型
PARAMS_PATH = _env_path("INVEST_LAB_PARAMS", ROOT / "lab" / "model_params.json")

if not OFFLINE:
    for _d in (DATA_DIR, CACHE_DIR, TDCC_DIR):
        _d.mkdir(parents=True, exist_ok=True)
for _d in (OUT_DIR, RESEARCH_DIR, SNAPSHOT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def today() -> dt.date:
    """模型眼中的「今天」。離線重跑時固定在凍結日,快取 key 與結果才不會隨真實日期變動。"""
    v = os.environ.get("INVEST_LAB_AS_OF")
    if not v and OFFLINE:
        f = DATA_DIR / "freeze_date.txt"
        if f.exists():
            v = f.read_text(encoding="utf-8").strip()
    return dt.date.fromisoformat(v) if v else dt.date.today()


# SEC 要求宣告 User-Agent(名稱 + 信箱)。預設用不含個資的佔位信箱(SEC 會擋 localhost 這類格式),
# 使用者可以在 .env 填自己的聯絡方式。
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT") or "invest-lab personal research tool contact@example.com"
SEC_UA_IS_DEFAULT = not os.environ.get("SEC_USER_AGENT")
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")  # 免費額度不需要 token;有 token 上限較高
# FinMind 額度計數檔:跨程序共用,固定在主資料夾(不跟著 DATA_DIR 走,避免指到副本就脫離共用額度)
FINMIND_BUDGET_FILE = _env_path("INVEST_LAB_BUDGET_FILE", MAIN_ROOT / "data" / "finmind_calls.json")
# 實驗登記簿:所有 agent、所有 worktree 共用同一個檔(「試了幾次」才不會被低估)
REGISTRY_FILE = _env_path("INVEST_LAB_REGISTRY", MAIN_ROOT / "data" / "research" / "experiments.jsonl")
HOST = os.environ.get("INVEST_LAB_HOST", "127.0.0.1")  # 只綁本機,不對外開放
# Red-team L4(專案規則:服務只綁本機):環境變數不能把伺服器改綁到本機以外(例如 0.0.0.0 會對區網開放)
if HOST not in ("127.0.0.1", "localhost", "::1"):
    raise SystemExit(f"INVEST_LAB_HOST={HOST!r} 不允許:伺服器只能綁本機(127.0.0.1)")
PORT = int(os.environ.get("INVEST_LAB_PORT", "8765"))

# 快取有效時間(秒)
TTL = {
    "price": 60 * 30,          # 股價 30 分鐘
    "info": 60 * 60 * 6,       # 公司基本資料 / 分析師 6 小時
    "statements": 60 * 60 * 24,  # 財報 1 天
    "statements_slow": 60 * 60 * 24 * 5,  # 台股季財報、股利(一季才變一次)5 天,省 FinMind 額度
    "chips": 60 * 60 * 4,      # 籌碼 4 小時
    "news": 60 * 30,           # 新聞 30 分鐘
    "universe": 60 * 60 * 24 * 3,  # 股票清單 3 天
    "peers": 60 * 60 * 24,
    "macro": 60 * 60 * 2,
}


_GIT_SHA: str | None = None


def git_sha() -> str:
    """目前程式版本(寫進快照、實驗登記簿,讓每筆紀錄都能追溯到哪一版程式)。"""
    global _GIT_SHA
    if _GIT_SHA is None:
        import subprocess
        try:
            _GIT_SHA = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=10", "HEAD"], capture_output=True,
                                      text=True, timeout=5).stdout.strip() or "unknown"
        except Exception:  # noqa: BLE001
            _GIT_SHA = "unknown"
    return _GIT_SHA


def load_params() -> dict:
    """模型參數。新式參數檔用 default_model 指向出廠模型,這裡把模型的面向權重與短期門檻填回原本的位置
    (鍵的順序和 M1 前相同);舊式參數檔(內含 pillar_weights,例如凍結資料夾、tests/baseline)原樣回傳。"""
    with open(PARAMS_PATH, encoding="utf-8") as f:
        p = json.load(f)
    if "pillar_weights" in p or "default_model" not in p:
        return p
    from .analysis.scoring import DEFAULT_MODEL
    from .models.schema import load_shipped
    if p["default_model"] != DEFAULT_MODEL:   # 權重來自參數檔指定的模型、指標來自 scoring.DEFAULT_MODEL,兩者必須一致
        raise ValueError(f"model_params.json 的 default_model={p['default_model']!r} 和 scoring.DEFAULT_MODEL 不一致")
    m = load_shipped(p["default_model"])
    out = {}
    for k, v in p.items():
        if k == "default_model":
            out["pillar_weights"] = m["pillar_weights"]
            out["short_term_thresholds"] = m["short_term_thresholds"]
        elif k != "default_model_note":
            out[k] = v
    out["default_model"] = p["default_model"]
    return out


if OFFLINE:
    from . import offline as _offline

    _offline.install()
