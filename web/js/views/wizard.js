// 建立模型精靈(規格 docs/上手精靈規格.md、docs/介面商業化規格.md)。路由 #/wizard。
// 三步:① 選市場與範本 → ② 依面向分組調整訊號(不看 / 普通 / 很重要,debounce 呼叫 /api/wizard/preview)
//       → ③ 先給一句白話結論,再列今天的排名(前 10 / 後 10)、存成我的模型、看過去表現(先存檔再回測,旁邊固定顯示嘗試次數)。
//       統計細節(勝率、隨機對照、IC 檢定)收在可展開區塊。
// - 重要度換算成權重在後端做(lab/models/wizard.py 的 apply_choices),前端只送選擇。
// - 只顯示分數、等級、排名;不輸出任何買賣字眼。
// - 這一頁新寫的文字都走 i18n 字典 t();後端的多語文字用 loc()。所有插入 HTML 的值都經過 esc(數字也是)。
// - 精靈的狀態放在模組變數 W,換頁再回來會保留。
import { esc, num, pct, isNum } from "../fmt.js";
import { ZH, t as t0, loc, isEn } from "../i18n/index.js";
import * as WA from "../wizardapi.js";
import { PILLAR_LABEL, PILLARS, MODEL_ID_RE, UNIVERSES, productText } from "../modelapi.js";
import { backtestResultHTML } from "./models.js";
import { chart, base, axis, C, fmtAxisPct } from "../charts.js";
import * as Setup from "../setup.js";

// 這一頁改版後的文字(介面商業化規格 §2):先查這裡,沒有才用 i18n 字典。之後由總管併進 i18n/zh-TW.js。
const TX = {
  "wizard.step1": ZH`選因子範本`, "wizard.step2": ZH`調整訊號`, "wizard.step3": ZH`看結果`,
  "wizard.stepSub1": ZH`市場與因子範本`, "wizard.stepSub2": ZH`你在意的訊號`, "wizard.stepSub3": ZH`排名、存檔、回測`,
  "wizard.lead": ZH`三個步驟建立你自己的選股模型:自己選一個因子範本當起點(沒有預設、不推薦),看過權重、調整你在意的訊號,再存成你自己的模型。`,
  "wizard.tpl.startNote": ZH`因子範本只是起點:沒有預設、不推薦,也沒有證實有效。從因子範本建立的模型,嘗試次數從 1 開始算。`,
  "wizard.signals.lead": ZH`每個面向與訊號都可以選「不看 / 普通 / 很重要」。普通 = 因子範本原本的比重,很重要 = 兩倍,不看 = 不算進分數。改了之後會自動重算。`,
  "wizard.status.hash": ZH`設定指紋 {hash}`,
  "wizard.bt.icDetails": ZH`統計細節:IC 與顯著性檢定`,
  "wizard.bt.more": ZH`更多數字:勝率、和隨機選股比較、交易成本`,
  "wizard.sum.h": ZH`結論`,
  "wizard.sum.wait": ZH`正在用你的設定重新計算…`,
  "wizard.sum.none": ZH`還沒有結果;回上一步確認設定沒有問題。`,
  "wizard.sum.rank": ZH`依你的設定,今天{universe}分數最高的是 {names}。這只是依你的選擇重新計算的排名,還沒有證實有效。`,
  "wizard.sum.rankNoName": ZH`已經依你的設定排好今天的名次。這只是重新計算的排名,還沒有證實有效。`,
  "wizard.sum.next": ZH`下一步:存檔並看過去表現,確認這組設定在歷史資料上是不是真的比較好。`,
  "wizard.sum.btNot": ZH`在開發期({from} ~ {to})的模擬裡,你的前 20% 年化 {mine},股票池平均 {pool};但分數和之後報酬的關聯沒有證實有效,這個差距可能只是碰巧。`,
  "wizard.sum.btSig": ZH`在開發期({from} ~ {to})的模擬裡,你的前 20% 年化 {mine},股票池平均 {pool};分數和之後報酬的關聯在開發期統計上為正,但還沒有經過樣本外測試。`,
  "wizard.sum.btNotShort": ZH`回測完成:分數和之後報酬的關聯沒有證實有效。`,
  "wizard.sum.btSigShort": ZH`回測完成:分數和之後報酬的關聯在開發期統計上為正,但還沒有經過樣本外測試。`,
  "wizard.sum.btStale": ZH`設定改過了;上一次回測的結論不適用目前的設定,要看新的請再按一次。`,
  "wizard.sum.trials": ZH`這個模型系列已嘗試 {n} 個設定。`,
};
function t(key, vars) {
  if (!(key in TX)) return t0(key, vars);
  return TX[key].replace(/\{(\w+)\}/g, (m, k) => (vars && k in vars ? String(vars[k]) : m));
}
const CHECK = `<svg class="wz-ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M5 10.5l3.2 3.2L15 7"/></svg>`;

const LEVELS = ["off", "normal", "high"];
const DEBOUNCE_MS = 400;
const MARKETS = ["TW", "US"];

let W = null;

function fresh(market = "TW") {
  return {
    step: 1, market,
    tplList: null, tplErr: "", tplFor: null, tpl: null,
    catalog: null, baseModel: null, setupErr: "", setupFor: null,
    defaults: { pillars: {}, signals: {} }, choices: { pillars: {}, signals: {} },
    pv: null, pvErr: "", pvFor: null, pvSeq: 0, pvTimer: null, pvBusy: false, pvPromise: null,
    savedId: null, savedName: "", savedFor: null, saveErr: "", saving: false, nTrials: null,
    universe: defaultUniverse(market), bt: null, btErr: "", btRunning: false, btFor: null,
    form: { id: "", name: "" },
  };
}
// 正式股票池沒有回測資料、但同市場有別的股票池有(新使用者的精簡版只有大型股池)就用有資料的那個
const defaultUniverse = (market) => {
  const d = market === "US" ? "us_large40" : "tw_pit50";
  const have = SETUP?.research?.[market]?.universes || [];
  return have.length && !have.includes(d) ? have[0] : d;
};
const universesFor = (market) => UNIVERSES.filter(([k]) => k.startsWith(market === "US" ? "us_" : "tw_"));
const universeName = (u) => (UNIVERSES.find(([k]) => k === u) || [u, u])[1];
const choicesJSON = () => JSON.stringify(W.choices);

