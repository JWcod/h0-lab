// 更新橫幅、本機設定、App 內建立回測資料。
// - 只跟自己的後端講話(CSP connect-src 'self'),對外連線都在後端。
// - 版本說明是外部文字:一律用 textContent 顯示。
// - 不打擾:只有真的有新版才出現,按「稍後」這個版本一天內不再顯示。
import { hydrate } from "./icons.js";
import { api, btn, el, openSettings } from "./settings.js";
import { errText, ut } from "./update_text.js";

const LATER_KEY = "h0.update.later";
const LATER_MS = 24 * 3600 * 1000;
const POLL_IDLE_MS = 6 * 3600 * 1000;
const POLL_JOB_MS = 1000;

const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* 只是方便,存不了沒關係 */ } },
};

// ---------- 更新橫幅 ----------
let bar = null;
let lastStatus = null;
let pollTimer = null;
let restartingSince = 0;
let awaitingVersion = null;

function ensureBar() {
  if (bar?.isConnected) return bar;
  bar = el("div", { class: "h0u-bar", role: "status", "aria-live": "polite" });
  (document.querySelector(".u-main") || document.body).prepend(bar);
  return bar;
}

function clearBar() { if (bar) { bar.remove(); bar = null; } }

function setBar(message, actions = [], extra = null) {
  const b = ensureBar();
  b.replaceChildren(el("span", { class: "h0u-msg" }, message), ...(extra ? [extra] : []), el("span", { class: "h0u-acts" }, ...actions));
}

function showNotes(latest) {
  const body = el("pre", { class: "h0u-notes" });
  body.textContent = latest.notes || ut("notes.empty");
  const d = el("dialog", { class: "h0u-dlg" },
    el("h2", {}, ut("notes.title", { version: latest.version })),
    el("p", { class: "h0u-note" }, ut("notes.external")),
    body,
    el("div", { class: "h0u-acts" },
      el("a", { class: "u-btn", href: latest.url, target: "_blank", rel: "noopener noreferrer" }, ut("notes.link")),
      btn(ut("banner.close"), () => d.close())));
  d.addEventListener("close", () => d.remove());
  document.body.append(d);
  d.showModal();
}

async function startApply(st, confirmData = false) {
  try {
    const fresh = await api("/api/update/status");   // 權杖用過就換,寫入前都拿最新的
    await api("/api/update/apply", { token: fresh.token, version: st.latest.version, confirm_data_format: confirmData });
    awaitingVersion = st.latest.version;
    poll(0);
  } catch (e) {
    setBar(errText(e.code || "unexpected"), [btn(ut("banner.close"), clearBar)]);
  }
}

function renderAvailable(st) {
  const later = store.get(LATER_KEY);
  if (later?.version === st.latest.version && Date.now() - later.at < LATER_MS) { clearBar(); return; }
  const dismiss = () => { store.set(LATER_KEY, { version: st.latest.version, at: Date.now() }); clearBar(); };
  const notes = btn(ut("banner.notes"), () => showNotes(st.latest));
  const later_ = btn(ut("banner.later"), dismiss);
  const msg = ut("banner.available", { version: st.latest.version });
  if (st.can_apply) {
    setBar(msg, [notes, btn(ut("banner.update"), () => startApply(st), true), later_]);
    return;
  }
  const link = el("a", { class: "u-btn", href: st.latest.url, target: "_blank", rel: "noopener noreferrer" }, ut("banner.manual"));
  const why = st.blocker ? el("span", { class: "h0u-note" }, errText(st.blocker)) : null;
  const cmds = st.commands?.length ? el("code", { class: "h0u-cmd" }, st.commands.join(" && ")) : null;
  const box = el("span", { class: "h0u-detail" }, why, cmds);
  setBar(msg, [notes, ...(st.install_kind === "git" ? [] : [link]), later_], box);
}

function renderJob(st) {
  const job = st.job || {};
  const phase = job.phase;
  if (phase === "failed") {
    awaitingVersion = null;
    if (job.error === "data_format_confirm" && st.latest) {
      setBar(ut("confirm.data"), [btn(ut("confirm.data.go"), () => startApply(st, true), true), btn(ut("banner.later"), clearBar)]);
    } else {
      setBar(errText(job.error), [btn(ut("banner.close"), clearBar)]);
    }
    return true;
  }
  if (!phase || phase === "idle") return false;
  if (phase === "downloading") {
    const mb = (n) => (n / 1024 / 1024).toFixed(0);
    const prog = el("progress", { class: "h0u-prog", max: String(job.total || 1), value: String(job.done || 0) });
    setBar(ut("phase.downloading", { done: mb(job.done || 0), total: mb(job.total || 0) }), [], prog);
  } else {
    setBar(ut(`phase.${phase}`));
  }
  return true;
}

