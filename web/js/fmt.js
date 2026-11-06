// 數字格式化與安全輸出(所有外部文字都要經過 esc)
import { ZH, isEn } from "./i18n/index.js";

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const isNum = (v) => typeof v === "number" && isFinite(v);

// Q7:美股(info_only)在畫面上一律「不評等」;資料裡的 lt_rating 是模型內部等級,只給模擬帳戶與研究紀錄
// 修正前存下的舊資料沒有 info_only:美股一律當成 info_only(恢復美股評等後,新資料會明確寫 info_only: false)
export const infoOnly = (r) => (r?.info_only ?? r?.market === "US") === true;
export const shownRating = (r) => (infoOnly(r) ? ZH`不評等` : ratingName(r?.lt_rating) || "");
export const shownSignal = (r) => (infoOnly(r) ? ZH`不評等` : r?.st_signal || "");
// 舊資料與回測輸出裡的評等字眼 → 模型等級(只改顯示;資料檔與回測基準不動)
const RATING_NAME = { 強力買進: ZH`模型等級 A`, 買進: ZH`模型等級 B`, 持有: ZH`模型等級 C`, 減碼: ZH`模型等級 D`, 賣出: ZH`模型等級 E`, 沒有等級: ZH`沒有等級` };
export const ratingName = (x) => RATING_NAME[x] || x;

export function num(v, d = 2) {
  if (!isNum(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

// 圖表提示框的預設數字格式(只影響顯示):千分位,大數字最多 2 位小數,小數字保留較多位數
export function chartNum(v) {
  if (!isNum(v)) return v ?? "—";
  const a = Math.abs(v);
  return v.toLocaleString("en-US", { maximumFractionDigits: a >= 100 ? 2 : a >= 1 ? 3 : 4 });
}

export function pct(v, d = 1, sign = false) {
  if (!isNum(v)) return "—";
  const s = (v * 100).toFixed(d) + "%";
  return sign && v > 0 ? "+" + s : s;
}

export function pp(v, d = 1) {
  // 百分點(輸入是比例差)
  if (!isNum(v)) return "—";
  return (v > 0 ? "+" : "") + (v * 100).toFixed(d) + " pp";
}

export function mult(v, d = 1) {
  return isNum(v) ? v.toFixed(d) + "x" : "—";
}

// 大數字:中文模式台股用「億 / 兆」,其他用 B / T
export function big(v, market = "US", d = 2) {
  if (!isNum(v)) return "—";
  const a = Math.abs(v);
  const sgn = v < 0 ? "-" : "";
  if (market === "TW" && !isEn) {
    if (a >= 1e12) return sgn + (a / 1e12).toFixed(d) + ZH` 兆`;
    if (a >= 1e8) return sgn + (a / 1e8).toFixed(a >= 1e10 ? 0 : 1) + ZH` 億`;
    if (a >= 1e4) return sgn + (a / 1e4).toFixed(0) + ZH` 萬`;
    return sgn + a.toFixed(0);
  }
  if (a >= 1e12) return sgn + (a / 1e12).toFixed(d) + "T";
  if (a >= 1e9) return sgn + (a / 1e9).toFixed(d) + "B";
  if (a >= 1e6) return sgn + (a / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return sgn + (a / 1e3).toFixed(1) + "K";
  return sgn + a.toFixed(0);
}

export function lots(v) {
  // 張
  if (!isNum(v)) return "—";
  return (v > 0 ? "+" : "") + Math.round(v).toLocaleString("en-US") + ZH` 張`;
}

export function price(v, ccy) {
  if (!isNum(v)) return "—";
  const d = Math.abs(v) >= 1000 ? 1 : 2;
  return num(v, d);
}

export function byFmt(v, fmt) {
  switch (fmt) {
    case "pct": return pct(v);
    case "pct2": return pct(v, 2);
    case "pp": return pp(v);
    case "pp_raw": return isNum(v) ? (v > 0 ? "+" : "") + v.toFixed(2) + " pp" : "—";
    case "x": return mult(v, 2);
    case "int": return isNum(v) ? String(Math.round(v)) : "—";
    case "num": return num(v, 2);
    case "bool": return v ? ZH`是` : ZH`否`;
    case "trend": return { 2: ZH`強勢多頭`, 1: ZH`多頭`, 0: ZH`盤整`, "-1": ZH`空頭`, "-2": ZH`強勢空頭` }[v] ?? "—";
    default: return isNum(v) ? num(v, 2) : esc(v);
  }
}

export function timeAgo(ts) {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + ZH` 分鐘前`;
  if (s < 86400) return Math.round(s / 3600) + ZH` 小時前`;
  return Math.round(s / 86400) + ZH` 天前`;
}

export function dateStr(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function safeUrl(u) {
  return typeof u === "string" && /^https?:\/\//i.test(u) ? u : null;
}

// 分數顏色(0–100):橘 → 黃 → 青(避免與漲跌紅綠混淆)
export function scoreColor(s) {
  if (!isNum(s)) return "var(--muted-strong)";
  if (s >= 75) return "#3ee0c5";
  if (s >= 60) return "#7dd3fc";
  if (s >= 45) return "#ffd166";
  if (s >= 30) return "#ffa94d";
  return "#ff7b6b";
}

// 分數當文字色(表格數字):用評等色 token(深色亮、淺色深),兩種外觀都高對比;scoreColor 的亮色只當色塊 / 長條底色
export function scoreInk(s) {
  if (!isNum(s)) return "var(--muted-strong)";
  if (s >= 75) return "var(--r4)";
  if (s >= 60) return "var(--r3)";
  if (s >= 45) return "var(--r2)";
  if (s >= 30) return "var(--r1)";
  return "var(--r0)";
}

export const TONE = { pos: ZH`正面`, neg: ZH`負面`, watch: ZH`觀察` };

export function upDownClass(v) {
  if (!isNum(v) || v === 0) return "flat";
  return v > 0 ? "up" : "down";
}
