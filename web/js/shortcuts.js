// 全站快捷鍵(商業化規格 §7):「/」聚焦搜尋、G 再按一個字母跳頁、「?」顯示快捷鍵表、⌘K 指令面板(由 palette.js 自己接)。
// - 在輸入框、文字區、可編輯區、下拉選單裡打字時不觸發;按著 ⌘ / Ctrl / Alt 時也不觸發(留給瀏覽器)。
// - 快捷鍵表沿用指令面板的外觀(css/palette.css),只用 createElement + textContent。
// 用法:Shortcuts.mount({ go: (hash) => { location.hash = hash; }, focusSearch: () => $("#q").focus(), openPalette: Palette.open })
import { ZH } from "./i18n/index.js";

const CHORD_MS = 1200;   // 按 G 之後多久內要按第二個字母

export const GO_KEYS = [
  { key: "h", hash: "#/", label: ZH`首頁` },
  { key: "s", hash: "#/scan", label: ZH`選股掃描` },
  { key: "w", hash: "#/wizard", label: ZH`建立模型` },
  { key: "m", hash: "#/models", label: ZH`模型工作台` },
  { key: "b", hash: "#/backtest", label: ZH`研究紀錄` },
];

const isMac = () => typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");

/** 快捷鍵表的內容(純資料,測試與畫面共用)。 */
export function helpSections(mac = isMac()) {
  return [
    { title: ZH`一般`, rows: [
      { keys: [mac ? "⌘" : "Ctrl", "K"], label: ZH`打開指令面板`, palette: true },
      { keys: ["/"], label: ZH`聚焦搜尋框` },
      { keys: ["?"], label: ZH`顯示這張快捷鍵表` },
      { keys: ["Esc"], label: ZH`關閉面板或彈出視窗` },
    ] },
    { title: ZH`跳到頁面(先按 G,再按字母)`, rows: GO_KEYS.map((g) => ({ keys: ["G", g.key.toUpperCase()], label: g.label })) },
  ];
}

/** 焦點在可以打字的地方嗎?(input / textarea / select / contenteditable) */
export function isTyping(target) {
  if (!target || target.nodeType !== 1) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag !== "INPUT") return false;
  const type = (target.getAttribute("type") || "text").toLowerCase();
  return !["button", "submit", "reset", "checkbox", "radio", "range", "color", "file", "image"].includes(type);
}

let opts = { go: null, focusSearch: null, openPalette: null };
let mounted = false;
let chordAt = 0;
let help = null, helpReturn = null;

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

function buildHelp() {
  const scrim = el("div", "h0-pl-scrim");
  scrim.hidden = true;
  scrim.addEventListener("mousedown", (e) => { if (e.target === scrim) closeHelp(); });
  const box = el("div", "h0-pl");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-labelledby", "h0-keys-title");
  const head = el("div", "h0-pl-head");
  const title = el("h2", "h0-keys-title", ZH`鍵盤快捷鍵`);
  title.id = "h0-keys-title";
  const x = el("button", "h0-keys-x", "×");
  x.type = "button";
  x.setAttribute("aria-label", ZH`關閉快捷鍵表`);
  x.addEventListener("click", closeHelp);
  head.append(title, x);
  const body = el("div", "h0-keys-list");
  for (const sec of helpSections()) {
    body.appendChild(el("h3", "", sec.title));
    for (const r of sec.rows) {
      // 「打開指令面板」那列可以直接按(交給 openPalette)
      const row = el(r.palette ? "button" : "div", "h0-keys-row" + (r.palette ? " is-btn" : ""));
      if (r.palette) {
        row.type = "button";
        row.addEventListener("click", () => { closeHelp(); opts.openPalette?.(); });
      }
      const ks = el("span", "h0-keys-k");
      r.keys.forEach((k, i) => {
        if (i) ks.appendChild(document.createTextNode(r.keys[0] === "G" ? ZH`然後` : "+"));
        ks.appendChild(el("kbd", "h0-kbd", k));
      });
      row.append(el("span", "", r.label), ks);
      body.appendChild(row);
    }
  }
  box.append(head, body);
  // 焦點鎖在表內:Tab 只在表裡的按鈕之間輪流
  box.addEventListener("keydown", (e) => {
    if (e.key === "Escape" || e.key === "?") { e.preventDefault(); e.stopPropagation(); closeHelp(); }
    else if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) closeHelp();   // 讓給指令面板(palette.js 在 document 上接)
    else if (e.key === "Tab") {
      e.preventDefault();
      const bs = [...box.querySelectorAll("button")];
      const i = bs.indexOf(document.activeElement);
      bs[(i + (e.shiftKey ? -1 : 1) + bs.length) % bs.length].focus();
    }
  });
  scrim.appendChild(box);
  document.body.appendChild(scrim);
  return { scrim, x };
}

export function isHelpOpen() { return !!help && !help.scrim.hidden; }

export function openHelp() {
  if (!help) help = buildHelp();
  if (isHelpOpen()) return;
  helpReturn = document.activeElement;
  help.scrim.hidden = false;
  document.documentElement.classList.add("h0-pl-lock");
  help.x.focus();
}

export function closeHelp() {
  if (!isHelpOpen()) return;
  if (help.scrim.contains(document.activeElement)) document.activeElement.blur();
  help.scrim.hidden = true;
  document.documentElement.classList.remove("h0-pl-lock");
  const back = helpReturn;
  helpReturn = null;
  if (back && back.isConnected && typeof back.focus === "function") back.focus();
}

// 有別的對話框開著(指令面板、側邊檢視器的彈出卡)時,單鍵快捷鍵不搶
const modalOpen = () => [...document.querySelectorAll('[aria-modal="true"]')].some((d) => d.getClientRects().length > 0);

function onKey(e) {
    if (document.querySelector("dialog[open]")) return;   // 設定等視窗開著時不處理全站快捷鍵
  if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
  if (isTyping(e.target) || isTyping(document.activeElement)) return;
  if (isHelpOpen() || modalOpen()) return;
  const k = e.key;
  const now = Date.now();
  if (chordAt && now - chordAt <= CHORD_MS) {
    chordAt = 0;
    const g = GO_KEYS.find((x) => x.key === k.toLowerCase());
    if (g && typeof opts.go === "function") { e.preventDefault(); opts.go(g.hash); }
    return;
  }
  chordAt = 0;
  if (k === "/" && typeof opts.focusSearch === "function") { e.preventDefault(); opts.focusSearch(); }
  else if (k === "?") { e.preventDefault(); openHelp(); }
  else if ((k === "g" || k === "G") && !e.shiftKey) { chordAt = now; }
}

/** 掛上快捷鍵;可以重複呼叫來換掉 go / focusSearch / openPalette。 */
export function mount({ go, focusSearch, openPalette } = {}) {
  if (go !== undefined) opts.go = go;
  if (focusSearch !== undefined) opts.focusSearch = focusSearch;
  if (openPalette !== undefined) opts.openPalette = openPalette;
  if (!mounted) {
    mounted = true;
    document.addEventListener("keydown", onKey);
  }
  return { openHelp, closeHelp, isHelpOpen };
}
