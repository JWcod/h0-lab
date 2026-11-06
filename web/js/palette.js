// 指令面板(商業化規格 §7;macOS Spotlight / Raycast 風格):⌘K / Ctrl+K 開關,找公司、跳頁面、執行動作。
// - 只用 createElement + textContent,不插入 HTML;樣式在 css/palette.css(.h0-pl*)。
// - 純函式(fuzzyScore、rankItems、pushRecent)不碰 DOM,tests/test_web_palette_static.py 用 node 直接跑。
// - 最近使用存在 localStorage(只放代號 / 指令 id,不放個資),讀寫都包 try/catch,讀不到就當沒有。
// 用法:Palette.mount({
//   commands: [{ id: "go-scan", group: "頁面", label: "選股掃描", hint: "#/scan", keys: ["G", "S"], run: () => {...} }],
//   searchCompanies: (q) => fetch("/api/search?q=" + encodeURIComponent(q)).then((r) => r.json()).then((j) => j.results),
//   openCompany: (c) => go(c.symbol),     // 選填,預設跳 #/s/<代號>/overview
// });  之後 Palette.open() / Palette.close() / Palette.isOpen()
import { ZH } from "./i18n/index.js";

const RECENT_KEY = "h0.palette.recent";
const RECENT_MAX = 8;
const CO_MAX = 6;            // 公司最多列幾家
const GROUP_ORDER = [ZH`最近使用`, ZH`公司`, ZH`頁面`, ZH`動作`];
const SYMBOL_RE = /^[A-Za-z0-9.\-]{1,15}$/;

// ---------- 純函式 ----------

/** 模糊比對分數:q 的每個字依序出現在 text 裡才算符合(不分大小寫),沒符合回 -1。
 *  加分:整串相同 > 開頭相同 > 連續 > 出現在字首(空白、-、/ 之後或中英交界)。越前面、text 越短分越高。 */
export function fuzzyScore(q, text) {
  q = String(q ?? "").trim().toLowerCase();
  const s = String(text ?? "").toLowerCase();
  if (!q) return 0;
  if (!s) return -1;
  if (s === q) return 1000;
  const at = s.indexOf(q);
  if (at === 0) return 800 - s.length;
  if (at > 0) return 600 - at * 2 - s.length + (isWordStart(s, at) ? 50 : 0);
  let score = 300, last = -1, run = 0;
  for (const ch of q) {
    if (ch === " ") continue;
    const i = s.indexOf(ch, last + 1);
    if (i < 0) return -1;
    if (i === last + 1) { run += 1; score += 8 * run; } else { run = 0; score -= Math.min(i - last - 1, 10); }
    if (isWordStart(s, i)) score += 10;
    last = i;
  }
  return score - s.length;
}

function isWordStart(s, i) {
  if (i === 0) return true;
  const p = s[i - 1];
  return p === " " || p === "-" || p === "/" || p === "·" || p === "_" || p === "." || (/[a-z0-9]/.test(s[i]) !== /[a-z0-9]/.test(p));
}

/** 一個項目對查詢的最高分:比 label、hint、keywords、symbol、name,取最高的。 */
export function itemScore(q, item) {
  const fields = [item.label, item.symbol, item.name, item.hint, ...(item.keywords || [])];
  let best = -1;
  fields.forEach((f, i) => {
    if (f == null || f === "") return;
    const sc = fuzzyScore(q, f) - (i >= 3 ? 100 : 0);   // hint / keywords 符合的排在名稱符合的後面
    if (sc > best) best = sc;
  });
  return best;
}

