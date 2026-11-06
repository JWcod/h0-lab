import { ZH, dyn } from "../i18n/index.js";
import { esc, num, pct, price, big, mult, isNum } from "../fmt.js";
import { chart, base, axis, C } from "../charts.js";
import { showLog } from "../agentroom.js";
import * as UI from "../ui.js";

export function insightHTML(list) {
  return list.map((i) => `<div class="ins ${i.tone}"><div class="tone"></div><div>
    <div class="t"><span class="cat">${esc(i.category)}</span>${esc(i.title)}<span class="stars">${"★".repeat(i.importance)}</span></div>
    ${i.detail ? `<div class="d">${esc(i.detail)}</div>` : ""}${i.why ? `<div class="w">💡 ${esc(i.why)}</div>` : ""}</div></div>`).join("");
}

// 完整版總覽(「詳細總覽」分頁):關鍵數據、檢查清單、筆記、股價圖、見解。
// 開源版不提供內建模型的評等、八面向分數與綜合公平價(API 也不回傳);模型分數在上方的「我的模型」卡片(modelpick.js)。
export function renderFull(el, R, ctx) {
  const v = R.verdict || {}, t = R.technical;
  const ins = R.insights || [];
  const conf = v.confidence || {};
  el.innerHTML = ZH`
  ${R.agents?.length ? `<div class="agent-strip">${R.agents.map((a) => `<div class="agent-chip" style="--c:${a.color}" title="${esc((a.findings || []).join(" / "))}">
    <div class="n">${a.icon} ${esc(a.name)} <span class="sp"></span><span style="color:var(--pos)">✓</span></div><div class="h">${esc(a.headline || "")}</div></div>`).join("")}</div>` : ""}
  ${conf.missing_core?.length ? ZH`<div class="card" style="margin-bottom:14px;border-color:var(--watch)"><b class="watch">⚠ 資料不完整</b>
    <div class="note" style="margin-top:4px">這次分析缺少:${esc(conf.missing_core.join("、"))}。通常是免費資料來源暫時限流,你可以約 1 小時後按「重新分析」再試一次。</div></div>` : ""}
  ${redFlagsHTML(v.red_flags)}

  <div class="card"><h3>關鍵數據 <span class="sub">滑鼠移到 ? 看公式與判斷方式</span>
    <span class="right note">資料完整度 ${esc(conf.label || "—")} ${esc(conf.score ?? "—")} · 大盤環境 ${esc(R.regime?.label || "—")}</span></h3>
    <div class="stats">${statCards(R, ctx)}</div></div>

  ${R.checklist?.length ? ZH`<div class="card" style="margin-top:14px"><h3>研究檢查清單 <span class="sub">能用資料檢查的自動打勾;其餘要你自己查證</span>
    <span class="right">${R.checklist.filter((c) => c.status === "pass").length} / ${R.checklist.filter((c) => c.status !== "manual").length} 項符合</span></h3>
    <div class="grid g2">${R.checklist.map((c) => `<div class="flex" style="align-items:flex-start;gap:8px;padding:4px 0">
      <span class="check ${c.status === "pass" ? "ok" : c.status === "fail" ? "no" : ""}" style="${c.status === "warn" ? "color:var(--watch)" : c.status === "manual" ? "color:var(--accent)" : ""};min-width:18px">${{ pass: "✓", fail: "✗", warn: "!", manual: "☐" }[c.status]}</span>
      <div><span class="chip" style="margin-right:6px">${esc(c.group)}</span>${esc(c.text)}${c.detail ? `<div class="note">${esc(c.detail)}</div>` : ""}</div></div>`).join("")}</div></div>` : ""}

  <div class="card" style="margin-top:14px"><h3>研究筆記 <span class="sub">寫下研究假設和假設不成立的條件,之後追蹤時對照;只存在你的電腦,不會上傳</span>
    <span class="right" id="noteStamp">${R.notes?.updated ? ZH`上次儲存 ` + esc(R.notes.updated.replace("T", " ")) : ZH`尚未建立`}</span></h3>
    <div class="grid g2">
      ${[["thesis", ZH`研究假設(一句話:你認為這家公司會怎麼變化?)`], ["sell_rules", ZH`假設不成立的條件(發生什麼事代表想錯了?)`], ["watch", ZH`要追蹤的事(財報重點、產業訊號)`], ["free", ZH`其他筆記`]]
        .map(([k, l]) => `<div><div class="big-label">${l}</div><textarea data-k="${k}" rows="4" style="width:100%;resize:vertical;background:var(--bg2);color:var(--text);border:1px solid var(--line2);border-radius:8px;padding:8px;font-family:inherit;font-size:13px">${esc(R.notes?.[k] || "")}</textarea></div>`).join("")}
    </div>
    <div class="flex" style="margin-top:8px"><button class="btn primary" id="noteSave">儲存筆記</button><span class="note" id="noteMsg"></span></div></div>

  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>近一年股價 <span class="sub">與 50 / 200 日線</span></h3><div id="pxchart" class="chart tall"></div></div>
    <div class="card"><h3>重點摘要</h3>
      <div class="section-title" style="margin-top:0">✅ 正面觀察</div><div class="insights">${insightHTML(ins.filter((i) => i.tone === "pos").slice(0, 4))}</div>
      <div class="section-title">⚠️ 主要風險</div><div class="insights">${insightHTML(ins.filter((i) => i.tone === "neg").slice(0, 4))}</div></div>
  </div>

  <div class="card" style="margin-top:14px"><h3>全部見解 <span class="sub">${ins.length} 條,依重要性排序</span></h3>
    <div class="filters" id="insFilter"></div><div class="insights" id="insList"></div></div>`;

  el.querySelectorAll(".agent-chip").forEach((c) => (c.onclick = () => showLog(ctx.state.agentLog[R.meta.symbol], R.agents)));

  // 筆記
  const saveBtn = el.querySelector("#noteSave");
  if (saveBtn) saveBtn.onclick = async () => {
    const body = { symbol: R.meta.symbol };
    el.querySelectorAll("textarea[data-k]").forEach((x) => (body[x.dataset.k] = x.value));
    const r = await fetch("/api/notes", { method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "invest-lab" }, body: JSON.stringify(body) });
    const msg = el.querySelector("#noteMsg");
    if (r.ok) { const j = await r.json(); R.notes = j.note; msg.textContent = ZH`已儲存`; el.querySelector("#noteStamp").textContent = ZH`上次儲存 ` + j.note.updated.replace("T", " "); }
    else msg.textContent = ZH`儲存失敗。請再按一次;一直失敗的話,確認 H0 Lab 還在執行。`;
  };

  // 股價圖
  const cd = t.chart;
  if (cd) {
    const n = Math.min(cd.dates.length, 252);
    const sl = (a) => a.slice(-n);
    chart(el.querySelector("#pxchart"), {
      ...base(), xAxis: { type: "category", data: sl(cd.dates), ...axis(), boundaryGap: false },
      yAxis: { type: "value", scale: true, ...axis(), axisLabel: { showMaxLabel: false } },
      series: [
        { name: ZH`收盤`, type: "line", data: sl(cd.ohlc.map((x) => x[1])), symbol: "none", lineStyle: { width: 2, color: C.text } },
        { name: "MA50", type: "line", data: sl(cd.ma50), symbol: "none", lineStyle: { width: 1.2, color: C.watch } },
        { name: "MA200", type: "line", data: sl(cd.ma200), symbol: "none", lineStyle: { width: 1.2, color: C.accent2 } },
      ],
    });
  }

  // 見解篩選
  const cats = [ZH`全部`, ...new Set(ins.map((i) => i.category))];
  let cur = ZH`全部`;
  const fl = el.querySelector("#insFilter"), li = el.querySelector("#insList");
  const draw = () => {
    fl.innerHTML = cats.map((c) => `<button class="${c === cur ? "on" : ""}" data-c="${esc(c)}">${esc(c)}</button>`).join("");
    li.innerHTML = insightHTML(ins.filter((i) => cur === ZH`全部` || i.category === cur));
    fl.querySelectorAll("button").forEach((b) => (b.onclick = () => { cur = b.dataset.c; draw(); }));
  };
  draw();
}

