// 模型工作台(規格 docs/模型客製化規格.md、docs/介面商業化規格.md)。
// 版面:左邊是模型清單(像 macOS 的來源清單),右邊是選到的模型;沒選模型時右邊放下一步說明。
// 路由:#/models(清單 + 下一步)、#/models/<id>(清單 + 編輯器)。
// - 研究基準模型唯讀(只能經由開發時的正式檢定修改),只能「另存」成我的模型再改。介面上的狀態標「研究基準模型、唯讀」。
// - 每次修改都送 POST /api/models/validate(debounce),顯示繁中錯誤與設定指紋(score_hash);可輸入代號試算(只用已開過的報告)。
// - 我的模型只顯示分數與等級,固定標「未驗證」;不顯示任何買賣字眼或評等。
// - 回測驗證:POST /api/models/{id}/backtest 只算開發期,和研究基準模型、隨機對照比較,顯示嘗試次數與
//   Bonferroni;隨機對照只給分布不給判定。樣本外測試(holdout)要先確認、送 confirm: true;伺服器沒開放時照實顯示它的訊息。
// - 誠實機制(未驗證、嘗試次數、樣本外只能看一次)做成編輯器頂端的狀態徽章,一直看得到。
// - 伺服器回來的說明文字經過 API.productText 換成產品用語(只改顯示)。
// 所有插入 HTML 的值都經過 esc(數字也是)。
import { ZH } from "../i18n/index.js";
import { esc, num, isNum } from "../fmt.js";
import * as API from "../modelapi.js";
import * as Setup from "../setup.js";

const { PILLAR_LABEL, PILLARS, WEIGHT_GROUPS } = API;
const DEBOUNCE_MS = 350;
const APPLIES_TXT = { all: "", non_financial: ZH`非金融`, financial: ZH`金融` };
const MARKET_TXT = { all: "", TW: ZH`台股`, US: ZH`美股` };
const SCORE_TXT = { linear: ZH`線性`, peak: ZH`峰值`, map: ZH`對照表`, builtin: ZH`內建規則` };
const DISCLAIMER = ZH`模型工作台是研究工具:自訂模型的分數只是依你設定的權重重新計算;回測只看過去的開發期,不代表未來報酬,不構成投資建議,也不保證任何報酬。`;
const METRIC_TXT = { lt_12m: ZH`長期分數 · 持有 12 個月`, lt_6m: ZH`長期分數 · 持有 6 個月`, st_1m: ZH`短期分數 · 持有 1 個月` };
const HOLDOUT_CONFIRM = ZH`樣本外測試期是 2024-10-01 以後的資料,每個模型系列只有一次乾淨的測試機會。\n\n`
  + ZH`看過之後,這個模型系列(以及從它複製出來、或它的來源模型系列)之後的任何版本,都會標「已看過,不是乾淨的樣本外測試」。\n`
  + ZH`建議在開發期把設定定案之後才看。\n\n確定要看嗎?`;
const P = API.productText;
// 線性圖示(1.6px 線寬、圓角端點;和全站 SVG 圖示同一種畫法)
const ICON = {
  plus: `<svg class="mw-ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v12M4 10h12"/></svg>`,
  model: `<svg class="mw-ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 15l4-5 3 3 6-8"/><path d="M3 17h14"/></svg>`,
  lock: `<svg class="mw-ic" viewBox="0 0 20 20" aria-hidden="true"><rect x="4.5" y="9" width="11" height="8" rx="2"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9"/></svg>`,
  flask: `<svg class="mw-ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M8 3h4M8.5 3v5L4 16a1 1 0 0 0 .9 1.5h10.2A1 1 0 0 0 16 16l-4.5-8V3"/></svg>`,
  count: `<svg class="mw-ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M4 5h12M4 10h12M4 15h7"/></svg>`,
  eye: `<svg class="mw-ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M2 10s3-5.5 8-5.5S18 10 18 10s-3 5.5-8 5.5S2 10 2 10z"/><circle cx="10" cy="10" r="2.3"/></svg>`,
};

// 目前編輯中的模型(換頁時保留,用來提醒尚未存檔)
let ed = null;
let beforeUnloadBound = false;
let leaveOK = false;   // 使用者已確認放棄這次的修改(避免同一次離開被問兩次:工作台自己的按鈕 + 路由)
let renderToken = 0;   // 每次 render 加 1;等待中的回應若已經不是同一次 render 就丟掉(快速切換模型時網址與內容才一致)

const isDirty = () => !!(ed && !ed.readonly && JSON.stringify(ed.model) !== ed.savedJSON);
// 路由(app.js 的 hashchange)與工作台自己的按鈕都呼叫這個;回傳 false 代表留在原頁
export function confirmLeave() {
  if (!isDirty() || leaveOK) return true;
  leaveOK = window.confirm(ZH`這個模型還有沒存檔的修改,確定要離開嗎?`);
  return leaveOK;
}

function recentSymbols() {
  try { return (JSON.parse(localStorage.getItem("jwlab.recent") || "[]") || []).slice(0, 6); } catch (e) { return []; }
}

export async function render(el, state, modelId = null) {
  const token = ++renderToken;
  // 上次離開時已確定放棄修改(leaveOK 還是 true):回到同一個模型不能讓放棄的修改復活(Verifier)
  if (leaveOK && ed && !ed.readonly) ed.model = JSON.parse(ed.savedJSON);
  leaveOK = false;
  el.__mwState = state;
  if (!beforeUnloadBound) {
    beforeUnloadBound = true;
    window.addEventListener("beforeunload", (e) => {
      if (isDirty() && location.hash.startsWith("#/models")) { e.preventDefault(); e.returnValue = ""; }
    });
  }
  el.innerHTML = ZH`<div class="u-ov mw">
    <header class="mw-head">
      <div class="mw-head-t">
        <h1 class="u-largetitle">模型工作台</h1>
        <p class="u-sub mw-lead">細調你的選股模型,在開發期回測,誠實看它到底有沒有用。</p>
      </div>
      <button type="button" class="u-btn is-primary mw-new" data-new>${ICON.plus}建立模型</button>
    </header>
    <div class="mw-split">
      <nav class="mw-src" id="mwList" aria-label="模型清單"><div class="loading"><div class="spinner"></div>讀取模型清單…</div></nav>
      <div class="mw-detail"><div id="mwFork"></div><div id="mwEditor"></div></div>
    </div>
    <p class="u-disclaimer">${esc(DISCLAIMER)}</p>
  </div>`;
  el.querySelectorAll("[data-new]").forEach((b) => (b.onclick = goWizard));
  let info;
  try { info = await API.listModels(); } catch (e) {
    if (token !== renderToken || !el.querySelector("#mwList")) return;   // 舊的失敗回應不能蓋掉新畫面
    el.querySelector("#mwList").innerHTML = ZH`<p class="mw-err-txt mw-src-err">讀取模型清單失敗:${esc(P(e.message))}</p>`;
    el.querySelector("#mwEditor").innerHTML = ZH`<div class="u-card mw-empty-card"><h2 class="mw-h3">暫時讀不到模型清單</h2>
      <p class="u-sub">本機伺服器可能還在啟動或剛重開。重新整理頁面再試一次;一直不行的話,關掉再重新啟動 H0 Lab。</p></div>`;
    return;
  }
  if (token !== renderToken || !el.querySelector("#mwList")) return;   // 已經換頁或重畫
  state.modelsInfo = info;
  el.__mwInfo = info;
  renderList(el, info, modelId);
  if (modelId) await openEditor(el, info, modelId, token);
  else { ed = null; renderWelcome(el, info); }
}

function goWizard() {
  if (!confirmLeave()) return;
  location.hash = "#/wizard";
}

