import { ZH, KEY, t, isEn, setLang, translateDom } from "./i18n/index.js";
import * as Notice from "./notice.js";
import { esc, num, pct, price, big, upDownClass, isNum } from "./fmt.js";
import { disposeAll } from "./charts.js";
import * as Overview from "./views/overview.js";
import * as Valuation from "./views/valuation.js";
import * as Financials from "./views/financials.js";
import * as Health from "./views/health.js";
import * as Technical from "./views/technical.js";
import * as Chips from "./views/chips.js";
import * as Analysts from "./views/analysts.js";
import * as News from "./views/news.js";
import * as Company from "./views/company.js";
import * as Method from "./views/method.js";
// 預測追蹤(views/review.js)與模擬投資組合(views/paper.js)暫停顯示:目前用內建模型的評等,
// 下一版會重接到使用者自己的模型。程式碼保留,不載入、沒有導覽入口與路由。
import * as Scan from "./views/scan.js";
import * as Lab from "./views/lab.js";
import * as Backtest from "./views/backtest.js";
import * as Models from "./views/models.js";
import * as Wizard from "./views/wizard.js";
import * as Setup from "./setup.js";
import * as ModelPick from "./modelpick.js";
import { download as exportMd } from "./export.js";
import * as AgentRoom from "./agentroom.js";
import * as UI from "./ui.js";
import * as Icons from "./icons.js";
import * as Clock from "./clock.js";
import * as Palette from "./palette.js";
import * as Shortcuts from "./shortcuts.js";
import { toast } from "./toast.js";

translateDom(document.body);

const $ = (s, r = document) => r.querySelector(s);
const app = $("#app");

// ---------- 全站「非投資建議」聲明(唯一實作位置) ----------
// web/index.html 有一份同 id 的靜態備援(JS 失敗時也看得到);這裡沿用它並以這段文字為準,沒有才建立,所以不會重複。
// 放在 <main id="app"> 之後、#app 外面:所有路由(首頁、公司 9 個分頁、市場掃描、因子實驗室、模擬交易、
// 模型回測、復盤、方法論)都只會改寫 #app 的內容,不會碰到這個頁尾,所以每一頁都看得到。
export const SITE_DISCLAIMER = ZH`H0 Lab 是開源的研究工具,不是證券投資顧問。所有分數、等級與回測結果都是模型輸出,不構成投資建議,也不保證任何報酬;資料可能有誤。`;
(function mountSiteDisclaimer() {
  let f = document.getElementById("siteDisclaimer");
  if (!f) {
    f = document.createElement("footer");
    f.id = "siteDisclaimer";
    f.className = "site-disclaimer";
    f.setAttribute("role", "contentinfo");
    app.insertAdjacentElement("afterend", f);
  }
  f.textContent = SITE_DISCLAIMER;
})();

export const state = { report: null, symbol: null, tab: "overview", dict: {}, dictList: [], agentLog: {} };

const TABS = [
  ["overview", ZH`總覽`, Overview], ["detail", ZH`詳細總覽`, { render: Overview.renderFull }], ["valuation", ZH`估值計算機`, Valuation], ["financials", ZH`財務報表`, Financials],
  ["health", ZH`品質與安全`, Health], ["technical", ZH`技術面`, Technical], ["chips", ZH`籌碼面`, Chips],
  ["analysts", ZH`市場預期`, Analysts], ["news", ZH`消息面`, News], ["company", ZH`公司資料`, Company],
];

// ---------- 設定 ----------
function loadPrefs() {
  try { return JSON.parse(localStorage.getItem("jwlab.prefs") || "{}"); } catch (e) { return {}; }
}
function savePrefs(p) { try { localStorage.setItem("jwlab.prefs", JSON.stringify(p)); } catch (e) { /* noop */ } }
const prefs = loadPrefs();
function applyColors() {
  document.body.classList.toggle("us-colors", prefs.colors === "us");
  $("#navColorTxt").textContent = prefs.colors === "us" ? ZH`漲跌配色:綠漲紅跌` : ZH`漲跌配色:紅漲綠跌`;
}
applyColors();
function toggleColors() {
  prefs.colors = prefs.colors === "us" ? "tw" : "us"; savePrefs(prefs); applyColors(); route();
  toast(prefs.colors === "us" ? ZH`漲跌配色改成綠漲紅跌` : ZH`漲跌配色改成紅漲綠跌`, { action: { label: ZH`復原`, run: toggleColors } });
}
$("#navColor").onclick = toggleColors;
$("#navLangTxt").textContent = isEn ? KEY`繁體中文` : "English";
$("#navLang").onclick = () => setLang(isEn ? "zh-TW" : "en");

