// 設定視窗(左側分頁:一般、我的模型、資料來源、隱私)。文字都在 update_text.js(中英文各一份)。
// - 外觀、配色、選定模型由 app.js 管:這裡只發 h0:pref 事件,不直接碰畫面狀態。
// - 金鑰只送不收:後端只回「有沒有填」。路徑、模型名稱都用 textContent 顯示。
import { isEn, setLang } from "./i18n/index.js";
import { current } from "./modelpick.js";
import { ut } from "./update_text.js";

const HEADERS = { "Content-Type": "application/json", "X-Requested-With": "invest-lab" };
const TABS = ["general", "models", "data", "privacy"];
// 只放已經接好的來源;key 是「要不要填設定」
const SOURCES = [["yahoo", "none"], ["twse", "none"], ["tdcc", "none"], ["finmind", "optional"], ["sec", "required"], ["news", "none"]];

export function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "onclick") n.addEventListener("click", v);
    else n.setAttribute(k, v);
  }
  for (const c of kids) if (c != null) n.append(c);
  return n;
}

export const btn = (label, onclick, primary = false) => el("button", { type: "button", class: `u-btn${primary ? " is-primary" : ""}`, onclick }, label);

export async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: HEADERS, body: JSON.stringify(body) });
  let j = null;
  try { j = await r.json(); } catch (e) { /* 沒有 JSON 就看狀態碼 */ }
  if (!r.ok) { const err = new Error(j?.detail || `HTTP ${r.status}`); err.code = j?.detail; err.status = r.status; throw err; }
  return j;
}

async function saveLocal(body) {
  const { token } = await api("/api/settings/local");   // 權杖用過就換,每次寫入前拿最新的
  return api("/api/settings/local", { ...body, token });
}

function prefs() {
  try { return JSON.parse(localStorage.getItem("jwlab.prefs") || "{}"); } catch (e) { return {}; }
}

const setPref = (detail) => window.dispatchEvent(new CustomEvent("h0:pref", { detail }));

function row(label, control, note, labelFor) {
  return el("div", { class: "h0s-row" },
    el("div", { class: "h0s-label" }, labelFor ? el("label", { for: labelFor }, label) : label, note ? el("p", { class: "h0s-note" }, note) : null),
    el("div", { class: "h0s-ctl" }, control));
}

// 分段控制:選項 [值, 文字];按下立刻生效
function seg(label, options, value, onPick) {
  const buttons = options.map(([v, text]) => {
    const b = el("button", { type: "button", class: "h0s-seg-btn", "aria-pressed": String(v === value), onclick: () => {
      buttons.forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      onPick(v);
    } }, text);
    return b;
  });
  return el("div", { class: "h0s-seg", role: "group", "aria-label": label }, ...buttons);
}

function generalPane(st) {
  const p = prefs();
  const msg = el("p", { class: "h0s-note", role: "status" });
  const auto = el("input", { type: "checkbox", id: "h0sAuto" });
  auto.disabled = ["offline", "env"].includes(st.disabled_reason);
  auto.checked = st.auto_check && !auto.disabled;   // 這次不會檢查就不打勾
  auto.addEventListener("change", async () => {
    try {
      await saveLocal({ auto_check: auto.checked });
      st.auto_check = auto.checked;
      msg.textContent = ut("settings.saved");
    } catch (e) {
      auto.checked = st.auto_check;
      msg.textContent = ut("settings.err.generic");
    }
  });
  const why = st.disabled_reason === "offline" ? ut("settings.auto.offline") : st.disabled_reason === "env" ? ut("settings.auto.env") : ut("settings.auto.help");
  return el("div", {},
    row(ut("settings.lang"), seg(ut("settings.lang"), [["zh-TW", "繁體中文"], ["en", "English"]], isEn ? "en" : "zh-TW", setLang)),
    row(ut("settings.theme"), seg(ut("settings.theme"), [["system", ut("settings.theme.system")], ["dark", ut("settings.theme.dark")], ["light", ut("settings.theme.light")]],
      p.theme || "system", (theme) => setPref({ theme }))),
    row(ut("settings.colors"), seg(ut("settings.colors"), [["tw", ut("settings.colors.tw")], ["us", ut("settings.colors.us")]],
      p.colors === "us" ? "us" : "tw", (colors) => setPref({ colors })), ut("settings.colors.help")),
    row(ut("settings.auto"), auto, why, "h0sAuto"),
    row(ut("settings.version.label"), el("span", { class: "h0s-mono" }, `v${st.current}`)),
    msg);
}

