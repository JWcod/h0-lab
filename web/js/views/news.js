import { ZH, dyn } from "../i18n/index.js";
import { esc, num, pct, isNum, safeUrl, dateStr, timeAgo } from "../fmt.js";
import { chart, base, axis, C } from "../charts.js";

function officialHTML(R) {
  const x = R.extra || {};
  if (R.meta.market === "TW") {
    const a = x.announcements || [];
    return ZH`<div class="card" style="margin-top:14px"><h3>公司重大訊息(官方第一手)<span class="sub">公開資訊觀測站 · 已累積 ${x.announcements_days || 0} 天;上櫃公司暫不支援</span></h3>
      ${a.length ? a.map((r) => `<details style="border-bottom:1px solid var(--line);padding:8px 0"><summary style="cursor:pointer">
        <span class="num" style="color:var(--label)">${esc(r.date)}</span> ${(r.tags || []).map((t) => `<span class="chip">${esc(t)}</span>`).join(" ")} ${esc(r.subject)}</summary>
        <div class="note" style="white-space:pre-wrap;margin-top:6px">${esc(r.desc)}</div></details>`).join("")
      : ZH`<div class="note">目前累積的資料裡沒有這家公司的重大訊息。官方只提供最近一天的公告,你每天打開 H0 Lab 就會自動累積。</div>`}</div>`;
  }
  const f = (x.filings || []).filter((r) => ["8-K", "10-Q", "10-K"].includes(r.form)).slice(0, 12);
  if (!f.length) return "";
  return ZH`<div class="card" style="margin-top:14px"><h3>SEC 官方申報(第一手)<span class="sub">8-K = 重大事件、10-Q / 10-K = 季報 / 年報</span></h3>
    <table><tr><th>日期</th><th>表單</th><th>說明</th><th></th></tr>${f.map((r) => `<tr><td>${esc(r.date)}</td><td>${esc(r.form)}</td><td style="white-space:normal">${esc(r.desc || "")}</td>
    <td>${safeUrl(r.url) ? ZH`<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">開啟</a>` : ""}</td></tr>`).join("")}</table></div>`;
}

export function render(el, R, ctx) {
  const N = R.news, s = N.summary || {};
  const items = N.items || [];
  const evs = Object.entries(s.events || {}).sort((a, b) => b[1] - a[1]);
  el.innerHTML = ZH`
  <div class="grid g4">
    ${[[ZH`近 3 日`, s.d3], [ZH`近 7 日`, s.d7], [ZH`近 14 日`, s.d14], [ZH`近 30 日`, s.d30]].map(([n, w]) => ZH`<div class="card"><div class="big-label">${n}新聞情緒 ${ctx.hint("news_sentiment")}</div>
      <div style="font-size:24px;font-weight:800" class="num ${w?.avg > 0.05 ? "pos" : w?.avg < -0.05 ? "neg" : ""}">${isNum(w?.avg) ? (w.avg > 0 ? "+" : "") + w.avg.toFixed(2) : "—"}</div>
      <div class="note">${w?.n || 0} 則 · 正面 ${w?.pos || 0} · 負面 ${w?.neg || 0}</div></div>`).join("")}
  </div>
  <div class="grid g3" style="margin-top:14px">
    <div class="card span2"><h3>情緒與新聞量時間軸</h3><div id="tl" class="chart short"></div></div>
    <div class="card"><h3>近 14 日事件分類 <span class="sub">依標題關鍵字</span></h3>
      ${evs.length ? `<div id="ev" class="chart short"></div>` : ZH`<div class="note">近 14 日沒有明顯事件</div>`}</div>
  </div>
  ${officialHTML(R)}
  <div class="card" style="margin-top:14px"><h3>新聞列表 <span class="sub">${items.length} 則 · 來源:Google News / Yahoo · 點標題開啟原文(外部網站)</span></h3>
    <div class="filters" id="nf"></div><div class="news" id="nl"></div>
    <div class="note" style="margin-top:8px">情緒用詞典法判斷(找標題中的正面、負面詞),會誤判反諷或複雜句子。重要新聞請點開原文自己判斷。</div></div>`;

  const tl = s.timeline || [];
  if (tl.length) {
    chart(el.querySelector("#tl"), {
      ...base({ grid: { left: 44, right: 44, top: 30, bottom: 26 } }),
      xAxis: { type: "category", data: tl.map((x) => x.date.slice(5)), ...axis() },
      yAxis: [{ type: "value", min: -1, max: 1, ...axis() }, { type: "value", ...axis(), splitLine: { show: false } }],
      series: [{ name: ZH`平均情緒`, type: "bar", data: tl.map((x) => ({ value: x.avg, itemStyle: { color: x.avg >= 0 ? "rgba(62,224,197,.75)" : "rgba(255,138,92,.75)" } })) },
        { name: ZH`新聞數`, type: "line", yAxisIndex: 1, data: tl.map((x) => x.n), lineStyle: { color: C.accent }, itemStyle: { color: C.accent } }],
    });
  }
  if (evs.length) {
    chart(el.querySelector("#ev"), { ...base({ grid: { left: 80, right: 16, top: 10, bottom: 20 }, tooltip: { trigger: "axis" } }),
      xAxis: { type: "value", ...axis() }, yAxis: { type: "category", data: evs.map((e) => dyn(e[0])).reverse(), ...axis({ axisLabel: { color: C.text2 } }) },
      series: [{ type: "bar", data: evs.map((e) => e[1]).reverse(), itemStyle: { color: C.accent2 } }] });
  }
  const cats = [ZH`全部`, ZH`正面`, ZH`負面`, ...evs.map((e) => dyn(e[0]))];
  let cur = ZH`全部`;
  const draw = () => {
    el.querySelector("#nf").innerHTML = cats.map((c) => `<button class="${c === cur ? "on" : ""}" data-c="${esc(c)}">${esc(c)}</button>`).join("");
    el.querySelectorAll("#nf button").forEach((b) => (b.onclick = () => { cur = b.dataset.c; draw(); }));
    const list = items.filter((i) => cur === ZH`全部` || (cur === ZH`正面` && i.sentiment > 0) || (cur === ZH`負面` && i.sentiment < 0) || (i.events || []).includes(cur));
    el.querySelector("#nl").innerHTML = list.map((i) => {
      const u = safeUrl(i.link);
      const sc = i.sentiment > 0 ? "p" : i.sentiment < 0 ? "n" : "z";
      return `<div class="it"><div class="tm">${esc(dateStr(i.time))}<div>${esc(timeAgo(i.time))}</div></div><div>
        <div class="ti">${u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a>` : esc(i.title)}</div>
        <div class="src"><span class="sent ${sc}">${sc === "p" ? ZH`正面` : sc === "n" ? ZH`負面` : ZH`中性`} ${num(i.sentiment, 2)}</span>
          <span>${esc(i.publisher || "")}</span><span>· ${esc(i.origin || "")}</span>
          ${(i.events || []).map((e) => `<span class="chip">${esc(e)}</span>`).join("")}
          ${(i.pos_words || []).length ? `<span class="pos">+ ${esc(i.pos_words.join(", "))}</span>` : ""}${(i.neg_words || []).length ? `<span class="neg">− ${esc(i.neg_words.join(", "))}</span>` : ""}</div></div></div>`;
    }).join("") || ZH`<div class="note">這個分類沒有新聞,可以切回「全部」</div>`;
  };
  draw();
}
