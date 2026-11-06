// 設計系統元件(階段 3 D2):回傳 HTML 字串或操作 DOM。所有文字都經過 esc;搭配 css/tokens.css、css/ui.css。
// 電腦(> 820px)是 macOS 版面,手機(≤ 820px)是 iOS 版面;同一個元件在兩種版面都能用。
// 插入 HTML 的 ${…} 只能是 esc(…) 或本檔產生的數字 / 固定字串(tests/test_design_system.py 會逐一檢查)。
import { ZH } from "./i18n/index.js";
import { esc, isNum } from "./fmt.js";

export const MOBILE_QUERY = "(max-width: 820px)";
// 不用 Object.hasOwn:它要 iOS 15.4 以上。用舊寫法,舊 Safari 也能載入整個模組
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

// 證據狀態(規則 8:沒有證實有效的訊號不能用肯定符號;不用 ✓)。short 給標籤,long 給螢幕閱讀器與提示。
export const EVIDENCE = {
  proven: { ic: "▲", short: ZH`證實`, long: ZH`證實有效:95% 區間不含 0,且通過多重檢定校正` },
  none: { ic: "≈0", short: ZH`未證實`, long: ZH`沒有證實有效:95% 區間包含 0` },
  worse: { ic: "▼", short: ZH`可能更差`, long: ZH`可能更差:沒有通過不更差的門檻` },
  unval: { ic: "?", short: ZH`參考`, long: ZH`參考,未經回測驗證,不進等級` },
  thin: { ic: "n", short: ZH`樣本不足`, long: ZH`樣本不足,不給區間` },
};

export function evidence(status, long) {
  if (!has(EVIDENCE, status)) throw new Error(ZH`未知的證據狀態:${status}`);
  const e = EVIDENCE[status];
  const text = long || e.long;
  return `<span class="u-ev is-${esc(status)}" title="${esc(text)}"><span class="u-ev-ic" aria-hidden="true">${esc(e.ic)}</span>${esc(e.short)}<span class="u-sr">:${esc(text)}</span></span>`;
}

// ciBar、ring、rangeBar 一律輸出 span(CSS 設成區塊),可以放進可點的卡片(button)裡。

// 由 95% 區間判斷狀態。「證實」不能只看區間:必須由閘門結果明確給 proven: true(布林值,含多重檢定校正)。
export function ciStatus(lo, hi, { proven = false } = {}) {
  if (!isNum(lo) || !isNum(hi) || lo > hi) return { status: "thin", long: EVIDENCE.thin.long };
  if (hi < 0) return { status: "worse", long: ZH`95% 區間整段小於 0` };
  if (lo > 0) {
    return proven === true ? { status: "proven", long: EVIDENCE.proven.long }
      : { status: "none", long: ZH`95% 區間不含 0,但沒有經過多重檢定校正,不算證實` };
  }
  return { status: "none", long: EVIDENCE.none.long };
}

const sign = (v, d) => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(d);
const clamp01 = (v) => Math.min(1, Math.max(0, v));
const pct = (v) => (clamp01(v) * 100).toFixed(2);

// 信賴區間條:白線 = 0,藍條 = 95% 區間,白點 = 點估計。range 是畫面上的左右界(不合理時用預設 ±0.1)。
export function ciBar(mean, lo, hi, { range = [-0.1, 0.1], digits = 3, label = "" } = {}) {
  let [a, b] = range;
  if (!isNum(a) || !isNum(b) || b <= a) [a, b] = [-0.1, 0.1];
  const x = (v) => pct((v - a) / (b - a));
  if (!isNum(mean) || !isNum(lo) || !isNum(hi) || lo > hi) {
    return ZH`<span class="u-ci" role="img" aria-label="${esc(label ? label + ":" : "")}樣本不足"><span class="u-ci-ax"></span></span>`;
  }
  const aria = ZH`${label ? label + ":" : ""}${sign(mean, digits)},95% 區間 ${sign(lo, digits)} 到 ${sign(hi, digits)}`;
  const w = (Number(x(hi)) - Number(x(lo))).toFixed(2);
  return `<span class="u-ci" role="img" aria-label="${esc(aria)}"><span class="u-ci-ax"></span>`
    + `<span class="u-ci-bar" style="left:${x(lo)}%;width:${w}%"></span>`
    + `<span class="u-ci-zero" style="left:${x(0)}%"></span><span class="u-ci-pt" style="left:${x(mean)}%"></span></span>`;
}