// 外觀:跟系統 / 深色 / 淺色(規格 §7「我說了算」)。沒選過就是淺色,和 index.html 的預設一致
const DEFAULT_THEME = "light";
const THEMES = [["system", ZH`跟系統`], ["dark", ZH`深色`], ["light", ZH`淺色`]];
function applyTheme() {
  const th = THEMES.some(([k]) => k === prefs.theme) ? prefs.theme : DEFAULT_THEME;
  if (th === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = th;
  $("#navThemeTxt").textContent = ZH`外觀:` + THEMES.find(([k]) => k === th)[1];
}
applyTheme();
function setTheme(th) {
  prefs.theme = th; savePrefs(prefs); applyTheme();
  if (state.report || !location.hash.startsWith("#/s/")) route();   // 圖表顏色是畫圖當下讀的,要重畫;分析中不重畫
}
$("#navTheme").onclick = () => {
  const i = THEMES.findIndex(([k]) => k === (prefs.theme || DEFAULT_THEME));
  setTheme(THEMES[(i + 1) % THEMES.length][0]);
  toast(ZH`外觀:` + THEMES.find(([k]) => k === prefs.theme)[1]);
};

// 設定視窗(web/js/settings.js)改外觀、配色、選定模型都走這個事件
window.addEventListener("h0:pref", (e) => {
  const { theme, colors, model } = e.detail || {};
  if (theme) setTheme(theme);
  const redraw = () => { if (state.report || !location.hash.startsWith("#/s/")) route(); };   // 分析中不重畫,免得重跑
  if (colors) { prefs.colors = colors; savePrefs(prefs); applyColors(); redraw(); }
  if (model) { ModelPick.setSelected(model, state); redraw(); }
});

// macOS 工具列:上一頁 / 下一頁、市場時鐘、線性圖示
$("#navBack").onclick = () => history.back();
$("#navFwd").onclick = () => history.forward();
Clock.mount($("#mkClock"));
Icons.hydrate();

function recent() { try { return JSON.parse(localStorage.getItem("jwlab.recent") || "[]"); } catch (e) { return []; } }
function pushRecent(item) {
  const r = recent().filter((x) => x.symbol !== item.symbol);
  r.unshift({ symbol: item.symbol, name: item.name });
  try { localStorage.setItem("jwlab.recent", JSON.stringify(r.slice(0, 10))); } catch (e) { /* noop */ }
}

// ---------- 指標字典(提示) ----------
fetch("/api/dictionary").then((r) => r.json()).then((j) => {
  state.dictList = j.items;
  for (const it of j.items) state.dict[it.key] = it;
}).catch(() => {});

const tip = $("#tip");
document.addEventListener("mouseover", (e) => {
  const el = e.target.closest("[data-tip]");
  if (!el) { tip.style.display = "none"; return; }
  const d = state.dict[el.dataset.tip];
  const custom = el.dataset.tipText;
  if (!d && !custom) return;
  tip.innerHTML = d
    ? ZH`<b>${esc(d.name)}</b><div class="f">${esc(d.formula)}</div><div>${esc(d.meaning)}</div><div style="margin-top:4px">判斷:${esc(d.judge)}</div><div class="s">出處:${esc(d.source)}</div>`
    : `<div>${esc(custom)}</div>`;
  tip.style.display = "block";
  const r = el.getBoundingClientRect();
  const x = Math.min(window.innerWidth - 400, r.left);
  tip.style.left = Math.max(8, x) + "px";
  tip.style.top = (r.bottom + 8) + "px";
});
export const hint = (key) => `<span class="hint" data-tip="${esc(key)}">?</span>`;

// ---------- 搜尋 ----------
function bindSearch(input, dd) {
  let timer = null, items = [], idx = -1;
  const render = () => {
    if (!items.length) { dd.classList.remove("open"); return; }
    dd.innerHTML = items.map((x, i) => `<div class="item ${i === idx ? "active" : ""}" data-i="${i}">
      <span class="sym">${esc(x.symbol)}</span><span class="nm">${esc(x.name)}</span>
      <span class="tag">${esc(x.market === "TW" ? (x.exchange || ZH`台股`) + (x.industry ? " · " + x.industry : "") : x.market === "US" ? ZH`美股` : x.exchange || "")}</span></div>`).join("");
    dd.classList.add("open");
  };
  const pick = (x) => { dd.classList.remove("open"); input.value = ""; input.blur(); pushRecent(x); go(x.symbol, "overview"); };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) { items = []; render(); return; }
    timer = setTimeout(async () => {
      try {
        const r = await fetch("/api/search?q=" + encodeURIComponent(q));
        items = (await r.json()).results || []; idx = items.length ? 0 : -1; render();
      } catch (e) { items = []; render(); }
    }, 180);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { idx = Math.min(items.length - 1, idx + 1); render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { idx = Math.max(0, idx - 1); render(); e.preventDefault(); }
    else if (e.key === "Enter") {
      if (items[idx]) pick(items[idx]);
      else if (/^[A-Za-z0-9.\-]{1,15}$/.test(input.value.trim())) pick({ symbol: input.value.trim().toUpperCase(), name: input.value.trim() });
    } else if (e.key === "Escape") { dd.classList.remove("open"); }
  });
  dd.addEventListener("mousedown", (e) => { const it = e.target.closest(".item"); if (it) pick(items[+it.dataset.i]); });
  input.addEventListener("blur", () => setTimeout(() => dd.classList.remove("open"), 150));
}
bindSearch($("#q"), $("#dd"));

