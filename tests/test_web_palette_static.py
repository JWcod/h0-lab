"""指令面板、快捷鍵、提示訊息的前端靜態檢查(web/js/palette.js、shortcuts.js、toast.js、css/palette.css;
商業化規格 §7)。不啟動伺服器、不連網;純函式(模糊比對排序、最近使用、輸入框判斷)用 node 實際執行,沒有 node 就跳過。"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
JS = {n: WEB / "js" / f"{n}.js" for n in ("palette", "shortcuts", "toast")}
CSS = WEB / "css" / "palette.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(src: str) -> str:
    """去掉註解(// 行註解與 /* */),只檢查程式本身。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", src)


@pytest.mark.parametrize("name", sorted(JS))
def test_js_never_inserts_html(name):
    js = _code(_read(JS[name]))
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "onclick="):
        assert bad not in js, f"{name}.js 用了 {bad}"
    assert "textContent" in js and "createElement" in js


@pytest.mark.parametrize("name", sorted(JS))
def test_js_no_import_time_dom_access(name):
    """模組載入時不能碰 DOM(node 測試要能 import;總管也可能在 DOMContentLoaded 前 import)。"""
    js = _code(_read(JS[name]))
    top = [ln for ln in js.splitlines() if ln and not ln.startswith((" ", "}", ")", "]"))]
    for ln in top:
        assert not re.match(r"(document|window|localStorage)\.", ln), ln


def test_palette_aria_and_focus():
    js = _read(JS["palette"])
    for a in ('"role", "dialog"', '"aria-modal", "true"', '"role", "combobox"', '"role", "listbox"', '"role", "option"',
              '"aria-activedescendant"', '"aria-selected"', '"aria-live", "polite"', '"aria-controls"'):
        assert a in js, a
    assert "returnFocus" in js and "back.focus()" in js           # 關閉後焦點回原處
    assert 'e.key === "Tab"' in js                               # 焦點鎖在面板
    assert 'e.key === "Escape"' in js and "metaKey" in js and "ctrlKey" in js
    assert "export function open" in js and "export function close" in js and "export function mount" in js
    # localStorage 讀寫都包 try/catch
    for m in re.finditer(r"localStorage\.(getItem|setItem)", js):
        before = js[max(0, m.start() - 80):m.start()]
        assert "try" in before, m.group(0)


def test_shortcuts_aria_and_routes():
    js = _read(JS["shortcuts"])
    assert '"role", "dialog"' in js and '"aria-modal", "true"' in js and '"aria-labelledby"' in js and '"aria-label"' in js
    for key, h in (("h", "#/"), ("s", "#/scan"), ("w", "#/wizard"), ("m", "#/models"), ("b", "#/backtest")):
        assert f'key: "{key}", hash: "{h}"' in js
    assert "isTyping(e.target)" in js and "isContentEditable" in js
    assert "helpReturn" in js and "back.focus()" in js


def test_toast_aria():
    js = _read(JS["toast"])
    assert '"role", "status"' in js and '"aria-live", "polite"' in js and '"role", "alert"' in js
    assert "mouseenter" in js and "mouseleave" in js and "focusin" in js     # 滑過 / 聚焦時暫停
    assert '"aria-label", ZH`關閉提示`' in js and 'b.type = "button"' in js


def test_css_tokens_only_and_no_faded_text():
    css = re.sub(r"/\*.*?\*/", "", _read(CSS), flags=re.DOTALL)
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css), "不得寫死色碼"
    assert not re.search(r"\b(rgba?|hsla?|hwb|lab|lch|oklch|color-mix)\(", css)
    assert not re.search(r"(?<![-\w])(gray|grey|silver|darkgray|dimgray|lightgray|black|white)(?![-\w])", css, re.IGNORECASE)
    assert not re.search(r"opacity:\s*(0?\.\d|[1-9]\d?%)", css), "不得用半透明淡化"
    allowed = {"var(--text)", "var(--text2)", "var(--blue)", "var(--link)", "var(--green)", "var(--orange)", "var(--red)", "var(--on-accent)"}
    for v in re.findall(r"(?<![-\w])color:\s*([^;}]+)", css):
        assert v.strip() in allowed, v
    assert "var(--overlay-glass)" in css and "backdrop-filter" in css   # 浮層用幾乎不透明的毛玻璃,文字才讀得清楚
    assert "prefers-reduced-motion" in css
    tokens = _read(WEB / "css" / "tokens.css")
    for var in set(re.findall(r"var\((--[\w-]+)\)", css)):
        assert f"{var}:" in tokens, f"{var} 不在 tokens.css"