function redFlagsHTML(flags) {
  if (!flags?.length) return "";
  return ZH`<div class="card" style="margin-bottom:14px;border-color:var(--neg)"><b class="neg">⚠ 財務紅旗</b>
    <ul class="note" style="margin:6px 0 0;padding-left:18px">${flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
    <div class="note" style="margin-top:4px">來自破產風險與財報操縱模型(Altman Z、Beneish M)的門檻,請查證原始財報。</div></div>`;
}

function statCards(R, ctx) {
  const mu = R.valuation.multiples || {}, p = R.fundamentals.profitability, g = R.fundamentals.growth, h = R.fundamentals.health, t = R.technical;
  const s = [
    [ZH`本益比(近四季)`, mult(mu.pe_ttm), "pe_ttm", R.valuation.pe_band ? ZH`5 年中位數 ${num(R.valuation.pe_band.percentiles?.["50"], 1)}x` : ""],
    [ZH`預估本益比`, mult(mu.forward_pe), "forward_pe", R.expectations.ntm_basis || ""],
    ["PEG", num(mu.peg, 2), "peg", ""],
    ["EV/EBITDA", mult(mu.ev_ebitda), "ev_ebitda", ""],
    [ZH`股價淨值比`, mult(mu.pb, 2), "pb", ""],
    [ZH`FCF 殖利率`, pct(mu.fcf_yield), "fcf_yield", ""],
    [ZH`殖利率`, pct(mu.dividend_yield, 2), null, ""],
    ["ROIC", pct(p.roic), "roic", ""],
    ["ROE", pct(p.roe), "roe", ""],
    [ZH`毛利率`, pct(p.gross_margin), "gross_margin", ""],
    [ZH`營業利益率`, pct(p.operating_margin), "operating_margin", ""],
    [ZH`營收年增`, pct(g.revenue_yoy), "revenue_yoy", g.yoy_basis || ""],
    [ZH`EPS 年增`, pct(g.eps_yoy), "eps_yoy", ""],
    [ZH`營收 3 年 CAGR`, pct(g.revenue_cagr_3y), "revenue_cagr_3y", ""],
    ["Altman Z", num(h.altman_z, 2), "altman_z", ""],
    ["Piotroski", R.fundamentals.piotroski ? R.fundamentals.piotroski.score + " / 9" : "—", "piotroski", ""],
    [ZH`淨負債/EBITDA`, mult(h.net_debt_ebitda, 2), "net_debt_ebitda", isNum(h.net_debt) && h.net_debt < 0 ? ZH`淨現金` : ""],
    ["Beta", num(t.beta_adj ?? R.quote.beta, 2), "beta", ZH`調整後(2 年週報酬)`],
    [ZH`年化波動(1 年)`, pct(t.vol_1y), null, ""],
    [ZH`最大回撤(1 年)`, pct(t.max_dd_1y), "max_dd", ""],
  ];
  return s.map(([l, v, k, sub]) => `<div class="stat"><div class="l">${esc(l)}${k ? ctx.hint(k) : ""}</div><div class="v">${v}</div>${sub ? `<div class="s">${esc(sub)}</div>` : ""}</div>`).join("");
}