// ---------- 模型清單(左欄) ----------
function renderList(el, info, openId) {
  const box = el.querySelector("#mwList");
  const models = info.models || [];
  const shipped = models.filter((m) => m.source === "shipped");
  const mine = models.filter((m) => m.source !== "shipped");
  const row = (m) => {
    const isShip = m.source === "shipped";
    const cur = openId === m.id;
    const sub = m.error ? `<span class="mw-row-err">${esc(P(m.error))}</span>`
      : isShip ? ZH`<span class="mw-row-sub">${ICON.lock}唯讀 · ${esc(m.n_metrics)} 個指標</span>`
        : `<span class="mw-row-sub"><span class="mw-chip is-unval">${esc(API.UNVALIDATED)}</span><span class="mw-chip">${esc(trialsText(m.n_trials))}</span></span>`;
    return `<li><button type="button" class="mw-row${cur ? " is-open" : ""}" data-open="${esc(m.id)}"${cur ? ` aria-current="true"` : ""}${m.error ? " disabled" : ""}>
      <span class="mw-row-name">${esc(API.shownModelName(m))}</span>${sub}</button></li>`;
  };
  // 內建研究基準模型不列(GET /api/models 已經不回傳;舊伺服器回傳也不顯示):開源版不提供它的個股分數
  void shipped;
  box.innerHTML = ZH`
    <h2 class="mw-src-h">我的模型${mine.length ? ` <span class="mw-src-n u-num">${esc(mine.length)}</span>` : ""}</h2>
    ${mine.length ? `<ul class="mw-models">${mine.map(row).join("")}</ul>`
      : ZH`<div class="mw-src-empty"><p class="u-sub">還沒有你自己的模型。</p><button type="button" class="u-btn" data-new>${ICON.plus}建立模型</button></div>`}`;
  box.querySelectorAll("[data-new]").forEach((b) => (b.onclick = goWizard));
  box.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => {
    if (b.dataset.open === ed?.model?.id) return;
    if (!confirmLeave()) return;
    ed = null;
    location.hash = `#/models/${encodeURIComponent(b.dataset.open)}`;
  }));
}

// 沒選模型時的右欄:還沒有我的模型 → 下一步按鈕;已經有 → 請從左邊選,並說明誠實機制
function renderWelcome(el, info) {
  const box = el.querySelector("#mwEditor");
  if (!box) return;
  const mine = (info.models || []).filter((m) => m.source !== "shipped" && !m.error);
  const honest = ZH`<ul class="mw-honest">
      <li><span class="mw-chip is-unval">${esc(API.UNVALIDATED)}</span><span>你的模型一律標「未驗證」,只顯示分數與等級,不給買賣建議。</span></li>
      <li><span class="mw-chip">${ICON.count}嘗試次數</span><span>每回測一個新設定就記一次。試越多次,越容易碰巧找到好看的結果,統計檢定會依次數校正。</span></li>
      <li><span class="mw-chip">${ICON.eye}樣本外只能看一次</span><span>2024-10-01 以後的資料留作樣本外測試期,每個模型系列只有一次乾淨的機會,建議設定定案後再看。</span></li>
    </ul>`;
  box.innerHTML = mine.length
    ? ZH`<section class="u-card mw-welcome" aria-labelledby="mwWelH">
        <h2 class="mw-title" id="mwWelH">從左邊選一個模型</h2>
        <p class="u-sub">選好之後可以調整權重、回測,也可以試算單一檔股票。H0 Lab 用這三個機制讓結果誠實:</p>
        ${honest}
      </section>`
    : ZH`<section class="u-card mw-welcome mw-empty-card" aria-labelledby="mwWelH">
        <span class="mw-empty-ic" aria-hidden="true">${ICON.model}</span>
        <h2 class="mw-title" id="mwWelH">建立你的第一個模型</h2>
        <p class="mw-empty-lead">用三個步驟自己選一個因子範本(沒有預設、不推薦)、看過權重並調整你在意的訊號,再存成你的模型。</p>
        <div class="mw-btns mw-empty-btns">
          <button type="button" class="u-btn is-primary" data-new>${ICON.plus}建立模型</button>
        </div>
        <h3 class="mw-h4">H0 Lab 怎麼讓結果誠實</h3>
        ${honest}
      </section>`;
  box.querySelectorAll("[data-new]").forEach((b) => (b.onclick = goWizard));
  box.querySelectorAll("[data-fork]").forEach((b) => (b.onclick = () => showFork(el, info, b.dataset.fork)));
}

async function archive(el, id) {
  if (!window.confirm(ZH`確定要封存「${id}」嗎?\n封存後清單裡不再顯示;檔案會移到私人資料夾的 models/_archive/,不會刪除。`)) return;
  const b = el.querySelector("#mwArchive");
  if (b) b.disabled = true;
  try {
    await API.archiveModel(id);
    if (ed?.model?.id === id) ed = null;
    if (location.hash === `#/models/${encodeURIComponent(id)}`) location.hash = "#/models";
    else render(el, el.__mwState || {}, null);   // 帶原本的 state,state.modelsInfo 才會更新
  } catch (e) { if (b) b.disabled = false; window.alert(ZH`封存失敗:${P(e.message)}`); }
}

function showFork(el, info, baseId) {
  const base = (info.models || []).find((m) => m.id === baseId);
  const box = el.querySelector("#mwFork");
  box.innerHTML = ZH`<form class="u-card mw-form" id="mwForkForm" novalidate>
    <h3 class="mw-h3">從「${esc(base ? API.shownModelName(base) : baseId)}」另存成我的模型</h3>
    <p class="u-sub">新模型是新的模型系列,嘗試次數從 1 開始算。</p>
    <label class="mw-field"><span>代號</span><input class="u-input" name="id" required maxlength="48" autocomplete="off" spellcheck="false" placeholder="例:my-quality-tilt" pattern="[a-z0-9][a-z0-9\\-]{0,47}">
      <span class="u-sub">小寫英文、數字、連字號,第一個字不能是連字號,最多 48 字;存檔後不能改。</span></label>
    <label class="mw-field"><span>名稱</span><input class="u-input" name="name" required maxlength="60" autocomplete="off" placeholder="例:我的品質加重版"></label>
    <p class="mw-err-txt" id="mwForkErr" role="alert"></p>
    <div class="mw-btns"><button type="submit" class="u-btn is-primary">建立</button><button type="button" class="u-btn" id="mwForkCancel">取消</button></div>
  </form>`;
  const f = box.querySelector("form");
  f.elements.namedItem("id").focus();
  box.scrollIntoView?.({ block: "nearest" });
  box.querySelector("#mwForkCancel").onclick = () => { box.innerHTML = ""; };
  f.onsubmit = async (e) => {
    e.preventDefault();
    const fv = (n) => f.elements.namedItem(n).value.trim();
    const id = fv("id"), name = fv("name");
    const err = box.querySelector("#mwForkErr");
    if (!API.MODEL_ID_RE.test(id)) { err.textContent = ZH`代號只能用小寫英文、數字、連字號(第一個字不能是連字號,最多 48 字)`; return; }
    if (!name) { err.textContent = ZH`請輸入名稱`; return; }
    if ((info.models || []).some((m) => m.id === id)) { err.textContent = ZH`這個代號已經有模型了,請換一個`; return; }
    if (!confirmLeave()) return;
    err.textContent = "";
    f.querySelector("[type=submit]").disabled = true;
    try {
      const j = await API.forkModel(baseId, id, name);
      ed = null;
      location.hash = `#/models/${encodeURIComponent(j.saved?.id || id)}`;
    } catch (ex) {
      leaveOK = false;   // 沒有離開成功:之後換頁仍要提醒未存檔的修改(M7 Verifier)
      err.textContent = ZH`另存失敗:${P(ex.message)}`;
      f.querySelector("[type=submit]").disabled = false;
    }
  };
}