/** 排序:q 空白時最近使用的照使用順序排最前面、其他照原順序;有 q 時過濾掉不符合的,依分數排,最近用過的加分。 */
export function rankItems(q, items, recent = []) {
  const pos = new Map(recent.map((k, i) => [k, i]));
  const keyed = items.map((it, i) => ({ it, i, r: pos.has(it.key) ? pos.get(it.key) : -1 }));
  if (!String(q ?? "").trim()) {
    return keyed.sort((a, b) => (a.r < 0) - (b.r < 0) || (a.r - b.r) || (a.i - b.i)).map((x) => x.it);
  }
  return keyed
    .map((x) => ({ ...x, s: itemScore(q, x.it) }))
    .filter((x) => x.s >= 0)
    .map((x) => ({ ...x, s: x.s + (x.r >= 0 ? 250 - x.r * 20 : 0) }))
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .map((x) => x.it);
}

/** 新的最近使用清單:把 entry 放最前面、去掉同 key 的舊紀錄、最多 max 筆。 */
export function pushRecent(list, entry, max = RECENT_MAX) {
  return [entry, ...(list || []).filter((e) => e && e.k !== entry.k)].slice(0, max);
}

function loadRecent() {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    return Array.isArray(v) ? v.filter((e) => e && typeof e.k === "string") : [];
  } catch { return []; }
}

function saveRecent(list) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch { /* 無痕視窗等情況存不了,就不記 */ }
}

// ---------- 面板 ----------

let opts = { commands: [], searchCompanies: null, openCompany: null };
let root = null, input = null, list = null, live = null;
let rows = [];            // 目前畫出來的可選項目(照畫面順序)
let active = 0;
let companies = [];       // 最近一次查到的公司
let seq = 0, timer = 0;
let returnFocus = null;
let mounted = false;

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

function build() {
  root = el("div", "h0-pl-scrim");
  root.hidden = true;
  root.addEventListener("mousedown", (e) => { if (e.target === root) close(); });

  const box = el("div", "h0-pl");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-label", ZH`指令面板`);

  const head = el("div", "h0-pl-head");
  const ic = el("span", "h0-pl-ic", "⌕");
  ic.setAttribute("aria-hidden", "true");
  input = el("input", "h0-pl-input");
  input.type = "text";
  input.id = "h0-pl-input";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.placeholder = ZH`搜尋公司、頁面或動作…`;
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-expanded", "true");
  input.setAttribute("aria-controls", "h0-pl-list");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-label", ZH`搜尋公司、頁面或動作`);
  const esc = el("kbd", "h0-kbd", "Esc");
  esc.setAttribute("aria-hidden", "true");
  head.append(ic, input, esc);

  list = el("div", "h0-pl-list");
  list.id = "h0-pl-list";
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", ZH`結果`);

  const foot = el("div", "h0-pl-foot");
  foot.setAttribute("aria-hidden", "true");
  for (const [k, t] of [["↑↓", ZH`選擇`], ["Enter", ZH`開啟`], ["Esc", ZH`關閉`]]) {
    const s = el("span", "h0-pl-tip");
    s.append(el("kbd", "h0-kbd", k), document.createTextNode(" " + t));
    foot.appendChild(s);
  }

  live = el("div", "h0-sr");
  live.setAttribute("aria-live", "polite");

  box.append(head, list, foot, live);
  root.appendChild(box);
  document.body.appendChild(root);

  input.addEventListener("input", onInput);
  box.addEventListener("keydown", onKey);
  list.addEventListener("mousemove", (e) => {
    const o = e.target.closest("[role=option]");
    if (o && +o.dataset.i !== active) setActive(+o.dataset.i, false);
  });
  list.addEventListener("click", (e) => {
    const o = e.target.closest("[role=option]");
    if (o) choose(+o.dataset.i);
  });
}

function commandItems() {
  return (opts.commands || []).filter((c) => c && c.id && c.label).map((c) => ({ ...c, key: "cmd:" + c.id, kind: "cmd" }));
}

const companyItem = (c) => ({ key: "co:" + c.symbol, kind: "co", group: ZH`公司`, symbol: c.symbol, name: c.name || "", tag: c.tag || "", label: c.name || c.symbol });

