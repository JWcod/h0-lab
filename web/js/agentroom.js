// 分析進行中的畫面(階段 3 D1 第 2 版):一排 6 個小圓點(UI.steps),由 /api/report/stream 的 Server-Sent Events 驅動。
// 每個 agent 的發現與逐行執行紀錄收進細節面板(電腦右側檢視器 / 手機底部彈出卡),主畫面只留進度與目前在做的事。
// 沒有淡入、閃爍等透明度動畫(規則 7);轉圈動畫由 ui.css 處理,系統「減少動態效果」時停用。
import { ZH } from "./i18n/index.js";
import { esc } from "./fmt.js";
import * as UI from "./ui.js";

export const AGENTS = [
  { id: "data", name: ZH`資料蒐集`, step: ZH`資料`, role: ZH`抓取股價、財報、籌碼、新聞與官方公告`, icon: "◎", color: "#5ab0ff" },
  { id: "fund", name: ZH`財報分析`, step: ZH`財報`, role: ZH`整理三大報表,評估獲利、成長與財務安全`, icon: "▤", color: "#3ee0c5" },
  { id: "market", name: ZH`技術與籌碼`, step: ZH`技術籌碼`, role: ZH`判讀趨勢、動能、法人與大盤環境`, icon: "◭", color: "#ffd166" },
  { id: "news", name: ZH`消息面`, step: ZH`消息`, role: ZH`分類新聞情緒與重大訊息`, icon: "✦", color: "#f472b6" },
  { id: "val", name: ZH`估值`, step: ZH`估值`, role: ZH`DCF 蒙地卡羅、本益比河流、同業與分析師`, icon: "◆", color: "#9b8cff" },
  { id: "dec", name: ZH`資料整理`, step: ZH`整理`, role: ZH`整理資料完整度、財務紅旗與研究檢查清單`, icon: "★", color: "#ff8a5c" },
];
const BY = Object.fromEntries(AGENTS.map((a) => [a.id, a]));
const IDX = Object.fromEntries(AGENTS.map((a, i) => [a.id, i]));
const STATE_TXT = { wait: ZH`等待`, run: ZH`進行中`, done: ZH`完成`, fail: ZH`部分失敗` };

const short = (a) => a.name.replace(" Agent", "");
// 執行紀錄一行:時間(快取重播時沒有時間)+ agent + 訊息;發現前面加箭頭
function logItem(ev) {
  const a = BY[ev.agent];
  const ts = typeof ev.t === "number" && ev.t > 0 ? ZH`${ev.t.toFixed(2)} 秒 · ` : "";
  return { tag: ts + short(a), t: (ev.kind === "finding" ? "→ " : "") + (ev.msg || "") };
}

// 由事件整理出每個 agent 的狀態、目前訊息與發現(主畫面與細節面板共用)
function summarize(events) {
  const per = Object.fromEntries(AGENTS.map((a) => [a.id, { state: "wait", now: "", finds: [], bad: false }]));
  for (const ev of events) {
    const s = per[ev.agent]; if (!s) continue;
    if (ev.kind === "start" || ev.kind === "log") {
      if (s.state === "wait") s.state = "run";
      s.now = ev.msg || s.now;
      if (ev.ok === false) s.bad = true;            // 資料源失敗:之後標「部分失敗」(降級顯示)
    } else if (ev.kind === "finding") { s.finds.push(ev.msg || ""); }
    else if (ev.kind === "done") { s.state = s.bad ? "fail" : "done"; s.now = ev.msg || s.now; }
    else if (ev.kind === "error") { s.state = "fail"; s.bad = true; s.now = ev.msg || s.now; }
  }
  return per;
}

function detailSections(events, agents) {
  const per = summarize(events);
  const info = Object.fromEntries((agents || []).map((a) => [a.id, a]));
  const secs = AGENTS.map((a) => {
    const s = per[a.id], r = info[a.id] || {};
    const finds = s.finds.length ? s.finds : (r.findings || []);
    const head = r.headline || s.now;
    const st = events.length ? `(${STATE_TXT[s.state]})` : "";
    return { h: `${a.step} · ${a.name}${st}`, p: [a.role, ...(head ? [head] : [])], items: finds.map((f) => ({ t: f })) };
  });
  secs.push(events.length
    ? { h: ZH`執行紀錄(${events.length} 行,每一行都是程式真的在做的事)`, items: events.filter((ev) => BY[ev.agent]).map(logItem) }
    : { h: ZH`執行紀錄`, p: [ZH`這次是從快取載入,沒有逐步紀錄;按「重新分析」可以看到完整過程。`] });
  return secs;
}