// ---------- 編輯器 ----------
async function openEditor(el, info, id, token) {
  const box = el.querySelector("#mwEditor");
  const summary = (info.models || []).find((m) => m.id === id);
  // 同一個模型重畫(例如封存別的模型後)保留還沒存的修改
  if (!(ed && ed.model.id === id)) {
    box.innerHTML = ZH`<div class="loading"><div class="spinner"></div>讀取模型…</div>`;
    let model;
    try { model = await API.getModel(id); } catch (e) {
      if (token === renderToken) box.innerHTML = ZH`<div class="u-card mw-empty-card"><p class="mw-err-txt">讀取模型失敗:${esc(P(e.message))}</p><p class="u-sub">這個模型可能已經封存。從左邊清單選另一個模型。</p></div>`;
      return;
    }
    if (token !== renderToken || !el.querySelector("#mwEditor")) return;   // 等待期間換了模型或換頁
    ed = {
      model, savedJSON: JSON.stringify(model), readonly: summary ? summary.source === "shipped" : id === info.default,
      exprAvailable: info.expr_available === true, baseVars: info.base_vars || [],
      seq: 0, timer: null, valid: null, hash: summary?.score_hash || null, savedHash: summary?.score_hash || null,
      symbol: "", preview: null, previewError: "",
      nTrials: summary?.n_trials, universe: API.UNIVERSES[0][0], shippedNote: info.shipped_trials_note || "",
      baseName: baseName(info, model.based_on),
      bt: null, btError: "", btRunning: false, hoStatus: null, ho: null, hoError: "", hoRunning: false,
    };
  }
  renderEditor(el);
  scheduleValidate(el, 0);
  pickAvailableUniverse(el, ed);
}

// based_on 是「代號@設定指紋」;畫面上只顯示來源模型的名稱
function baseName(info, basedOn) {
  const id = String(basedOn || "").split("@")[0];
  const b = (info.models || []).find((x) => x.id === id);
  return b ? API.shownModelName(b) : id;
}

function renderEditor(el) {
  const box = el.querySelector("#mwEditor");
  const m = ed.model, ro = ed.readonly;
  const dis = ro ? " disabled" : "";
  box.innerHTML = ZH`
    <section class="mw-editor" aria-labelledby="mwEdTitle">
      <div class="mw-bar u-card">
        <div class="mw-bar-main">
          <span class="mw-kicker">${ro ? ZH`研究基準模型` : ZH`我的模型`}</span>
          <h2 class="mw-title" id="mwEdTitle">${esc(ro ? API.shownModelName({ ...m, source: "shipped" }) : m.name)}</h2>
          <div class="mw-badges" id="mwBadges">${badgesHTML()}</div>
          <div class="u-sub mw-meta">代號 <span class="u-num">${esc(m.id)}</span>${m.based_on ? ZH` · 從「${esc(ed.baseName)}」另存` : ""}</div>
          <div class="mw-status" id="mwStatus" role="status" aria-live="polite"></div>
        </div>
        <div class="mw-btns mw-bar-btns">
          ${ro ? ZH`<button type="button" class="u-btn is-primary" id="mwForkHere">另存成我的模型再改</button>`
            : ZH`<button type="button" class="u-btn" id="mwRevert">復原</button><button type="button" class="u-btn is-primary" id="mwSave">存檔</button>`}
        </div>
        ${ro ? "" : ZH`<div class="mw-bar-more"><button type="button" class="u-btn mw-quiet" id="mwForkHere">另存新模型</button><button type="button" class="u-btn mw-quiet mw-danger" id="mwArchive">封存</button></div>`}
      </div>
      ${ro ? ZH`<p class="u-card mw-note">${ICON.lock}<span>研究基準模型是唯讀的:它的設定只能經過開發時的正式檢定修改。你可以在這裡檢視、試算,或另存一份成我的模型再改。</span></p>` : ZH`
      <div class="u-card">
        <h3 class="mw-h3">名稱與說明</h3>
        <p class="u-sub">名稱、說明不影響分數,也不改設定指紋。</p>
        <label class="mw-field"><span>名稱</span><input class="u-input" id="mwName" maxlength="60" value="${esc(m.name)}"></label>
        <label class="mw-field"><span>說明</span><textarea class="u-input mw-ta" id="mwNotes" rows="2" maxlength="500">${esc(m.notes || "")}</textarea></label>
      </div>`}

      <div class="u-card mw-backtest" id="mwBacktest">${backtestHTML()}</div>

      <h2 class="u-label u-list-h">面向權重</h2>
      <p class="u-sub mw-hint">每個分數是各面向分數的加權平均;權重是相對大小,畫面上的百分比是佔總和的比例。權重 0 等於不使用這個面向。</p>
      <div class="u-grid mw-weights">${WEIGHT_GROUPS.map(([g, title]) => weightCard(g, title, dis)).join("")}</div>

      <div class="u-card mw-th">
        <h3 class="mw-h3">短期門檻</h3>
        <p class="u-sub">研究基準模型用來切短期分數等級的分界(下緣須小於上緣)。自訂模型不輸出短期分數等級,這兩個值目前只影響設定指紋,保留給之後的回測使用。</p>
        <div class="mw-inline">
          <label class="mw-field"><span>上緣</span><input class="u-input mw-num" type="number" step="1" data-th="bullish" value="${esc(m.short_term_thresholds?.bullish ?? "")}"${dis}></label>
          <label class="mw-field"><span>下緣</span><input class="u-input mw-num" type="number" step="1" data-th="bearish" value="${esc(m.short_term_thresholds?.bearish ?? "")}"${dis}></label>
        </div>
      </div>

      <h2 class="u-label u-list-h">指標</h2>
      <p class="u-sub mw-hint">數值的單位和原始指標相同(百分比用小數表示,0.25 = 25%)。線性打分:數值到「差」給 0 分、到「好」給 100 分,中間線性內插;「差」大於「好」代表越小越好。峰值打分:100 − |數值 − 中心| × 斜率。內建規則只能改權重與開關。</p>
      <div id="mwMetrics">${metricsHTML(dis)}</div>

      <h2 class="u-label u-list-h">自訂指標(運算式)</h2>
      <div id="mwExpr">${exprHTML()}</div>

      <h2 class="u-label u-list-h">試算</h2>
      <div class="u-card mw-preview">
        <p class="u-sub">用這個模型(含還沒存檔的修改)重算一檔股票。只會用已經打開過的報告,不會為了試算去抓資料。代號要完整,例如 2330.TW、NVDA。</p>
        <form class="mw-inline" id="mwPrevForm">
          <input class="u-input mw-sym" id="mwSym" autocomplete="off" spellcheck="false" maxlength="20" placeholder="例:2330.TW" aria-label="試算的股票代號" value="${esc(ed.symbol)}">
          <button type="submit" class="u-btn is-primary">試算</button>
        </form>
        ${recentSymbols().length ? ZH`<div class="mw-chips"><span class="u-sub">最近查詢:</span>${recentSymbols().map((r) => `<button type="button" class="u-btn" data-sym="${esc(r.symbol)}">${esc(r.name || r.symbol)} <span class="u-num">${esc(r.symbol)}</span></button>`).join("")}</div>` : ""}
        <div id="mwPrevOut" aria-live="polite"></div>
      </div>
    </section>`;
  bindEditor(el);
  renderStatus(el);
  renderPreview(el);
}

function weightCard(group, title, dis) {
  const ws = ed.model.pillar_weights?.[group] || {};
  const tot = Object.values(ws).reduce((a, b) => a + (isNum(b) ? b : 0), 0);
  return `<div class="u-card mw-wcard" data-group="${esc(group)}"><h3 class="mw-h3">${esc(title)}</h3>
    ${Object.entries(ws).map(([p, w]) => {
      const max = Math.max(100, isNum(w) ? Math.ceil(w) : 0);
      const share = tot > 0 && isNum(w) ? Math.round((w / tot) * 100) + "%" : "—";
      const id = `mw-w-${group}-${p}`;
      return `<div class="mw-wrow"><label for="${esc(id)}">${esc(PILLAR_LABEL[p] || p)}</label>
        <input type="range" id="${esc(id)}" min="0" max="${esc(max)}" step="1" value="${esc(isNum(w) ? w : 0)}" data-pw="${esc(group)}" data-p="${esc(p)}"${dis}>
        <span class="mw-wval u-num" data-wval="${esc(p)}">${esc(isNum(w) ? num(w, Number.isInteger(w) ? 0 : 1) : "—")}<span class="mw-share">${esc(share)}</span></span></div>`;
    }).join("")}
    ${tot > 0 ? "" : ZH`<p class="mw-err-txt">權重總和是 0,這個分數會算不出來。</p>`}
  </div>`;
}