async function modelsPane(close) {
  const go = (hash) => () => { close(); location.hash = hash; };
  const actions = el("div", { class: "h0u-acts h0s-acts" }, btn(ut("settings.models.new"), go("#/wizard"), true), btn(ut("settings.models.bench"), go("#/models")));
  let mine;
  try {
    mine = ((await api("/api/models")).models || []).filter((m) => m.source !== "shipped" && !m.error);
  } catch (e) {
    return el("div", {}, el("p", { role: "alert" }, ut("settings.models.err")), actions);
  }
  if (!mine.length) return el("div", {}, el("p", { class: "h0s-lead" }, ut("settings.models.empty")), el("p", { class: "h0s-note" }, ut("settings.models.why")), actions);
  const cur = current(mine, {});
  const list = el("div", { class: "h0s-list", role: "radiogroup", "aria-label": ut("settings.models.use") }, ...mine.map((m) => {
    const r = el("input", { type: "radio", name: "h0sModel", value: m.id });
    r.checked = m.id === cur;
    r.addEventListener("change", () => setPref({ model: m.id }));
    const date = String(m.updated || m.created || "").slice(0, 10);
    return el("label", { class: "h0s-item" }, r, el("span", { class: "h0s-item-name" }, m.name || m.id), date ? el("span", { class: "h0s-mono" }, date) : null);
  }));
  return el("div", {}, el("p", { class: "h0s-lead" }, ut("settings.models.use")), list, el("p", { class: "h0s-note" }, ut("settings.models.why")), actions);
}

function dataPane(local) {
  const msg = el("p", { class: "h0s-note", role: "status" });
  const sec = el("input", { type: "text", id: "h0uSec", autocomplete: "off", spellcheck: "false", maxlength: "200", placeholder: "Jane Doe jane@your-domain.tld" });
  const tok = el("input", { type: "password", id: "h0uTok", autocomplete: "off", spellcheck: "false", maxlength: "4096" });
  const secNote = el("p", { class: "h0s-note" });
  const tokNote = el("p", { class: "h0s-note" });
  const clear = btn(ut("settings.token.clear"), () => send({ finmind_token: "" }));
  const paint = (s) => {
    secNote.textContent = s.has_sec_user_agent ? ut("settings.sec.set") : ut("settings.sec.help");
    tokNote.textContent = s.has_finmind_token ? ut("settings.token.set") : ut("settings.token.help");
    clear.hidden = !s.has_finmind_token;
  };
  async function send(body) {
    try {
      paint(await saveLocal(body));
      msg.textContent = ut("settings.saved");
      if ("sec_user_agent" in body) sec.value = "";
      if ("finmind_token" in body) tok.value = "";
      window.dispatchEvent(new CustomEvent("h0:settings-saved"));
    } catch (err) {
      const k = `settings.err.${err.code}`;
      msg.textContent = ut(k) === k ? ut("settings.err.generic") : ut(k);
    }
  }
  const submit = (e) => {
    e.preventDefault();
    const body = {};
    if (sec.value.trim()) body.sec_user_agent = sec.value;
    if (tok.value.trim()) body.finmind_token = tok.value;
    if (Object.keys(body).length) send(body);
    else msg.textContent = ut("settings.empty");
  };
  paint(local);
  const form = el("form", { class: "h0s-form" },
    el("label", { class: "h0u-field", for: "h0uSec" }, ut("settings.sec")), sec, secNote,
    el("label", { class: "h0u-field", for: "h0uTok" }, ut("settings.token")), tok, tokNote,
    el("div", { class: "h0u-acts h0s-acts" }, el("button", { type: "submit", class: "u-btn is-primary" }, ut("settings.save")), clear), msg);
  form.addEventListener("submit", submit);
  const table = el("table", { class: "h0s-table" },
    el("thead", {}, el("tr", {}, el("th", { scope: "col" }, ut("settings.src.name")), el("th", { scope: "col" }, ut("settings.src.what")), el("th", { scope: "col" }, ut("settings.src.key")))),
    el("tbody", {}, ...SOURCES.map(([id, need]) => el("tr", {},
      el("td", {}, ut(`settings.src.${id}`)), el("td", {}, ut(`settings.src.${id}.what`)),
      el("td", {}, ut(`settings.src.need.${need}`))))));
  return el("div", {}, form, el("h3", {}, ut("settings.src.title")), table);
}

