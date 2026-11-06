"""模型工作台(M6)前端的純靜態檢查:檔案存在、導覽有入口、沒有 inline script、沒有灰色 / 淡化文字、
會改資料的請求都帶 CSRF 標頭、自訂模型的分數區塊不出現買賣或評等字眼、固定標「未驗證」。

不啟動伺服器、不連網;customScoreHTML 用 node 實際執行(沒有 node 就跳過那一個測試)。
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
MODELS_JS = WEB / "js" / "views" / "models.js"
MODELAPI_JS = WEB / "js" / "modelapi.js"
MODELPICK_JS = WEB / "js" / "modelpick.js"
MODELS_CSS = WEB / "css" / "models.css"

# 我的模型不得出現的字眼(規格 §4.4:只輸出分數、等級、排名)
FORBIDDEN = ("買進", "賣出", "減碼", "加碼", "評等", "強力", "偏多", "偏空", "看多", "看空")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in src.splitlines())


def test_files_exist_and_are_wired():
    for p in (MODELS_JS, MODELAPI_JS, MODELPICK_JS, MODELS_CSS):
        assert p.is_file(), p
    html = _read(WEB / "index.html")
    assert "/static/css/models.css" in html
    assert 'data-route="models"' in html and "模型工作台" in html
    app = _read(WEB / "js" / "app.js")
    assert 'from "./views/models.js"' in app and 'from "./modelpick.js"' in app
    assert 'parts[0] === "models"' in app and 'models: ZH`模型工作台`' in app
    assert '"#/models"' in app, "手機「更多」要有模型工作台"
    assert 'id="modelPick"' in app and "ModelPick.mount(" in app, "報告頁要有模型選擇器"


def test_no_inline_script_in_html():
    for f in WEB.glob("*.html"):
        bad = re.findall(r"<script\b(?![^>]*\bsrc\s*=)[^>]*>", _read(f), re.IGNORECASE)
        assert not bad, (f.name, bad)


def test_no_inline_event_handlers_or_eval():
    # CSP 是 script-src 'self':HTML 字串裡的 onclick= 之類不會執行,也不得用 eval / new Function
    for p in (MODELS_JS, MODELAPI_JS, MODELPICK_JS):
        src = _strip_js_comments(_read(p))
        assert not re.search(r"<[^>]*\son[a-z]+\s*=", src), p.name
        assert "eval(" not in src and "new Function" not in src, p.name


def test_models_css_has_no_gray_or_faded_text():
    css = re.sub(r"/\*.*?\*/", "", _read(MODELS_CSS), flags=re.DOTALL)
    assert not re.search(r"opacity:\s*(0?\.\d|[1-9]\d?%)", css), "不得用 0 < opacity < 1 淡化"
    assert not re.search(r"\b(gray|grey|silver|darkgray|dimgray|lightgray|darkgrey|dimgrey)\b", css, re.IGNORECASE), "不得用灰色色名"
    assert "color-mix" not in css
    assert not re.search(r"filter:\s*[^;]*(brightness|opacity|grayscale|saturate|contrast)\(", css), "不得用 filter 淡化或變灰"
    assert "mix-blend-mode" not in css
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css), "顏色要用 tokens.css 的變數,不要寫死"
    assert not re.findall(r"rgba?\(", css) and not re.findall(r"hsla?\(", css), "顏色要用變數"
    # 文字色只能用高對比的 token(不用 --sep 這類分隔線色當文字)
    for v in re.findall(r"(?<![-\w])color:\s*([^;]+);", css):
        assert v.strip() in {"var(--text)", "var(--text2)", "var(--link)", "var(--green)", "var(--yellow)",
                             "var(--orange)", "var(--red)"}, v
    assert "prefers-reduced-motion" in css


def test_mutating_requests_carry_csrf_header():
    api = _strip_js_comments(_read(MODELAPI_JS))
    assert '"X-Requested-With": "invest-lab"' in api
    post = api[api.index("export const postJSON"):api.index("export const listModels")]
    assert 'method: "POST"' in post and "...CSRF" in post
    for name in ("saveModel", "validateModel", "forkModel", "archiveModel", "backtestModel", "holdoutModel"):
        line = next(x for x in api.splitlines() if f"export const {name}" in x)
        assert "postJSON(" in line, name
    # 工作台與選擇器不得自己發請求(任何方法都一律經過 modelapi 的 getJSON / postJSON;標頭由 node 實跑檢查)
    for p in (MODELS_JS, MODELPICK_JS):
        src = _strip_js_comments(_read(p))
        assert 'method: "POST"' not in src and "method:'POST'" not in src, p.name
        assert "fetch" not in src and "XMLHttpRequest" not in src and "sendBeacon" not in src, p.name
    assert api.count("fetch(") == 1, "modelapi.js 只能有一個 fetch 呼叫點(call)"


def test_custom_model_output_has_no_trade_words():
    pick = _strip_js_comments(_read(MODELPICK_JS))
    api = _strip_js_comments(_read(MODELAPI_JS))
    block = api[api.index("export function customScoreHTML"):]
    for src, name in ((pick, "modelpick.js"), (block, "customScoreHTML")):
        hits = [w for w in FORBIDDEN if w in src]
        assert not hits, (name, hits)
    assert "UNVALIDATED" in block and "c.disclaimer" in block
    assert 'UNVALIDATED = ZH`未驗證`' in api
    assert "API.customScoreHTML(custom)" in pick


def test_workbench_texts():
    src = _read(MODELS_JS)
    for need in ("不構成投資建議", "運算式語言開發中", "研究基準模型、唯讀", "expr_available", "base_vars",
                 "API.validateModel", "API.saveModel", "API.forkModel", "API.archiveModel", "window.confirm"):
        assert need in src, need
    code = _strip_js_comments(src)
    hits = [w for w in ("買進", "賣出", "減碼", "加碼") if w in code]
    assert not hits, hits
    # builtin 打分只能改權重與開關
    assert 'x.score?.type === "builtin") return' in code


def test_backtest_wiring():
    """M7:回測接上 M5;holdout 要先確認、送 confirm: true;有沒存檔的修改或計算中時停用按鈕。"""
    api = _strip_js_comments(_read(MODELAPI_JS))
    line = next(x for x in api.splitlines() if "export const holdoutModel" in x)
    assert "confirm: true" in line and "/holdout`" in line
    line = next(x for x in api.splitlines() if "export const backtestModel" in x)
    assert "/backtest`" in line and "{ universe }" in line
    for u in ("tw_pit50", "tw_large40", "us_large40"):
        assert f'"{u}"' in api, u
    code = _strip_js_comments(_read(MODELS_JS))
    assert "即將推出" not in code, "回測已經接上,不得再顯示即將推出"
    assert "API.backtestModel(" in code and "API.holdoutModel(" in code
    run_ho = code[code.index("async function runHoldout"):code.index("async function refreshList")]
    assert run_ho.index("window.confirm(HOLDOUT_CONFIRM)") < run_ho.index("API.holdoutModel(")
    assert "計算中" in code and "b.disabled = busy || dirty" in code
    for need in ("n_trials", "multi_note", "shipped_note", "p_bonferroni", "mine_vs_shipped", "mine_share_above",
                 "p2_5", "p97_5", "r.disclaimer", "只用開發期", "st.label", "shipped_trials_note", "trialsText(m.n_trials)"):
        assert need in code, need
    rnd = code[code.index("function randomHTML"):code.index("function bindBacktest")]
    assert "verdict" not in rnd and "不做判定" in rnd, "隨機對照組不給判定"


def test_m6_verifier_fixes():
    code = _strip_js_comments(_read(MODELS_JS))
    assert "render(el, {}," not in code and "el.__mwState" in code, "封存後要帶原本的 state(state.modelsInfo 才會更新)"
    assert "export function confirmLeave" in code
    app = _strip_js_comments(_read(WEB / "js" / "app.js"))
    hc = app[app.index('addEventListener("hashchange"'):]
    hc = hc[:hc.index("});")]
    assert "Models.confirmLeave()" in hc, "側邊欄換頁要先問有沒存檔"
    # 取消時回到原頁,但不能用 replaceState 改寫紀錄(會毀掉「上一頁」那筆,Verifier 第二輪 N1)
    assert "history.forward()" in hc and "history.back()" in hc and "history.replaceState(null" not in hc
    pick = _strip_js_comments(_read(MODELPICK_JS))
    assert "/api/report" not in pick, "切分頁不得重打 /api/report?model=(會在背景重建報告)"
    assert "API.validateModel(" in pick and "cache.get(key)" in pick and "generated_at" in pick
    raw = _read(MODELS_JS) + _read(MODELPICK_JS)
    assert "回測驗證(M4/M5)還沒做" not in raw and "見 PR 說明" not in raw, "過時註解要更新"
    assert "已在記憶體快取的報告」可能直接回傳" not in raw



def test_m7_verifier_fixes():
    code = _strip_js_comments(_read(MODELS_JS))
    fork = code[code.index("function showFork"):code.index("async function openEditor")]
    assert "leaveOK = false" in fork[fork.index("catch (ex)"):], "另存失敗時要撤銷「已確認離開」"
    run_bt = code[code.index("async function runBacktest"):code.index("async function runHoldout")]
    assert run_bt.index("const forHash = my.savedHash") < run_bt.index("await API.backtestModel(")
    assert "my.btForHash = forHash" in run_bt
    early = run_bt[run_bt.index("if (ed !== my"):]
    assert early.index("refreshList(") < early.index("return;"), "換頁後回測完成仍要更新清單"
    run_ho = code[code.index("async function runHoldout"):code.index("async function refreshList")]
    assert "my.hoStatus = {" in run_ho and "clean: false" in run_ho, "看過 holdout 後狀態要更新"
    st = code[code.index("function holdoutStatusHTML"):code.index("function btOutHTML")]
    assert "ed.hoStatus ||" in st and "btStale()" in st


def test_m6_verifier_round2_fixes():
    """M6 Verifier 第一、二輪指出、M7 之後仍存在的競態與狀態問題(雲端分支補上)。"""
    code = _strip_js_comments(_read(MODELS_JS))
    sv = code[code.index("async function save"):code.index("function trialsText")]
    assert "const snap = JSON.stringify(my.model)" in sv and "my.savedJSON = snap" in sv, "存檔要用送出當下的快照"
    assert "my.savedJSON = JSON.stringify(my.model)" not in sv
    sched = code[code.index("function scheduleValidate"):code.index("async function runValidate")]
    assert "my.seq++" in sched, "debounce 期間舊的 validate 回應要作廢"
    oe = code[code.index("async function openEditor"):code.index("function renderEditor")]
    assert "token !== renderToken" in oe, "快速切換模型時,等待中的回應要作廢"
    rd = code[code.index("export async function render"):code.index("function renderList")]
    assert rd.index("ed.model = JSON.parse(ed.savedJSON)") < rd.index("leaveOK = false"), "確定放棄後回到同一模型,修改不能復活"
    ex = code[code.index("function bindExpr"):code.index("function changed")]
    assert "const my = ed" in ex and "if (ed !== my" in ex, "新增自訂指標 await 後要確認還是同一個模型"
    rl = code[code.index("async function refreshList"):]
    assert "ed?.model?.id" in rl, "重抓清單後反白當下正在編輯的模型"
    st = code[code.index("function renderStatus"):code.index("function renderPreview")]
    assert 'role="alert"' not in st and 'role="alert"' not in sv, "#mwStatus 是 role=status,裡面不再放 role=alert"
    pick = _strip_js_comments(_read(MODELPICK_JS))
    assert "esc(name || custom.model?.name)" in pick, "卡片名稱以清單為準(只改名稱時快取結果是舊名稱)"


# 插入 HTML 的未跳脫值:直接引用外部文字欄位的 ${…} 一律要經過 esc()。
# 例外只有不會變成 HTML 的地方(textContent、alert、confirm、之後才 esc 的錯誤字串)。
# 已知限制:先存成別名再插入、或用字串相加組 HTML 的寫法抓不到(靠 code review)。
_UNTRUSTED = (r"\b(name|label|metricLabel|expr|error|err|message|symbol|notes|disclaimer|based_on|family_id|score_hash"
              r"|id|key|var|previewSym|previewError|btError|hoError)\b")
_NON_HTML_LINE = ("textContent", "alert(", "confirm(", "error:", "err = ", "j = {")


def _interpolations(src: str):
    # 取出每一個 ${...}(含巢狀在別的 ${…} 裡面的),回傳 (行號, 運算式)
    i = 0
    while True:
        i = src.find("${", i)
        if i < 0:
            return
        depth, j = 1, i + 2
        while j < len(src) and depth:
            depth += {"{": 1, "}": -1}.get(src[j], 0)
            j += 1
        yield src.count("\n", 0, i) + 1, src[i + 2:j - 1]
        i += 2


def _strip_escaped(e: str) -> str:
    # 去掉 esc(...) 呼叫(處理巢狀括號)與雙引號字串,剩下的才是「沒有跳脫就插進 HTML」的部分
    out, i = [], 0
    while i < len(e):
        if e.startswith("esc(", i) and (i == 0 or not (e[i - 1].isalnum() or e[i - 1] in "_.")):
            depth, j = 1, i + 4
            while j < len(e) and depth:
                depth += {"(": 1, ")": -1}.get(e[j], 0)
                j += 1
            out.append("ESC")
            i = j
        else:
            out.append(e[i])
            i += 1
    rest = re.sub(r'"[^"\n]*"', '""', "".join(out))
    if "?" in rest:   # 三元運算的條件不會輸出成 HTML,只看兩個分支
        rest = rest.split("?", 1)[1]
    return rest


def test_untrusted_text_is_escaped():
    for p in (MODELS_JS, MODELPICK_JS, MODELAPI_JS):
        src = _strip_js_comments(_read(p))
        lines = src.splitlines()
        bad = []
        for ln, ex in _interpolations(src):
            e = ex.strip()
            if e.startswith(("esc(", "encodeURIComponent(")):
                continue
            if re.fullmatch(r'[^?"`]*\?\s*"[^"]*"\s*:\s*"[^"]*"', e):   # 條件只決定固定字串(例:是否加 class)
                continue
            if "`" in e:   # 含巢狀樣板(map、三元裡的 HTML 片段):裡面的每個 ${…} 都會各自被檢查
                continue
            if not re.search(_UNTRUSTED, _strip_escaped(e)):
                continue
            if any(k in lines[ln - 1] for k in _NON_HTML_LINE):
                continue
            bad.append(f"{p.name}:{ln}: ${{{e}}}")
        assert not bad, bad


NODE = shutil.which("node")
_SCRIPT = r"""
const API = await import(process.argv[2]);
const out = {};
out.full = API.customScoreHTML({
  model: { id: "x", name: "<b>n</b>", score_hash: "<i>h</i>" },
  long_term: { score: 71.23, grade: "B+" }, short_term: { score: null, grade: "—" },
  pillars: { quality: { score: 80, grade: "A", coverage: 0.9 }, flows: { score: 40.5, grade: "<s>", coverage: 1 } },
  unvalidated_pillars: ["flows"], status: "未驗證", disclaimer: "<script>x</script>研究用途,非投資建議。",
});
out.empty = API.customScoreHTML(null);
out.noDisc = API.customScoreHTML({ long_term: {}, short_term: {}, pillars: {} });
out.idOk = ["my-model", "a", "-bad", "Bad", "a".repeat(49)].map((s) => API.MODEL_ID_RE.test(s));
const calls = [];
globalThis.fetch = async (url, opts = {}) => { calls.push({ url, method: opts.method || "GET", headers: opts.headers || {}, body: opts.body });
  return { ok: true, status: 200, json: async () => ({ ok: true }) }; };