function metricLabel(x) { return String(x.label || x.key).replace(/\{[^{}]*\}/g, "…"); }

function scoreCells(x, i, dis) {
  const sc = x.score || {};
  const inp = (f, v, lab) => `<label class="mw-sc"><span>${esc(lab)}</span><input class="u-input mw-num" type="number" step="any" data-m="${esc(i)}" data-f="${esc(f)}" value="${esc(isNum(v) ? v : "")}" aria-label="${esc(metricLabel(x) + " " + lab)}"${dis}></label>`;
  if (sc.type === "linear") return inp("bad", sc.bad, ZH`差`) + inp("good", sc.good, ZH`好`);
  if (sc.type === "peak") return inp("center", sc.center, ZH`中心`) + inp("slope", sc.slope, ZH`斜率`);
  if (sc.type === "map") {
    const t = Object.entries(sc.table || {}).sort((a, b) => Number(a[0]) - Number(b[0])).map(([k, v]) => `${k}→${v}`).join(" ");
    return ZH`<span class="u-sub">${esc(t)}(固定)</span>`;
  }
  return ZH`<span class="u-sub">內建規則,只能改權重與開關</span>`;
}

function metricsHTML(dis) {
  const ms = ed.model.metrics || [];
  return PILLARS.map((p) => {
    const rows = ms.map((x, i) => [x, i]).filter(([x]) => x.pillar === p);
    if (!rows.length) return "";
    const on = rows.filter(([x]) => x.enabled !== false).length;
    return ZH`<details class="u-card mw-mgroup" open><summary><b>${esc(PILLAR_LABEL[p])}</b><span class="u-sub">${esc(rows.length)} 個指標 · 開啟 ${esc(on)} 個</span></summary>
      <div class="tbl-wrap"><table class="mw-mtable">
        <thead><tr><th>使用</th><th>指標</th><th>打分</th><th>參數</th><th class="n">權重</th></tr></thead><tbody>
        ${rows.map(([x, i]) => {
          const isExpr = !!x.value?.expr;
          const tags = [APPLIES_TXT[x.applies || "all"], MARKET_TXT[x.market || "all"], x.peer_blend ? ZH`含同業比較` : "", isExpr ? ZH`自訂` : ""].filter(Boolean);
          return `<tr class="${x.enabled === false ? "is-off" : ""}">
            <td><input type="checkbox" data-m="${esc(i)}" data-f="enabled"${x.enabled !== false ? " checked" : ""} aria-label="${esc(ZH`使用 ` + metricLabel(x))}"${dis}></td>
            <td class="mw-mname"><span class="mw-mlabel">${esc(metricLabel(x))}</span>${tags.map((t) => `<span class="u-tag">${esc(t)}</span>`).join("")}
              <br><span class="u-sub u-num">${esc(x.key)}</span>${isExpr ? `<br><code class="mw-code">${esc(x.value.expr)}</code>` : ""}
              ${isExpr && !dis ? ZH`<br><button type="button" class="u-btn mw-danger mw-del" data-del="${esc(i)}">移除</button>` : ""}</td>
            <td>${esc(SCORE_TXT[x.score?.type] || x.score?.type || "")}</td>
            <td class="mw-scs">${scoreCells(x, i, dis)}</td>
            <td class="n"><input class="u-input mw-num mw-w" type="number" min="0" step="0.1" data-m="${esc(i)}" data-f="weight" value="${esc(isNum(x.weight) ? x.weight : "")}" aria-label="${esc(metricLabel(x) + ZH` 權重`)}"${dis}></td>
          </tr>`;
        }).join("")}
        </tbody></table></div></details>`;
  }).join("");
}

function exprHTML() {
  if (!ed.exprAvailable) {
    return ZH`<div class="u-card mw-note"><b>運算式語言開發中</b><p class="u-sub">之後可以用基礎變數寫自己的指標(例如 <span class="u-num">safe_div(fcf_yield, pb)</span>)。目前還不能新增,其他設定都可以先調。</p>
      <button type="button" class="u-btn" disabled aria-disabled="true">新增自訂指標</button></div>`;
  }
  if (ed.readonly) return ZH`<p class="u-sub mw-empty">研究基準模型不能新增指標;另存成我的模型後就可以。</p>`;
  const vars = ed.baseVars;
  return ZH`<form class="u-card mw-form" id="mwExprForm" novalidate>
    <p class="u-sub">只能用下方的基礎變數、四則運算、比較、<span class="u-num">abs、min、max、log、sqrt、clip、safe_div、coalesce</span>。任何輸入沒有資料時,這個指標就是「不可得」。自訂指標用線性打分。</p>
    <div class="mw-inline">
      <label class="mw-field"><span>代號</span><input class="u-input" name="key" maxlength="40" autocomplete="off" spellcheck="false" placeholder="例:fcf_to_pb"></label>
      <label class="mw-field"><span>名稱</span><input class="u-input" name="label" maxlength="80" autocomplete="off" placeholder="例:自由現金流殖利率 ÷ 股價淨值比"></label>
    </div>
    <div class="mw-inline">
      <label class="mw-field"><span>面向</span><select class="u-input" name="pillar">${PILLARS.map((p) => `<option value="${esc(p)}">${esc(PILLAR_LABEL[p])}</option>`).join("")}</select></label>
      <label class="mw-field"><span>適用公司</span><select class="u-input" name="applies"><option value="all">全部</option><option value="non_financial">非金融</option><option value="financial">金融</option></select></label>
      <label class="mw-field"><span>市場</span><select class="u-input" name="market"><option value="all">全部</option><option value="TW">台股</option><option value="US">美股</option></select></label>
    </div>
    <label class="mw-field"><span>運算式</span><textarea class="u-input mw-ta mw-code" name="expr" rows="2" maxlength="500" spellcheck="false" placeholder="例:safe_div(fcf_yield, pb)"></textarea></label>
    <div class="mw-vars"><input class="u-input mw-varq" id="mwVarQ" placeholder="篩選基礎變數…" aria-label="篩選基礎變數" autocomplete="off">
      <div class="mw-varlist" id="mwVarList">${vars.map((v) => `<button type="button" class="u-btn mw-var" data-var="${esc(v)}">${esc(v)}</button>`).join("")}</div></div>
    <div class="mw-inline">
      <label class="mw-field"><span>差(0 分)</span><input class="u-input mw-num" type="number" step="any" name="bad" value="0"></label>
      <label class="mw-field"><span>好(100 分)</span><input class="u-input mw-num" type="number" step="any" name="good" value="1"></label>
      <label class="mw-field"><span>權重</span><input class="u-input mw-num" type="number" step="0.1" min="0" name="weight" value="1"></label>
    </div>
    <p class="mw-err-txt" id="mwExprErr" role="alert"></p>
    <div class="mw-btns"><button type="submit" class="u-btn is-primary">加入模型</button></div>
  </form>`;
}