$("#brand").onclick = () => { location.hash = "#/"; };
{ const nw = $("#navWizardTxt"); if (nw) nw.textContent = t("nav.wizard"); }
// 快捷鍵與 ⌘K 指令面板(規格 §7):/ 搜尋、G 再按字母跳頁、? 快捷鍵表;指令面板找公司、跳頁、執行動作
const PAGES = [["", ZH`首頁`, ["G", "H"]], ["scan", ZH`選股掃描`, ["G", "S"]], ["wizard", ZH`建立模型`, ["G", "W"]], ["models", ZH`模型工作台`, ["G", "M"]],
  ["backtest", ZH`研究紀錄`, ["G", "B"]], ["lab", ZH`因子研究`], ["method", ZH`方法與資料`]];
const onStock = () => !!(state.report && location.hash.startsWith("#/s/"));
Palette.mount({
  commands: [
    ...PAGES.map(([r, label, keys]) => ({ id: "go-" + (r || "home"), group: ZH`頁面`, label, hint: "#/" + r, keys, run: () => { location.hash = "#/" + r; } })),
    { id: "act-watch", group: ZH`動作`, label: ZH`加入 / 移除觀察清單(目前的公司)`, run: () => { if (onStock()) toggleWatchUI(state.report.meta.symbol, state.report.meta.name); else toast(ZH`先打開一家公司,才能加入觀察清單`, { tone: "warn" }); } },
    { id: "act-export", group: ZH`動作`, label: ZH`匯出目前公司的分析報告`, run: () => { if (onStock()) { exportMd(state.report); toast(ZH`已下載分析報告`); } else toast(ZH`先打開一家公司,才能匯出報告`, { tone: "warn" }); } },
    { id: "act-theme-system", group: ZH`動作`, label: ZH`外觀:跟系統`, run: () => setTheme("system") },
    { id: "act-theme-dark", group: ZH`動作`, label: ZH`外觀:深色`, run: () => setTheme("dark") },
    { id: "act-theme-light", group: ZH`動作`, label: ZH`外觀:淺色`, run: () => setTheme("light") },
    { id: "act-colors", group: ZH`動作`, label: ZH`切換漲跌配色(紅漲綠跌 / 綠漲紅跌)`, run: toggleColors },
    { id: "act-lang", group: ZH`動作`, label: ZH`切換語言`, run: () => setLang(isEn ? "zh-TW" : "en") },
    { id: "act-settings", group: ZH`動作`, label: ZH`開啟設定`, run: () => window.dispatchEvent(new CustomEvent("h0:open-settings")) },
    { id: "act-help", group: ZH`動作`, label: ZH`顯示快捷鍵`, keys: ["?"], run: () => Shortcuts.openHelp() },
  ],
  searchCompanies: (q) => fetch("/api/search?q=" + encodeURIComponent(q)).then((r) => r.json()).then((j) => (j.results || []).map((x) => ({
    symbol: x.symbol, name: x.name, tag: x.market === "TW" ? (x.exchange || ZH`台股`) : x.market === "US" ? ZH`美股` : (x.exchange || "") }))),
  openCompany: (c) => { pushRecent(c); go(c.symbol); },
});
Shortcuts.mount({ go: (hash) => { location.hash = hash; }, focusSearch: () => $("#q").focus(), openPalette: Palette.open });
$("#cmdK").onclick = () => Palette.open();
// 表格標題格補 scope:各頁的表格都是非同步畫出來的,畫面有新節點時(同一輪只做一次)統一補上
let scopeQueued = false;
const hasTh = (n) => n.nodeType === 1 && (n.tagName === "TH" || n.querySelector("th"));
new MutationObserver((muts) => {
  if (scopeQueued || !muts.some((m) => [...m.addedNodes].some(hasTh))) return;
  scopeQueued = true;
  setTimeout(() => { scopeQueued = false; UI.scopeHeaders(document); }, 0);   // 不用 requestAnimationFrame:背景分頁不會觸發
}).observe(document.body, { childList: true, subtree: true });
// 外框導覽(側邊欄與手機標籤列共用 data-route)
document.querySelectorAll("[data-route]").forEach((b) => (b.onclick = () => { location.hash = "#/" + b.dataset.route; }));
// 手機「更多」:底部彈出卡列出其他頁面
$("#tabMore").onclick = () => UI.openDetail({
  title: ZH`更多`, sections: [
    { h: ZH`模型`, go: [{ label: ZH`模型工作台`, hash: "#/models" }] },
    { h: ZH`驗證`, go: [{ label: ZH`研究紀錄`, hash: "#/backtest" }, { label: ZH`因子研究`, hash: "#/lab" }] },
    { h: ZH`工具`, go: [{ label: ZH`方法與資料`, hash: "#/method" }] },

    ...(state.watch.length ? [{ h: ZH`觀察清單`, go: state.watch.map((w) => ({ label: w.name || w.symbol, hash: `#/s/${encodeURIComponent(w.symbol)}/overview` })) }] : []),
  ],
  // 設定是「動作」不是頁面:直接執行,不經過網址(Verifier33 問題 1)
  actions: [{ label: prefs.colors === "us" ? ZH`漲跌配色:改成紅漲綠跌` : ZH`漲跌配色:改成綠漲紅跌`,
    run: toggleColors }],
});
const ROUTE_TITLE = { "": ZH`首頁`, scan: ZH`選股掃描`, wizard: t("nav.wizard"), models: ZH`模型工作台`, backtest: ZH`研究紀錄`, lab: ZH`因子研究`, method: ZH`方法與資料` };
let navNow = ["", null];
const MORE_ROUTES = new Set(["models", "backtest", "lab", "method"]);   // 手機上這些頁面在「更多」裡
function markNav(key, title) {
  navNow = [key, title];
  document.querySelectorAll(".u-nav[data-route], .u-tab[data-route], #sideWatch .u-nav").forEach((b) => {
    // 手機的「首頁」標籤也涵蓋個股頁(個股頁是從首頁搜尋進去的)
    const on = b.dataset.route === key || (b.dataset.sym && key === "s:" + b.dataset.sym)
      || (b.classList.contains("u-tab") && b.dataset.route === "" && key.startsWith("s:"));
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  if (MORE_ROUTES.has(key)) $("#tabMore").setAttribute("aria-current", "page"); else $("#tabMore").removeAttribute("aria-current");
  const t = title || ROUTE_TITLE[key] || "H0 Lab";
  $("#tbTitle").textContent = t;
  document.title = t === ZH`首頁` ? ZH`H0 Lab — 量化選股研究` : `${t} · H0 Lab`;
}
function renderSideWatch() {
  const box = $("#sideWatch");
  box.innerHTML = state.watch.map((w) => `<button class="u-nav" data-sym="${esc(w.symbol)}"><span class="u-ic" aria-hidden="true">${Icons.icon("star", 16)}</span>${esc(w.name || w.symbol)}</button>`).join("");
  box.querySelectorAll("[data-sym]").forEach((b) => (b.onclick = () => go(b.dataset.sym)));
  markNav(...navNow);   // 新加入的觀察清單項目也要標出目前頁面
}

// ---------- 觀察清單 ----------
state.watch = [];
async function loadWatch() {
  try { state.watch = (await (await fetch("/api/watchlist")).json()).items || []; } catch (e) { state.watch = []; }
  renderSideWatch();
}
// 介面上的加入 / 移除:更新星號按鈕,右下角提示,可以復原
async function toggleWatchUI(sym, name) {
  await toggleWatch(sym, name);
  const on = state.watch.some((x) => x.symbol === sym);
  const star = $("#star");
  if (star && state.report?.meta.symbol === sym) star.textContent = on ? ZH`★ 已在觀察清單` : ZH`☆ 加入觀察清單`;
  toast(on ? ZH`已加入觀察清單:${name}` : ZH`已從觀察清單移除:${name}`, { tone: "ok", action: { label: ZH`復原`, run: () => toggleWatchUI(sym, name) } });
}
async function toggleWatch(sym, name) {
  const on = state.watch.some((x) => x.symbol === sym);
  const r = await fetch("/api/watchlist", { method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "invest-lab" },
    body: JSON.stringify({ symbol: sym, name, action: on ? "remove" : "add" }) });
  if (r.ok) state.watch = (await r.json()).items;
  renderSideWatch();
}

// ---------- 資料服務狀態(側邊欄底部):背景掃描有沒有在跑、FinMind 額度 ----------
async function pollSys() {
  try {
    const st = await (await fetch("/api/scan/status")).json();
    const box = $("#sys"), txt = $("#sysTxt");
    const fm = st.finmind || {};
    if (st.running) {
      box.classList.add("busy");
      txt.textContent = ZH`掃描中 ${st.done}/${st.total}`;
      box.title = ZH`正在分析 ${st.current || ""}`;
    } else {
      box.classList.remove("busy");
      txt.textContent = fm.cooldown_remaining ? ZH`台股資料暫停中,約 ${Math.ceil(fm.cooldown_remaining / 60)} 分鐘後恢復` : ZH`資料服務正常`;
      box.title = ZH`台股資料來源(FinMind)本小時已用 ${fm.used_last_hour ?? "—"} / ${fm.budget ?? "—"} 次`;
    }
  } catch (e) { /* noop */ }
}
pollSys();
setInterval(pollSys, 15000);

// ---------- 路由 ----------
export function go(symbol, tab = "overview") { location.hash = `#/s/${encodeURIComponent(symbol)}/${tab}`; }
// 模型工作台有沒存檔的修改時,側邊欄、分頁列、上一頁換頁都先問;取消就回到原頁,不重畫(M6 Verifier)。
// 取消時不能用 replaceState 改寫紀錄(會把「上一頁」那筆換掉,之後上一頁 / 下一頁就壞了):
// 每筆紀錄標 idx(時間戳記,新紀錄比較大);按「上一頁」來的就 forward,點連結(新紀錄,沒有 idx)或「下一頁」來的就 back。
let lastHash = location.hash;
let curIdx = 0;
let restoring = false;   // 回到原頁的那次 hashchange 不再問、不重畫
function stampHistory() {
  const st = history.state;
  if (st && typeof st.idx === "number") { curIdx = st.idx; return; }
  curIdx = Date.now();
  history.replaceState({ ...(st || {}), idx: curIdx }, "", location.hash || "#/");
}
stampHistory();
window.addEventListener("hashchange", () => {
  if (restoring) {
    restoring = false;
    if (location.hash === lastHash) return;
  }
  if (lastHash.startsWith("#/models") && location.hash !== lastHash && !Models.confirmLeave()) {
    const st = history.state;
    restoring = true;
    if (st && typeof st.idx === "number" && st.idx < curIdx) history.forward();
    else history.back();
    return;
  }
  lastHash = location.hash;
  stampHistory();
  route();
});
// 系統切換深色 / 淺色時重畫目前頁面:圖表顏色是畫圖當下從 CSS 變數讀的(charts.js)。分析進行中(還沒有報告)不重畫,避免重複分析
window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
  if (state.report || !location.hash.startsWith("#/s/")) route();
});

