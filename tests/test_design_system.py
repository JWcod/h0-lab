"""設計系統(階段 3 D2)的靜態檢查:顏色對比、沒有灰字 / 透明度淡化、減少動態效果、元件輸出有跳脫。

- 顏色從 web/css/tokens.css 讀;深色是 `:root`,淺色是 `:root[data-theme="light"]`。
- ui.js 的元件用 node 實際執行(沒有 node 就跳過那一個測試)。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
TOKENS = (WEB / "css" / "tokens.css").read_text(encoding="utf-8")
UI_CSS = (WEB / "css" / "ui.css").read_text(encoding="utf-8")

BGS = ("--bg", "--surface", "--surface2", "--chrome", "--side")
MAIN_TEXT = ("--text", "--text2")
STATUS_TEXT = ("--link", "--green", "--yellow", "--orange", "--red")


def _block(selector_regex: str) -> dict[str, str]:
    m = re.search(selector_regex + r"\s*\{(.*?)\n\}", TOKENS, re.S)
    assert m, selector_regex
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", m.group(1)))


DARK = _block(r"(?m)^:root")
LIGHT = _block(r':root\[data-theme="light"\]')


def _lum(hx: str) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_text_contrast(theme):
    t = DARK if theme == "dark" else LIGHT
    low = []
    for fg, need in [(k, 7.0) for k in MAIN_TEXT] + [(k, 4.5) for k in STATUS_TEXT]:
        for bg in BGS:
            c = _contrast(t[fg], t[bg])
            if c < need:
                low.append(f"{fg} on {bg} = {c:.2f} < {need}")
    assert not low, low


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_change_chip_contrast(theme):
    t = DARK if theme == "dark" else LIGHT
    for bg in ("--red", "--green"):
        assert _contrast(t["--on-status"], t[bg]) >= 4.5, (theme, bg)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_on_accent_contrast(theme):
    t = DARK if theme == "dark" else LIGHT
    on = t.get("--on-accent", DARK["--on-accent"])
    assert _contrast(on, t.get("--sel", DARK["--sel"])) >= 4.5


def test_dark_light_blocks_define_same_colors():
    # 淺色的 media query 版本與 data-theme 版本要一致(兩份手寫,容易漏改)
    m = re.search(r'@media \(prefers-color-scheme: light\)\s*\{\s*:root:not\(\[data-theme="dark"\]\)\s*\{(.*?)\}', TOKENS, re.S)
    media = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", m.group(1)))
    assert media == LIGHT
    assert set(BGS + MAIN_TEXT + STATUS_TEXT) <= set(DARK) and set(BGS + MAIN_TEXT + STATUS_TEXT) <= set(LIGHT)


def test_dark_secondary_text_is_not_gray():
    # 規則 7:次要文字要是淡藍白,不能是灰(R≈G≈B 且偏暗)
    hx = DARK["--text2"]
    r, g, b = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
    assert b - r >= 20 and _lum(hx) > 0.6, hx


def test_ui_css_has_no_faded_or_gray_text():
    css = re.sub(r"/\*.*?\*/", "", UI_CSS, flags=re.S)
    assert not re.search(r"opacity:\s*(0?\.\d|[1-9]\d?%)", css), "ui.css 不得用 0 < opacity < 1 淡化"
    assert not re.search(r"\b(gray|grey|silver|darkgray|dimgray|lightgray)\b", css, re.I), "ui.css 不得用灰色色名"
    assert "color-mix" not in css, "ui.css 不得用 color-mix 混出淡化色"
    hexes = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", css)}
    assert hexes <= {"#fff", "#000"}, f"ui.css 的顏色要用 token,不要寫死:{hexes}"
    rgbs = re.findall(r"rgba?\(([^)]*)\)", css)
    assert all(v.replace(" ", "").startswith("0,0,0") for v in rgbs), f"ui.css 只允許黑色陰影,其他顏色用 token:{rgbs}"


def test_reduced_motion_supported():
    assert "prefers-reduced-motion" in TOKENS and "prefers-reduced-motion" in UI_CSS
    rm = UI_CSS[UI_CSS.rindex("@media (prefers-reduced-motion"):]
    for sel in (".u-shell *", ".u-detail *", ".u-scrim"):
        assert sel in rm, f"減少動態效果要涵蓋 {sel}"
    # 進場動畫的時間跟著 --dur(減少動態效果時 --dur = 0)
    assert not re.search(r"animation:\s*u-(ring|mk)-in\s+[\d.]+m?s", UI_CSS)


def test_breakpoints_are_complementary():
    # 桌面規則用 MOBILE_QUERY 的補集,小數寬度(例如 820.5px)不會兩邊都不套用
    assert "not all and (max-width: 820px)" in UI_CSS and "max-width: 820px" in UI_CSS
    assert not re.search(r"min-width:\s*82[01]", UI_CSS)
    assert 'MOBILE_QUERY = "(max-width: 820px)"' in (WEB / "js" / "ui.js").read_text(encoding="utf-8")


def _resolve(theme: dict, val: str) -> str | None:
    val = val.strip()
    alias = {"--up": "--red", "--down": "--green"}
    if val in ("#fff", "#ffffff"):
        return "#ffffff"
    if val in ("#000", "#000000"):
        return "#000000"
    m = re.fullmatch(r"var\((--[\w-]+)\)", val)
    if not m:
        return None
    k = alias.get(m.group(1), m.group(1))
    return theme.get(k, DARK.get(k))


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_css_rules_with_background_and_text_have_contrast(theme):
    # ui.css 裡同一條規則同時設了底色與文字色(例如完成圓點、選取的側邊欄、漲跌小方塊),兩色要有 4.5:1
    t = DARK if theme == "dark" else LIGHT
    css = re.sub(r"/\*.*?\*/", "", UI_CSS, flags=re.S)
    checked, low = 0, []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        bg = re.search(r"background(?:-color)?:\s*([^;]+);", body)
        fg = re.search(r"(?<![-\w])color:\s*([^;]+);", body)
        if not (bg and fg):
            continue
        b, f = _resolve(t, bg.group(1)), _resolve(t, fg.group(1))
        if b is None or f is None:
            continue
        checked += 1
        c = _contrast(f, b)
        if c < 4.5:
            low.append(f"{sel.strip()}: {c:.2f}")
    assert checked >= 4 and not low, (checked, low)


def test_detail_panel_lifecycle_guards():
    src = (WEB / "js" / "ui.js").read_text(encoding="utf-8")
    opn = src[src.index("export function openDetail"):src.index("export function closeDetail")]
    assert "clearTimeout(hideTimer)" in opn, "剛關又開時要取消隱藏計時器"
    assert "applyModal()" in opn and "setBackgroundInert(true)" in src, "手機版要把背景設成 inert"
    assert 'addEventListener("change", applyModal)' in src, "面板開著時切換版面要重設 modal"
    assert "inerted" in src, "只還原自己設的 inert"
    assert "Object.hasOwn(" not in src, "舊 Safari 不支援 Object.hasOwn"


def test_design_page_references_system():
    html = (WEB / "design.html").read_text(encoding="utf-8")
    assert "/static/css/tokens.css" in html and "/static/css/ui.css" in html and "/static/js/design.js" in html
    assert "<script>" not in html.replace("<script type=\"module\" src=", "")   # CSP:不得有內嵌 script
    assert "不構成投資建議" in html


def test_no_inline_scripts_anywhere():
    # 伺服器 CSP 是 script-src 'self':任何沒有 src 的 <script> 都會被擋
    for f in WEB.glob("*.html"):
        bad = re.findall(r"<script\b(?![^>]*\bsrc\s*=)[^>]*>", f.read_text(encoding="utf-8"), re.I)
        assert not bad, (f.name, bad)


def test_detail_panel_escapes_everything():
    # openDetail 要操作 DOM,node 沒辦法直接跑;改成檢查原始碼:每個插入的值都要經過 esc,而且不接受任意 HTML
    src = (WEB / "js" / "ui.js").read_text(encoding="utf-8")
    block = src[src.index("export function openDetail"):src.index("export function closeDetail")]
    for need in ("${esc(title)}", "${esc(desc)}", "${esc(k)}", "${esc(v)}"):
        assert need in block, need
    inserted = re.findall(r"\$\{([^}]*)\}", block)
    assert all(i.startswith(("esc(", "desc ?", "rows.map", "actions.map")) for i in inserted), inserted   # data-act 的 ${i} 是陣列索引(數字)
    assert "html" not in block.split(")", 1)[0], "openDetail 不接受任意 HTML 參數"
    assert "sectionsHTML(sections)" in block
    sec = src[src.index("export function sectionsHTML"):src.index("// 細節面板:電腦是右側檢視器")]
    allowed = ("esc(", "go ?", "it.tag ?", "it.ev ?", "it.d ?", "sec.items.map", "evidence(")
    for i in re.findall(r"\$\{([^`]*?)\}", sec):
        assert i in ("h", "p", "rows", "items", "go") or i.startswith(allowed), i   # 這幾個是本函式內已跳脫的片段
    assert "HASH_OK.test" in sec, "跳頁只允許站內路徑"


NODE = shutil.which("node")
_SCRIPT = r"""
const UI = await import(process.argv[2]);
const out = {};
out.evAll = Object.keys(UI.EVIDENCE).map((k) => UI.evidence(k));
try { UI.evidence("bogus"); out.unknown = "no-throw"; } catch (e) { out.unknown = "throw"; }
out.evLong = UI.evidence("none", '<img src=x onerror=alert(1)>');
out.st = [UI.ciStatus(-0.01, 0.05), UI.ciStatus(0.01, 0.05), UI.ciStatus(0.01, 0.05, { proven: true }), UI.ciStatus(-0.05, -0.01), UI.ciStatus(null, 0.1)].map((s) => s.status);
out.ciNaN = UI.ciBar(NaN, NaN, NaN, { label: "x" });
out.ci = UI.ciBar(0.02, -0.01, 0.05, { label: "<b>" });
out.rIn = UI.rangeBar({ lo: 10, mid: 15, hi: 20, price: 12 });
out.rLow = UI.rangeBar({ lo: 10, mid: 15, hi: 20, price: 5 });
out.rNA = UI.rangeBar({ lo: null, mid: null, hi: null, price: 5 });
out.ring = UI.ring("<q>", null, null);
out.row = UI.row({ title: "<script>x</script>", sub: "&" });
out.steps = UI.steps(["<a>"]);
out.labels = Object.values(UI.EVIDENCE).map((e) => e.short + e.ic);
try { UI.evidence("toString"); out.proto = "no-throw"; } catch (e) { out.proto = "throw"; }
out.st2 = [UI.ciStatus(0.01, 0.05, { proven: "no" }), UI.ciStatus(0.05, 0.01, { proven: true }), UI.ciStatus(0.05, -0.01)].map((s) => s.status);
out.ciFlat = UI.ciBar(0.01, 0, 0.02, { range: [0, 0] });
out.ringName = UI.ring("<b>", "<i>", Infinity);
out.rFmt = UI.rangeBar({ lo: 1, mid: 2, hi: 3, price: 2, fmt: (v) => "<u>" + v });
out.rowData = UI.row({ title: "t", data: { d: '"><img>' }, ev: "unval" });
try { UI.row({ title: "t", data: { "x y": "1" } }); out.badKey2 = "no-throw"; } catch (e) { out.badKey2 = "throw"; }
out.mk = UI.rangeBar({ lo: 10, mid: 15, hi: 20, price: 15 });
out.secs = UI.sectionsHTML([{ h: "<h>", p: ["<p>"], rows: [["<k>", "<v>"]], items: [{ t: "<t>", d: "<d>", tag: "<g>", ev: "unval" }],
  go: [{ label: "<l>", hash: "#/s/2330.TW/valuation" }, { label: "壞", hash: "javascript:alert(1)" }, { label: "外", hash: "https://x" }] }]);
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="沒有 node")
def test_ui_components_escape_and_logic(tmp_path):
    script = tmp_path / "run.mjs"
    script.write_text(_SCRIPT, encoding="utf-8")
    r = subprocess.run([NODE, str(script), (WEB / "js" / "ui.js").as_uri()], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert len(o["evAll"]) == 5 and not any("✓" in s for s in o["evAll"])     # 規則 8:不用勾
    assert o["unknown"] == "throw"
    assert "<img" not in o["evLong"] and "&lt;img" in o["evLong"]
    assert o["st"] == ["none", "none", "proven", "worse", "thin"]               # 區間不含 0 但沒校正 → 不算證實
    assert "樣本不足" in o["ciNaN"]
    assert "<b>" not in o["ci"] and "&lt;b&gt;" in o["ci"]
    assert "is-out" not in o["rIn"] and "is-out-low" in o["rLow"] and "低於區間下緣" in o["rLow"]
    assert "不可得" in o["rNA"]
    assert "&lt;q&gt;" in o["ring"] and "—" in o["ring"]
    assert "<script>" not in o["row"] and "&lt;script&gt;" in o["row"] and "&amp;" in o["row"]
    assert "&lt;a&gt;" in o["steps"]
    assert not any(re.search(r"PASS|通過|✓|✔|OK", s) for s in o["labels"]), o["labels"]
    assert o["proto"] == "throw"
    assert o["st2"] == ["none", "thin", "thin"]                                   # proven 只認 true;反轉區間 = 無效
    assert "NaN" not in o["ciFlat"]
    assert "<b>" not in o["ringName"] and "<i>" not in o["ringName"] and "&lt;b&gt;" in o["ringName"]
    assert "stroke-dashoffset:157.08" in o["ringName"]                            # Infinity 當成沒分數:空環
    assert "<u>" not in o["rFmt"] and "&lt;u&gt;" in o["rFmt"]
    assert "<img>" not in o["rowData"] and 'data-d="&quot;&gt;&lt;img&gt;"' in o["rowData"] and "is-unval" in o["rowData"]
    assert o["badKey2"] == "throw"
    assert 'style="left:50.00%"' in o["mk"]
    assert not re.search(r"<(h|p|k|v|t|d|g|l)>", o["secs"]) and o["secs"].count("data-go-hash") == 1   # 只留站內路徑
    assert "javascript:" not in o["secs"] and "https://x" not in o["secs"]                                        # 直接輸出最終位置,不靠動畫函式


# ---------- 舊頁面樣式 app.css 的深色 / 淺色(階段 3 D3 第 4 部分:開放淺色模式) ----------
APP_CSS = (WEB / "css" / "app.css").read_text(encoding="utf-8")
APP_ROOT = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", re.search(r"(?m)^:root\s*\{(.*?)\n\}", APP_CSS, re.S).group(1)))
# 深色:app.css 先載入,tokens.css 的 :root 後載入會覆蓋同名變數(--bg、--text、--text2…)
APP_DARK = {**APP_ROOT, **DARK}
APP_BGS = BGS + ("--bg2", "--panel", "--panel2", "--card-end")
APP_MAIN_TEXT = MAIN_TEXT + ("--label", "--muted-strong", "--tip-f", "--pin")
APP_STATUS_TEXT = STATUS_TEXT + ("--accent", "--accent2", "--pos", "--neg", "--watch", "--r0", "--r1", "--r2", "--r3", "--r4")


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_app_css_text_contrast(theme):
    # 舊頁面(卡片、表格、標籤、狀態色)在兩種主題都要清楚:主要文字 ≥ 7,狀態色 ≥ 4.5
    t = APP_DARK if theme == "dark" else {**APP_DARK, **LIGHT}
    low = []
    for fg, need in [(k, 7.0) for k in APP_MAIN_TEXT] + [(k, 4.5) for k in APP_STATUS_TEXT]:
        for bg in APP_BGS:
            c = _contrast(t[fg], t[bg])
            if c < need:
                low.append(f"{fg} on {bg} = {c:.2f} < {need}")
    assert not low, low


def test_light_block_covers_app_css_colors():
    # app.css :root 裡每個顏色變數,淺色區塊都要有對應的值(漲跌色由 tokens.css 的 --up / --down 處理)
    missing = sorted(set(APP_ROOT) - set(LIGHT) - {"--up", "--down"})
    assert not missing, f"淺色區塊缺少 app.css 的變數:{missing}"


def test_app_css_has_no_hardcoded_dark_colors():
    # :root 以外不寫死色碼;只允許放在亮色色塊上的深色字(#07101f)與 agent 預設色(--c 的預設值)
    css = re.sub(r"/\*.*?\*/", "", APP_CSS, flags=re.S)
    body = css[re.search(r"(?m)^:root\s*\{.*?\n\}", css, re.S).end():]
    body = re.sub(r"body\.us-colors\s*\{[^}]*\}", "", body)   # 這兩個會被 tokens.css 覆蓋
    hexes = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", body)}
    assert hexes <= {"#07101f", "#5ab0ff"}, hexes
    # 深色半透明底(rgba 開頭是深藍)會讓淺色模式出現深色塊
    dark_rgba = [v for v in re.findall(r"rgba\(([^)]*)\)", body) if all(int(x) < 40 for x in v.split(",")[:3]) and not v.replace(" ", "").startswith("0,0,0")]
    assert not dark_rgba, dark_rgba


def test_site_defaults_to_light_theme():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<html[^>]*data-theme="light"', html), "沒選過外觀時預設淺色"
    app = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
    assert 'const DEFAULT_THEME = "light"' in app and 'if (th === "system") delete document.documentElement.dataset.theme' in app, "使用者仍可選跟系統"
    assert 'name="color-scheme"' in html
    # 圖表顏色從 CSS 變數讀(淺色模式下文字、格線才看得清楚);views 不寫死深色提示框或白色線
    charts = (WEB / "js" / "charts.js").read_text(encoding="utf-8")
    assert "getPropertyValue" in charts and "--chart-grid" in charts
    for f in (WEB / "js" / "views").glob("*.js"):
        src = f.read_text(encoding="utf-8")
        assert "#0f1a33" not in src and 'color: "#fff"' not in src, f.name


def test_redesigned_pages_use_design_system():
    # 四個頁面改用新元件:大標題、卡片、細節面板;方向對錯不用 ✓ / ✗(規則 8);每頁有非投資建議
    for name in ("paper", "review", "method", "lab"):
        src = (WEB / "js" / "views" / f"{name}.js").read_text(encoding="utf-8")
        assert 'class="u-largetitle"' in src and "u-card" in src and "UI.openDetail" in src, name
        assert "✓" not in src and "✗" not in src, name
        assert "不構成投資建議" in src, name


def test_view_hex_colors_have_light_mapping():
    # 各頁寫死的系列色(#rrggbb)淺色時要靠 charts.js 的 TO_LIGHT 換成深色版;漏掉就會在白底看不見(Verifier36 問題 1)
    charts = (WEB / "js" / "charts.js").read_text(encoding="utf-8")
    mapped = {k.lower() for k in re.findall(r'"(#[0-9a-fA-F]{6})":', charts[charts.index("const TO_LIGHT"):charts.index("const RGB_TO_LIGHT")])}
    missing = {}
    for f in (WEB / "js" / "views").glob("*.js"):
        for h in re.findall(r'"(#[0-9a-fA-F]{6})"', f.read_text(encoding="utf-8")):
            if h.lower() not in mapped and h.lower() not in ("#ffffff", "#000000"):
                missing.setdefault(f.name, set()).add(h)
    assert not missing, missing


def test_score_text_uses_rating_tokens():
    # 分數數字當文字色要用評等 token(--r0..--r4,淺色時換深色,對比由 test_app_css_text_contrast 檢查);
    # scoreColor 的寫死亮色只能當色塊底色(2026-11-03:淺色模式掃描表分數對比只有 1.3–2.4)
    fmt = (WEB / "js" / "fmt.js").read_text(encoding="utf-8")
    body = fmt[fmt.index("export function scoreInk"):]
    body = body[:body.index("\n}\n")]
    assert set(re.findall(r'return "([^"]+)"', body)) == {"var(--muted-strong)", "var(--r0)", "var(--r1)", "var(--r2)", "var(--r3)", "var(--r4)"}
    for f in (WEB / "js" / "views").glob("*.js"):
        assert not re.search(r"color:\s*\$\{scoreColor\(", f.read_text(encoding="utf-8")), f.name
    for v in ("--r0", "--r1", "--r2", "--r3", "--r4"):
        assert v in APP_ROOT and v in LIGHT