// ---------- 範本、訊號的欄位整理(後端欄位以規格 §3.3、§4.1 為準,這裡容許 template 欄位包一層) ----------
export function normTemplate(x) {
  const T = x && typeof x.template === "object" && x.template ? { ...x.template, ...x } : (x || {});
  // 後端(lab/models/wizard.py template_summary)回 status: "ready" | "preparing" 與 missing_vars
  const req = Array.isArray(T.missing_vars) ? T.missing_vars : [];
  const notReady = T.status === "preparing" || req.length > 0;
  return {
    id: String(T.id ?? ""), order: isNum(T.order) ? T.order : 99, title: loc(T.title || T.name || T.id),
    forWhom: loc(T.for_whom), focus: loc(T.focus), ignores: loc(T.ignores), rationale: productText(loc(T.rationale)),
    sources: (Array.isArray(T.sources) ? T.sources : []).map((v) => productText(loc(v))).filter(Boolean), requires: req.map(String), ready: !notReady,
  };
}

// 範本模型裡某個訊號的原權重(同一個 key 可能有金融 / 非金融兩筆,取最大)
function tplWeight(model, key, market) {
  let w = 0;
  for (const m of model?.metrics || []) {
    if (m.key !== key || m.enabled === false) continue;
    if (m.market && m.market !== "all" && m.market !== market) continue;
    if (isNum(m.weight)) w = Math.max(w, m.weight);
  }
  return w;
}
function pillarShare(model, p) {
  const lt = model?.pillar_weights?.long_term || {};
  const tot = Object.values(lt).reduce((a, b) => a + (isNum(b) ? b : 0), 0);
  return tot > 0 && isNum(lt[p]) ? lt[p] / tot : 0;
}
function buildDefaults() {
  const d = { pillars: {}, signals: {} };
  // 面向預設一律「普通」(= 範本原樣,評分雜湊和範本相同);長期占比 0 的面向仍可能有短期權重,不能預設成「不看」
  for (const p of PILLARS) d.pillars[p] = "normal";
  for (const s of W.catalog || []) d.signals[s.key] = tplWeight(W.baseModel, s.key, W.market) > 0 ? "normal" : "off";
  return d;
}

// ---------- 畫面 ----------
export function render(el) {
  if (!W) W = fresh();
  el.innerHTML = `<div class="u-ov wz">
    <h1 class="u-largetitle" id="wzTitle" tabindex="-1">${esc(t("wizard.title"))}</h1>
    ${WA.mockOn() ? `<p class="wz-mock" role="note">${esc(t("wizard.mock.banner"))}</p>` : ""}
    <p class="u-sub wz-lead">${esc(t("wizard.lead"))}</p>
    <ol class="wz-steps" id="wzSteps" aria-label="${esc(t("wizard.title"))}"></ol>
    <div id="wzBody"></div>
    <p class="u-disclaimer">${esc(t("wizard.disclaimer"))}</p>
  </div>`;
  // 事件掛在這一頁自己的根節點(#app 會留著給其他頁用,掛在它上面換頁後會重複累積)
  const root = el.querySelector(".wz");
  root.addEventListener("click", (e) => onClick(el, e));
  root.addEventListener("change", (e) => onChange(el, e));
  root.addEventListener("input", (e) => onInput(el, e));
  // 表單裡有 name="id" 的輸入框,form.id 會被它蓋掉,所以一律用 getAttribute("id")
  root.addEventListener("submit", (e) => { if (e.target.getAttribute("id") === "wzSaveForm") { e.preventDefault(); doSave(el); } });
  Setup.bindCopy(root);
  draw(el);
  // 第一次執行引導:回測資料齊不齊(拿到後把第 3 步「沒有 raw_tw.sqlite」這種錯誤換成說明卡)
  Setup.fetchStatus(true).then((st) => {
    SETUP = st;
    // 回測股票池預設:正式股票池沒有資料、但同市場有別的股票池有(新使用者的精簡版)就改用它
    const have = st?.research?.[W.market]?.universes || [];
    if (have.length && !have.includes(W.universe) && !W.bt && !W.btRunning) W.universe = have[0];
    if (W.step !== 3 || !el.isConnected) return;
    const sel = el.querySelector("#wzUniverse"); if (sel) sel.value = W.universe;   // 只換排名區與回測結果區,不重畫存檔表單(使用者可能正在打字)
    const r = el.querySelector("#wzRank"); if (r) r.innerHTML = rankInnerHTML();
    const o = el.querySelector("#wzBtOut"); if (o) o.innerHTML = btOutHTML();
  }).catch(() => {});
}

let SETUP = null;
// 這個市場的回測資料還沒建立(看狀態 API,或後端錯誤訊息附了 backtest_pit 指令)
const researchMissing = (msg) => SETUP?.research?.[W.market]?.ready === false || Setup.isResearchMissing(msg);

function draw(el, focus = false) {
  if (!el.isConnected || !el.querySelector("#wzBody")) return;
  // 像 macOS 設定助理的 1-2-3 進度:做完的步驟可以點回去(和「上一步」一樣,不會清掉設定)
  el.querySelector("#wzSteps").innerHTML = [1, 2, 3].map((n) => {
    const cur = W.step === n, done = W.step > n;
    const inner = `<span class="wz-dot" aria-hidden="true">${done ? CHECK : esc(n)}</span>`
      + `<span class="wz-step-t"><b>${esc(t("wizard.step" + n))}</b><span>${esc(t("wizard.stepSub" + n))}</span></span>`
      + `<span class="u-sr">${esc(t("wizard.stepOf", { n }))}</span>`;
    return `<li class="wz-step${cur ? " is-cur" : ""}${done ? " is-done" : ""}"${cur ? ` aria-current="step"` : ""}>`
      + (done ? `<button type="button" class="wz-step-b" data-goto="${esc(n)}">${inner}</button>` : `<span class="wz-step-b">${inner}</span>`) + `</li>`;
  }).join("");
  const body = el.querySelector("#wzBody");
  if (W.step === 1) { body.innerHTML = step1HTML(); ensureTemplates(el); }
  else if (W.step === 2) { body.innerHTML = step2HTML(); ensureSetup(el); }
  else { body.innerHTML = step3HTML(); syncStep3(el); drawChart(el); }
  if (focus) el.querySelector("#wzTitle")?.focus({ preventScroll: false });
}