let room = null;   // 目前的分析中畫面(agentroom);換頁或換股票時先關掉,舊串流不會寫進新畫面

async function route() {
  const h = location.hash.replace(/^#\/?/, "");
  const parts = h.split("/").map(decodeURIComponent);
  disposeAll();
  UI.closeDetail();
  markNav(parts[0] === "s" ? "s:" + (parts[1] || "").toUpperCase() : (ROUTE_TITLE[parts[0]] !== undefined ? parts[0] : ""),
    parts[0] === "s" ? (parts[1] || "").toUpperCase() : null);
  if (parts[0] === "s" && parts[1]) {
    const sym = parts[1].toUpperCase();
    const tab = parts[2] || "overview";
    const fresh = state.symbol !== sym || !state.report;
    if (fresh) await load(sym, false);
    if (state.report && state.symbol === sym) {
      renderCompany(tab);
      if (fresh) focusTitle();
    }
  } else if (parts[0] === "scan") {
    stopRoom(); app.innerHTML = ""; Scan.render(app, state);
  } else if (parts[0] === "lab") {
    stopRoom(); app.innerHTML = ""; Lab.render(app, state);
  } else if (parts[0] === "backtest") {
    stopRoom(); app.innerHTML = ""; Backtest.render(app, state);
  } else if (parts[0] === "wizard") {
    stopRoom(); app.innerHTML = ""; Wizard.render(app, state);
  } else if (parts[0] === "models") {
    stopRoom(); app.innerHTML = ""; Models.render(app, state, parts[1] || null);
  } else if (parts[0] === "method") {
    stopRoom(); app.innerHTML = ""; Method.render(app, state);
  } else {
    stopRoom(); renderHome();
  }
}
// 剛分析完:原本按的按鈕已經不在畫面上,焦點移到公司名稱(螢幕閱讀器從新頁面開頭讀)
function focusTitle() {
  if (document.activeElement && document.activeElement !== document.body) return;
  const h1 = app.querySelector("h1"); if (h1) { h1.tabIndex = -1; h1.focus({ preventScroll: true }); }
}
function stopRoom() { if (room) { room.close(); room = null; } if (!state.report) state.symbol = null; }

async function load(sym, refresh) {
  state.symbol = sym; state.report = null;
  const known = recent().find((x) => x.symbol === sym) || state.watch.find((x) => x.symbol === sym);
  stopRoom();
  state.symbol = sym;
  const my = room = AgentRoom.mount(app, known?.name || sym, sym);
  const fail = (msg) => {
    if (my.dead) return;
    my.fail(msg);
    app.insertAdjacentHTML("beforeend", ZH`<div class="note" style="text-align:center;margin-top:8px">可以試試輸入完整代號(台股:2330、6488;美股:AAPL)。</div>`);
  };
  // 1) 串流 agent 的工作過程
  const ok = await new Promise((resolve) => {
    let es;
    try { es = new EventSource(`/api/report/stream?symbol=${encodeURIComponent(sym)}${refresh ? "&refresh=1" : ""}`); }
    catch (e) { resolve(true); return; }
    my.attach(es, resolve);
    es.onmessage = (m) => {
      let ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
      if (ev.kind === "complete") { es.close(); resolve(true); }
      else if (ev.kind === "fatal") { es.close(); my.settle().then(() => fail(ev.msg)); resolve(false); }
      else my.push(ev);
    };
    es.onerror = () => { es.close(); resolve(true); }; // 串流失敗就改用一般請求
  });
  if (!ok) return;
  // 2) 取完整報告(伺服器已在記憶體快取)
  try {
    const r = await fetch(`/api/report?symbol=${encodeURIComponent(sym)}`);
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || ZH`分析失敗`);
    await my.settle();
    if (my.dead) return;
    my.finish();
    state.agentLog[sym] = my.log;
    await new Promise((r2) => setTimeout(r2, 600));
    if (my.dead || state.symbol !== sym) return;
    room = null;
    state.report = j;
    pushRecent({ symbol: sym, name: j.meta.name });
  } catch (e) {
    fail(e.message);
  }
}