function sections(q) {
  const recent = loadRecent();
  const keys = recent.map((e) => e.k);
  const cmds = commandItems();
  const out = new Map();
  const add = (g, it) => { if (!out.has(g)) out.set(g, []); out.get(g).push(it); };

  if (!q) {
    const byKey = new Map(cmds.map((c) => [c.key, c]));
    for (const e of recent) {
      if (e.k.startsWith("co:")) add(ZH`最近使用`, companyItem(e));
      else if (byKey.has(e.k)) add(ZH`最近使用`, byKey.get(e.k));
    }
    const seen = new Set(keys);
    for (const c of cmds) if (!seen.has(c.key)) add(c.group || ZH`動作`, c);
  } else {
    const cos = companies.slice(0, CO_MAX).map(companyItem);
    // 「2330」和結果裡的「2330.TW」算同一檔(台股代號後面的 .TW / .TWO 不算),不另外列「直接開啟」
    const bare = (s) => String(s).toUpperCase().replace(/\.TWO?$/, "");
    if (SYMBOL_RE.test(q) && !cos.some((c) => bare(c.symbol) === bare(q))) {
      cos.push({ key: "co:" + q.toUpperCase(), kind: "co", group: ZH`公司`, symbol: q.toUpperCase(), name: ZH`直接開啟這個代號`, tag: "", label: q.toUpperCase() });
    }
    for (const c of cos) add(ZH`公司`, c);                       // 公司照後端順序(後端已經排過)
    for (const c of rankItems(q, cmds, keys)) add(c.group || ZH`動作`, c);
  }
  const order = [...GROUP_ORDER, ...[...out.keys()].filter((g) => !GROUP_ORDER.includes(g))];
  return order.filter((g) => out.has(g)).map((g) => [g, out.get(g)]);
}

function render() {
  const q = input.value.trim();
  const secs = sections(q);
  list.replaceChildren();
  rows = [];
  secs.forEach(([g, items], gi) => {
    const grp = el("div", "h0-pl-group");
    grp.setAttribute("role", "group");
    const hd = el("div", "h0-pl-gh", g);
    hd.id = "h0-pl-g" + gi;
    grp.setAttribute("aria-labelledby", hd.id);
    grp.appendChild(hd);
    for (const it of items) {
      const i = rows.length;
      rows.push(it);
      const o = el("div", "h0-pl-opt");
      o.id = "h0-pl-o" + i;
      o.dataset.i = i;
      o.setAttribute("role", "option");
      if (it.kind === "co") {
        o.append(el("span", "h0-pl-sym", it.symbol), el("span", "h0-pl-label", it.name || it.symbol));
        if (it.tag) o.appendChild(el("span", "h0-pl-hint", it.tag));
      } else {
        o.appendChild(el("span", "h0-pl-label", it.label));
        if (it.hint) o.appendChild(el("span", "h0-pl-hint", it.hint));
        if (it.keys && it.keys.length) {
          const ks = el("span", "h0-pl-keys");
          ks.setAttribute("aria-hidden", "true");
          for (const k of it.keys) ks.appendChild(el("kbd", "h0-kbd", k));
          o.appendChild(ks);
        }
      }
      grp.appendChild(o);
    }
    list.appendChild(grp);
  });
  if (!rows.length) list.appendChild(el("div", "h0-pl-empty", q ? ZH`找不到「${q}」` : ZH`沒有可用的指令`));
  live.textContent = rows.length ? ZH`${rows.length} 個結果` : ZH`沒有結果`;
  setActive(Math.min(active, Math.max(0, rows.length - 1)), true);
}

function setActive(i, scroll) {
  active = i;
  list.querySelectorAll("[role=option]").forEach((o) => {
    const on = +o.dataset.i === i;
    o.classList.toggle("is-active", on);
    o.setAttribute("aria-selected", on ? "true" : "false");
    if (on && scroll) o.scrollIntoView({ block: "nearest" });
  });
  if (rows[i]) input.setAttribute("aria-activedescendant", "h0-pl-o" + i);
  else input.removeAttribute("aria-activedescendant");
}