NODE = shutil.which("node")
_SCRIPT = r"""
const P = await import(process.argv[2]);
const S = await import(process.argv[3]);
const out = {};
const cmds = [
  { key: "cmd:scan", label: "選股掃描", hint: "#/scan" },
  { key: "cmd:models", label: "模型工作台", hint: "#/models", keywords: ["models"] },
  { key: "cmd:backtest", label: "模型驗證", hint: "#/backtest", keywords: ["backtest"] },
  { key: "cmd:theme", label: "切換深色 / 淺色", keywords: ["theme dark light"] },
  { key: "cmd:wizard", label: "建立模型", keywords: ["wizard"] },
];
const labels = (xs) => xs.map((x) => x.label);
out.none = P.fuzzyScore("zz", "選股掃描");
out.empty = P.fuzzyScore("", "abc");
out.exactBeatsPrefix = P.fuzzyScore("tsm", "tsm") > P.fuzzyScore("tsm", "tsmc");
out.prefixBeatsInner = P.fuzzyScore("ts", "tsmc") > P.fuzzyScore("ts", "ats");
out.contigBeatsGap = P.fuzzyScore("abc", "abcxx yy") > P.fuzzyScore("abc", "axbxc");
out.subseq = P.fuzzyScore("bt", "backtest") > 0;
out.caseInsensitive = P.fuzzyScore("TSM", "tsm") === 1000;
out.q模型 = labels(P.rankItems("模型", cmds));
out.qBack = labels(P.rankItems("back", cmds));
out.qTheme = labels(P.rankItems("dark", cmds));
out.recentEmpty = labels(P.rankItems("", cmds, ["cmd:wizard", "cmd:theme"]));
out.recentBoost = labels(P.rankItems("模型", cmds, ["cmd:wizard"]));
let r = [];
for (const k of ["a", "b", "c", "a"]) r = P.pushRecent(r, { k }, 3);
out.recent = r.map((e) => e.k);
const fake = (tag, attrs = {}, extra = {}) => ({ nodeType: 1, tagName: tag, isContentEditable: false, getAttribute: (n) => attrs[n] ?? null, ...extra });
out.typing = [fake("INPUT"), fake("INPUT", { type: "checkbox" }), fake("INPUT", { type: "search" }), fake("TEXTAREA"), fake("SELECT"),
  fake("DIV", {}, { isContentEditable: true }), fake("BUTTON"), null].map((t) => S.isTyping(t));
out.help = S.helpSections(true).flatMap((s) => s.rows.map((x) => x.keys.join("+")));
out.helpWin = S.helpSections(false)[0].rows[0].keys;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="沒有 node")
def test_pure_functions_with_node(tmp_path):
    script = tmp_path / "run.mjs"
    script.write_text(_SCRIPT, encoding="utf-8")
    r = subprocess.run([NODE, str(script), JS["palette"].as_uri(), JS["shortcuts"].as_uri()],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o["none"] == -1 and o["empty"] == 0
    assert o["exactBeatsPrefix"] and o["prefixBeatsInner"] and o["contigBeatsGap"] and o["subseq"] and o["caseInsensitive"]
    assert o["q模型"] == ["模型驗證", "模型工作台", "建立模型"]      # 開頭符合在前(短的優先),中間符合在後;選股掃描被濾掉
    assert o["qBack"] == ["模型驗證"]                              # 只靠 keywords 也找得到
    assert o["qTheme"] == ["切換深色 / 淺色"]
    assert o["recentEmpty"][:2] == ["建立模型", "切換深色 / 淺色"]    # 沒輸入時最近使用照順序排最前
    assert o["recentEmpty"][2:] == ["選股掃描", "模型工作台", "模型驗證"]
    assert o["recentBoost"][0] == "建立模型"                        # 最近用過的加分排到前面
    assert o["recent"] == ["a", "c", "b"]
    assert o["typing"] == [True, False, True, True, True, True, False, False]
    assert o["help"] == ["⌘+K", "/", "?", "Esc", "G+H", "G+S", "G+W", "G+M", "G+B"]
    assert o["helpWin"] == ["Ctrl", "K"]
