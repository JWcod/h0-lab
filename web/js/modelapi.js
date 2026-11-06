// 模型客製化前端共用:API 呼叫、面向名稱、自訂模型分數的顯示、伺服器文字換成產品用語。
// 會改資料的請求一律帶 X-Requested-With: invest-lab(伺服器的 CSRF 檢查,沿用 app.js 觀察清單的寫法)。
// 規格 docs/模型客製化規格.md §4.4:我的模型只顯示分數、等級(A+ ~ F),不顯示買賣字眼或評等;固定標「未驗證」。
import { ZH, t, isEn } from "./i18n/index.js";
import { esc, num, isNum } from "./fmt.js";

// 出廠模型(JW-MF)只出現在研究紀錄與回測對照,介面上叫「研究基準模型」;只改顯示文字,id 與後端欄位不變。
// 2026-11-03 起不提供它的個股分數,模型清單也不列它(GET /api/models 只回使用者自己的模型)。
export const SAMPLE = t("model.sample");
export const shownModelName = (m) => {
  if (!m) return "";
  if (m.source !== "shipped") return m.name || m.id || "";
  return SAMPLE;
};

// 伺服器回來的說明文字(lab/research/model_eval.py)還有內部用語,顯示前換成產品用語(規格 §2)。
// 只改顯示,不拿來判斷邏輯(r.status === "開發集" 這類比對一律用原值)。
const TERMS = [
  [/\s*[((]v\d[^()()]*[))]/g, ""],          // 「(v2 實驗登記簿,截至 …)」這類開發歷史
  [/\s*backtest_pit\s*\/\s*scripts\/holdout\.py\s*/g, ZH`開發時的正式流程`], [/\s*scripts\/holdout\.py\s*/g, ZH`開發時的正式流程`],
  [/\s*[((]\s*docs\/[^()()]*[))]/g, ""],   // 「(docs/模型白皮書.md)」→ 刪除
  [/\s*[((]\s*v\d\s+P\d+-H\d+\s*[))]/g, ""],   // 「(v2 P2-H06)」這類事先登記代號
  [/出廠的\s*JW-MF\s*v?[\d.]+(-dev)?/g, SAMPLE], [/出廠模型\s*JW-MF\s*v?[\d.]+(-dev)?/g, SAMPLE],
  [/(?:H0\s*)?範例模型|JW-MF\s*v?[\d.]+(-dev)?|JW 多因子模型/g, SAMPLE],
  [/出廠模型/g, SAMPLE], [/模型族/g, ZH`模型系列`], [/開發集/g, ZH`開發期`], [/試了幾次/g, ZH`嘗試次數`],
  [/閘門/g, ZH`正式檢定`], [/評分雜湊/g, ZH`設定指紋`],
  [/乾淨的樣本外/g, ZH`乾淨的樣本外測試`], [/\s*holdout\s*/gi, ZH`樣本外測試`],
];
export function productText(s) {
  let x = s == null ? "" : String(s);
  if (isEn) return x;    // 英文的產品用語已在目錄裡
  for (const [re, to] of TERMS) x = x.replace(re, to);
  return x;
}

export const PILLAR_LABEL = {
  quality: ZH`品質`, growth: ZH`成長`, valuation: ZH`估值`, health: ZH`財務安全`,
  momentum: ZH`技術動能`, flows: ZH`籌碼`, revisions: ZH`預期修正`, sentiment: ZH`消息情緒`, regime: ZH`大盤環境`,
};
export const PILLARS = ["quality", "growth", "valuation", "health", "momentum", "flows", "revisions", "sentiment"];
export const WEIGHT_GROUPS = [
  ["long_term", ZH`長期分數`], ["short_term_tw", ZH`短期分數(台股)`], ["short_term_us", ZH`短期分數(美股)`],
];
export const MODEL_ID_RE = /^[a-z0-9][a-z0-9-]{0,47}$/;
export const METRIC_KEY_RE = /^[a-z0-9_]{1,40}$/;
export const UNVALIDATED = ZH`未驗證`;

const CSRF = { "X-Requested-With": "invest-lab" };