// ---------- 乾淨版總覽:主畫面只放最重要的資料,細節點了才展開 ----------
// 模型分數在上方「我的模型」卡片(modelpick.js):只來自使用者自己選的模型;沒有模型時是引導卡。這裡只放資料。
const CHECK_TAG = { pass: ZH`符合`, fail: ZH`不符合`, warn: ZH`注意`, manual: ZH`自己查證` };

function details(R) {
  const v = R.verdict || {}, sym = R.meta.symbol;
  const tab = (t) => `#/s/${encodeURIComponent(sym)}/${t}`;
  const D = {};
  const ins = R.insights || [];
  D.insights = {
    title: ZH`見解分析(${ins.length} 則)`, desc: ZH`每則見解都由固定規則產生,附上依據與限制。`,
    sections: ["pos", "neg", "watch"].map((tone) => ({
      h: { pos: ZH`正面觀察`, neg: ZH`風險`, watch: ZH`值得觀察` }[tone],
      items: ins.filter((i) => i.tone === tone).map((i) => ({ tag: i.category, t: i.title, d: [i.detail, i.why ? ZH`限制:` + i.why : ""].filter(Boolean).join(" ") })),
    })).filter((sec) => sec.items.length),
  };
  const ck = R.checklist || [];
  D.checklist = {
    title: ZH`研究檢查清單`, desc: ZH`能用資料檢查的 ${ck.filter((c) => c.status !== "manual").length} 項中,符合 ${ck.filter((c) => c.status === "pass").length} 項。其餘要你自己查證。`,
    sections: [{ items: ck.map((c) => ({ tag: CHECK_TAG[c.status] || c.status, t: `${c.group}:${c.text}`, d: c.detail })) }],
  };
  const c = v.confidence || {};
  D.completeness = {
    title: c.title || ZH`資料完整度`, desc: c.note || "",
    sections: [{ rows: [[ZH`等級`, `${c.label || "—"} ${c.score ?? "—"}`], ...Object.entries(c.parts || {}).map(([k, x]) => [dyn(k), dyn(String(x))])] }],
  };
  const d = R.valuation?.dcf || {}, pb = R.valuation?.pe_band || {};
  D.tools = {
    title: ZH`估值工具`, desc: ZH`各估值工具在預設假設下的結果;假設可以在估值計算機裡自己調整。這些不是綜合公平價,也不是交易價位。`,
    sections: [{ rows: [[ZH`DCF 每股價值(預設假設)`, price(d.base)], [ZH`反向 DCF:市場隱含起始成長`, pct(d.implied_growth)],
      [ZH`目前本益比在自身 5 年的位置`, isNum(pb.current_pct_rank) ? ZH`高於 ${pct(pb.current_pct_rank, 0)} 的時間` : "—"]] },
    { go: [{ label: ZH`打開估值計算機`, hash: tab("valuation") }] }],
  };
  return D;
}

