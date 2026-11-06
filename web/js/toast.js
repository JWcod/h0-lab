// 提示訊息(商業化規格 §7):右下角堆疊、自動消失、滑過暫停計時,可附「復原」之類的動作按鈕。
// - 只用 createElement + textContent,不插入 HTML;樣式在 css/palette.css(.h0-toast*)。
// - 容器 role="status" + aria-live="polite";錯誤訊息用 role="alert"(螢幕閱讀器立刻念)。
// 用法:toast("已加入觀察清單", { action: { label: "復原", run: () => undo() }, tone: "ok", ms: 5000 })
//       回傳 { close() },可提早關掉。
import { ZH } from "./i18n/index.js";

const MAX = 4;          // 同時最多幾則,多的把最舊的擠掉
const DEFAULT_MS = 4000;
const ACTION_MS = 6000; // 有動作按鈕的給久一點,讓人來得及按
let host = null;

function ensureHost() {
  if (host && host.isConnected) return host;
  host = document.createElement("div");
  host.className = "h0-toasts";
  host.setAttribute("role", "status");
  host.setAttribute("aria-live", "polite");
  host.setAttribute("aria-relevant", "additions");
  document.body.appendChild(host);
  return host;
}

export function toast(msg, { action = null, tone = "", ms } = {}) {
  const box = ensureHost();
  const el = document.createElement("div");
  el.className = "h0-toast" + (tone ? " is-" + tone : "");
  if (tone === "err") el.setAttribute("role", "alert");

  const ic = document.createElement("span");
  ic.className = "h0-toast-ic";
  ic.setAttribute("aria-hidden", "true");
  ic.textContent = tone === "ok" ? "✓" : tone === "warn" ? "!" : tone === "err" ? "✕" : "i";
  const tx = document.createElement("span");
  tx.className = "h0-toast-msg";
  tx.textContent = String(msg ?? "");
  el.append(ic, tx);

  let timer = 0, left = ms ?? (action ? ACTION_MS : DEFAULT_MS), started = 0, done = false;
  const close = () => {
    if (done) return;
    done = true;
    clearTimeout(timer);
    el.classList.add("is-out");
    const rm = () => el.remove();
    el.addEventListener("animationend", rm, { once: true });
    setTimeout(rm, 400);   // 關掉動畫(prefers-reduced-motion)時 animationend 不會來
  };
  const start = () => { if (done || left <= 0) return; started = Date.now(); timer = setTimeout(close, left); };
  const pause = () => { clearTimeout(timer); if (started) left -= Date.now() - started; started = 0; };

  if (action && action.label && typeof action.run === "function") {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "h0-toast-act";
    b.textContent = action.label;
    b.addEventListener("click", () => { close(); action.run(); });
    el.appendChild(b);
  }
  const x = document.createElement("button");
  x.type = "button";
  x.className = "h0-toast-x";
  x.setAttribute("aria-label", ZH`關閉提示`);
  x.textContent = "×";
  x.addEventListener("click", close);
  el.appendChild(x);

  // 滑鼠滑過或鍵盤焦點在裡面時暫停,離開後繼續倒數
  el.addEventListener("mouseenter", pause);
  el.addEventListener("mouseleave", start);
  el.addEventListener("focusin", pause);
  el.addEventListener("focusout", (e) => { if (!el.contains(e.relatedTarget)) start(); });

  box.appendChild(el);
  const live = [...box.children].filter((c) => !c.classList.contains("is-out"));
  for (const old of live.slice(0, Math.max(0, live.length - MAX))) old.querySelector(".h0-toast-x")?.click();
  start();
  return { close };
}