// 分數環:value 是 0–1。沒有分數(null、NaN、Infinity)就畫空環;沒有等級顯示 —。
// 直接輸出最終位置,進場動畫由 CSS keyframes 從 0 長到這裡(不呼叫任何函式也不會停在錯的位置)。
export function ring(name, grade, value) {
  const C = 2 * Math.PI * 25;
  const ok = isNum(value);
  const v = ok ? clamp01(value) : 0;
  const color = !ok ? "var(--sep)" : v >= 0.65 ? "var(--green)" : v >= 0.45 ? "var(--yellow)" : "var(--orange)";
  const g = grade || "—";
  const off = (C * (1 - v)).toFixed(2);
  return `<span><svg class="u-ring" viewBox="0 0 62 62" role="img" aria-label="${esc(name)} ${esc(g)}">`
    + `<circle class="u-ring-bg" cx="31" cy="31" r="25"/>`
    + `<circle class="u-ring-fg" cx="31" cy="31" r="25" stroke="${color}" stroke-dasharray="${C.toFixed(2)}" style="--c:${C.toFixed(2)};stroke-dashoffset:${off}"/>`
    + `<text x="31" y="32" aria-hidden="true">${esc(g)}</text></svg><span class="u-ring-name">${esc(name)}</span></span>`;
}

// 現價在公平價區間的位置。lo / hi 是區間,mid 是中心。現價超出區間時標記貼在邊上並換色。
export function rangeBar({ lo, mid, hi, price, fmt = (v) => String(v) }) {
  if (![lo, mid, hi, price].every(isNum) || hi <= lo) {
    return ZH`<span class="u-sub">公平價區間不可得</span>`;
  }
  const raw = (price - lo) / (hi - lo);
  const cls = (raw < 0 ? " is-out-low" : raw > 1 ? " is-out-high" : "") + (raw < 0.1 ? " lb-start" : raw > 0.9 ? " lb-end" : "");
  const where = raw < 0 ? ZH`低於區間下緣` : raw > 1 ? ZH`高於區間上緣` : ZH`在區間 ${Math.round(raw * 100)}% 的位置`;
  const aria = ZH`現價 ${fmt(price)},公平價區間 ${fmt(lo)} 到 ${fmt(hi)},${where}`;
  return `<span class="u-range${cls}" role="img" aria-label="${esc(aria)}"><span class="u-range-mk" style="left:${pct(raw)}%">`
    + ZH`<span class="u-range-lb" aria-hidden="true">現價</span></span></span>`
    + ZH`<span class="u-range-ends" aria-hidden="true"><span>${esc(fmt(lo))}</span><span>公平 ${esc(fmt(mid))}</span><span>${esc(fmt(hi))}</span></span>`;
}

// 清單列(可點)。data:{鍵: 值} 會變成 data-鍵="值"(鍵只允許小寫英數與 -,值跳脫);ev:右側證據標籤的狀態。
const DATA_KEY = /^[a-z][a-z0-9-]*$/;
export function row({ title, sub = "", ev = null, data = {} }) {
  const attrs = Object.entries(data).map(([k, v]) => {
    if (!DATA_KEY.test(k)) throw new Error(ZH`不合法的 data 屬性名稱:${k}`);
    return ` data-${esc(k)}="${esc(v)}"`;
  }).join("");
  return `<li><button type="button" class="u-row"${attrs}><span class="u-grow"><span class="u-t">${esc(title)}</span>`
    + (sub ? `<br><span class="u-sub">${esc(sub)}</span>` : "") + `</span>${ev ? evidence(ev) : ""}<span class="u-chev" aria-hidden="true">›</span></button></li>`;
}

