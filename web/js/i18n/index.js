// 語言切換與翻譯(繁體中文是原文,英文查 catalog.en.js;後端輸出用同一份目錄,見 lab/i18n.py)。
// - ZH`…`:介面文字一律用這個標籤。中文模式原樣組字串;英文模式把 HTML 標籤之間的文字段與屬性值逐段查目錄,
//   ${} 的位置以 {0}、{1} 表示,所以譯文可以調整語序。第一次編譯後依模板物件快取。
// - t(key):精靈與引導頁用的鍵值字典(zh-TW.js);英文模式以中文字串查目錄。
// - dyn(s):畫面要顯示「資料裡的鍵」時用(例如回測策略名稱);查不到就原樣回傳。
// 代入的值不會自動 escape,插進 HTML 前一樣要經過 fmt.js 的 esc。
import { DICT } from "./zh-TW.js";
import CATALOG from "./catalog.en.js";

const STORE_KEY = "h0lab.lang";
const COOKIE = "h0lab_lang";    // 後端依這個 cookie 決定 API 文字的語言(EventSource 不能帶自訂標頭)
const hasDom = typeof document !== "undefined";

function detect() {
  if (!hasDom) return "zh-TW";    // node 測試環境
  try {
    const s = localStorage.getItem(STORE_KEY);
    if (s === "en" || s === "zh-TW") return s;
  } catch (e) { /* 私密視窗等情況:改看瀏覽器語言 */ }
  const first = (navigator.languages && navigator.languages[0]) || navigator.language || "";
  return first.toLowerCase().startsWith("zh") ? "zh-TW" : "en";
}

export const LANG = detect();
export const isEn = LANG === "en";

function syncCookie(lang) {
  document.cookie = `${COOKIE}=${lang}; path=/; max-age=31536000; SameSite=Strict`;
}
if (hasDom) {
  syncCookie(LANG);
  document.documentElement.lang = isEn ? "en" : "zh-Hant";
}

export function setLang(lang) {
  try { localStorage.setItem(STORE_KEY, lang); } catch (e) { /* 只影響記憶,不影響這次切換 */ }
  syncCookie(lang);
  location.reload();
}

const has = (k) => Object.prototype.hasOwnProperty.call(CATALOG, k);
const CJK = /[㐀-鿿]/;
const NEEDS = /[　-〿㐀-鿿＀-￯]/;   // 中文字或全形標點
const PUNCT = [[/：/g, ": "], [/，/g, ", "], [/；/g, "; "], [/。/g, ". "], [/、/g, ", "], [/（/g, " ("], [/）/g, ")"],
  [/？/g, "? "], [/！/g, "! "], [/[「」『』]/g, "\""], [/～/g, "~"], [/　/g, " "]];
const asciiPunct = (s) => PUNCT.reduce((x, [re, to]) => x.replace(re, to), s);

const missing = new Set();
export const missingKeys = () => [...missing];    // 開發時檢查漏翻;靜態測試 tests/test_i18n_static.py 會擋

export function dyn(s) {
  if (!isEn || typeof s !== "string" || !NEEDS.test(s)) return s;
  const k = s.trim();
  if (has(k)) return CATALOG[k];
  if (!CJK.test(k)) return asciiPunct(s);
  return s;
}

const MARK = /(\d+)/g;

function translateRun(text) {
  if (!NEEDS.test(text)) return text;
  const core = text.trim();
  const lead = text.slice(0, text.indexOf(core)), trail = text.slice(text.indexOf(core) + core.length);
  const order = [];
  const key = core.replace(MARK, (m, i) => { order.push(i); return `{${order.length - 1}}`; });
  if (has(key)) {
    return lead + CATALOG[key].replace(/\{(\d+)\}/g, (m, i) => (order[+i] === undefined ? m : `${order[+i]}`)) + trail;
  }
  if (!CJK.test(core)) return lead + asciiPunct(core) + trail;
  missing.add(key);
  return text;
}

const TAG = /(<\/?[a-zA-Z!][^<>]*>)/;
const ATTR = /=(["'])([^"']*)\1/g;

function compile(strings) {
  let src = strings[0];
  for (let i = 1; i < strings.length; i++) src += `${i - 1}${strings[i]}`;
  const out = src.split(TAG).map((p, i) => (i % 2 ? p.replace(ATTR, (m, q, v) => `=${q}${translateRun(v)}${q}`) : translateRun(p))).join("");
  // 切成 [字串, 值索引, 字串, ...]
  return out.split(MARK).map((p, i) => (i % 2 ? +p : p));
}

const compiled = new WeakMap();

export function ZH(strings, ...vals) {
  if (!isEn) {
    let s = strings[0];
    for (let i = 0; i < vals.length; i++) s += vals[i] + strings[i + 1];
    return s;
  }
  let parts = compiled.get(strings);
  if (!parts) { parts = compile(strings); compiled.set(strings, parts); }
  let s = "";
  for (let i = 0; i < parts.length; i++) s += i % 2 ? vals[parts[i]] : parts[i];
  return s;
}

// 資料裡的中文鍵(後端的鍵不翻譯),例如 KEY`外資`:原樣回傳,靜態檢查不要求翻譯。
export const KEY = (strings) => strings[0];

// 鍵值字典;找不到就回傳 key 本身(畫面上看得出漏了哪個 key)。
export function t(key, vars) {
  let s = Object.prototype.hasOwnProperty.call(DICT, key) ? DICT[key] : key;
  if (isEn && has(s)) s = CATALOG[s];
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, k) => (Object.prototype.hasOwnProperty.call(vars, k) && vars[k] !== null && vars[k] !== undefined ? String(vars[k]) : m));
}

// 後端的多語文字:字串直接用;{ "zh-TW": ..., "en": ... } 取目前語言,沒有就退回 zh-TW、再退回第一個值。
export function loc(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "string" || typeof v === "number") return String(v);
  if (Array.isArray(v)) return v.map(loc).join(isEn ? ", " : "、");
  if (typeof v === "object") {
    if (typeof v[LANG] === "string") return v[LANG];
    if (typeof v["zh-TW"] === "string") return v["zh-TW"];
    const first = Object.values(v).find((x) => typeof x === "string");
    return first || "";
  }
  return "";
}

// index.html 的靜態文字(外框、導覽列):開機時翻一次。
export function translateDom(root) {
  if (!isEn || !hasDom) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (NEEDS.test(n.nodeValue)) n.nodeValue = translateRun(n.nodeValue);
  }
  for (const el of root.querySelectorAll("[title],[aria-label],[placeholder]")) {
    for (const a of ["title", "aria-label", "placeholder"]) {
      if (el.hasAttribute(a) && NEEDS.test(el.getAttribute(a))) el.setAttribute(a, translateRun(el.getAttribute(a)));
    }
  }
}
