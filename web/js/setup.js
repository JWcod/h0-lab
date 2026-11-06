// 第一次執行引導(規格 docs/安裝與上手規格.md §3):首頁引導卡、精靈「還沒有回測資料」說明卡。
// - 狀態來自 GET /api/setup/status(後端只看檔案在不在、不回 token 值);文字一律走 t("setup.*")。
// - 指令可以複製(127.0.0.1 是安全來源,navigator.clipboard 可用;失敗就選取文字讓使用者自己複製)。
import { esc, num } from "./fmt.js";
import { t, ZH } from "./i18n/index.js";
import { ut } from "./update_text.js";

let last = null;

export async function fetchStatus(force = false) {
  if (last && !force) return last;
  const r = await fetch("/api/setup/status");
  if (!r.ok) throw new Error("HTTP " + r.status);
  last = await r.json();
  return last;
}

export const refreshStatus = () => fetchStatus(true).catch(() => null);

const cmdHTML = (c) => `<div class="su-cmd"><code>${esc(c)}</code><button type="button" class="u-btn su-copy" data-copy="${esc(c)}">${esc(t("setup.copy"))}</button></div>`;

export function durationText(min) {
  if (!(min > 0)) return "";
  if (min < 60) return t("setup.minutes", { n: num(min, 0) });
  return t("setup.hours", { n: num(min / 60, min % 60 ? 1 : 0) });
}

function metaText(s) {
  const parts = [s.network ? t("setup.meta.network") : t("setup.meta.local")];
  if (s.network && s.requests > 0) parts.push(t("setup.meta.requests", { n: num(s.requests, 0) }));
  const d = durationText(s.minutes);
  if (d) parts.push(t("setup.meta.about", { d }));
  return parts.join(" · ");
}

// 新使用者的精簡版回測資料:一行指令(scripts/setup_research.py),台股、美股各 40 檔大型股
const RESEARCH_CMD = ".venv/bin/python scripts/setup_research.py";
// 打包的 App(s.job)沒有終端機:改成按鈕,進度由 web/js/update.js 接手
const researchAction = (s) => (s?.job
  ? `<div class="su-acts"><button type="button" class="u-btn is-primary" data-h0-action="research-start">${esc(ut("research.start"))}</button>
      <button type="button" class="u-btn" data-h0-action="research-stop">${esc(ut("research.stop"))}</button></div><p class="su-note" data-h0-research-progress role="status"></p>`
  : cmdHTML(s?.command || RESEARCH_CMD));
const settingsBtn = () => `<div class="su-acts"><button type="button" class="u-btn is-primary" data-h0-action="open-settings">${esc(ut("first.open"))}</button></div>`;
const stepTitle = (s) => (s.id === "sec_user_agent" ? ut("first.sec.title") : t("setup.step." + s.id + ".title"));
function researchBody(s) {
  const vars = { d: durationText(s?.minutes) || "—", b: durationText(s?.build_minutes) || "—" };
  // 上次沒有完全成功:重新開啟後還是要看得到原因,才知道為什麼這一步還在
  const lastResult = s?.last_result ?`<p class="su-warn" role="note">${esc(ut("research.err." + s.last_result))}</p>` : "";
  return `${lastResult}<p class="su-body">${esc(s?.job ? ut("research.body", vars) : t("setup.step.research.body", vars))}</p>
    ${researchAction(s)}
    <p class="su-note">${esc(t("setup.step.research.note"))}</p>`;
}

function stepBody(s) {
  if (s.id === "install") return `<p class="su-body">${esc(t("setup.step.install.body"))}</p>${cmdHTML("./install.sh")}`;
  if (s.id === "first_report") {
    return `<p class="su-body">${esc(t("setup.step.first_report.body"))}</p>
      <div class="su-acts"><button type="button" class="u-btn is-primary" data-s="2330.TW">${esc(t("setup.step.first_report.tw"))}</button>
      <button type="button" class="u-btn" data-s="NVDA">${esc(t("setup.step.first_report.us"))}</button></div>`;
  }
  if (s.id === "sec_user_agent") return `<p class="su-body">${esc(ut("first.sec.body"))}</p>${settingsBtn()}`;
  if (s.id === "finmind_token" && s.settings) return `<p class="su-body">${esc(ut("first.token.body"))}</p>${settingsBtn()}`;
  if (s.id === "finmind_token") return `<p class="su-body">${esc(t("setup.step.finmind_token.body"))}</p>${cmdHTML("FINMIND_TOKEN=")}`;
  if (s.id === "research") return researchBody(s);
  return "";
}