const loadingHTML = () => `<div class="loading wz-loading"><div class="spinner"></div>${esc(t("wizard.loading"))}</div>`;
const errHTML = (msg) => `<div class="u-card wz-errbox"><p class="wz-err" role="alert">${esc(t("wizard.loadFail", { msg }))}</p>
  <button type="button" class="u-btn" data-act="retry">${esc(t("wizard.retry"))}</button></div>`;
const seg = (name, cur, attrs, disabledLv = []) => `<div class="u-seg wz-seg" role="group" aria-label="${esc(name)}">${LEVELS.map((lv) =>
  `<button type="button" ${attrs} data-lv="${lv}" aria-pressed="${cur === lv}"${disabledLv.includes(lv) ? " disabled" : ""}>${esc(t("wizard.level." + lv))}</button>`).join("")}</div>`;
const navHTML = (prev, next, nextOk = true) => `<div class="wz-nav">
  ${prev ? `<button type="button" class="u-btn" data-act="prev">${esc(t("wizard.prev"))}</button>` : "<span></span>"}
  ${next ? `<button type="button" class="u-btn is-primary" data-act="next"${nextOk ? "" : " disabled"}>${esc(t("wizard.next"))}</button>` : ""}
</div>`;

// ---------- 第 ① 步 ----------
function step1HTML() {
  const mk = `<div class="wz-market"><span class="u-label" id="wzMkLbl">${esc(t("wizard.market"))}</span>
    <div class="u-seg wz-seg" role="group" aria-labelledby="wzMkLbl">${MARKETS.map((m) =>
      `<button type="button" data-market="${m}" aria-pressed="${W.market === m}">${esc(t("wizard.market." + m))}</button>`).join("")}</div></div>`;
  let list;
  if (W.tplErr) list = errHTML(W.tplErr);
  else if (!W.tplList || W.tplFor !== W.market) list = loadingHTML();
  else if (!W.tplList.length) list = `<p class="u-sub">${esc(t("wizard.tpl.none"))}</p>`;
  else list = `<ul class="wz-tpls" aria-label="${esc(t("wizard.pickTemplate"))}">${W.tplList.map(tplCard).join("")}</ul>`;
  return `${mk}
    <h2 class="u-label u-list-h">${esc(t("wizard.pickTemplate"))}</h2>
    <p class="u-sub wz-hint">${esc(t("wizard.tpl.startNote"))}</p>
    ${list}
    ${navHTML(false, true, !!(W.tpl && W.tpl.ready))}
    ${W.tpl ? "" : `<p class="u-sub wz-navhint">${esc(t("wizard.tpl.needPick"))}</p>`}`;
}

function tplCard(x) {
  const sel = W.tpl?.id === x.id;
  const row = (k, v) => (v ? `<div class="wz-kv"><span class="u-label">${esc(t(k))}</span><span>${esc(v)}</span></div>` : "");
  return `<li><button type="button" class="u-card wz-tpl${sel ? " is-sel" : ""}" data-tpl="${esc(x.id)}" aria-pressed="${sel}"${x.ready ? "" : " disabled aria-disabled=\"true\""}>
    <span class="wz-tpl-h"><b>${esc(x.title)}</b>${x.ready ? "" : `<span class="wz-soon">${esc(t("wizard.tpl.notReady"))}</span>`}<span class="wz-check" aria-hidden="true">${CHECK}</span>${sel ? `<span class="u-sr">${esc(t("wizard.tpl.selected"))}</span>` : ""}</span>
    ${row("wizard.tpl.forWhom", x.forWhom)}${row("wizard.tpl.focus", x.focus)}${row("wizard.tpl.ignores", x.ignores)}
    ${!x.ready && x.requires.length ? `<span class="u-sub wz-src">${esc(t("wizard.tpl.notReadyWhy", { vars: x.requires.join("、") }))}</span>` : ""}
  </button>
  ${x.rationale || x.sources.length ? `<details class="wz-more"><summary>${esc(t("wizard.tpl.more"))}</summary>
    ${x.rationale ? `<p class="u-sub wz-why">${esc(x.rationale)}</p>` : ""}
    ${x.sources.length ? `<p class="u-label">${esc(t("wizard.tpl.sources"))}</p><ul class="wz-src-list">${x.sources.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>` : ""}
  </details>` : ""}</li>`;
}

async function ensureTemplates(el) {
  if (W.tplErr || (W.tplList && W.tplFor === W.market) || W.tplLoading === W.market) return;
  const my = W, market = W.market;
  my.tplLoading = market;
  try {
    const j = await WA.templates(market);
    if (W !== my || my.market !== market) return;
    my.tplList = (j.templates || []).map(normTemplate).filter((x) => x.id).sort((a, b) => a.order - b.order);
    my.tplFor = market;
    if (my.tpl) my.tpl = my.tplList.find((x) => x.id === my.tpl.id && x.ready) || null;
  } catch (e) {
    if (W !== my || my.market !== market) return;
    my.tplErr = e.message;
  }
  my.tplLoading = null;
  if (W.step === 1) draw(el);
}

// ---------- 第 ② 步 ----------
async function ensureSetup(el) {
  const key = `${W.market}|${W.tpl?.id}`;
  if (W.setupErr || W.setupFor === key || W.setupLoading === key) return;
  const my = W;
  my.setupLoading = key;
  try {
    const [cat, pv] = await Promise.all([WA.catalog(my.market), WA.preview(my.tpl.id, my.market, {})]);
    if (W !== my || `${my.market}|${my.tpl?.id}` !== key) return;
    my.baseModel = pv.model || null;
    // 目錄是所有範本的聯集;只留這個範本、這個市場有的訊號(後端遇到範本沒有的 key 會報錯)
    const has = new Set((my.baseModel?.metrics || []).filter((m) => !m.market || m.market === "all" || m.market === my.market).map((m) => m.key));
    my.catalog = (cat.signals || cat.items || []).filter((s) => s && s.key && has.has(s.key) && (!Array.isArray(s.markets) || s.markets.includes(my.market)));
    my.defaults = buildDefaults();
    my.choices = JSON.parse(JSON.stringify(my.defaults));
    my.setupFor = key;
    acceptPreview(pv, choicesJSON());
  } catch (e) {
    if (W !== my) return;
    my.setupErr = e.message;
  }
  my.setupLoading = null;
  if (W.step === 2) draw(el);
}