function greeting(d = new Date()) {
  const h = d.getHours();
  const hi = h < 5 ? ZH`夜深了` : h < 11 ? ZH`早安` : h < 14 ? ZH`午安` : h < 18 ? ZH`午後好` : ZH`晚安`;
  return ZH`${hi},今天想研究哪家公司?`;
}

function renderHome() {
  state.report = null; state.symbol = null;
  const rc = recent();
  const chips = (list) => list.map(([s, n]) => `<button data-s="${esc(s)}">${esc(n)}<span class="num">${esc(s)}</span></button>`).join("");
  app.innerHTML = ZH`<section class="home-hero">
    <div class="home-top"><span class="home-hello">${esc(greeting())}${rc[0] ? ZH`<button type="button" class="home-resume" data-s="${esc(rc[0].symbol)}">繼續看 ${esc(rc[0].name || rc[0].symbol)} →</button>` : ""}</span>
      <span class="home-eyebrow">開源 · 免費 · 在你的電腦上執行</span></div>
    <h1>打造你自己的選股模型,<br><span class="grad">並誠實驗證它。</span></h1>
    <p class="home-lead">一個平台看完台股、美股的財報、估值、籌碼與新聞;不用寫程式就能組出自己的量化模型,再用不偷看未來的回測檢驗它到底有沒有用。</p>
    <div class="search" id="heroSearch"><span class="icon">${Icons.icon("search", 18)}</span><input id="q2" type="text" autocomplete="off" aria-label="搜尋公司" placeholder="輸入公司名稱或代號,例如 2330、NVDA"><div class="dropdown" id="dd2"></div></div>
    <div class="home-quick"><span>熱門:</span>${chips([["2330.TW", ZH`台積電`], ["2454.TW", ZH`聯發科`], ["2317.TW", ZH`鴻海`], ["NVDA", "NVIDIA"], ["AAPL", "Apple"], ["MSFT", "Microsoft"]])}</div>
    ${state.watch.length ? ZH`<div class="home-quick"><span>觀察清單:</span>${chips(state.watch.map((x) => [x.symbol, x.name]))}</div>` : ""}
    ${rc.length ? ZH`<div class="home-quick"><span>最近查詢:</span>${chips(rc.map((x) => [x.symbol, x.name]))}</div>` : ""}
    <div class="home-cta">
      <button type="button" class="u-btn is-primary" data-go="#/wizard">${esc(t("home.wizard.go"))}</button>
      <button type="button" class="u-btn" data-go="#/backtest">看研究紀錄</button>
    </div>
  </section>
  <div id="setupCard"></div>

  <div class="home-values">
    ${[["search", ZH`個股研究`, ZH`財報、估值、技術面、籌碼、分析師預期與新聞整合在一頁。每個指標都附公式與出處,滑過就看得到。`, "", ZH`輸入代號開始`],
      ["wizard", t("home.wizard.title"), t("home.wizard.body"), "#/wizard", ZH`建立模型`],
      ["backtest", ZH`誠實驗證`, ZH`回測只用當時拿得到的資料,自動記錄你的嘗試次數,最近兩年的樣本外資料每個模型只能看一次。`, "#/backtest", ZH`看研究紀錄`]]
      .map(([ic, h, d, go, more]) => `<button type="button" class="home-value" ${go ? `data-go="${go}"` : 'data-focus="q2"'}><span class="ic" aria-hidden="true">${Icons.icon(ic, 20)}</span><b>${esc(h)}</b><span>${esc(d)}</span><span class="more">${esc(more)} →</span></button>`).join("")}
  </div>

  <div class="home-h0"><span class="big" aria-hidden="true">H₀</span><div><b>先假設策略無效,證明了才算數。</b>
    <p>H₀ 是統計上的「虛無假設」。開發時的研究模型用 2016 年以來的資料嚴格回測,目前<b>沒有證實的預測力</b>,結果照實放在研究紀錄。H0 Lab 不提供內建評等;分數只來自你自己建立的模型,並用同樣的標準檢驗。</p></div></div>

  <div class="home-sec"><h2>今日市場</h2><span class="u-sub">大盤位置、估值與總體指標,幫你判斷現在的市場環境。</span></div>
  <div id="dash"></div>

  <div class="home-sec"><h2>一次分析會做哪些事</h2><span class="u-sub">輸入公司後,六個分析模組依序完成,過程即時顯示。</span></div>
  <ol class="home-flow">${AgentRoom.AGENTS.map((a) => `<li><b>${esc(a.name)}</b><span>${esc(a.role)}</span></li>`).join("")}</ol>

  <p class="home-sources">資料來源:Yahoo Finance、FinMind、SEC EDGAR、證交所 / 櫃買中心、集保結算所、Google News。資料可能延遲或有誤,重要決策前請查證原始公告。</p>`;
  bindSearch($("#q2"), $("#dd2"));
  app.querySelectorAll(".home-quick button, .home-resume").forEach((b) => (b.onclick = () => go(b.dataset.s)));
  app.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => { location.hash = b.dataset.go; }));
  app.querySelectorAll("[data-focus]").forEach((b) => (b.onclick = () => { const i = $("#" + b.dataset.focus); i.scrollIntoView({ block: "center", behavior: "smooth" }); i.focus({ preventScroll: true }); }));
  if (!window.matchMedia("(pointer: coarse)").matches) setTimeout(() => $("#q2")?.focus(), 50);   // 手機不自動聚焦,避免一進首頁就跳鍵盤
  renderSetup();
  renderDash();
}