export function mount(el, title, symbol) {
  el.innerHTML = ZH`<section class="u-card ar" aria-labelledby="arH">
      <h1 class="ar-h" id="arH">正在分析 ${esc(title)}${title === symbol ? "" : ` <span class="u-sub u-num">${esc(symbol)}</span>`}</h1>
      ${UI.steps(AGENTS.map((a) => a.step))}
      <p class="ar-now" id="arNow">準備中…</p>
      <div class="ar-foot"><span class="u-sub u-num" id="arTime">已執行 0.0 秒</span>
        <button type="button" class="u-btn" id="arMore">看細節 ›</button></div>
      <p class="u-sr" id="arLive" role="status" aria-live="polite"></p>
    </section>
    <div id="arBanner"></div>`;
  const steps = el.querySelector(".u-steps");
  const t0 = performance.now();
  // 切換到別的股票或頁面時,app.js 會呼叫 close():之後這個畫面的串流、計時器、事件一律停止,不寫到新畫面上
  let dead = false, es = null, onClose = null, detailOpened = false;
  const timer = setInterval(() => {
    if (dead) { clearInterval(timer); return; }
    const e = el.querySelector("#arTime");
    if (e) e.textContent = ZH`已執行 ${((performance.now() - t0) / 1000).toFixed(1)} 秒`; else clearInterval(timer);
  }, 100);
  const log = [];
  const shown = [];                                   // 已經套用到畫面的事件(細節面板用這份)
  const queue = [];
  const stepState = AGENTS.map(() => "wait");
  let draining = false;

  const setStep = (i, st) => {
    if (stepState[i] === st) return;
    stepState[i] = st;
    UI.setStep(steps, i, st);
    // 只在步驟狀態改變時通知螢幕閱讀器,不逐行朗讀
    if (st !== "wait") el.querySelector("#arLive").textContent = `${AGENTS[i].step}:${STATE_TXT[st]}`;
  };
  const apply = (ev) => {
    if (dead) return;
    const a = BY[ev.agent];
    if (!a) return;
    shown.push(ev);
    const s = summarize(shown)[a.id];
    setStep(IDX[a.id], s.state);
    const now = el.querySelector("#arNow");
    if (now && ev.msg) now.textContent = `${a.step}:${ev.kind === "finding" ? "→ " : ""}${ev.msg}`;
  };
  // 事件一起湧進來時稍微錯開顯示,看得出每一步的節奏
  const drain = () => {
    if (dead || !queue.length) { draining = false; return; }
    draining = true;
    apply(queue.shift());
    setTimeout(drain, queue.length > 40 ? 8 : queue.length > 15 ? 25 : 55);
  };
  el.querySelector("#arMore").addEventListener("click", () => {
    detailOpened = true;
    UI.openDetail({ title: ZH`分析細節`, desc: ZH`${title} ${symbol} · 按下時的即時紀錄;關閉後再按一次「看細節」可以看最新進度`,
      sections: detailSections(shown) });
  });
  return {
    push(ev) { if (dead) return; log.push(ev); queue.push(ev); if (!draining) drain(); },
    log,
    // 串流交給 room 管理:close() 時一起關掉,並讓等待串流的 Promise 以「不繼續」結束
    attach(source, resolve) { es = source; onClose = resolve; },
    get dead() { return dead; },
    close() {
      if (dead) return;
      dead = true; clearInterval(timer); queue.length = 0;
      if (es) es.close();
      if (onClose) onClose(false);
    },
    async settle() { while (!dead && (queue.length || draining)) await new Promise((r) => setTimeout(r, 60)); },
    finish() {
      if (dead) return;
      clearInterval(timer);
      // 報告已經取回:還沒收到完成事件的步驟(例如串流中斷改用一般請求)算完成;已經有資料源失敗的維持「部分失敗」
      const per = summarize(shown);
      AGENTS.forEach((a, i) => { if (stepState[i] === "wait" || stepState[i] === "run") setStep(i, per[a.id].bad ? "fail" : "done"); });
      // 分析中開的細節面板內容已經過時,報告畫好前先關掉
      if (detailOpened) UI.closeDetail();
      const now = el.querySelector("#arNow");
      if (now) now.textContent = ZH`分析完成,正在整理報告…`;
    },
    fail(msg) {
      if (dead) return;
      clearInterval(timer);
      AGENTS.forEach((_, i) => { if (stepState[i] === "run") setStep(i, "fail"); });
      const b = el.querySelector("#arBanner");
      if (b) b.innerHTML = ZH`<div class="u-card u-warn" role="alert"><b>分析失敗</b><p class="u-p" style="margin-top:6px">${esc(msg)}</p></div>`;
    },
  };
}

// 報告裡「分析過程」:同一個細節面板,列出每個 agent 的結論、發現與執行紀錄
export function showLog(events, agents) {
  UI.openDetail({ title: ZH`分析過程`, sections: detailSections(events || [], agents) });
}