// 首頁引導卡;全部完成回傳空字串(選用的 token 沒填不擋)
export function homeCardHTML(st) {
  if (!st || !Array.isArray(st.steps) || st.all_done) return "";
  const n = (st.pending || []).length;
  const items = st.steps.map((s) => `<li class="su-step${s.done ? " is-done" : ""}">
      <span class="su-mark" aria-hidden="true">${s.done ? "✓" : "○"}</span>
      <div class="su-main"><b>${esc(stepTitle(s))}${s.optional ? ` <span class="su-opt">${esc(t("setup.optional"))}</span>` : ""}</b>
        <span class="u-sr">${esc(s.done ? t("setup.done") : t("setup.todo"))}</span>
        ${s.done ? "" : `<span class="su-meta">${esc(metaText(s))}</span>${stepBody(s)}`}</div></li>`).join("");
  return `<section class="u-card su-card" aria-labelledby="suH">
    <h2 class="su-h" id="suH">${esc(t("setup.title", { n }))}</h2>
    <div class="su-progress"><span class="su-bar" aria-hidden="true"><span style="width:${st.steps.length ? Math.round(100 * st.steps.filter((s) => s.done).length / st.steps.length) : 0}%"></span></span>
      <span>${esc(t("setup.progress", { done: st.steps.filter((s) => s.done).length, total: st.steps.length }))}</span></div>
    <p class="su-lead">${esc(t("setup.lead"))}</p>
    ${st.offline ? `<p class="su-warn" role="note">${esc(t("setup.offline"))}</p>` : ""}
    <ol class="su-steps">${items}</ol>
  </section>`;
}

// 快速設定只建大型 40 檔:回測資料齊了但這個股票池沒有,重跑設定也不會有,不能再叫使用者重建
const needsFullDataHTML = () => `<p class="u-sub">${esc(ZH`這個股票池要完整的研究資料,首頁的快速設定沒有包含,所以這裡算不出來。請先看其他股票池(它們用今天的名單,有存活者偏誤)。`)}</p>`;

export function needsFullData(st, market, universe) {
  const info = (st || last)?.research?.[market];
  return universe === "tw_pit50" && !!info?.ready && !(info.universes || []).includes(universe);
}

// 有哪些股票池有回測資料(工作台選預設用)
export const availableUniverses = (st) => Object.values(st?.research || {}).flatMap((r) => r.universes || []);

// 回測股票池旁邊的固定說明
export const survivorshipNoteHTML = () => `<p class="u-sub">${esc(ZH`「大型 40 檔」用今天的名單,過去下市或被剔除的公司不在裡面,回測結果會偏好看(存活者偏誤)。`)}</p>`;

// 精靈:某個市場還沒有回測資料時的說明卡(取代「沒有 raw_tw.sqlite」這種錯誤)
// short:同一頁上面已經有完整說明時,只放一行(避免同一段指令出現兩次)
export function researchMissingHTML(st, market, rawMsg, short = false, universe = "") {
  if (needsFullData(st, market, universe)) return needsFullDataHTML();
  const s = ((st || last)?.steps || []).find((x) => x.id === "research");
  const title = t("setup.wizard.title", { market: t(market === "US" ? "setup.market.US" : "setup.market.TW") });
  if (/rows_|\.sqlite|python -m/.test(rawMsg || "")) rawMsg = "";   // 檔名與開發指令對使用者沒有意義,上面的說明已經講了要做什麼
  if (short) return `<div class="su-card su-inline" role="note"><b class="su-h">${esc(title)}</b><p class="su-body">${esc(t("setup.wizard.seeAbove"))}</p></div>`;
  return `<div class="su-card su-inline" role="note">
    <b class="su-h">${esc(title)}</b>
    <p class="su-body">${esc(t("setup.wizard.body"))}</p>
    ${researchBody(s)}
    ${rawMsg ? `<p class="su-note">${esc(t("setup.wizard.raw", { msg: rawMsg }))}</p>` : ""}
  </div>`;
}

// 錯誤訊息是不是「這個設定就是出廠模型」(lab/research/model_eval.py _check_user_model 的 409;訊息也提到 backtest_pit,不是缺資料)
export const isShippedModel = (msg) => typeof msg === "string" && /出廠模型/.test(msg) && /不走模型工作台/.test(msg);

// 錯誤訊息是不是「回測資料還沒建立」(後端 lab/research/model_eval.py 的訊息都附 backtest_pit 指令;出廠模型的 409 除外)
export const isResearchMissing = (msg) => typeof msg === "string" && /backtest_pit/.test(msg) && !isShippedModel(msg);

// 設定和出廠模型相同:這裡不重算,白話說明並引導到「研究紀錄」頁(#/backtest)。不附後端原文(只有內部檔名與 holdout 用語)
export function shippedModelHTML() {
  return `<div class="su-card su-inline" role="note">
    <b class="su-h">${esc(t("setup.shipped.title"))}</b>
    <p class="su-body">${esc(t("setup.shipped.body"))}</p>
    <p class="su-body">${esc(t("setup.shipped.tweak"))}</p>
    <a class="u-btn is-primary su-go" href="#/backtest">${esc(t("setup.shipped.go"))}</a>
  </div>`;
}

// 複製按鈕(事件委派;el 是卡片所在的根節點)
export function bindCopy(el) {
  if (el.dataset.copyBound) return;
  el.dataset.copyBound = "1";
  el.addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-copy]");
    if (!b || !el.contains(b)) return;
    try {
      await navigator.clipboard.writeText(b.dataset.copy);
      b.textContent = t("setup.copied");
    } catch (err) {
      const code = b.parentElement?.querySelector("code");
      if (code) { const r = document.createRange(); r.selectNodeContents(code); const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r); }
      b.textContent = t("setup.copyManual");
    }
    setTimeout(() => { b.textContent = t("setup.copy"); }, 2000);
  });
}