function bindEditor(el) {
  const box = el.querySelector("#mwEditor");
  const m = ed.model;
  box.querySelector("#mwForkHere")?.addEventListener("click", () => showFork(el, el.__mwInfo || { models: [] }, m.id));
  box.querySelector("#mwArchive")?.addEventListener("click", () => archive(el, m.id));
  box.querySelector("#mwName")?.addEventListener("input", (e) => { m.name = e.target.value; renderStatus(el); });
  box.querySelector("#mwNotes")?.addEventListener("input", (e) => { m.notes = e.target.value; renderStatus(el); });
  box.querySelector("#mwSave")?.addEventListener("click", () => save(el));
  box.querySelector("#mwRevert")?.addEventListener("click", () => {
    if (!isDirty() || !window.confirm(ZH`放棄所有還沒存檔的修改?`)) return;
    ed.model = JSON.parse(ed.savedJSON);
    renderEditor(el); scheduleValidate(el, 0);
  });
  // 面向權重
  box.querySelectorAll("input[data-pw]").forEach((inp) => inp.addEventListener("input", () => {
    const g = inp.dataset.pw;
    m.pillar_weights[g][inp.dataset.p] = Number(inp.value);
    const card = box.querySelector(`.mw-wcard[data-group="${g}"]`);
    // 只更新數字(不重畫整張卡,拖動中的滑桿才不會失去焦點)
    const ws = m.pillar_weights[g];
    const tot = Object.values(ws).reduce((a, b) => a + (isNum(b) ? b : 0), 0);
    card.querySelectorAll("[data-wval]").forEach((s) => {
      const w = ws[s.dataset.wval];
      s.innerHTML = `${esc(num(w, Number.isInteger(w) ? 0 : 1))}<span class="mw-share">${esc(tot > 0 ? Math.round((w / tot) * 100) + "%" : "—")}</span>`;
    });
    let warn = card.querySelector(".mw-err-txt");
    if (tot > 0 && warn) warn.remove();
    if (tot <= 0 && !warn) { card.insertAdjacentHTML("beforeend", ZH`<p class="mw-err-txt">權重總和是 0,這個分數會算不出來。</p>`); }
    changed(el);
  }));
  // 短期門檻
  box.querySelectorAll("input[data-th]").forEach((inp) => inp.addEventListener("input", () => {
    m.short_term_thresholds[inp.dataset.th] = parseNum(inp.value);
    changed(el);
  }));
  bindMetrics(el);
  bindExpr(el);
  // 試算
  const form = box.querySelector("#mwPrevForm");
  form.onsubmit = (e) => { e.preventDefault(); ed.symbol = box.querySelector("#mwSym").value.trim().toUpperCase(); scheduleValidate(el, 0); };
  box.querySelectorAll("[data-sym]").forEach((b) => (b.onclick = () => {
    ed.symbol = b.dataset.sym.toUpperCase(); box.querySelector("#mwSym").value = ed.symbol; scheduleValidate(el, 0);
  }));
  bindBacktest(el);
}

function parseNum(s) {
  if (String(s).trim() === "") return null;
  const v = Number(s);
  return Number.isFinite(v) ? v : null;
}

function bindMetrics(el) {
  const box = el.querySelector("#mwMetrics");
  const ms = ed.model.metrics;
  box.querySelectorAll("input[data-m]").forEach((inp) => inp.addEventListener(inp.type === "checkbox" ? "change" : "input", () => {
    const x = ms[Number(inp.dataset.m)];
    if (!x) return;
    const f = inp.dataset.f;
    if (f === "enabled") {
      x.enabled = inp.checked;
      inp.closest("tr").classList.toggle("is-off", !inp.checked);
      const det = inp.closest("details");
      const rows = ms.filter((y) => y.pillar === x.pillar);
      det.querySelector("summary .u-sub").textContent = ZH`${rows.length} 個指標 · 開啟 ${rows.filter((y) => y.enabled !== false).length} 個`;
    } else if (f === "weight") {
      x.weight = parseNum(inp.value);
    } else if (["bad", "good", "center", "slope"].includes(f)) {
      // builtin 只能改權重與開關(表格不提供它的參數欄,這裡再擋一次)
      if (x.score?.type === "builtin") return;
      x.score[f] = parseNum(inp.value);
    }
    changed(el);
  }));
  box.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => {
    const i = Number(b.dataset.del);
    const x = ms[i];
    if (!x?.value?.expr) return;   // 只能移除自訂指標
    if (!window.confirm(ZH`從模型移除自訂指標「${metricLabel(x)}」?`)) return;
    ms.splice(i, 1);
    rerenderMetrics(el);
    changed(el);
  }));
}

function rerenderMetrics(el) {
  el.querySelector("#mwMetrics").innerHTML = metricsHTML(ed.readonly ? " disabled" : "");
  bindMetrics(el);
}

function bindExpr(el) {
  const f = el.querySelector("#mwExprForm");
  if (!f) return;
  const fe = (n) => f.elements.namedItem(n);
  const ta = fe("expr");
  f.querySelectorAll("[data-var]").forEach((b) => (b.onclick = () => {
    const v = b.dataset.var;
    const s = ta.selectionStart ?? ta.value.length, e2 = ta.selectionEnd ?? ta.value.length;
    ta.value = ta.value.slice(0, s) + v + ta.value.slice(e2);
    ta.focus(); ta.selectionStart = ta.selectionEnd = s + v.length;
  }));
  f.querySelector("#mwVarQ").addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    f.querySelectorAll("[data-var]").forEach((b) => { b.hidden = !!q && !b.dataset.var.toLowerCase().includes(q); });
  });
  f.onsubmit = async (e) => {
    e.preventDefault();
    const my = ed;
    const err = f.querySelector("#mwExprErr");
    const v = (n) => fe(n).value.trim();
    const key = v("key"), label = v("label"), expr = v("expr");
    const pillar = v("pillar"), applies = v("applies"), market = v("market");
    const bad = parseNum(v("bad")), good = parseNum(v("good")), weight = parseNum(v("weight"));
    if (!API.METRIC_KEY_RE.test(key)) { err.textContent = ZH`代號只能用小寫英文、數字、底線(最多 40 字)`; return; }
    if (!label) { err.textContent = ZH`請輸入名稱`; return; }
    if (/[{}]/.test(label)) { err.textContent = ZH`名稱不能有大括號`; return; }
    if (!expr) { err.textContent = ZH`請輸入運算式`; return; }
    if (bad === null || good === null) { err.textContent = ZH`「差」與「好」都要是數字`; return; }
    if (weight === null || weight < 0) { err.textContent = ZH`權重必須是 ≥ 0 的數字`; return; }
    const metric = { key, pillar, applies, market, label, value: { expr }, score: { type: "linear", bad, good }, weight,
      fmt: "num", note: ZH`自訂指標`, peer_blend: null, requires: null, gate: null, enabled: true };
    const cand = { ...my.model, metrics: [...my.model.metrics, metric] };
    err.textContent = ZH`檢查中…`;
    try {
      const j = await API.validateModel(cand);
      if (!j.ok) { err.textContent = P(j.error) || ZH`格式不正確`; return; }
    } catch (ex) { if (ed === my) err.textContent = ZH`檢查失敗:${ex.message}`; return; }
    if (ed !== my || !f.isConnected) return;   // 檢查期間換了模型:不加進別的模型
    err.textContent = "";
    my.model.metrics.push(metric);
    f.reset();
    rerenderMetrics(el);
    changed(el);
  };
}

// ---------- 驗證、試算、存檔 ----------
function changed(el) { leaveOK = false; renderStatus(el); scheduleValidate(el, DEBOUNCE_MS); }

function scheduleValidate(el, delay) {
  clearTimeout(ed.timer);
  const my = ed;
  my.seq++;            // 還在路上的舊請求一律作廢(runValidate 會比對序號),舊回應不會蓋掉「驗證中…」
  my.valid = null;
  renderStatus(el);
  my.timer = setTimeout(() => runValidate(el, my), delay);
}

async function runValidate(el, my) {
  if (ed !== my) return;
  const seq = ++my.seq;
  const sym = my.symbol;
  let j;
  try { j = await API.validateModel(my.model, sym || undefined); } catch (e) { j = { ok: false, error: ZH`驗證失敗:${e.message}`, net: true }; }
  if (ed !== my || seq !== my.seq || !el.querySelector("#mwEditor")) return;   // 有更新的請求或已換頁
  my.valid = j.ok === true;
  my.error = j.ok ? "" : (P(j.error) || ZH`格式不正確`);
  if (j.ok) {
    my.hash = j.score_hash;
    // 沒有修改時,這份就是存檔版本:以伺服器剛算的雜湊為準(評分雜湊已不受 JSON 數字格式 0.0 / 0 影響,這裡只是保險)
    if (!isDirty()) my.savedHash = j.score_hash;
  }
  if (sym) {
    my.preview = j.ok ? (j.preview || null) : null;
    my.previewError = j.ok ? P(j.preview_error || "") : ZH`模型格式有錯誤,修正後才能試算`;
    my.previewSym = sym;
  }
  renderStatus(el);
  renderPreview(el);
}