await API.saveModel({ id: "m" });
await API.validateModel({ id: "m" }, "2330.TW");
await API.forkModel("jw-mf-2.0", "a-b", "名");
await API.archiveModel("a/b");
await API.backtestModel("m", "tw_pit50");
await API.holdoutModel("m", "tw_pit50");
await API.listModels();
out.calls = calls;
globalThis.fetch = async () => ({ ok: false, status: 404, json: async () => ({ detail: "找不到模型 x" }) });
try { await API.getModel("x"); out.err = "no-throw"; } catch (e) { out.err = e.message; }
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="沒有 node")
def test_custom_score_html_escapes_and_labels(tmp_path):
    script = tmp_path / "run.mjs"
    script.write_text(_SCRIPT, encoding="utf-8")
    r = subprocess.run([NODE, str(script), MODELAPI_JS.as_uri()],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    full = o["full"]
    assert "未驗證" in full and "研究用途,非投資建議" in full
    assert "<script>" not in full and "&lt;script&gt;" in full and "<s>" not in full and "<i>" not in full
    assert "B+" in full and "71.2" in full and "品質" in full and "籌碼" in full
    assert not [w for w in FORBIDDEN if w in full]
    assert o["empty"] == ""
    assert "未驗證" in o["noDisc"] and "非投資建議" in o["noDisc"]
    assert o["idOk"] == [True, True, False, False, False]
    posts = [c for c in o["calls"] if c["method"] != "GET"]
    assert len(posts) == 6 and all(c["method"] == "POST" for c in posts)
    assert all(c["headers"].get("X-Requested-With") == "invest-lab" for c in posts), posts
    assert [c["url"] for c in posts] == ["/api/models", "/api/models/validate", "/api/models/jw-mf-2.0/fork",
                                         "/api/models/a%2Fb/archive", "/api/models/m/backtest", "/api/models/m/holdout"]
    assert json.loads(posts[0]["body"]) == {"model": {"id": "m"}}
    assert json.loads(posts[1]["body"]) == {"model": {"id": "m"}, "symbol": "2330.TW"}
    assert json.loads(posts[5]["body"]) == {"universe": "tw_pit50", "confirm": True}
    assert o["err"] == "找不到模型 x"


_BT_SCRIPT = r"""
const M = await import(process.argv[2]);
const r = JSON.parse(process.argv[3]);
const out = {};
out.bt = M.backtestResultHTML(r);
out.stale = M.backtestResultHTML(r, true);
out.unv = M.backtestResultHTML({ status: "未驗證", reason: "<b>算不出分數</b>", disclaimer: "回測結果不代表未來報酬,非投資建議" });
const ho = { status: "holdout", universe: "tw_pit50", score_hash: "h", clean: false, label: "已看過,不是乾淨的樣本外",
  repeat: true, metrics: { lt_12m: { mine: r.metrics.lt_12m.mine, random: r.metrics.lt_12m.random } },
  note: "holdout 不和出廠模型比較", disclaimer: "回測結果不代表未來報酬,非投資建議" };
out.ho = M.holdoutResultHTML(ho);
console.log(JSON.stringify(out));
"""


def _bt_payload() -> dict:
    """M5 開發集回測回應的形狀(數字是合成的,不是任何真實資料的結果)。"""
    blk = lambda ic: {"ic": ic, "ci95": [ic - 0.02, ic + 0.02], "p": 0.4, "n_months": 64,  # noqa: E731
                      "first_month": "2018-07-02", "last_month": "2023-10-02", "verdict": "沒有證實有效"}
    m = {
        "mine": blk(0.0063) | {"p_bonferroni": 0.8, "verdict": "<i>沒有證實有效</i>"},
        "shipped": blk(0.0051),
        "mine_vs_shipped": {"diff": 0.0012, "ci95": [-0.013, 0.0156], "n_months": 64, "verdict": "和出廠模型沒有差別",
                            "coverage": {"n_both": 2112, "coverage_base": 0.88, "coverage_new": 0.9}},
        "random": {"draws": 30, "median": 0.0019, "p2_5": -0.007, "p97_5": 0.0047, "mine_share_above": 0.9,
                   "note": "把每天的分數隨機打亂 30 次;隨機排序也可能碰巧看起來有效,所以這裡只給分布"},
    }
    return {"status": "開發集", "split": "dev", "model": "mine-a", "family": "mine-a", "score_hash": "<b>8eab</b>",
            "universe": "tw_pit50", "n_trials": 2, "multi_note": "試越多次,越容易碰巧找到好看的結果;所以 p 值乘上試過的次數(Bonferroni 校正)",
            "shipped_note": "出廠模型在開發時已經試了 37 個設定", "metrics": {"lt_12m": m, "lt_6m": m, "st_1m": m},
            "seconds": 15.8, "disclaimer": "回測結果不代表未來報酬,非投資建議",
            "holdout": {"viewed": False, "family_viewed": False, "clean": True, "label": "還沒看過"}}


@pytest.mark.skipif(NODE is None, reason="沒有 node")
def test_backtest_result_html(tmp_path):
    script = tmp_path / "run.mjs"
    script.write_text(_BT_SCRIPT, encoding="utf-8")
    r = subprocess.run([NODE, str(script), MODELS_JS.as_uri(),
                        json.dumps(_bt_payload(), ensure_ascii=False)],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    bt = o["bt"]
    for need in ("只用開發期", "已嘗試 2 個設定", "Bonferroni", "p × 2", "37 個設定", "台股 50 檔(依當時成分)",
                 "我的模型", "研究基準模型", "和研究基準模型沒有差別", "隨機排序對照組", "打亂 30 次", "贏過其中 90%",
                 "只給分布", "不做判定", "非投資建議", "0.006", "-0.014 ~ 0.026", "2018-07 ~ 2023-10", "15.8 秒", "2112 筆"):
        assert need in bt, need
    assert bt.count("<section") == 3
    assert "<i>" not in bt and "<b>8eab" not in bt and "&lt;b&gt;8eab" in bt
    assert not [w for w in FORBIDDEN if w in bt]
    # 伺服器文字裡的內部用語顯示前換成產品用語(介面商業化規格 §2)
    for old in ("出廠模型", "模型族", "開發集", "評分雜湊", "holdout"):
        assert old not in bt, old
    assert "沒有證實有效" in bt and "&lt;i&gt;沒有證實有效" in bt, "結論句照實顯示,仍要跳脫"
    assert "存檔前那個版本" not in bt and "存檔前那個版本" in o["stale"]
    assert "未驗證" in o["unv"] and "&lt;b&gt;" in o["unv"] and "非投資建議" in o["unv"]
    ho = o["ho"]
    assert "已看過,不是乾淨的樣本外" in ho and "當時的紀錄" in ho and "不和研究基準模型比較" in ho
    assert "範例模型</th>" not in ho and "我的模型 − 範例模型" not in ho and "holdout" not in ho
    assert not [w for w in FORBIDDEN if w in ho]


def test_product_terms_and_empty_state():
    """商業化改版:不顯示內部代號與開發歷史;沒有我的模型時給下一步按鈕;誠實機制做成狀態徽章。"""
    code = _strip_js_comments(_read(MODELS_JS))
    for old in ("模型族", "試了幾次", "評分雜湊", "出廠", "scripts/gate.py", "family_id)"):
        assert old not in code, old
    assert not re.search(r"\bM[4-7]\b(?![\d\s.])", code), "不顯示內部階段代號(M4、M5…)"
    assert "樣本外" in code and "嘗試次數" in code and "設定指紋" in code
    wel = code[code.index("function renderWelcome"):code.index("async function archive")]
    assert "data-new" in wel and "建立你的第一個模型" in wel and 'location.hash = "#/wizard"' in code
    bad = code[code.index("function badgesHTML"):code.index("function renderBadges")]
    assert "is-unval" in bad and "trialsText(ed.nTrials)" in bad and "樣本外:已看過" in bad
    assert "mw-err-txt" not in bad, "誠實機制不是錯誤,不用錯誤樣式"
    api = _strip_js_comments(_read(MODELAPI_JS))
    assert "export function productText" in api and "PIT" not in api
    pick = _strip_js_comments(_read(MODELPICK_JS))
    assert "#/wizard" in pick and "出廠" not in pick


@pytest.mark.skipif(NODE is None, reason="沒有 node")
def test_sample_model_name_and_product_text(tmp_path):
    script = tmp_path / "run.mjs"
    script.write_text(r"""
const A = await import(process.argv[2]);
console.log(JSON.stringify({
  h0: A.shownModelName({ source: "shipped", name: "H0 範例模型" }),
  old: A.shownModelName({ source: "shipped", name: "JW-MF v2.0-dev" }),
  mine: A.shownModelName({ source: "user", name: "我的價值" }),
  note: A.productText("出廠模型在開發時已經試了 37 個設定(v2 實驗登記簿,截至 2026-10-27)。"),
  ho: A.productText("出廠模型的回測與 holdout 只走 backtest_pit / scripts/holdout.py,不走模型工作台"),
}));
""", encoding="utf-8")
    r = subprocess.run([NODE, str(script), MODELAPI_JS.as_uri()],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    # 2026-11-03 起出廠模型在介面上叫「研究基準模型」(只出現在研究紀錄與回測對照)
    assert o["h0"] == "研究基準模型" and o["old"] == "研究基準模型" and o["mine"] == "我的價值"
    assert o["note"] == "研究基準模型在開發時已經試了 37 個設定。"
    assert o["ho"] == "研究基準模型的回測與樣本外測試只走開發時的正式流程,不走模型工作台"


WIZARD_JS = WEB / "js" / "views" / "wizard.js"


def test_wizard_product_copy_and_layout():
    """精靈商業化改版:1-2-3 進度可點回、範本卡選取狀態、第 ③ 步先給白話結論、統計細節收合、產品用語。"""
    code = _strip_js_comments(_read(WIZARD_JS))
    for old in ("試了幾次", "評分雜湊", "模型族", "出廠"):
        assert old not in code, old
    assert "data-goto" in code and "n < W.step" in code, "只能點回做完的步驟"
    assert "wz-check" in code and "is-sel" in code
    s3 = code[code.index("function step3HTML"):code.index("function sumHTML")]
    assert s3.index("wzSum") < s3.index("wzRank"), "結論要在排名前面"
    sm = code[code.index("function sumHTML"):code.index("function updateSum")]
    assert "沒有證實有效" not in sm and "btNotProven(W.bt)" in sm, "結論的判定和下方「沒有證實有效」同一個規則"
    assert "wizard.sum.btNot" in sm and "還沒有證實有效" in code
    bo = code[code.index("export function backtestOutHTML"):code.index("const btNotProven")]
    assert bo.count("<details") == 2 and bo.index("wizard.bt.more") < bo.index("wizard.bt.random")
    assert "productText(loc(T.rationale))" in code, "範本說明裡的舊名稱(JW-MF)換成產品用語"
    css = re.sub(r"/\*.*?\*/", "", _read(WEB / "css" / "wizard.css"), flags=re.DOTALL)
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css) and "rgb" not in css, "只用 token"