// 第一次執行引導卡(web/js/setup.js):還有沒完成的步驟才顯示;每次回首頁重新檢查
async function renderSetup() {
  let st;
  try { st = await Setup.fetchStatus(true); } catch (e) { return; }
  const box = $("#setupCard");
  if (!box) return;
  box.innerHTML = Setup.homeCardHTML(st);
  box.querySelectorAll("button[data-s]").forEach((b) => (b.onclick = () => go(b.dataset.s)));
  if (!box.dataset.bound) { Setup.bindCopy(box); box.dataset.bound = "1"; }
}

async function renderDash() {
  const box = $("#dash");
  if (!box) return;
  let j;
  try { j = await (await fetch("/api/home")).json(); } catch (e) { return; }
  if (!$("#dash")) return;
  const mk = (m, x) => {
    if (!x) return "";
    const tone = x.label === ZH`順風` ? "pos" : x.label === ZH`逆風` ? "neg" : "watch";
    return ZH`<div class="card"><h2>${m === "TW" ? ZH`台股` : ZH`美股`}市場環境 <span class="right pill ${tone}">${esc(x.label || "")} ${num(x.score, 0)}</span></h2>
      <div class="kv"><span class="k">${esc(x.name || "")}</span><span class="v">${num(x.price, 0)}</span>
        <span class="k">相對 200 日線</span><span class="v">${pct(x.vs_ma200, 1, true)}</span>
        <span class="k">近 1 月 / 3 月</span><span class="v">${pct(x.ret_1m, 1, true)} / ${pct(x.ret_3m, 1, true)}</span>
        <span class="k">距一年高點</span><span class="v">${pct(x.dd, 1)}</span>
        ${x.market_pe ? ZH`<span class="k">大盤本益比(${esc(x.market_pe.etf)})</span><span class="v">${num(x.market_pe.pe, 1)} 倍 · ${esc(x.market_pe.label)}</span>` : ""}
        ${x.vix ? `<span class="k">VIX</span><span class="v">${num(x.vix.value, 1)} · ${esc(x.vix.label)}</span>` : ""}
        ${x.us10y ? ZH`<span class="k">美 10 年債</span><span class="v">${pct(x.us10y.value, 2)}</span>` : ""}
        ${x.usdtwd ? `<span class="k">USD/TWD</span><span class="v">${num(x.usdtwd.value, 2)}</span>` : ""}</div></div>`;
  };
  const w = j.watch || [];
  const scanT = (k) => esc((j.scans?.[k] || "—").replace("T", " ").slice(5, 16));
  box.innerHTML = ZH`<div class="grid g3" style="margin-top:10px">${mk("TW", j.markets.TW)}${mk("US", j.markets.US)}
    <div class="card"><h2>選股掃描</h2><div class="kv">
      <span class="k">最近掃描(台 / 美)</span><span class="v" style="font-size:11px">${scanT("tw_large")} / ${scanT("us_large")}</span></div>
      <p class="note" style="margin-top:8px">掃描只用你自己的模型排名;還沒有模型的話,先用精靈建立一個。</p>
      <div class="flex" style="margin-top:10px"><button class="btn" data-go="#/scan">選股掃描</button><button class="btn" data-go="#/wizard">建立模型</button></div></div></div>
  ${w.length ? ZH`<div class="card" style="margin-top:14px;text-align:left"><h2>觀察清單 <span class="sub">最近一次分析的日期與當時股價</span></h2><div class="tbl-wrap"><table>
    <tr><th>公司</th><th>分析日</th><th class="n">當時股價</th></tr>
    ${w.map((x) => { const s = x.snap || {}; return `<tr data-s="${esc(x.symbol)}" style="cursor:pointer"><td><b>${esc(x.name)}</b> <span class="num" style="color:var(--label)">${esc(x.symbol)}</span></td>
      <td>${esc(s.date || "—")}</td><td class="n">${price(s.price)}</td></tr>`; }).join("")}</table></div></div>` : ""}`;
  box.querySelectorAll("tr[data-s]").forEach((tr) => (tr.onclick = () => go(tr.dataset.s)));
  box.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => { location.hash = b.dataset.go; }));
}