function privacyPane(local) {
  const labels = { data: "settings.folder.data", private: "settings.folder.private", env_file: "settings.folder.env", support: "settings.folder.support", logs: "settings.folder.logs" };
  const folders = el("dl", { class: "h0s-folders" }, ...Object.entries(local.folders || {}).flatMap(([k, path]) => [
    el("dt", {}, ut(labels[k] || k)), el("dd", { class: "h0s-mono" }, path)]));
  return el("div", {},
    el("ul", { class: "h0s-points" }, ...["local", "account", "network", "keys"].map((k) => el("li", {}, ut(`settings.privacy.${k}`)))),
    el("h3", {}, ut("settings.folder.title")), folders,
    el("h3", {}, ut("settings.disclaimer.title")), el("p", {}, ut("settings.disclaimer")));
}

let opening = false;

export async function openSettings(tab = "general") {
  if (opening) return;
  opening = true;
  let local; let st;
  try {
    [local, st] = await Promise.all([api("/api/settings/local"), api("/api/update/status")]);
  } catch (e) {
    return;
  } finally {
    opening = false;
  }
  document.querySelector("dialog.h0s-dlg")?.remove();
  const pane = el("div", { class: "h0s-pane", role: "tabpanel", id: "h0sPane", tabindex: "0" });
  const close = () => d.close();
  const tabs = TABS.map((id) => el("button", { type: "button", class: "h0s-tab", role: "tab", id: `h0sTab-${id}`, "aria-controls": "h0sPane", onclick: () => show(id) }, ut(`settings.tab.${id}`)));
  let shown = 0;
  async function show(id) {
    const ticket = ++shown;
    tabs.forEach((t, i) => {
      const on = TABS[i] === id;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
    });
    pane.setAttribute("aria-labelledby", `h0sTab-${id}`);
    heading.textContent = ut(`settings.tab.${id}`);
    if (id === "data") try { local = await api("/api/settings/local"); } catch (e) { /* 沿用上次的狀態 */ }   // 剛存過要顯示已填寫
    const content = id === "general" ? generalPane(st) : id === "models" ? await modelsPane(close) : id === "data" ? dataPane(local) : privacyPane(local);
    if (ticket === shown) pane.replaceChildren(content);   // 切太快時只留最後一個
  }
  const heading = el("h3", { class: "h0s-heading" });
  const rail = el("div", { class: "h0s-rail", role: "tablist", "aria-orientation": "vertical", "aria-label": ut("settings.title") }, ...tabs);
  rail.addEventListener("keydown", (e) => {
    const i = tabs.indexOf(document.activeElement);
    const to = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    const next = tabs[(to + tabs.length) % tabs.length];
    next.focus();
    show(TABS[tabs.indexOf(next)]);
  });
  const d = el("dialog", { class: "h0s-dlg", "aria-labelledby": "h0sTitle" },
    el("header", { class: "h0s-head" }, el("h2", { id: "h0sTitle" }, ut("settings.title")),
      el("button", { type: "button", class: "h0s-x", "aria-label": ut("settings.close"), onclick: close }, "×")),
    el("div", { class: "h0s-body" }, rail, el("div", { class: "h0s-main" }, heading, pane)));
  d.addEventListener("close", () => d.remove());
  let downOnBackdrop = false;   // 從輸入框拖曳選字到視窗外放開時不關閉
  d.addEventListener("mousedown", (e) => {
    const b = d.getBoundingClientRect();
    downOnBackdrop = e.target === d && (e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom);
  });
  d.addEventListener("click", (e) => {
    const b = d.getBoundingClientRect();
    const outside = e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom;
    if (e.target === d && downOnBackdrop && outside) close();
  });
  document.body.append(d);
  d.showModal();
  await show(TABS.includes(tab) ? tab : "general");
  document.getElementById(`h0sTab-${TABS.includes(tab) ? tab : "general"}`)?.focus();
}