function step2HTML() {
  if (W.setupErr) return errHTML(W.setupErr) + navHTML(true, false);
  if (W.setupFor !== `${W.market}|${W.tpl?.id}`) return loadingHTML();
  const groups = PILLARS.filter((p) => pillarShare(W.baseModel, p) > 0 || W.catalog.some((s) => s.pillar === p));
  return `<p class="u-sub wz-hint">${esc(t("wizard.signals.lead"))}</p>
    <div class="wz-bar u-card"><b>${esc(W.tpl.title)}</b><span class="wz-unval">${esc(t("wizard.unvalidated"))}</span>
      <span class="wz-status" id="wzStatus" role="status" aria-live="polite">${statusHTML()}</span>
      <button type="button" class="u-btn" data-act="reset">${esc(t("wizard.signals.reset"))}</button></div>
    ${groups.map(pillarHTML).join("")}
    ${navHTML(true, true, previewOk())}`;
}

function pillarHTML(p) {
  const share = pillarShare(W.baseModel, p);
  const name = PILLAR_LABEL[p] || p;
  const sigs = W.catalog.filter((s) => s.pillar === p);
  const unused = share <= 0;
  return `<section class="u-card wz-pillar" aria-labelledby="wzP-${esc(p)}">
    <div class="wz-pillar-h">
      <h2 class="wz-h2" id="wzP-${esc(p)}">${esc(name)}</h2>
      <span class="u-sub">${unused ? "" : esc(t("wizard.pillar.share", { pct: Math.round(share * 100) + "%" }))}</span>
      ${seg(t("wizard.pillar.levelLabel", { name }), W.choices.pillars[p], `data-pillar="${esc(p)}"`)}
    </div>
    ${unused ? `<p class="u-sub wz-note">${esc(t("wizard.pillar.unused"))}</p>` : ""}
    ${sigs.length ? `<ul class="wz-sigs">${sigs.map(signalHTML).join("")}</ul>` : `<p class="u-sub">${esc(t("wizard.signal.none"))}</p>`}
  </section>`;
}

function signalHTML(s) {
  const name = loc(s.plain_name) || s.key;
  const good = loc(s.good_when?.text ?? s.good_when);
  return `<li class="wz-sig">
    <div class="wz-sig-h"><b>${esc(name)}</b>${s.backtestable === false ? `<span class="wz-nobt">${esc(t("wizard.signal.noBacktest"))}</span>` : ""}</div>
    ${loc(s.one_liner) ? `<p class="wz-sig-d">${esc(loc(s.one_liner))}</p>` : ""}
    ${good ? `<p class="u-sub">${esc(t("wizard.signal.goodWhen", { txt: good }))}</p>` : ""}
    <p class="u-sub">${esc(t("wizard.signal.inTemplate", { level: t("wizard.level." + (W.defaults.signals[s.key] || "off")) }))}</p>
    ${seg(t("wizard.signal.levelLabel", { name }), W.choices.signals[s.key], `data-sig="${esc(s.key)}"`)}
  </li>`;
}

const previewOk = () => !!(W.pv && W.pv.ok !== false && W.pvFor === choicesJSON() && !W.pvErr);
function statusHTML() {
  if (W.pvBusy || W.pvFor !== choicesJSON()) return `<span class="wz-busy">${esc(t("wizard.status.checking"))}</span>`;
  if (W.pvErr) return `<span class="wz-err">${esc(t("wizard.status.err", { msg: W.pvErr }))}</span>`;
  if (W.pv && W.pv.ok === false) return `<span class="wz-err">${esc(t("wizard.status.err", { msg: W.pv.error || "" }))}</span>`;
  const h = W.pv?.score_hash || W.pv?.validation?.score_hash;
  return `<span class="wz-ok">${esc(t("wizard.status.ok"))}</span>${h ? ` <span class="u-sub">${esc(t("wizard.status.hash", { hash: h }))}</span>` : ""}`;
}

// 後端可能把驗證結果放在最上層(ok / error)或 validation 底下,這裡統一成最上層
function acceptPreview(pv, forJSON) {
  const v = pv?.validation || {};
  W.pv = { ...pv, ok: pv?.ok ?? v.ok, error: pv?.error ?? v.error, score_hash: pv?.score_hash ?? v.score_hash };
  W.pvFor = forJSON; W.pvErr = "";
}

function allPillarsOff() { return PILLARS.every((p) => W.choices.pillars[p] === "off" || pillarShare(W.baseModel, p) <= 0); }

function schedulePreview(el, delay = DEBOUNCE_MS) {
  const my = W;
  clearTimeout(my.pvTimer);
  my.pvTimer = setTimeout(() => { if (W === my) runPreview(el); }, delay);   // 換範本 / 市場後舊計時器不作用在新狀態
  refreshStatus(el);
}
function runPreview(el) {
  clearTimeout(W.pvTimer);
  const my = W, seq = ++my.pvSeq, forJSON = choicesJSON();
  if (allPillarsOff()) {   // 前端先擋,不送請求(後端也會回同樣的錯誤)
    my.pv = { ok: false, error: t("wizard.status.allOff") }; my.pvFor = forJSON; my.pvErr = ""; my.pvBusy = false;
    refreshStatus(el);
    return Promise.resolve();
  }
  my.pvBusy = true;
  refreshStatus(el);
  my.pvPromise = WA.preview(my.tpl.id, my.market, JSON.parse(forJSON)).then((pv) => {
    if (W !== my || seq !== my.pvSeq) return;
    acceptPreview(pv, forJSON);
  }, (e) => {
    if (W !== my || seq !== my.pvSeq) return;
    my.pvErr = e.message; my.pvFor = forJSON;
  }).finally(() => {
    if (W !== my || seq !== my.pvSeq) return;
    my.pvBusy = false;
    refreshStatus(el);
  });
  return my.pvPromise;
}
// 只更新狀態、下一步按鈕與第 ③ 步的排名區(不重畫整頁,輸入焦點與捲動位置不會跑掉)
function refreshStatus(el) {
  const st = el.querySelector("#wzStatus");
  if (st) st.innerHTML = statusHTML();
  const next = el.querySelector('[data-act="next"]');
  if (next && W.step === 2) next.disabled = !previewOk();
  if (W.step === 3) {
    const r = el.querySelector("#wzRank"); if (r) r.innerHTML = rankInnerHTML();
    updateSum(el);
    syncStep3(el);
  }
}