function renderCompany(tab) {
  const R = state.report;
  state.tab = TABS.find((t) => t[0] === tab) ? tab : "overview";
  const m = R.meta, q = R.quote;
  markNav("s:" + m.symbol, m.name);
  const cls = upDownClass(q.change);
  app.innerHTML = ZH`
  <div class="head">
    <div>
      <h1 class="title">${esc(m.name)}<span class="code">${esc(m.symbol)}</span></h1>
      <div class="meta">
        <span class="chip">${esc(m.market === "TW" ? ZH`台股 · ` + (m.exchange || "") : ZH`美股 · ` + (m.exchange || ""))}</span>
        ${m.industry_tw ? `<span class="chip">${esc(m.industry_tw)}</span>` : ""}
        ${m.sector ? `<span class="chip">${esc(m.sector)}</span>` : ""}${m.industry ? `<span class="chip">${esc(m.industry)}</span>` : ""}
        ${m.name_en && m.name_en !== m.name ? `<span class="chip">${esc(m.name_en)}</span>` : ""}
      </div>
    </div>
    <div>
      <span class="px">${price(q.price)}</span><span class="chg ${cls}">${isNum(q.change) ? (q.change > 0 ? "+" : "") + num(q.change, 2) : ""} (${pct(q.change_pct, 2, true)})</span>
      <div class="meta"><span>${esc(m.currency)}</span><span>市值 ${big(q.market_cap, m.market)}</span><span>最後交易日 ${esc(q.last_trade)}</span></div>
    </div>
    <div class="right">
      <span class="stamp">分析時間 ${esc(m.generated_at.replace("T", " "))}</span>
      <button class="btn" id="star" title="加入 / 移除觀察清單">${state.watch.some((x) => x.symbol === m.symbol) ? ZH`★ 已在觀察清單` : ZH`☆ 加入觀察清單`}</button>
      <button class="btn" id="agentLog" title="重看這次分析每個步驟做了什麼">分析過程</button>
      <button class="btn" id="exportMd" title="把這份分析下載成 Markdown 報告">匯出報告</button>
      <button class="btn" id="refresh" title="不用快取,重新抓取最新資料">重新分析</button>
    </div>
  </div>
  <div class="tabs" role="navigation" aria-label="分頁">${TABS.map(([k, n]) => `<button data-t="${k}" class="${k === state.tab ? "on" : ""}"${k === state.tab ? ' aria-current="page"' : ""}>${n}</button>`).join("")}</div>
  <div id="modelPick"></div>
  <h2 class="u-sr">${esc(TABS.find((t) => t[0] === state.tab)[1])}</h2>
  <div id="view"></div>
  <div class="disclaimer">資料可能延遲或有誤,重要決策前請查證原始公告。</div>`;
  app.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => { go(m.symbol, b.dataset.t); window.scrollTo(0, 0); }));
  $("#refresh").onclick = async () => { disposeAll(); await load(m.symbol, true); if (state.report && state.symbol === m.symbol) { renderCompany(state.tab); focusTitle(); } };
  $("#exportMd").onclick = () => { exportMd(state.report); toast(ZH`已下載分析報告(Markdown)`); };
  $("#agentLog").onclick = () => AgentRoom.showLog(state.agentLog[m.symbol], (state.report.agents || []));
  $("#star").onclick = () => toggleWatchUI(m.symbol, m.name);
  // 模型分數只在總覽顯示(使用者自己選的模型;沒有模型就顯示引導卡)。其他資料頁不需要模型
  if (state.tab === "overview" || state.tab === "detail") ModelPick.mount($("#modelPick"), R, state);
  const view = $("#view");
  const mod = TABS.find((t) => t[0] === state.tab)[2];
  try { mod.render(view, R, { go, hint, state }); }
  catch (e) { console.error(e); view.innerHTML = ZH`<div class="err">這個分頁顯示時發生錯誤:${esc(e.message)}</div>`; }
}

loadWatch().then(route);
Notice.init();