function renderStatus(el) {
  const s = el.querySelector("#mwStatus");
  if (!s || !ed) return;
  const dirty = isDirty();
  let html;
  if (ed.valid === null) html = ZH`<span class="u-sub">驗證中…</span>`;
  else if (ed.valid) html = ZH`<span class="mw-ok">設定沒有問題</span><span class="u-sub" title="同一組設定會得到同一個指紋,用來計算嘗試次數">設定指紋 <span class="u-num">${esc(ed.hash || "—")}</span>${ed.savedHash && ed.hash !== ed.savedHash ? ZH`(存檔版本 <span class="u-num">${esc(ed.savedHash)}</span>)` : ""}</span>`;
  else html = `<span class="mw-err-txt">${esc(ed.error)}</span>`;   // #mwStatus 已是 role=status,不再加 role=alert(避免唸兩次)
  if (!ed.readonly) html += dirty ? ZH`<span class="mw-dirty">尚未存檔</span>` : ZH`<span class="u-sub">已存檔</span>`;
  s.innerHTML = html;
  const btn = el.querySelector("#mwSave");
  if (btn) btn.disabled = !dirty || ed.valid !== true;
  const rv = el.querySelector("#mwRevert");
  if (rv) rv.disabled = !dirty;
  syncBacktestControls(el);   // 有沒存檔的修改時,回測與 holdout 停用
}

function renderPreview(el) {
  const out = el.querySelector("#mwPrevOut");
  if (!out || !ed) return;
  if (!ed.previewSym) { out.innerHTML = ""; return; }
  if (ed.previewError) {
    const needReport = /報告/.test(ed.previewError);
    out.innerHTML = `<p class="mw-err-txt">${esc(ed.previewSym)}:${esc(ed.previewError)}</p>`
      + (needReport ? ZH`<button type="button" class="u-btn" id="mwOpenReport">打開 ${esc(ed.previewSym)} 的報告</button>` : "");
    out.querySelector("#mwOpenReport")?.addEventListener("click", () => {
      if (!confirmLeave()) return;
      location.hash = `#/s/${encodeURIComponent(ed.previewSym)}/overview`;
    });
    return;
  }
  if (!ed.preview) { out.innerHTML = ed.valid === null ? ZH`<p class="u-sub">試算中…</p>` : ""; return; }
  out.innerHTML = `<h3 class="mw-h3">${esc(ed.previewSym)} · ${esc(ed.model.name)}</h3>${API.customScoreHTML(ed.preview)}`;
}

async function save(el) {
  const btn = el.querySelector("#mwSave");
  if (!ed || ed.readonly || ed.valid !== true) return;
  btn.disabled = true;
  const my = ed;
  // 送出當下的快照:存檔請求進行中又改的部分不能被標成「已存檔」(Verifier:否則修改會無聲遺失)
  const snap = JSON.stringify(my.model);
  try {
    const j = await API.saveModel(JSON.parse(snap));
    if (ed !== my) return;
    my.savedJSON = snap;
    my.savedHash = j.saved?.score_hash || my.hash;
    await refreshList(el);   // 清單上的名稱、雜湊、更新日一起更新
    if (ed !== my) return;
    renderStatus(el);
    updateBacktest(el);   // 舊的回測結果標「存檔前那個版本」
    const t = el.querySelector("#mwEdTitle");
    if (t) t.textContent = my.model.name;
  } catch (e) {
    if (ed !== my) return;
    renderStatus(el);
    el.querySelector("#mwStatus")?.insertAdjacentHTML("beforeend", ZH`<span class="mw-err-txt">存檔失敗:${esc(P(e.message))}</span>`);
  }
}

// ---------- 回測驗證(開發期)與樣本外測試 ----------
// 回測與樣本外測試都用伺服器上存檔的版本,所以有沒存檔的修改時按鈕停用。隨機對照組只給分布,不給判定字眼。
function trialsText(n) {
  return isNum(n) && n > 0 ? ZH`已嘗試 ${n} 個設定` : ZH`還沒有回測紀錄`;
}
const fx = (v) => (isNum(v) ? num(v, 3) : "—");
const fci = (ci) => (Array.isArray(ci) && isNum(ci[0]) && isNum(ci[1]) ? `${num(ci[0], 3)} ~ ${num(ci[1], 3)}` : "—");
const fp = (p) => (isNum(p) ? (p < 0.001 ? "< 0.001" : num(p, 3)) : "—");
const fpct = (v) => (isNum(v) ? `${Math.round(v * 100)}%` : "—");
const universeName = (u) => (API.UNIVERSES.find(([k]) => k === u) || [u, u])[1];
const busyHTML = (what) => ZH`<p class="mw-busy" role="status"><span class="mw-spin" aria-hidden="true"></span>${esc(what)}計算中…大約要 15–20 秒</p>`;

// 誠實機制的狀態徽章(編輯器頂端):未驗證、嘗試次數、樣本外測試看過沒。都是狀態,不是錯誤。
function holdoutState() {
  if (ed.bt && btStale()) return null;
  return ed.hoStatus || ed.bt?.holdout || null;
}
function badgesHTML() {
  if (ed.readonly) return ZH`<span class="mw-chip">${ICON.lock}研究基準模型、唯讀</span>`;
  const st = holdoutState();
  const ho = !st ? ZH`<span class="mw-chip" title="跑一次開發期回測後會顯示這個設定看過樣本外測試沒有">${ICON.eye}樣本外:回測後顯示</span>`
    : st.clean ? ZH`<span class="mw-chip is-ok" title="${esc(P(st.label))}">${ICON.eye}樣本外:還沒看過</span>`
      : ZH`<span class="mw-chip is-seen" title="${esc(P(st.label))}">${ICON.eye}樣本外:已看過</span>`;
  return ZH`<span class="mw-chip is-unval" title="沒有經過驗證;只顯示分數與等級">${esc(API.UNVALIDATED)}</span>`
    + ZH`<span class="mw-chip" id="mwTrials" title="每回測一個新設定就記一次;次數越多,統計檢定的校正越嚴格">${ICON.count}${esc(trialsText(ed.nTrials))}</span>${ho}`;
}
function renderBadges(el) {
  const b = el.querySelector("#mwBadges");
  if (b && ed) b.innerHTML = badgesHTML();
}

