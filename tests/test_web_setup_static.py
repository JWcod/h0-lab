"""第一次執行引導的前端靜態檢查(web/js/setup.js;規格 docs/安裝與上手規格.md §3)。"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lab import setup_status

WEB = Path(__file__).resolve().parent.parent / "web"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_every_setup_key_is_in_dictionary():
    js = _read(WEB / "js" / "setup.js")
    dic = _read(WEB / "js" / "i18n" / "zh-TW.js")
    keys = {k for k in re.findall(r't\("(setup\.[\w.]+)"', js) if not k.endswith(".")}
    for sid in (s["id"] for s in setup_status.status()["steps"] if s["id"] != "sec_user_agent"):   # 這步的文字在 update_text.js
        keys.add(f"setup.step.{sid}.title")
    missing = [k for k in sorted(keys) if f'"{k}":' not in dic]
    assert not missing, missing
    text = _read(WEB / "js" / "update_text.js")
    for key in ("first.sec.title", "first.sec.body"):
        assert text.count(f'"{key}"') == 2, key   # 中文、英文各一份


def test_wired_into_home_and_wizard():
    app = _read(WEB / "js" / "app.js")
    assert 'import * as Setup from "./setup.js"' in app and 'id="setupCard"' in app and "Setup.homeCardHTML" in app
    wz = _read(WEB / "js" / "views" / "wizard.js")
    assert "Setup.researchMissingHTML" in wz and "Setup.isResearchMissing" in wz


def test_setup_css_has_no_gray_or_faded_text():
    css = _read(WEB / "css" / "wizard.css")
    block = re.sub(r"/\*.*?\*/", "", css[css.index("第一次執行引導"):], flags=re.DOTALL)
    assert ".su-card" in block
    assert not re.search(r"opacity:\s*(0?\.\d|[1-9]\d?%)", block)
    assert not re.search(r"\b(gray|grey|silver)\b", block, re.IGNORECASE)
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", block) and "rgb" not in block
    for v in re.findall(r"(?<![-\w])color:\s*([^;]+);", block):
        assert v.strip() in {"var(--text)", "var(--text2)", "var(--green)", "var(--orange)"}, v


def test_setup_js_escapes_and_has_no_inline_handlers():
    js = _read(WEB / "js" / "setup.js")
    assert "onclick=" not in js and "eval(" not in js
    # 指令與訊息都經過 esc
    assert "${esc(c)}" in js and 't("setup.wizard.raw", { msg: rawMsg })' in js


def test_research_commands_runnable():
    """Verifier 2026-11-02:scripts/ 的指令要能從專案根目錄直接跑(帶 PYTHONPATH=. 或腳本自己設 sys.path)。"""
    root = WEB.parent
    js = _read(WEB / "js" / "setup.js") + json.dumps(setup_status.RESEARCH)
    cmds = re.findall(r"(?:PYTHONPATH=\. )?\.venv/bin/python (scripts/[\w/]+\.py)", js)
    assert "scripts/setup_research.py" in cmds
    for c in cmds:
        assert "sys.path.insert" in _read(root / c) or f"PYTHONPATH=. .venv/bin/python {c}" in js, c


NODE = shutil.which("node")


def _backend_messages() -> list[str]:
    """lab/research/model_eval.py 會丟給前端的錯誤訊息(f-string 的 {…} 換成檔名,只看文字)。"""
    src = _read(WEB.parent / "lab" / "research" / "model_eval.py")
    msgs = []
    for m in re.finditer(r"ModelEvalError\(", src):   # 跨行訊息:收集到右括號為止的所有字串片段
        i, depth = m.end(), 1
        while depth:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        parts = re.findall(r'f?"([^"]*)"', src[m.end():i - 1])
        if parts:
            msgs.append(re.sub(r"\{[^}]*\}", "raw_tw.sqlite", "".join(parts)))
    return msgs


@pytest.mark.skipif(NODE is None, reason="沒有 node")
def test_shipped_model_409_is_not_research_missing(tmp_path):
    """精靈選「範例模型(綜合)」看過去表現:後端回 409「出廠模型…backtest_pit…」,不得誤判成缺回測資料(2026-11-03)。"""
    msgs = _backend_messages()
    shipped = [m for m in msgs if "出廠模型" in m]
    missing = [m for m in msgs if "backtest_pit" in m and "出廠模型" not in m]
    assert len(shipped) == 1 and "backtest_pit" in shipped[0] and missing
    script = tmp_path / "run.mjs"
    script.write_text("""