// 分析進度:6 個小圓點。state:wait / run / done / fail。done 的 ✓ 代表「這一步做完」,不是訊號好壞。
export function steps(names) {
  return ZH`<ol class="u-steps" aria-label="分析進度">${names.map((n) => ZH`<li class="u-step" data-state="wait"><span class="u-dot" aria-hidden="true"></span>${esc(n)}<span class="u-sr">:等待</span></li>`).join("")}</ol>`;
}
const STEP_TXT = { wait: ["", ZH`等待`], run: ["", ZH`進行中`], done: ["✓", ZH`完成`], fail: ["!", ZH`部分失敗`] };
export function setStep(root, i, state) {
  const el = root.querySelectorAll(".u-step")[i];
  if (!el || !has(STEP_TXT, state)) return;
  el.className = "u-step" + (state === "wait" ? "" : ` is-${state}`);
  el.dataset.state = state;
  el.querySelector(".u-dot").textContent = STEP_TXT[state][0];
  el.querySelector(".u-sr").textContent = ":" + STEP_TXT[state][1];
}

// 細節面板的分段內容(全部是純文字,一律跳脫;不接受任意 HTML):
// sections:[{ h: 小標, p: [段落], rows: [[鍵, 值]], items: [{ t: 標題, d: 說明, tag: 前綴標籤, ev: 證據狀態 }], go: [{ label, hash }] }]
// go 的 hash 只允許站內路徑(#/ 開頭),點了換頁並關閉面板。
const HASH_OK = /^#\/[\w/.%-]*$/;
export function sectionsHTML(sections = []) {
  return sections.map((sec) => {
    const h = sec.h ? `<h3 class="u-sec-h">${esc(sec.h)}</h3>` : "";
    const p = (sec.p || []).map((x) => `<p class="u-p">${esc(x)}</p>`).join("");
    const rows = (sec.rows || []).map(([k, v]) => `<div class="u-kv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
    const items = (sec.items || []).length
      ? `<ul class="u-items">${sec.items.map((it) => `<li>${it.tag ? `<span class="u-tag">${esc(it.tag)}</span>` : ""}<span class="u-it">${esc(it.t)}</span>`
        + `${it.ev ? " " + evidence(it.ev) : ""}${it.d ? `<span class="u-it-d">${esc(it.d)}</span>` : ""}</li>`).join("")}</ul>` : "";
    const go = (sec.go || []).filter((g) => HASH_OK.test(g.hash || ""))
      .map((g) => `<button type="button" class="u-btn" data-go-hash="${esc(g.hash)}">${esc(g.label)} ›</button>`).join(" ");
    return `<section class="u-sec">${h}${p}${rows}${items}${go ? `<div class="u-go">${go}</div>` : ""}</section>`;
  }).join("");
}

// 細節面板:電腦是右側檢視器,手機是底部彈出卡(手機時背景設 inert,焦點圈在面板裡)。
// rows:[[鍵, 值]](純文字);sections 見 sectionsHTML。不接受任意 HTML。
let lastFocus = null;
let hideTimer = null;
// Safari(含 iOS)點按鈕不會讓按鈕取得焦點,activeElement 會是 body;記下最後點到的可操作元素,關閉面板時把焦點還給它
let lastPressed = null;
if (typeof document !== "undefined") {             // node 單元測試載入本檔時沒有 document
  document.addEventListener("click", (e) => { lastPressed = e.target.closest?.("button, a[href], [tabindex]") || null; }, true);
}
const inerted = new Set();                         // 只還原自己設成 inert 的元素,不動別人原本的狀態
const isOpen = () => document.body.classList.contains("u-detail-open");
function setBackgroundInert(on) {
  if (on) {
    for (const el of document.body.children) {
      if (el.id === "uDetail" || el.id === "uScrim" || el.tagName === "SCRIPT" || el.hasAttribute("inert")) continue;
      el.setAttribute("inert", ""); inerted.add(el);
    }
  } else {
    inerted.forEach((el) => el.removeAttribute("inert")); inerted.clear();
  }
}
// 面板開著時切換版面(例如視窗從手機寬度拉寬):重新決定是否為 modal 與背景 inert
function applyModal() {
  const d = document.getElementById("uDetail");
  if (!d || !isOpen()) return;
  const modal = window.matchMedia(MOBILE_QUERY).matches;
  d.setAttribute("aria-modal", String(modal));
  setBackgroundInert(false);
  if (modal) setBackgroundInert(true);
}
function detailEls() {
  let d = document.getElementById("uDetail");
  if (!d) {
    const scrim = document.createElement("div");
    scrim.className = "u-scrim"; scrim.id = "uScrim"; scrim.addEventListener("click", closeDetail);
    d = document.createElement("aside");
    d.className = "u-detail"; d.id = "uDetail"; d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "false"); d.setAttribute("aria-labelledby", "uDetailTitle");
    d.hidden = true;
    document.body.append(scrim, d);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) closeDetail(); });
    window.matchMedia(MOBILE_QUERY).addEventListener("change", applyModal);
  }
  return d;
}
// actions:[{ label, run }]:面板底部的動作按鈕(例如切換設定),label 跳脫、run 是呼叫端的函式;按下後先關面板再執行。
export function openDetail({ title, desc = "", rows = [], sections = [], actions = [] }) {
  const d = detailEls();
  clearTimeout(hideTimer);                       // 剛關又開:取消「關完隱藏」的計時器
  if (!isOpen()) {                                // 已開著再開一次時,保留最早的焦點(關閉動畫中再開也算重新開)
    const a = document.activeElement;
    lastFocus = a && a !== document.body ? a : lastPressed;
  }
  d.innerHTML = ZH`<button type="button" class="u-btn u-close" aria-label="關閉">✕</button><h2 id="uDetailTitle">${esc(title)}</h2>`
    + (desc ? `<p class="u-desc">${esc(desc)}</p>` : "")
    + rows.map(([k, v]) => `<div class="u-kv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("") + sectionsHTML(sections)
    + (actions.length ? `<div class="u-go">${actions.map((a, i) => `<button type="button" class="u-btn" data-act="${i}">${esc(a.label)}</button>`).join(" ")}</div>` : "");
  d.querySelector(".u-close").addEventListener("click", closeDetail);
  // iOS 幽靈點擊:同一次觸控在面板滑上來後可能落到剛出現的按鈕上,開啟後 450ms 內不接受跳頁
  const openedAt = Date.now();
  d.querySelectorAll("[data-go-hash]").forEach((b) => b.addEventListener("click", () => {
    if (Date.now() - openedAt < 450) return;
    closeDetail(); location.hash = b.dataset.goHash;
  }));
  d.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
    if (Date.now() - openedAt < 450) return;
    closeDetail(); actions[+b.dataset.act].run();
  }));
  d.hidden = false;
  void d.offsetWidth;                            // 先套用 hidden=false 的版面,轉場才會播放
  document.body.classList.add("u-detail-open");
  applyModal();
  d.querySelector(".u-close").focus({ preventScroll: true });
}
export function closeDetail() {
  const d = document.getElementById("uDetail");
  if (!d || !isOpen()) return;
  document.body.classList.remove("u-detail-open");
  setBackgroundInert(false);
  const dur = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--dur")) || 0;
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => { if (!isOpen()) d.hidden = true; }, dur * 1000 + 20);
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  lastFocus = null;
}

// 表格標題格補 scope(D0 稽核 C6):在 thead 裡、或整列都是 th 的是欄標題(col),其他(列首)是列標題(row)。
// 已經寫了 scope 的不動。各頁的表格由 app.js 在畫面變動後統一呼叫,不必逐頁改。
export function scopeHeaders(root = document) {
  root.querySelectorAll("th:not([scope])").forEach((th) => {
    const tr = th.parentElement;
    const cells = tr ? [...tr.children] : [];
    // 整列只有一格(例如跨欄的分組列)不算欄標題
    const col = th.closest("thead") || (cells.length > 1 && cells.every((c) => c.tagName === "TH"));
    th.setAttribute("scope", col ? "col" : "row");
  });
}