export function render(el, R, ctx) {
  const v = R.verdict || {}, m = R.meta, f = R.fundamentals || {};
  const p = f.profitability || {}, g = f.growth || {}, h = f.health || {}, t = R.technical || {};
  const D = details(R);
  const ins = R.insights || [];
  const nPos = ins.filter((i) => i.tone === "pos").length, nNeg = ins.filter((i) => i.tone === "neg").length;
  const ck = R.checklist || [];
  const missing = v.confidence?.missing_core || [];
  const d = R.valuation?.dcf || {};
  const rows = [
    UI.row({ title: ZH`見解分析`, sub: ZH`正面 ${nPos} 則 · 風險 ${nNeg} 則`, data: { d: "insights" } }),
    UI.row({ title: ZH`研究檢查清單`, sub: ZH`${ck.filter((c) => c.status === "pass").length} / ${ck.filter((c) => c.status !== "manual").length} 項符合`, data: { d: "checklist" } }),
    UI.row({ title: ZH`估值工具`, sub: ZH`DCF、反向 DCF、本益比位置`, data: { d: "tools" } }),
  ].join("");
  const pages = [["detail", ZH`詳細總覽(完整版)`], ["valuation", ZH`估值計算機`], ["financials", ZH`財務報表`], ["health", ZH`品質與安全`], ["technical", ZH`技術面`]]
    .map(([x, n]) => UI.row({ title: n, data: { tab: x } })).join("");
  const kv = (l, val) => `<span class="k">${esc(l)}</span><span class="v">${val}</span>`;
  el.innerHTML = ZH`<div class="u-ov">
    ${missing.length ? ZH`<div class="u-card u-warn" role="status"><b>資料不完整</b><div class="u-sub">這次分析缺少:${esc(missing.join("、"))}。通常是免費資料來源暫時限流,你可以約 1 小時後按「重新分析」再試一次。</div></div>` : ""}
    ${redFlagsHTML(v.red_flags)}
    <div class="u-grid">
      <button type="button" class="u-card" data-tab="health"><span class="u-label">獲利與財務</span>
        <span class="kv" style="margin-top:8px">${kv("ROIC", pct(p.roic))}${kv(ZH`毛利率`, pct(p.gross_margin))}${kv(ZH`營收年增`, pct(g.revenue_yoy))}${kv(ZH`淨負債/EBITDA`, mult(h.net_debt_ebitda, 2))}</span></button>
      <button type="button" class="u-card" data-tab="technical"><span class="u-label">股價與技術面</span>
        <span class="kv" style="margin-top:8px">${kv(ZH`趨勢`, esc(t.trend?.label || "—"))}${kv(ZH`12-1 月動能`, pct(t.mom_12_1))}${kv(ZH`距 52 週高點`, pct(t.pct_from_52w_high))}${kv(ZH`一年波動`, pct(t.vol_1y))}</span></button>
    </div>
    <div class="u-grid">
      <button type="button" class="u-card u-kpi" data-d="tools"><span class="u-label">估值工具(預設假設)</span>
        <span class="u-verdict"><b class="u-num">${price(d.base)}</b><span class="u-sub">DCF 每股</span></span></button>
      <button type="button" class="u-card u-kpi" data-d="completeness"><span class="u-label">${esc(v.confidence?.title || ZH`資料完整度`)}</span>
        <span class="u-verdict"><b>${esc(v.confidence?.label || "—")}</b><span class="u-num">${esc(v.confidence?.score ?? "—")}</span></span></button>
    </div>
    <h2 class="u-label u-list-h">細節</h2>
    <ul class="u-list">${rows}</ul>
    <h2 class="u-label u-list-h">完整分析</h2>
    <ul class="u-list">${pages}</ul>
  </div>`;
  el.querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => { const x = D[b.dataset.d]; if (x) UI.openDetail(x); }));
  el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { ctx.go(m.symbol, b.dataset.tab); window.scrollTo(0, 0); }));
}