const S = await import(process.argv[2]);
const [shipped, missing] = JSON.parse(process.argv[3]);
console.log(JSON.stringify({
  shipped: shipped.map((m) => [S.isShippedModel(m), S.isResearchMissing(m)]),
  missing: missing.map((m) => [S.isShippedModel(m), S.isResearchMissing(m)]),
  other: [S.isShippedModel("HTTP 500"), S.isResearchMissing("HTTP 500"), S.isShippedModel(null), S.isResearchMissing(undefined)],
  html: S.shippedModelHTML(),
}));
""", encoding="utf-8")
    r = subprocess.run([NODE, str(script), (WEB / "js" / "setup.js").as_uri(),
                        json.dumps([shipped, missing], ensure_ascii=False)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o["shipped"] == [[True, False]]
    assert all(x == [False, True] for x in o["missing"]), o["missing"]
    assert o["other"] == [False, False, False, False]
    h = o["html"]
    assert 'href="#/backtest"' in h and "研究紀錄" in h and "setup_research" not in h and "<code>" not in h
    assert "backtest_pit" not in h and "holdout" not in h          # 白話:不露內部指令與英文術語


def test_wizard_and_workbench_show_shipped_card_before_research_card():
    for name, var in (("wizard.js", "W.btErr"), ("models.js", "ed.btError"), ("models.js", "ed.hoError")):
        src = _read(WEB / "js" / "views" / name)
        a = src.index(f"Setup.isShippedModel({var})")
        b = src.index(f"Setup.isResearchMissing({var})")
        assert a < b and "Setup.shippedModelHTML" in src, name


def test_factor_page_handles_races_and_missing_data():
    js = (Path(__file__).resolve().parent.parent / "web" / "js" / "views" / "lab.js").read_text(encoding="utf-8")
    assert "seq !== renderSeq" in js                  # 舊回應不能蓋掉新選的股票池
    assert 'state.labKey : "tw_large40"' in js        # 新使用者的快速設定只有 40 檔股票池
    assert "Setup.researchMissingHTML" in js          # 缺資料給引導卡,不顯示開發指令


def test_quick_data_users_are_not_told_to_rebuild_forever():
    root = Path(__file__).resolve().parent.parent / "web" / "js"
    setup = (root / "setup.js").read_text(encoding="utf-8")
    models = (root / "views" / "models.js").read_text(encoding="utf-8")
    assert "needsFullDataHTML()" in setup                    # 資料齊了但這個股票池沒有:不叫使用者重建
    assert "pickAvailableUniverse(el, ed)" in models         # 工作台預設選有資料的股票池
    assert "universeTouched" in models and "universePicked" in models   # 使用者選過的不蓋掉
    bind = models[models.index("function bindBacktest"):models.index("function updateBacktest")]
    assert "Setup.bindCopy(box)" in bind                     # 重畫後複製按鈕要繼續能用
    pick = models[models.index("async function pickAvailableUniverse"):models.index("async function researchCardReady")]
    assert pick.index("if (!have.length) return") < pick.index("my.universePicked = true")   # 資料建好後回來還要能選
    assert pick.index("const stale") < pick.index("my.universePicked = true")                # 資料建好後舊卡片要清掉,才輪得到改選
    assert 'Setup.needsFullData(null, market, ed.universe) ? ""' in models                   # 要完整資料的說明不重複
    assert "ed.universe);" in models
    for v in ("wizard.js", "models.js"):                     # 看回測的兩個地方都要說存活者偏誤
        assert "survivorshipNoteHTML" in (root / "views" / v).read_text(encoding="utf-8")