// 回傳 JSON;HTTP 錯誤時丟出帶繁中訊息的 Error(FastAPI 的 detail)。
async function call(url, opts = {}) {
  let r;
  try { r = await fetch(url, opts); } catch (e) { throw new Error(ZH`連不上本機伺服器`); }
  let j = null;
  try { j = await r.json(); } catch (e) { /* 不是 JSON */ }
  if (!r.ok) {
    const d = j && j.detail;
    throw new Error(typeof d === "string" ? d : ZH`請求失敗(${r.status})`);
  }
  return j || {};
}
export const getJSON = (url) => call(url);
export const postJSON = (url, body) => call(url, {
  method: "POST", headers: { "Content-Type": "application/json", ...CSRF }, body: JSON.stringify(body || {}),
});

export const listModels = () => getJSON("/api/models");
export const getModel = (id) => getJSON(`/api/models/${encodeURIComponent(id)}`).then((j) => j.model);
export const saveModel = (model) => postJSON("/api/models", { model });
export const validateModel = (model, symbol) => postJSON("/api/models/validate", symbol ? { model, symbol } : { model });
export const forkModel = (baseId, id, name) => postJSON(`/api/models/${encodeURIComponent(baseId)}/fork`, { id, name });
export const archiveModel = (id) => postJSON(`/api/models/${encodeURIComponent(id)}/archive`, {});
// 誠實驗證:開發期回測(約 16 秒;新的設定指紋會計入嘗試次數)、樣本外測試(要確認,每個模型系列一次)。
// 兩個都用伺服器上「存檔的版本」,不是編輯器裡還沒存的修改。
export const UNIVERSES = [["tw_pit50", ZH`台股 50 檔(依當時成分)`], ["tw_large40", ZH`台股大型 40 檔`], ["us_large40", ZH`美股大型 40 檔`]];
export const backtestModel = (id, universe) => postJSON(`/api/models/${encodeURIComponent(id)}/backtest`, { universe });
export const holdoutModel = (id, universe) => postJSON(`/api/models/${encodeURIComponent(id)}/holdout`, { universe, confirm: true });

// 自訂模型的分數區塊(報告頁、工作台試算共用)。c = apply.rescore 的輸出(custom_model 或 preview)。
// 只有分數、等級、各面向分數;不輸出任何評等或買賣字眼。
export function customScoreHTML(c) {
  if (!c) return "";
  const lt = c.long_term || {}, st = c.short_term || {};
  const P = c.pillars || {};
  const scoreTxt = (x) => (isNum(x?.score) ? num(x.score, 1) : "—");
  const keys = [...PILLARS, "regime"].filter((k) => P[k]);
  const unv = (c.unvalidated_pillars || []).map((k) => PILLAR_LABEL[k] || k);
  return ZH`<div class="mw-score">
    <div class="mw-score-top">
      <span class="mw-big"><span class="u-label">長期分數</span><b class="u-hero-sm">${esc(lt.grade || "—")}</b><span class="u-num">${esc(scoreTxt(lt))}</span></span>
      <span class="mw-big"><span class="u-label">短期分數</span><b class="u-hero-sm">${esc(st.grade || "—")}</b><span class="u-num">${esc(scoreTxt(st))}</span></span>
      <span class="mw-unval" title="沒有經過回測驗證">${esc(UNVALIDATED)}</span>
    </div>
    <table class="mw-pillars"><thead><tr><th>面向</th><th class="n">分數</th><th>等級</th><th class="n">資料完整度</th></tr></thead><tbody>
      ${keys.map((k) => { const p = P[k]; return `<tr><td>${esc(PILLAR_LABEL[k] || k)}</td><td class="n">${esc(scoreTxt(p))}</td><td>${esc(p.grade || "—")}</td>`
        + `<td class="n">${esc(isNum(p.coverage) ? Math.round(p.coverage * 100) + "%" : "—")}</td></tr>`; }).join("")}
    </tbody></table>
    ${unv.length ? ZH`<p class="u-sub">其中 ${esc(unv.join("、"))} 缺少能回測的歷史資料,沒有經過驗證。</p>` : ""}
    <p class="mw-disc">${esc(productText(c.disclaimer || ZH`自訂模型只顯示分數與等級,不是買賣建議。研究用途,非投資建議。`))}</p>
    ${c.model?.score_hash ? ZH`<p class="u-sub" title="同一組設定會得到同一個指紋,用來計算嘗試次數">設定指紋 <span class="u-num">${esc(c.model.score_hash)}</span></p>` : ""}
  </div>`;
}