function renderResult(st) {
  const r = st.last_result;
  if (!r?.time || store.get("h0.update.seenResult") === r.time) return false;
  store.set("h0.update.seenResult", r.time);
  const msg = r.status === "ok" ? ut("result.ok", { version: r.to_version || st.current }) : errText(r.code || "unexpected");
  setBar(msg, [btn(ut("banner.close"), clearBar)]);
  return true;
}

function render(st) {
  if (renderJob(st)) return;
  if (renderResult(st)) return;
  if (st.data_format?.newer_than_app) {
    setBar(ut("newer.data"), [btn(ut("banner.close"), clearBar)]);
    return;
  }
  if (st.update_available && st.latest) renderAvailable(st);
  else if (!bar?.dataset.keep) clearBar();
}

async function tick() {
  let st;
  try {
    st = await api("/api/update/status");
  } catch (e) {
    // 伺服器連不上:只有更新重啟時才需要提示,其他情況安靜
    if (awaitingVersion || restartingSince) {
      if (!restartingSince) restartingSince = Date.now();
      setBar(ut(Date.now() - restartingSince > 120000 ? "restart.slow" : "phase.restarting"));
      poll(1500);
    } else {
      poll(POLL_IDLE_MS);
    }
    return;
  }
  if (restartingSince || (awaitingVersion && st.current === awaitingVersion)) { location.reload(); return; }
  lastStatus = st;
  render(st);
  const active = st.job && !["idle", "failed", undefined].includes(st.job.phase);
  poll(active ? POLL_JOB_MS : POLL_IDLE_MS);
}

function poll(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(tick, ms);
}

// ---------- 設定入口 ----------
function mountSettingsEntry() {
  const foot = document.querySelector(".app-side-foot");
  if (!foot || document.getElementById("navLocalSettings")) return;
  const b = el("button", { class: "u-nav", id: "navLocalSettings", type: "button", onclick: () => openSettings() },
    el("span", { class: "u-ic", "data-ic": "settings", "aria-hidden": "true" }, "⚙"), el("span", {}, ut("settings.nav")));
  foot.prepend(b);
  hydrate(b);
}

// 打包版第一次真的要用 SEC 資料時提示填寫
let secBar = null;
async function refreshSecBanner() {
  let local;
  try { local = await api("/api/settings/local"); } catch (e) { return; }
  if (local.sec_required && local.sec_blocked && !local.has_sec_user_agent) {
    if (secBar?.isConnected) return;
    secBar = el("div", { class: "h0u-bar", role: "status" }, el("span", { class: "h0u-msg" }, ut("sec.banner")),
      el("span", { class: "h0u-acts" }, btn(ut("sec.fill"), () => openSettings("data"), true)));
    (document.querySelector(".u-main") || document.body).prepend(secBar);
  } else if (secBar) {
    secBar.remove(); secBar = null;
  }
}

// ---------- App 內建立回測資料(按鈕由 web/js/setup.js 畫,這裡接事件) ----------
let researchTimer = null;

function researchBox(from) {
  return (from || document).querySelector("[data-h0-research-progress]");
}

async function researchPoll() {
  clearTimeout(researchTimer);
  let j;
  try { j = await api("/api/setup/research/status"); } catch (e) { return; }
  const box = researchBox();
  if (j.running) {
    if (box) {
      box.textContent = `${ut("research.running")} ${j.line || ""}`;
      box.closest(".su-main, .su-inline")?.querySelector(".su-warn")?.remove();   // 上次的失敗原因不適用於這次
    }
    researchTimer = setTimeout(researchPoll, 3000);
    return;
  }
  if (!box || !j.started) return;
  if (j.cancelled) box.textContent = ut("research.cancelled");
  else if (j.result === "done") box.textContent = ut("research.done");
  else if (j.result) box.textContent = ut(`research.err.${j.result}`);
}

document.addEventListener("click", async (e) => {
  const t = e.target.closest?.("[data-h0-action]");
  if (!t) return;
  const action = t.dataset.h0Action;
  if (action === "open-settings") { openSettings("data"); return; }
  const box = researchBox(t.closest("section, div.su-inline") || document);
  try {
    if (action === "research-start") {
      await api("/api/setup/research/start", {});
      researchPoll();
    } else if (action === "research-stop") {
      await api("/api/setup/research/stop", {});
      researchPoll();
    }
  } catch (err) {
    if (err.code === "sec_ua_required") openSettings("data");
    if (box) box.textContent = ut(`research.err.${err.code}`);
  }
});

window.addEventListener("h0:settings-saved", refreshSecBanner);
window.addEventListener("h0:open-settings", () => openSettings());
mountSettingsEntry();
tick();
refreshSecBanner();
researchPoll();
setInterval(refreshSecBanner, 15000);
export const _debug = { get status() { return lastStatus; } };