// ---------- 第 ③ 步 ----------
function step3HTML() {
  return `<section class="u-card wz-sum" aria-labelledby="wzSumH">
      <h2 class="wz-sum-h" id="wzSumH">${esc(t("wizard.sum.h"))} <span class="wz-unval">${esc(t("wizard.unvalidated"))}</span></h2>
      <div id="wzSum" aria-live="polite">${sumHTML()}</div>
    </section>
    <section class="u-card wz-rank" aria-labelledby="wzRankH">
      <h2 class="wz-h2" id="wzRankH">${esc(t("wizard.rank.title"))} <span class="wz-unval">${esc(t("wizard.unvalidated"))}</span></h2>
      <p class="u-sub">${esc(t("wizard.rank.lead"))}</p>
      <div id="wzRank" aria-live="polite">${rankInnerHTML()}</div>
    </section>
    <section class="u-card wz-save" aria-labelledby="wzSaveH">${saveHTML()}</section>
    <section class="u-card wz-bt" aria-labelledby="wzBtH">${btHTML()}</section>
    ${navHTML(true, false)}`;
}

// 第 ③ 步最上面的一句白話結論:回測過就講回測(先講數字、再講有沒有證實有效),沒有就講今天的排名。
// 只用回應裡現成的數字(normPlain、verdict、信賴區間),前端不另外計算。
function sumHTML() {
  if (!W.pv || W.pv.ok === false || W.pvErr) return `<p class="wz-sum-t">${esc(t("wizard.sum.none"))}</p>`;
  if (W.pvBusy || W.pvFor !== choicesJSON()) return `<p class="wz-sum-t">${esc(t("wizard.sum.wait"))}</p>`;
  const trials = isNum(W.nTrials) ? ` <span class="wz-sum-n">${esc(t("wizard.sum.trials", { n: W.nTrials }))}</span>` : "";
  if (W.bt && !W.btRunning && !W.btErr) {
    if (W.btFor !== choicesJSON()) return `<p class="wz-sum-t">${esc(t("wizard.sum.btStale"))}</p>`;
    const sig = !btNotProven(W.bt);
    const p = W.bt.plain ? normPlain(W.bt.plain) : null;
    const mine = p?.stats?.top?.cagr, pool = p?.stats?.pool?.cagr;
    const txt = p && isNum(mine) && isNum(pool) && p.start && p.end
      ? t(sig ? "wizard.sum.btSig" : "wizard.sum.btNot", { from: p.start, to: p.end, mine: pct(mine, 1, true), pool: pct(pool, 1, true) })
      : t(sig ? "wizard.sum.btSigShort" : "wizard.sum.btNotShort");
    return `<p class="wz-sum-t">${esc(txt)}</p>${trials ? `<p class="u-sub wz-sum-sub">${trials}</p>` : ""}`;
  }
  const { top } = rankRows(W.pv.ranking);
  const names = top.slice(0, 3).map((r) => r.name || r.symbol).filter(Boolean);
  const R = W.pv.ranking || {};
  const uni = R.universe_name || universeName(R.universe || "") || "";
  const txt = names.length ? t("wizard.sum.rank", { universe: uni ? ZH`在「${uni}」裡` : "", names: names.join(isEn ? ", " : "、") }) : t("wizard.sum.rankNoName");
  return `<p class="wz-sum-t">${esc(txt)}</p><p class="u-sub wz-sum-sub">${esc(t("wizard.sum.next"))}${trials}</p>`;
}
function updateSum(el) {
  const s = el.querySelector("#wzSum");
  if (s) s.innerHTML = sumHTML();
}

export function rankRows(ranking) {
  if (!ranking) return { top: [], bottom: [] };
  const withRank = (arr, start) => arr.map((r, i) => ({ ...r, rank: isNum(r.rank) ? r.rank : isNum(start) ? start + i : "—" }));   // 不知道總檔數時,後 10 名的名次顯示「—」
  if (Array.isArray(ranking.top) || Array.isArray(ranking.bottom)) {
    const n = ranking.n;
    const bottom = ranking.bottom || [];
    return { top: withRank((ranking.top || []).slice(0, 10), 1), bottom: withRank(bottom.slice(-10), isNum(n) ? n - Math.min(10, bottom.length) + 1 : null) };
  }
  const rows = (ranking.rows || ranking.items || []).filter((r) => r && isNum(r.score)).slice().sort((a, b) => b.score - a.score);
  const ranked = withRank(rows, 1);
  return { top: ranked.slice(0, 10), bottom: rows.length > 10 ? ranked.slice(Math.max(10, rows.length - 10)) : [] };
}

function rankInnerHTML() {
  const stale = W.pvBusy || W.pvFor !== choicesJSON();
  if (!W.pv || W.pv.ok === false || W.pvErr) return `<p class="u-sub">${esc(t("wizard.rank.empty"))}</p>`;
  const R = W.pv.ranking;
  const warn = (Array.isArray(W.pv.warnings) ? W.pv.warnings : []).map((w) => `<p class="wz-warn">${esc(loc(w) || w)}</p>`).join("");
  const { top, bottom } = rankRows(R);
  if (!top.length) {
    const msg = W.pv.ranking_error ? loc(W.pv.ranking_error) || String(W.pv.ranking_error) : "";
    if (msg && researchMissing(msg)) return warn + Setup.researchMissingHTML(SETUP, W.market, msg);
    return `${warn}<p class="u-sub">${esc(msg ? t("wizard.rank.error", { msg }) : t("wizard.rank.empty"))}</p>`;
  }
  const n = R.n ?? (R.rows || R.items || []).length;
  const table = (cap, rows) => `<div class="wz-rank-col"><h3 class="wz-h3">${esc(cap)}</h3><div class="tbl-wrap"><table class="wz-table">
    <thead><tr><th class="n">${esc(t("wizard.rank.col.rank"))}</th><th>${esc(t("wizard.rank.col.stock"))}</th><th class="n">${esc(t("wizard.rank.col.score"))}</th><th>${esc(t("wizard.rank.col.grade"))}</th></tr></thead>
    <tbody>${rows.map((r) => `<tr><td class="n">${esc(r.rank)}</td><td>${r.name ? `<b>${esc(r.name)}</b> ` : ""}<span class="u-num wz-sym">${esc(r.symbol || "")}</span></td>`
      + `<td class="n">${esc(num(r.lt_score ?? r.score, 1))}</td><td>${esc(r.grade || "—")}</td></tr>`).join("")}</tbody></table></div></div>`;
  return `${stale ? `<p class="wz-busy">${esc(t("wizard.rank.stale"))}</p>` : ""}${warn}
    <p class="u-sub wz-meta"><b class="wz-date">${esc(t("wizard.rank.asOf", { date: R.as_of || R.date || R.data_date || W.pv.as_of || t("wizard.rank.noDate") }))}</b> · ${esc(t("wizard.rank.universe", { name: R.universe_name || universeName(R.universe || ""), n }))}</p>
    <div class="wz-rank-cols">${table(t("wizard.rank.top"), top)}${bottom.length ? table(t("wizard.rank.bottom"), bottom) : ""}</div>`;
}