function onInput() {
  active = 0;
  const q = input.value.trim();
  clearTimeout(timer);
  const my = ++seq;
  if (!q || typeof opts.searchCompanies !== "function") { companies = []; render(); return; }
  render();
  timer = setTimeout(async () => {
    try {
      const res = await opts.searchCompanies(q);
      if (my !== seq) return;           // 已經有更新的查詢,舊結果丟掉
      companies = Array.isArray(res) ? res.filter((c) => c && c.symbol) : [];
    } catch { if (my !== seq) return; companies = []; }
    render();
  }, 150);
}

function onKey(e) {
  if (e.key === "ArrowDown" || (e.ctrlKey && e.key === "n")) { e.preventDefault(); if (rows.length) setActive((active + 1) % rows.length, true); }
  else if (e.key === "ArrowUp" || (e.ctrlKey && e.key === "p")) { e.preventDefault(); if (rows.length) setActive((active - 1 + rows.length) % rows.length, true); }
  else if (e.key === "Home" && e.ctrlKey) { e.preventDefault(); setActive(0, true); }
  else if (e.key === "End" && e.ctrlKey) { e.preventDefault(); setActive(rows.length - 1, true); }
  else if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); choose(active); }
  else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
  else if (e.key === "Tab") { e.preventDefault(); input.focus(); }   // 焦點鎖在面板:唯一能聚焦的是輸入框,選項用方向鍵
}

function choose(i) {
  const it = rows[i];
  if (!it) return;
  const entry = it.kind === "co" ? { k: it.key, symbol: it.symbol, name: it.name === ZH`直接開啟這個代號` ? "" : it.name, tag: it.tag } : { k: it.key };
  saveRecent(pushRecent(loadRecent(), entry));
  close();
  try {
    if (it.kind === "co") {
      if (typeof opts.openCompany === "function") opts.openCompany({ symbol: it.symbol, name: entry.name, tag: it.tag });
      else location.hash = `#/s/${encodeURIComponent(it.symbol)}/overview`;
    } else it.run?.();
  } catch (err) { console.error(ZH`指令執行失敗`, it.id, err); }
}

export function isOpen() { return !!root && !root.hidden; }

export function open(q = "") {
  if (!mounted) mount({});
  if (isOpen()) { input.focus(); return; }
  returnFocus = document.activeElement;
  root.hidden = false;
  document.documentElement.classList.add("h0-pl-lock");
  input.value = q;
  active = 0;
  companies = [];
  onInput();
  input.focus();
}

export function close() {
  if (!isOpen()) return;
  clearTimeout(timer);
  seq++;
  if (root.contains(document.activeElement)) document.activeElement.blur();   // 不然原本焦點在 body 時,鍵盤輸入會跑進藏起來的輸入框
  root.hidden = true;
  document.documentElement.classList.remove("h0-pl-lock");
  const back = returnFocus;
  returnFocus = null;
  if (back && back.isConnected && typeof back.focus === "function") back.focus();
}

export function toggle() { if (isOpen()) close(); else open(); }

function onGlobalKey(e) {
    if (document.querySelector("dialog[open]")) return;   // 設定等視窗開著時不處理全站快捷鍵
  if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && (e.key === "k" || e.key === "K")) {
    e.preventDefault();
    toggle();
  }
}

/** 掛上面板。可以重複呼叫來更新 commands / searchCompanies(例如觀察清單變了)。 */
export function mount({ commands, searchCompanies, openCompany } = {}) {
  if (commands !== undefined) opts.commands = commands || [];
  if (searchCompanies !== undefined) opts.searchCompanies = searchCompanies;
  if (openCompany !== undefined) opts.openCompany = openCompany;
  if (!mounted) {
    mounted = true;
    build();
    document.addEventListener("keydown", onGlobalKey);
  } else if (isOpen()) render();
  return { open, close, toggle, isOpen };
}