function backtestHTML() {
  if (ed.readonly) {
    return ZH`<h3 class="mw-h3">${ICON.flask}回測驗證</h3>
      <p class="u-sub">研究基準模型的回測與樣本外測試已經在開發時用正式流程做完,不在工作台重跑;開發期的結果在「研究紀錄」頁,樣本外成績不在網站顯示。另存成我的模型之後,就可以在這裡用開發期回測,和研究基準模型比較。</p>
      ${ed.shippedNote ? `<p class="mw-state">${ICON.count}${esc(P(ed.shippedNote))}</p>` : ""}`;
  }
  const busy = ed.btRunning || ed.hoRunning;
  return ZH`<h3 class="mw-h3">${ICON.flask}回測驗證 <span class="u-tag">只用開發期</span></h3>
    <p class="u-sub">用<b>存檔的版本</b>重算股票池過去每個月的分數,和研究基準模型、隨機排序對照組比較。只用 2024-10-01 以前的開發期;每個新的設定都會算進嘗試次數。</p>
    <div class="mw-inline">
      <label class="mw-field"><span>股票池</span><select class="u-input" id="mwUniverse"${busy ? " disabled" : ""}>${API.UNIVERSES.map(([k, t]) => `<option value="${esc(k)}"${k === ed.universe ? " selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
      <div class="mw-btns mw-bt-btns"><button type="button" class="u-btn is-primary" id="mwBtRun">${ed.btRunning ? ZH`計算中…` : ZH`跑開發期回測`}</button></div>
    </div>
    ${Setup.survivorshipNoteHTML()}
    <p class="mw-dirty mw-bt-hint" id="mwBtHint"></p>
    <div id="mwBtOut" aria-live="polite">${btOutHTML()}</div>
    <div class="mw-ho">
      <h4 class="mw-h4">${ICON.eye}樣本外測試(2024-10-01 起)</h4>
      <div id="mwHoStatus">${holdoutStatusHTML()}</div>
      <p class="u-sub">每個模型系列只有一次乾淨的樣本外測試;看過之後,這個模型系列與相關的模型系列,新版本都會標「已看過」。樣本外測試不和研究基準模型比較。建議在開發期把設定定案之後再看。</p>
      <div class="mw-btns"><button type="button" class="u-btn" id="mwHoRun">${ed.hoRunning ? ZH`計算中…` : ZH`看樣本外測試…`}</button></div>
      <div id="mwHoOut" aria-live="polite">${hoOutHTML()}</div>
    </div>`;
}

function holdoutStatusHTML() {
  if (ed.bt && btStale()) return ZH`<p class="mw-state">樣本外狀態:模型改過了,重跑開發期回測後會更新。</p>`;
  const st = ed.hoStatus || ed.bt?.holdout;   // hoStatus:這次看過樣本外之後的狀態(比回測當下的快照新)
  if (!st) return ZH`<p class="mw-state">樣本外狀態:跑一次開發期回測後會顯示(還沒看過 / 已看過)。</p>`;
  return ZH`<p class="mw-state ${st.clean ? "is-ok" : "is-seen"}">樣本外狀態:${esc(P(st.label))}</p>`
    + (st.viewed ? ZH`<p class="u-sub">這個設定已經看過樣本外測試;再看只會回傳當時的紀錄,不會重算。</p>` : "");
}

function btOutHTML() {
  if (ed.btRunning) return busyHTML(ZH`開發期回測`);
  // 回測資料還沒建立:換成第一次執行引導的說明卡(和精靈同一張),不顯示內部檔名當錯誤
  if (ed.btError && Setup.isShippedModel(ed.btError)) return Setup.shippedModelHTML();
  if (ed.btError && Setup.isResearchMissing(ed.btError)) return Setup.researchMissingHTML(null, ed.universe.startsWith("us_") ? "US" : "TW", ed.btError, false, ed.universe);
  if (ed.btError) return ZH`<p class="mw-err-txt" role="alert">回測沒有完成:${esc(P(ed.btError))}</p>`;
  if (!ed.bt) return "";
  return backtestResultHTML(ed.bt, btStale());
}

const btStale = () => !!(ed.btForHash && ed.savedHash && ed.btForHash !== ed.savedHash);

// 開發期回測結果(純函式,tests/test_web_models_static.py 用 node 實際執行)。stale = 模型存檔後雜湊變了
export function backtestResultHTML(r, stale = false) {
  if (r.status !== ZH`開發集`) {
    return `<div class="mw-bt-res"><p class="mw-err-txt">${esc(P(r.status) || API.UNVALIDATED)}:${esc(P(r.reason || ""))}</p>
      <p class="mw-disc">${esc(P(r.disclaimer || ZH`回測結果不代表未來報酬,非投資建議`))}</p></div>`;
  }
  const n = r.n_trials;
  const lt = r.metrics?.lt_12m?.mine;
  return ZH`<div class="mw-bt-res">
    ${stale ? ZH`<p class="mw-dirty">這是存檔前那個版本的結果;模型已經改過,請重跑。</p>` : ""}
    ${lt?.verdict ? ZH`<p class="mw-bt-sum">長期分數(持有 12 個月)在開發期的結論:<b>${esc(P(lt.verdict))}</b></p>` : ""}
    <div class="mw-bt-head"><span class="u-tag">只用開發期</span><span class="u-sub">${esc(universeName(r.universe))} · 設定指紋 <span class="u-num">${esc(r.score_hash || "—")}</span>${isNum(r.seconds) ? ZH` · 計算 ${esc(num(r.seconds, 1))} 秒` : ""}</span></div>
    <div class="mw-trials-box">
      <p class="mw-trials-n">${ICON.count}${esc(trialsText(n))}</p>
      <p class="u-sub">${esc(P(r.multi_note || ""))}。${isNum(n) && n >= 2 ? ZH`「Bonferroni 後的 p」= p × ${esc(n)}(上限 1),判定用校正後的 p。` : ZH`目前只試了 1 個設定,校正後的 p 和原本相同。`}</p>
      ${r.shipped_note ? `<p class="u-sub">${esc(P(r.shipped_note))}</p>` : ""}
    </div>
    ${Object.keys(METRIC_TXT).filter((k) => r.metrics?.[k]).map((k) => metricBlockHTML(k, r.metrics[k], true)).join("")}
    <p class="mw-disc">${esc(r.disclaimer || ZH`回測結果不代表未來報酬,非投資建議`)}</p>
  </div>`;
}

function hoOutHTML() {
  if (ed.hoRunning) return busyHTML(ZH`樣本外測試`);
  if (ed.hoError && Setup.isShippedModel(ed.hoError)) return Setup.shippedModelHTML();
  const market = ed.universe.startsWith("us_") ? "US" : "TW";
  // 兩邊都缺資料:上面已經有說明,不重複。建立資料卡用一行帶過;要完整資料的情況上面那段就夠了,也不能叫人去建資料
  if (ed.hoError && Setup.isResearchMissing(ed.hoError) && ed.btError && Setup.isResearchMissing(ed.btError)) return Setup.needsFullData(null, market, ed.universe) ? "" : ZH`<p class="u-sub">回測資料還沒建立,請照上面的說明做。</p>`;   // 兩張完整卡的進度只會更新第一張
  if (ed.hoError && Setup.isResearchMissing(ed.hoError)) return Setup.researchMissingHTML(null, ed.universe.startsWith("us_") ? "US" : "TW", ed.hoError, false, ed.universe);
  if (ed.hoError) return ZH`<p class="mw-state" role="alert">樣本外測試沒有執行:${esc(P(ed.hoError))}</p>`;
  return ed.ho ? holdoutResultHTML(ed.ho) : "";
}

export function holdoutResultHTML(r) {
  return ZH`<div class="mw-bt-res">
    <div class="mw-bt-head"><span class="u-tag">樣本外測試</span><b class="${r.clean ? "mw-ok" : "mw-seen"}">${esc(P(r.label || ""))}</b><span class="u-sub">${esc(universeName(r.universe))} · 設定指紋 <span class="u-num">${esc(r.score_hash || "—")}</span></span></div>
    ${r.repeat ? ZH`<p class="u-sub">這個設定之前已經看過,這裡是當時的紀錄。</p>` : ""}
    ${Object.keys(METRIC_TXT).filter((k) => r.metrics?.[k]).map((k) => metricBlockHTML(k, r.metrics[k], false)).join("")}
    ${r.note ? `<p class="u-sub">${esc(P(r.note))}</p>` : ""}
    <p class="mw-disc">${esc(r.disclaimer || ZH`回測結果不代表未來報酬,非投資建議`)}</p>
  </div>`;
}

function metricBlockHTML(k, b, withShipped) {
  const mine = b.mine || {};
  const row = (label, x, isMine) => (x ? `<tr><th scope="row">${esc(label)}</th><td class="n">${esc(fx(x.ic))}</td><td class="n">${esc(fci(x.ci95))}</td>`
    + `<td class="n">${esc(fp(x.p))}</td><td class="n">${esc(isMine ? fp(x.p_bonferroni) : "—")}</td><td>${esc(P(x.verdict) || "—")}</td></tr>` : "");
  const pv = withShipped ? b.mine_vs_shipped : null;
  const cov = pv?.coverage;
  const span = mine.first_month && mine.last_month
    ? ZH`,${esc(String(mine.first_month).slice(0, 7))} ~ ${esc(String(mine.last_month).slice(0, 7))},${esc(mine.n_months)} 個月` : "";
  return ZH`<section class="mw-bt-m">
    <h4 class="mw-h4">${esc(METRIC_TXT[k] || k)}</h4>
    <p class="u-sub">月 IC(分數排名和之後報酬排名的相關係數;正的代表分數高的之後表現較好)${span}</p>
    <div class="tbl-wrap"><table class="mw-bt-table"><thead><tr><th>組別</th><th class="n">IC</th><th class="n">95% 區間</th><th class="n">p</th><th class="n">Bonferroni<br>後的 p</th><th>判定</th></tr></thead><tbody>
      ${row(ZH`我的模型`, b.mine, true)}${withShipped ? row(ZH`研究基準模型`, b.shipped, false) : ""}
    </tbody></table></div>
    ${pv ? ZH`<p class="mw-bt-line"><b>我的模型 − 研究基準模型</b>:IC 差 <span class="u-num">${esc(fx(pv.diff))}</span>(95% 區間 <span class="u-num">${esc(fci(pv.ci95))}</span>,${esc(pv.n_months)} 個月)→ <b>${esc(P(pv.verdict) || "—")}</b>`
      + (cov ? ZH`<br><span class="u-sub">有判斷的比例:我的模型 ${esc(fpct(cov.coverage_new))}、研究基準模型 ${esc(fpct(cov.coverage_base))};配對只比兩邊都有判斷的 ${esc(cov.n_both)} 筆</span>` : "") + `</p>` : ""}
    ${randomHTML(b.random || {})}
  </section>`;
}

function randomHTML(rd) {
  if (!rd.draws) return ZH`<p class="mw-bt-line"><b>隨機排序對照組</b>:算不出來(樣本不足)。</p>`;
  return ZH`<p class="mw-bt-line"><b>隨機排序對照組</b>(打亂 ${esc(rd.draws)} 次):IC 中位數 <span class="u-num">${esc(fx(rd.median))}</span>,`
    + ZH`2.5% ~ 97.5% 分位 <span class="u-num">${esc(fx(rd.p2_5))} ~ ${esc(fx(rd.p97_5))}</span>`
    + `${isNum(rd.mine_share_above) ? ZH`;我的模型的 IC 贏過其中 ${esc(fpct(rd.mine_share_above))}` : ""}。`
    + ZH`<br><span class="u-sub">${esc(P(rd.note) || ZH`這裡只給分布`)},不做判定。</span></p>`;
}

function bindBacktest(el) {
  const box = el.querySelector("#mwBacktest");
  if (!box || !ed || ed.readonly) return;
  Setup.bindCopy(box);   // 綁在這張卡:整頁重畫後引導卡還在,複製要繼續能用;不綁 #app,它跨頁保留,別頁的按鈕會觸發兩次
  box.querySelector("#mwUniverse").addEventListener("change", (e) => { ed.universe = e.target.value; ed.universeTouched = true; });
  box.querySelector("#mwBtRun").addEventListener("click", () => runBacktest(el));
  box.querySelector("#mwHoRun").addEventListener("click", () => runHoldout(el));
  syncBacktestControls(el);
}

// 只更新結果與按鈕(不重畫整張卡,live region 才唸得出結果)
function updateBacktest(el) {
  const box = el.querySelector("#mwBacktest");
  if (!box || !ed || ed.readonly) return;
  const set = (sel, html) => { const n = box.querySelector(sel); if (n) n.innerHTML = html; };
  set("#mwBtOut", btOutHTML());
  set("#mwHoStatus", holdoutStatusHTML());
  set("#mwHoOut", hoOutHTML());
  box.querySelector("#mwBtRun").textContent = ed.btRunning ? ZH`計算中…` : ZH`跑開發期回測`;
  box.querySelector("#mwHoRun").textContent = ed.hoRunning ? ZH`計算中…` : ZH`看樣本外測試…`;
  renderBadges(el);
  syncBacktestControls(el);
}

function syncBacktestControls(el) {
  if (!ed || ed.readonly) return;
  const busy = ed.btRunning || ed.hoRunning;
  const dirty = isDirty();
  for (const id of ["#mwBtRun", "#mwHoRun"]) {
    const b = el.querySelector(id);
    if (b) { b.disabled = busy || dirty; b.setAttribute("aria-busy", String(busy)); }
  }
  const u = el.querySelector("#mwUniverse");
  if (u) u.disabled = busy;
  const hint = el.querySelector("#mwBtHint");
  if (hint) hint.textContent = dirty ? ZH`有還沒存檔的修改:回測和樣本外測試用的是存檔的版本,請先存檔。` : "";
}

// 每次進入工作台對照最新狀態。資料可能在別頁建好了:
// - 舊的「還沒有資料」卡片要清掉(這個股票池有資料了,或使用者沒自己選過、等一下會改選)
// - 新使用者的快速資料沒有預設的股票池:改選有資料的(和精靈一樣),不然回測永遠回「請建立資料」;
//   每個編輯狀態只選一次,使用者動過或已經有結果就不改(重新進入不能蓋掉選擇)
async function pickAvailableUniverse(el, my) {
  const have = Setup.availableUniverses(await Setup.refreshStatus());
  if (!have.length) return;   // 還沒有資料或狀態沒拿到:等下次進來再選
  if (ed !== my || my.btRunning || my.hoRunning) return;
  const stale = (err) => err && Setup.isResearchMissing(err) && (have.includes(my.universe) || !my.universeTouched);
  const clearBt = stale(my.btError), clearHo = stale(my.hoError);
  if (clearBt) my.btError = "";
  if (clearHo) my.hoError = "";
  if (!my.universePicked) {
    my.universePicked = true;
    if (!my.universeTouched && !have.includes(my.universe) && !my.bt && !my.btError && !my.ho && !my.hoError) {
      my.universe = API.UNIVERSES.map(([k]) => k).find((k) => have.includes(k)) || my.universe;
      const sel = el.querySelector("#mwUniverse");
      if (sel) sel.value = my.universe;
    }
  }
  if (clearBt || clearHo) updateBacktest(el);
}

// 引導卡要知道是不是 App 版(按鈕或終端機指令),畫之前先拿最新狀態
async function researchCardReady(msg) {
  if (Setup.isResearchMissing(msg)) await Setup.refreshStatus();
}

async function runBacktest(el) {
  if (!ed || ed.readonly || ed.btRunning || ed.hoRunning || isDirty()) return;
  const my = ed;
  const forHash = my.savedHash;   // 回測用的是按下時的存檔版本(計算中仍可能再存檔,M7 Verifier)
  my.btRunning = true; my.btError = ""; my.bt = null;
  updateBacktest(el);
  try {
    my.bt = await API.backtestModel(my.model.id, my.universe);
    my.btForHash = forHash;
    my.hoStatus = null;   // 回測回應帶最新的 holdout 狀態
  } catch (e) { my.btError = e.message; await researchCardReady(e.message); }
  my.btRunning = false;
  if (ed !== my || !el.querySelector("#mwBacktest")) {   // 已換頁或換模型:嘗試次數仍要更新到 state.modelsInfo
    if (my.bt) await refreshList(el);
    return;
  }
  if (isNum(my.bt?.n_trials)) my.nTrials = my.bt.n_trials;
  updateBacktest(el);   // 結果、按鈕與頂端徽章(嘗試次數、樣本外狀態)一起更新
  await refreshList(el);   // 清單上的嘗試次數一起更新
}

async function runHoldout(el) {
  if (!ed || ed.readonly || ed.btRunning || ed.hoRunning || isDirty()) return;
  if (!window.confirm(HOLDOUT_CONFIRM)) return;
  const my = ed;
  my.hoRunning = true; my.hoError = ""; my.ho = null;
  updateBacktest(el);
  try {
    my.ho = await API.holdoutModel(my.model.id, my.universe);
    my.hoStatus = { viewed: true, family_viewed: true, clean: false, label: ZH`已看過,不是乾淨的樣本外` };
  } catch (e) { my.hoError = e.message; await researchCardReady(e.message); }
  my.hoRunning = false;
  if (ed !== my || !el.querySelector("#mwBacktest")) return;
  updateBacktest(el);
}

// 重抓模型清單:畫面上的清單、el.__mwInfo、state.modelsInfo(報告頁的模型選擇器用)一起更新。
// 反白的模型用回應回來「當下」正在編輯的那個:等待期間換了模型,不能反白舊的(Verifier N2)
async function refreshList(el) {
  try {
    const info = await API.listModels();
    el.__mwInfo = info;
    if (el.__mwState) el.__mwState.modelsInfo = info;
    if (el.querySelector("#mwList")) renderList(el, info, ed?.model?.id ?? null);
  } catch (e) { /* 清單更新失敗不影響結果 */ }
}