const savedDirty = () => !!W.savedId && W.savedFor !== choicesJSON();
// 和伺服器 wizard.default_name 同規則:去掉範本名稱結尾的括號說明(「籌碼(只有台股)」→「我的籌碼」)
const defaultName = () => t("wizard.save.defaultName", { name: (W.tpl?.title || "").replace(/\s*[((][^()()]*[))]\s*$/, "") || W.tpl?.title || "" });
function saveHTML() {
  const f = W.form;
  return `<h2 class="wz-h2" id="wzSaveH">${esc(t("wizard.save.title"))}</h2>
    <p class="u-sub">${esc(t("wizard.save.lead"))}</p>
    ${W.savedId ? `<p class="wz-ok">${esc(t("wizard.save.done", { name: W.savedName, id: W.savedId }))}</p>` : ""}
    <p class="wz-dirty" id="wzSaveDirty">${savedDirty() ? esc(t("wizard.save.dirty")) : ""}</p>
    <form id="wzSaveForm" class="wz-form" novalidate>
      <label class="wz-field"><span>${esc(t("wizard.save.name"))}</span><input class="u-input" name="name" maxlength="60" autocomplete="off"
        placeholder="${esc(defaultName())}" value="${esc(f.name || W.savedName)}"><span class="u-sub">${esc(t("wizard.save.nameHint"))}</span></label>
      ${W.savedId ? "" : `<details class="wz-adv"${f.id ? " open" : ""}><summary>${esc(t("wizard.save.adv"))}</summary>
        <label class="wz-field"><span>${esc(t("wizard.save.id"))}</span><input class="u-input" name="id" maxlength="48" autocomplete="off" spellcheck="false"
          placeholder="${esc(t("wizard.save.idPh"))}" value="${esc(f.id)}"><span class="u-sub">${esc(t("wizard.save.idHint"))}</span></label></details>`}
      <p class="wz-err" id="wzSaveErr" role="alert">${esc(W.saveErr)}</p>
      <div class="wz-btns">
        <button type="submit" class="u-btn is-primary" id="wzSaveBtn">${esc(W.savedId ? t("wizard.save.again") : t("wizard.save.btn"))}</button>
        ${W.savedId ? `<button type="button" class="u-btn" data-act="workbench">${esc(t("wizard.save.toWorkbench"))}</button>` : ""}
      </div>
    </form>`;
}

