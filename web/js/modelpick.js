// 使用者目前選定的模型(總覽、市場掃描共用)。2026-11-03「自建模型優先」:開源版不提供內建模型的個股評等,
// 分數只來自使用者自己存的模型;沒有模型時顯示引導卡,連到建立模型的精靈,其餘資料照常顯示。
// - 清單只列使用者自己的模型(GET /api/models 已經不列內建模型);選擇記在瀏覽器(h0lab.model),只是畫面偏好。
// - 總覽的分數:POST /api/models/validate 帶 symbol,伺服器只用記憶體裡已經算好的報告重算(不抓資料、不重建報告)。
//   只顯示分數與等級(沿用自訂模型的顯示 API.customScoreHTML),固定標「未驗證」,不是買賣建議。
// - 結果在前端快取(股票 + 報告時間 + 模型 + 存檔雜湊與更新日),切分頁不重打 API。
// 不用 GET /api/report?model=:記憶體快取過期時會在背景重建整份報告。
import { ZH } from "./i18n/index.js";
import { esc } from "./fmt.js";
import * as API from "./modelapi.js";

const LS_KEY = "h0lab.model";
let seq = 0;
const cache = new Map();   // key → custom_model(apply.rescore 的輸出)
const CACHE_MAX = 40;

function stored() { try { return localStorage.getItem(LS_KEY); } catch (e) { return null; } }
export function setSelected(id, state) {
  if (state) state.modelId = id || null;
  try { if (id) localStorage.setItem(LS_KEY, id); else localStorage.removeItem(LS_KEY); } catch (e) { /* 只影響記憶 */ }
}

// 使用者自己的模型(有錯誤的檔案不列);順便更新 state.modelsInfo
export async function myModels(state) {
  let info = state.modelsInfo;
  try { info = await API.listModels(); state.modelsInfo = info; } catch (e) { if (!info) throw e; }
  return (info.models || []).filter((m) => m.source !== "shipped" && !m.error);
}

// 目前選定的模型:先看這次瀏覽的選擇,再看瀏覽器記住的;都沒有(或已封存)就用最近更新的那個
export function current(mine, state) {
  const ids = new Set(mine.map((m) => m.id));
  const want = state.modelId || stored();
  if (want && ids.has(want)) return want;
  const latest = mine.slice().sort((a, b) => String(b.updated || b.created || "").localeCompare(String(a.updated || a.created || "")))[0];
  return latest ? latest.id : null;
}

export function guideHTML(what) {
  return ZH`<div class="u-card mw-custom mw-guide">
    <h3 class="mw-h3">還沒有你自己的模型</h3>
    <p class="u-sub">${esc(what)}H0 Lab 沒有內建的個股分數:分數與等級只來自你自己建立、看過權重並存檔的模型。用精靈從因子範本開始(沒有預設、不推薦),三個步驟就能存一個。</p>
    <a class="u-btn is-primary" href="#/wizard">建立我的模型</a>
  </div>`;
}

export function selectHTML(mine, cur, id = "mwPickSel") {
  return ZH`<div class="mw-pick">
    <label class="mw-pick-l" for="${esc(id)}">我的模型</label>
    <select class="u-input mw-pick-sel" id="${esc(id)}">
      ${mine.map((m) => `<option value="${esc(m.id)}"${m.id === cur ? " selected" : ""}>${esc(m.name || m.id)}</option>`).join("")}
    </select>
    <a class="mw-pick-link" href="#/models">模型工作台 ›</a>
  </div>`;
}

export async function mount(box, R, state) {
  if (!box) return;
  let mine;
  try { mine = await myModels(state); } catch (e) { box.innerHTML = ""; return; }
  if (!box.isConnected) return;
  if (!mine.length) { box.innerHTML = guideHTML(ZH`這一頁的其他資料照常顯示。`); return; }
  const cur = current(mine, state);
  box.innerHTML = selectHTML(mine, cur) + `<div id="mwPickOut" aria-live="polite"></div>`;
  const sel = box.querySelector("#mwPickSel");
  sel.onchange = () => { setSelected(sel.value, state); show(box, R, state, sel.value); };
  show(box, R, state, cur);
}

async function show(box, R, state, id) {
  const out = box.querySelector("#mwPickOut");
  const my = ++seq;
  if (!id) { out.innerHTML = ""; return; }
  const sym = R.meta.symbol;
  const summ = (state.modelsInfo?.models || []).find((m) => m.id === id);
  const name = summ?.name || id;   // 名稱以清單為準:只改名稱不改雜湊(同一天 updated 也不變),快取結果裡可能是舊名稱
  const key = [sym, R.meta.generated_at || "", id, summ?.score_hash || "", summ?.updated || ""].join("|");
  let custom = cache.get(key) || null, err = "";
  if (!custom) {
    out.innerHTML = ZH`<div class="u-card mw-custom"><p class="u-sub">用「${esc(name)}」計算中…</p></div>`;
    try {
      const m = await API.getModel(id);
      const v = await API.validateModel(m, sym);
      if (!v.ok) err = ZH`模型格式有錯誤:${API.productText(v.error)}`;
      else if (v.preview) custom = v.preview;
      else if (/還沒開啟過/.test(v.preview_error || "")) err = ZH`伺服器記憶體裡已經沒有這份報告(例如伺服器重開過);按上方「重新分析」之後再選一次`;
      else err = API.productText(v.preview_error) || ZH`這份報告沒有辦法用你的模型計算`;
    } catch (e) { err = e.message; }
    if (custom) {
      cache.set(key, custom);
      if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value);
    }
  }
  if (my !== seq || !out.isConnected) return;
  if (!custom) {
    out.innerHTML = ZH`<div class="u-card mw-custom"><p class="mw-err-txt">「${esc(name)}」計算失敗:${esc(err)}</p></div>`;
    return;
  }
  out.innerHTML = ZH`<div class="u-card mw-custom">
    <h3 class="mw-h3">我的模型:${esc(name || custom.model?.name)}</h3>
    <p class="u-sub">用你的模型對這家公司計算的分數與等級。只是模型輸出,不是買賣建議;下方的資料頁不需要模型。</p>
    ${API.customScoreHTML(custom)}
  </div>`;
}
