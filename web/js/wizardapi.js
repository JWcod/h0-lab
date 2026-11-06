// 上手精靈(S2)的 API 呼叫。端點依 docs/上手精靈規格.md §8:
//   GET  /api/templates?market=TW        → { templates: [...] }(含「準備中」狀態)
//   GET  /api/catalog?market=TW          → { items: [...] }(訊號目錄)
//   POST /api/wizard/preview             → 送 { template, market, choices },回 { model, ok, error, score_hash, ranking }
//   POST /api/models、/api/models/{id}/backtest → 沿用 modelapi.js(回應多一個 plain 區塊,規格 §5)
// 會改資料的請求一律經過 modelapi.js 的 postJSON(帶 X-Requested-With: invest-lab)。
//
// 開發用假資料開關(預設關閉):在瀏覽器主控台執行
//   localStorage.setItem("h0lab.dev.wizardMock", "1")
// 後重新整理,精靈的所有呼叫(含存檔、回測)改走 web/js/dev/wizard-mock.js,不會碰伺服器。
// 只在 127.0.0.1 / localhost 生效;沒開時假資料模組根本不會被載入(動態 import),正式路徑不經過它。
import * as API from "./modelapi.js";

export const MOCK_KEY = "h0lab.dev.wizardMock";

export function mockOn() {
  try {
    const host = location.hostname;
    if (host !== "127.0.0.1" && host !== "localhost") return false;
    return localStorage.getItem(MOCK_KEY) === "1";
  } catch (e) { return false; }
}

let mockMod = null;
async function mock() {
  if (!mockMod) mockMod = await import("./dev/wizard-mock.js");
  return mockMod;
}

const q = (market) => `market=${encodeURIComponent(market)}`;

export async function templates(market) {
  if (mockOn()) return (await mock()).templates(market);
  return API.getJSON(`/api/templates?${q(market)}`);
}
export async function catalog(market) {
  if (mockOn()) return (await mock()).catalog(market);
  return API.getJSON(`/api/catalog?${q(market)}`);
}
// choices = { pillars: { quality: "off" | "normal" | "high", ... }, signals: { roic: ..., ... } };空物件 = 範本原樣
export async function preview(template, market, choices) {
  const body = { template, market, choices };
  if (mockOn()) return (await mock()).preview(body);
  return API.postJSON("/api/wizard/preview", body);
}
export async function listModels() {
  if (mockOn()) return (await mock()).listModels();
  return API.listModels();
}
// opts.auto_id = true:代號由伺服器產生(名稱可留空,伺服器補「我的<範本名稱>」);opts.create_only = true:用自訂代號新建,
// 代號已經有人用(含封存)就回錯誤,不覆寫。兩個都沒給 = 覆寫同一個代號(存成新的版本)。回傳 { saved: { id, name, ... } }
export async function saveModel(model, opts = {}) {
  if (mockOn()) return (await mock()).saveModel(model, opts);
  return API.postJSON("/api/models", { model, ...opts });
}
export async function backtest(id, universe) {
  if (mockOn()) return (await mock()).backtest(id, universe);
  return API.backtestModel(id, universe);
}