const trialsTxt = () => (isNum(W.nTrials) ? t("wizard.bt.trials", { n: W.nTrials }) : t("wizard.bt.trialsNone"));
function btHTML() {
  const us = universesFor(W.market);
  return `<h2 class="wz-h2" id="wzBtH">${esc(t("wizard.bt.title"))}</h2>
    <p class="u-sub">${esc(t("wizard.bt.lead"))}</p>
    <div class="wz-inline">
      <label class="wz-field"><span>${esc(t("wizard.bt.universe"))}</span><select class="u-input" id="wzUniverse">${us.map(([k, n]) =>
        `<option value="${esc(k)}"${k === W.universe ? " selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
      <div class="wz-btns"><button type="button" class="u-btn is-primary" id="wzBtRun" data-act="backtest"></button>
        <span class="wz-trials" id="wzTrials" aria-live="polite">${esc(trialsTxt())}</span></div>
    </div>
    <p class="u-sub">${esc(t("wizard.bt.countNote"))}</p>
    ${Setup.survivorshipNoteHTML()}
    <p class="wz-dirty" id="wzBtHint"></p>
    <div id="wzBtOut" aria-live="polite">${btOutHTML()}</div>`;
}

function syncStep3(el) {
  const b = el.querySelector("#wzBtRun");
  if (b) {
    const needSave = !W.savedId || savedDirty();
    b.textContent = W.btRunning ? t("wizard.bt.running") : needSave ? t("wizard.bt.btnSave") : t("wizard.bt.btn");
    b.disabled = W.btRunning || W.saving || !previewOk();
    b.setAttribute("aria-busy", String(W.btRunning));
  }
  const s = el.querySelector("#wzSaveBtn");
  if (s) s.disabled = W.saving || W.btRunning || !previewOk();
  const d = el.querySelector("#wzSaveDirty");
  if (d) d.textContent = savedDirty() ? t("wizard.save.dirty") : "";
  const h = el.querySelector("#wzBtHint");
  if (h) h.textContent = !W.savedId ? t("wizard.bt.needSave") : "";
  const tr = el.querySelector("#wzTrials");
  if (tr) tr.textContent = trialsTxt();
  updateSum(el);
  const u = el.querySelector("#wzUniverse");
  if (u) u.disabled = W.btRunning;
}

function btOutHTML() {
  if (W.btRunning) return `<p class="wz-busy" role="status"><span class="wz-spin" aria-hidden="true"></span>${esc(t("wizard.bt.running"))}</p>`;
  // 回測資料還沒建立:排名區已經有完整說明(狀態 API 說這個市場不齊)就只放一行
  if (W.btErr && Setup.isShippedModel(W.btErr)) return Setup.shippedModelHTML();
  if (W.btErr && Setup.isResearchMissing(W.btErr)) return Setup.researchMissingHTML(SETUP, W.market, W.btErr, SETUP?.research?.[W.market]?.ready === false, W.universe);
  if (W.btErr) return `<p class="wz-err" role="alert">${esc(t("wizard.bt.fail", { msg: W.btErr }))}</p>`;
  if (!W.bt) return "";
  const stale = W.btFor !== choicesJSON();
  return (stale ? `<p class="wz-dirty">${esc(t("wizard.bt.stale"))}</p>` : "") + backtestOutHTML(W.bt);
}

// 回測回應 → HTML(純函式)。有 plain 就畫白話版,IC 結果收進「進階」;沒有 plain 只顯示現有 IC 結果。
export function backtestOutHTML(r) {
  const p = r && r.plain ? normPlain(r.plain) : null;
  if (!p) return `${r?.status === ZH`開發集` ? `<p class="u-sub">${esc(r?.plain?.note ? t("wizard.bt.plainNote", { msg: r.plain.status ? `${r.plain.status}:${r.plain.note}` : r.plain.note }) : t("wizard.bt.noPlain"))}</p>` : ""}${backtestResultHTML(r || {})}`;
  const notProven = btNotProven(r);
  const span = p.start && p.end ? t("wizard.bt.devOnly", { from: p.start, to: p.end }) : t("wizard.bt.devOnlyNoSpan");
  const lines = [["top", "wizard.bt.line.top"], ["pool", "wizard.bt.line.pool"], ["market", "wizard.bt.line.market"]];
  const st = p.stats;
  return `<div class="wz-plain">
    <p class="wz-fixed">${esc(t("wizard.bt.fixed"))}</p>
    ${notProven ? `<p class="wz-notproven">${esc(t("wizard.bt.notProven"))}</p>` : ""}
    <p class="u-sub">${esc(span)}</p>
    ${p.curve ? `<h3 class="wz-h3">${esc(t("wizard.bt.chart"))}</h3><div class="wz-chart" id="wzBtChart" role="img" aria-label="${esc(t("wizard.bt.chartAlt"))}"></div>` : ""}
    <div class="tbl-wrap"><table class="wz-table"><thead><tr><th>${esc(t("wizard.bt.col.group"))}</th><th class="n">${esc(t("wizard.bt.col.cagr"))}</th><th class="n">${esc(t("wizard.bt.col.mdd"))}</th></tr></thead><tbody>
      ${lines.filter(([k]) => st[k]).map(([k, lbl]) => `<tr><th scope="row">${esc(t(lbl))}</th><td class="n">${esc(pct(st[k].cagr, 1, true))}</td><td class="n">${esc(pct(st[k].mdd, 1))}</td></tr>`).join("")}
    </tbody></table></div>
    ${isNum(p.winRate) || p.random || isNum(p.costBuy) ? `<details class="wz-adv"><summary>${esc(t("wizard.bt.more"))}</summary>
    ${isNum(p.winRate) ? `<p class="wz-line">${esc(isNum(p.nMonths) ? t("wizard.bt.win", { pct: pct(p.winRate, 0), n: p.nMonths }) : t("wizard.bt.winNoN", { pct: pct(p.winRate, 0) }))}</p>` : ""}
    ${p.random ? `<p class="wz-line">${esc(t("wizard.bt.random", { mine: pct(p.random.mine, 1, true), median: pct(p.random.median, 1, true), lo: pct(p.random.lo, 1, true), hi: pct(p.random.hi, 1, true) }))}</p>` : ""}
    ${isNum(p.costBuy) ? `<p class="u-sub">${esc(t("wizard.bt.cost", { buy: pct(p.costBuy, 2), sell: pct(p.costSell, 2) }))}</p>` : ""}
    </details>` : ""}
    <details class="wz-adv"><summary>${esc(t("wizard.bt.icDetails"))}</summary>${backtestResultHTML(r)}</details>
    ${r.disclaimer ? `<p class="u-sub wz-disc">${esc(r.disclaimer)}</p>` : ""}
  </div>`;
}

const btNotProven = (r) => r?.metrics?.lt_12m?.mine?.verdict !== ZH`顯著為正` || icCiHasZero(r);
function icCiHasZero(r) {
  const ci = r?.metrics?.lt_12m?.mine?.ci95;
  return Array.isArray(ci) && isNum(ci[0]) && isNum(ci[1]) && ci[0] <= 0 && ci[1] >= 0;
}

// plain 區塊(規格 §5,lab/research/model_eval.py 的 plain_summary)整理成畫面用的結構。
// curves 是每個持有月月底的累積報酬,開頭補 start(=0),圖才有起點。
// 隨機對照是「扣成本前」,所以要和 top_gross_cagr 比,不能和扣成本後的年化報酬並列。
export function normPlain(pl) {
  if (!pl || typeof pl !== "object" || pl.status !== "ok") return null;
  const cv = pl.curves || {};
  const months = Array.isArray(pl.months) ? pl.months.map(String) : [];
  const lead = pl.start ? [0] : [];
  const ser = (a) => (Array.isArray(a) ? [...lead, ...a.map((v) => (isNum(v) ? v : null))] : null);
  const curve = months.length ? { dates: pl.start ? [String(pl.start), ...months] : months,
    top: ser(cv.top), pool: ser(cv.universe), market: ser(cv.bench) } : null;
  const stats = {};
  for (const [k, src] of [["top", "top"], ["pool", "universe"], ["market", "bench"]]) {
    const x = (pl.stats || {})[src];
    if (x && (isNum(x.cagr) || isNum(x.max_dd))) stats[k] = { cagr: x.cagr, mdd: x.max_dd };
  }
  const rd = pl.random_cagr;
  const random = rd && isNum(rd.median) ? { median: rd.median, lo: rd.p2_5, hi: rd.p97_5, mine: pl.top_gross_cagr } : null;
  const c = pl.costs || {};
  return {
    start: pl.first_month || "", end: pl.last_month || "", curve, stats, random,
    winRate: pl.win_share_vs_universe, nMonths: months.length, costBuy: c.buy, costSell: c.sell, note: pl.note || "",
  };
}

function drawChart(el) {
  if (W.step !== 3 || !W.bt || W.btRunning) return;
  const box = el.querySelector("#wzBtChart");
  const p = normPlain(W.bt.plain);
  if (!box || !p || !p.curve) return;
  const cv = p.curve;
  const defs = [["top", "wizard.bt.line.top", C.accent, "solid", 3], ["pool", "wizard.bt.line.pool", C.watch, "dashed", 2], ["market", "wizard.bt.line.market", C.pos, "dotted", 2]];
  const series = defs.filter(([k]) => cv[k]).map(([k, lbl, color, type, width]) => ({
    name: t(lbl), type: "line", data: cv[k], showSymbol: false, connectNulls: false, lineStyle: { type, width, color }, itemStyle: { color },
  }));
  chart(box, base({
    legend: { data: series.map((s) => s.name), textStyle: { color: C.text2 }, top: 4 },
    grid: { left: 56, right: 20, top: 40, bottom: 32 },
    tooltip: { trigger: "axis", backgroundColor: C.tip, borderColor: C.line, textStyle: { color: C.text, fontSize: 12 }, valueFormatter: (v) => pct(v, 1, true) },
    xAxis: { type: "category", data: cv.dates, ...axis() },
    yAxis: { type: "value", ...axis({ axisLabel: { color: C.label, fontSize: 11, formatter: fmtAxisPct } }) },
    series,
  }));
}

// ---------- 事件 ----------
function onClick(el, e) {
  const b = e.target.closest("button");
  if (!b || b.disabled || !el.contains(b)) return;
  if (b.dataset.market) {
    if (b.dataset.market === W.market) return;
    W = fresh(b.dataset.market);
    draw(el);
  } else if (b.dataset.tpl) {
    const x = (W.tplList || []).find((y) => y.id === b.dataset.tpl);
    if (!x || !x.ready) return;
    if (W.tpl?.id !== x.id) {
      const keep = { tplList: W.tplList, tplFor: W.tplFor };
      W = { ...fresh(W.market), ...keep, tpl: x };
    }
    draw(el);
  } else if (b.dataset.pillar && b.dataset.lv) {
    W.choices.pillars[b.dataset.pillar] = b.dataset.lv;
    setPressed(b);
    schedulePreview(el);
  } else if (b.dataset.sig && b.dataset.lv) {
    W.choices.signals[b.dataset.sig] = b.dataset.lv;
    setPressed(b);
    schedulePreview(el);
  } else if (b.dataset.act === "reset") {
    W.choices = JSON.parse(JSON.stringify(W.defaults));
    draw(el);
    schedulePreview(el, 0);
  } else if (b.dataset.act === "next") {
    if (W.step === 1 && W.tpl?.ready) { W.step = 2; draw(el, true); }
    else if (W.step === 2 && previewOk()) { W.step = 3; draw(el, true); }
  } else if (b.dataset.goto) {
    const n = Number(b.dataset.goto);
    if (n >= 1 && n < W.step) { W.step = n; draw(el, true); }
  } else if (b.dataset.act === "prev") {
    W.step = Math.max(1, W.step - 1); draw(el, true);
  } else if (b.dataset.act === "retry") {
    W.tplErr = ""; W.setupErr = ""; draw(el);
  } else if (b.dataset.act === "backtest") {
    runBacktest(el);
  } else if (b.dataset.act === "workbench") {
    if (W.savedId) location.hash = `#/models/${encodeURIComponent(W.savedId)}`;
  }
}
function setPressed(b) {
  b.parentElement.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
}
function onChange(el, e) {
  if (e.target.id === "wzUniverse") { W.universe = e.target.value; }
}
function onInput(el, e) {
  const f = e.target.form;
  if (f && f.getAttribute("id") === "wzSaveForm" && (e.target.name === "id" || e.target.name === "name")) W.form[e.target.name] = e.target.value;
}

// ---------- 存檔、回測 ----------
async function ensureFreshPreview(el) {
  if (W.pvFor !== choicesJSON() || W.pvBusy) await runPreview(el);
  return previewOk();
}

// 回傳 true = 存檔成功(或已是最新的存檔)
async function doSave(el) {
  if (W.saving) return false;
  const my = W;
  const err = (msg) => { my.saveErr = msg; const n = el.querySelector("#wzSaveErr"); if (n) n.textContent = msg; return false; };
  // 代號:已存過就沿用(存成新的版本);進階裡有自訂就用自訂(已經有人用就由伺服器回錯誤);都沒有就由伺服器產生
  const custom = (my.form.id || "").trim();
  const id = my.savedId || custom;
  const name = (my.form.name || my.savedName || "").trim() || defaultName();
  if (!my.savedId && custom && !MODEL_ID_RE.test(custom)) return err(t("wizard.save.badId"));
  my.saveErr = ""; my.saving = true; err("");
  syncStep3(el);
  try {
    if (!(await ensureFreshPreview(el))) return err(t("wizard.save.invalid"));
    // 預覽確認後立刻記下要存的模型與它對應的設定(之後的 await 期間使用者可能又改了設定;Verifier 問題 1)
    const forJSON = my.pvFor;
    const model = JSON.parse(JSON.stringify(my.pv.model));
    delete model.template;   // 範本說明欄位只屬於範本檔
    model.id = id; model.family_id = id; model.name = name;
    const opts = my.savedId ? {} : custom ? { create_only: true } : { auto_id: true };
    const res = await WA.saveModel(model, opts);
    const sid = res?.saved?.id || id;
    if (!sid) return err(t("wizard.save.fail", { msg: t("wizard.save.noId") }));
    my.savedId = sid; my.savedName = res?.saved?.name || name; my.savedFor = forJSON; my.form.name = my.savedName;
    try {
      const info = await WA.listModels();
      const s = (info.models || []).find((m) => m.id === sid);
      if (s && isNum(s.n_trials)) my.nTrials = s.n_trials;
    } catch (e2) { /* 嘗試次數更新失敗不影響存檔 */ }
    return true;
  } catch (e) {
    return err(t("wizard.save.fail", { msg: e.message }));
  } finally {
    my.saving = false;
    if (W === my && W.step === 3 && el.querySelector("#wzBody")) {
      const sv = el.querySelector(".wz-save");
      if (sv) sv.innerHTML = saveHTML();
      syncStep3(el);
    }
  }
}

async function runBacktest(el) {
  if (W.btRunning || W.saving) return;
  const my = W;
  if (!my.savedId || savedDirty()) {
    if (!(await doSave(el))) return;
  }
  if (W !== my) return;
  const forJSON = my.savedFor;
  my.btRunning = true; my.btErr = ""; my.bt = null;
  updateBt(el);
  try {
    my.bt = await WA.backtest(my.savedId, my.universe);
    my.btFor = forJSON;
    if (isNum(my.bt?.n_trials)) my.nTrials = my.bt.n_trials;
  } catch (e) { my.btErr = e.message; }
  my.btRunning = false;
  if (W !== my) return;
  updateBt(el);
}
function updateBt(el) {
  if (W.step !== 3) return;
  const o = el.querySelector("#wzBtOut");
  if (o) o.innerHTML = btOutHTML();
  syncStep3(el);
  drawChart(el);
}

// 測試與除錯用:讀目前狀態、重設精靈
export const _debug = { get state() { return W; }, reset() { W = null; } };
