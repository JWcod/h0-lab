// 市場掃描:只用使用者選定的模型排名(2026-11-03「自建模型優先」)。沒有模型就顯示引導卡、不排名。
// 「開始掃描」照舊在背景更新整組股票的資料(也照舊累積前瞻紀錄);排名用掃描時存下的基礎變數,
// 由 GET /api/scan/latest?model= 在伺服器重算,不抓資料。內建模型的評等、分數、綜合公平價都不顯示。
import { ZH } from "../i18n/index.js";
import { esc, num, pct, price, big, scoreInk, isNum } from "../fmt.js";
import * as UI from "../ui.js";
import * as Pick from "../modelpick.js";
import { PILLAR_LABEL, UNVALIDATED } from "../modelapi.js";

const PK = ["quality", "growth", "valuation", "health", "momentum", "flows", "revisions", "sentiment"];
let timer = null;

export async function render(el, state) {
  clearInterval(timer);
  const unis = (await (await fetch("/api/scan/universes")).json()).items;
  const cur = state.scanKey || "tw_large";
  let mine = [];
  try { mine = await Pick.myModels(state); } catch (e) { mine = []; }
  const model = Pick.current(mine, state);
  el.innerHTML = ZH`
  <h1 class="u-largetitle">選股掃描</h1>
  <p class="u-sub" style="margin:-6px 0 14px">用你自己的模型替整組股票打分數並排名,整理成可以深入查證的研究清單。H0 Lab 不提供內建模型的排名。</p>
  <div class="u-card">
    <div class="flex" style="flex-wrap:wrap;gap:10px;align-items:center">
      <span class="toggle" id="uni">${unis.map((u) => `<button data-k="${u.key}" class="${u.key === cur ? "on" : ""}">${esc(u.label)}(${u.n})</button>`).join("")}</span>
      <button class="u-btn is-primary" id="run">開始掃描</button>
      <button class="u-btn" id="howto">說明</button>
      <span class="u-sub" id="prog"></span>
    </div>
    ${mine.length ? `<div style="margin-top:10px">${Pick.selectHTML(mine, model, "scanModel")}</div>` : ""}
  </div>
  <div class="card" style="margin-top:14px;display:none" id="feedCard"><h2><span class="live-dot"></span> 掃描進度 <span class="sub">每完成一檔就更新</span></h2><div class="feed" id="feed"></div></div>
  <div class="card" style="margin-top:14px"><h2 id="tt" style="flex-wrap:wrap">結果</h2>
    <div class="filters" id="flt"></div><div class="tbl-wrap" id="tbl"></div></div>`;
  el.querySelectorAll("#uni button").forEach((b) => (b.onclick = () => { state.scanKey = b.dataset.k; render(el, state); }));
  const sel = el.querySelector("#scanModel");
  if (sel) sel.onchange = () => { Pick.setSelected(sel.value, state); loadTable(el, cur, sel.value, mine); };
  el.querySelector("#howto").onclick = () => UI.openDetail({ title: ZH`選股掃描怎麼用`, sections: [{ p: [
    ZH`先用精靈建立你自己的模型。掃描會用你選的模型替整組股票打分數並排名,挑有興趣的公司點進去查證。`,
    ZH`「開始掃描」會更新整組股票的資料;第一次每檔都要抓完整資料(台股 40 檔約 10–20 分鐘)。FinMind 免費額度用完時會用快取或略過部分籌碼資料,之後有快取會快很多。`,
    ZH`排名只是你的模型輸出,一律標「未驗證」;要知道它有沒有用,到模型工作台做開發期回測。不是買賣建議。`] }] });
  el.querySelector("#run").onclick = async () => {
    const r = await fetch(`/api/scan/start?universe=${encodeURIComponent(cur)}`, { method: "POST", headers: { "X-Requested-With": "invest-lab" } });
    if (!r.ok) { el.querySelector("#prog").textContent = (await r.json()).detail || ZH`無法開始`; return; }
    poll(el, state, cur, () => Pick.current(mine, state), mine);
  };
  await loadTable(el, cur, model, mine);
  const st = await (await fetch("/api/scan/status")).json();
  if (st.finmind) {
    const fm = st.finmind;
    const tip = esc(ZH`FinMind 免費額度:本小時已用 ${fm.used_last_hour} / ${fm.budget}`);
    el.querySelector("#prog").innerHTML = fm.cooldown_remaining
      ? ZH`<span class="watch" data-tip-text="${tip}">台股資料額度暫時用完,約 ${Math.ceil(fm.cooldown_remaining / 60)} 分鐘後恢復;這段時間台股籌碼 / 財報會先用快取</span>`
      : ZH`<span data-tip-text="${tip}">資料服務正常</span>`;
  }
  if (st.running) poll(el, state, cur, () => Pick.current(mine, state), mine);
}

function poll(el, state, key, modelNow, mine) {
  clearInterval(timer);
  const tick = async () => {
    if (!document.body.contains(el.querySelector("#prog"))) { clearInterval(timer); return; }
    const st = await (await fetch("/api/scan/status")).json();
    const p = el.querySelector("#prog");
    const fc = el.querySelector("#feedCard"), fd = el.querySelector("#feed");
    if (fc && st.recent?.length) {
      fc.style.display = "";
      fd.innerHTML = st.recent.map((r) => `<div class="f"><b>${esc(r.name)}</b><span class="num" style="color:var(--label)">${esc(r.symbol)}</span>
        <span class="sp"></span><span class="num">${price(r.price)}</span></div>`).join("");
    }
    if (st.running) {
      p.innerHTML = ZH`掃描「${esc(st.label)}」中:${st.done} / ${st.total}(目前 ${esc(st.current || "")})${st.errors?.length ? ZH` · 失敗 ${st.errors.length}` : ""}
        <span class="mini-bar" style="width:${Math.round((st.done / st.total) * 160)}px;background:var(--accent);margin-left:8px"></span>`;
    } else {
      clearInterval(timer);
      p.textContent = st.finished ? ZH`完成(${st.finished.replace("T", " ")})` : "";
      if (st.key === key) loadTable(el, key, modelNow(), mine);
    }
  };
  tick();
  timer = setInterval(tick, 2500);
}

async function loadTable(el, key, model, mine) {
  const tbl = el.querySelector("#tbl"), tt = el.querySelector("#tt"), flt = el.querySelector("#flt");
  if (!model) {
    tt.innerHTML = ZH`結果`;
    flt.innerHTML = "";
    tbl.innerHTML = Pick.guideHTML(ZH`選股掃描只用你自己的模型排名;沒有模型時不排名。`);
    return;
  }
  const r = await fetch(`/api/scan/latest?universe=${encodeURIComponent(key)}&model=${encodeURIComponent(model)}`);
  const j = await r.json();
  if (!r.ok) { flt.innerHTML = ""; tbl.innerHTML = `<div class="note">${esc(j.detail || ZH`讀取失敗`)}</div>`; return; }
  const rows = j.rows || [];
  const mname = j.model?.name || (mine.find((m) => m.id === model) || {}).name || model;
  tt.innerHTML = ZH`結果 <span class="sub">${esc(j.label || "")} · ${j.time ? ZH`掃描時間 ` + esc(j.time.replace("T", " ")) : ZH`尚未掃描`} · 依「${esc(mname)}」排名 ${rows.length} 檔${j.errors?.length ? ZH` · 失敗 ${j.errors.length} 檔:${esc(j.errors.map((e) => e.symbol).join(", "))}` : ""}</span>
    <span class="mw-unval" title="沒有經過回測驗證">${esc(UNVALIDATED)}</span>`;
  if (!rows.length) {
    flt.innerHTML = "";
    tbl.innerHTML = `<div class="note">${esc(j.message || (j.time ? ZH`這次掃描沒有算得出分數的股票。` : ZH`還沒有掃描結果,按「開始掃描」。`))}</div>`;
    return;
  }
  let sortKey = "rank", asc = true, filter = ZH`全部`;
  const cols = [
    ["rank", ZH`排名`], ["name", ZH`公司`], ["price", ZH`股價`], ["m_lt", ZH`長期分`], ["m_ltg", ZH`長期等級`], ["m_st", ZH`短期分`], ["m_stg", ZH`短期等級`],
    ...PK.map((k) => ["p_" + k, PILLAR_LABEL[k] || k]), ["forward_pe", ZH`預估PE`], ["mom_12_1", ZH`12-1動能`], ["market_cap", ZH`市值`], ["confidence", ZH`資料完整度`],
  ];
  const val = (x, k) => (k.startsWith("p_") ? x.model?.pillars?.[k.slice(2)] : k === "m_lt" ? x.model?.lt_score : k === "m_st" ? x.model?.st_score
    : k === "m_ltg" ? x.model?.lt_grade : k === "m_stg" ? x.model?.st_grade : x[k]);
  const filters = [ZH`全部`, ZH`有紅旗`, ZH`循環股`];
  const draw = () => {
    flt.innerHTML = filters.map((f) => `<button class="${f === filter ? "on" : ""}" data-f="${f}">${f}</button>`).join("");
    flt.querySelectorAll("button").forEach((b) => (b.onclick = () => { filter = b.dataset.f; draw(); }));
    let rs = rows.filter((x) => filter === ZH`全部` || (filter === ZH`有紅旗` && x.red_flags?.length) || (filter === ZH`循環股` && x.cyclical));
    rs = rs.slice().sort((a, b) => {
      const x = val(a, sortKey), y = val(b, sortKey);
      if (!isNum(x) && !isNum(y)) return String(x ?? "").localeCompare(String(y ?? "")) * (asc ? 1 : -1);
      if (!isNum(x)) return 1; if (!isNum(y)) return -1;
      return asc ? x - y : y - x;
    });
    const left = (k) => k === "name" || k === "m_ltg" || k === "m_stg";
    tbl.innerHTML = `<table><tr>${cols.map(([k, n]) => `<th class="${left(k) ? "" : "n"}" data-k="${k}" style="cursor:pointer">${esc(n)}${k === sortKey ? (asc ? " ▲" : " ▼") : ""}</th>`).join("")}</tr>
      ${rs.map((x) => `<tr data-s="${esc(x.symbol)}" style="cursor:pointer"><td class="n">${x.rank ?? "—"}</td>
        <td><b>${esc(x.name)}</b> <span class="num" style="color:var(--label)">${esc(x.symbol)}</span>${x.cyclical ? ZH` <span class="chip">循環</span>` : ""}${x.red_flags?.length ? ' <span class="neg">⚠</span>' : ""}</td>
        <td class="n">${price(x.price)}</td>
        <td class="n" style="color:${scoreInk(x.model?.lt_score)}">${num(x.model?.lt_score, 0)}</td><td>${esc(x.model?.lt_grade || "—")}</td>
        <td class="n" style="color:${scoreInk(x.model?.st_score)}">${num(x.model?.st_score, 0)}</td><td>${esc(x.model?.st_grade || "—")}</td>
        ${PK.map((k) => `<td class="n" style="color:${scoreInk(x.model?.pillars?.[k])}">${num(x.model?.pillars?.[k], 0)}</td>`).join("")}
        <td class="n">${isNum(x.forward_pe) ? x.forward_pe.toFixed(1) : "—"}</td><td class="n">${pct(x.mom_12_1, 0, true)}</td><td class="n">${big(x.market_cap, x.market)}</td>
        <td class="n" ${x.data_fails?.length ? `data-tip-text="${esc(ZH`缺少:` + x.data_fails.join("、"))}"` : ""}>${num(x.confidence, 0)}${x.data_fails?.length > 2 ? ' <span class="watch">!</span>' : ""}</td></tr>`).join("")}</table>
      <p class="note" style="margin-top:8px">${esc(j.disclaimer || "")}${j.no_env ? ZH` 另有 ${j.no_env} 檔是較舊的掃描結果或算不出分數,沒有排名;重新掃描後就會有。` : ""}</p>`;
    tbl.insertAdjacentHTML("beforeend", `<ul class="u-list scan-list">${rs.map((x) => UI.row({ title: x.name || x.symbol,
      sub: ZH`${x.symbol} · 第 ${x.rank ?? "—"} 名 · 長期 ${x.model?.lt_grade || "—"} ${num(x.model?.lt_score, 0)}`,
      data: { s: x.symbol } })).join("")}</ul>`);
    tbl.querySelectorAll(".scan-list [data-s]").forEach((b) => (b.onclick = () => { location.hash = `#/s/${encodeURIComponent(b.dataset.s)}/overview`; }));
    tbl.querySelectorAll("th").forEach((th) => (th.onclick = () => { if (sortKey === th.dataset.k) asc = !asc; else { sortKey = th.dataset.k; asc = th.dataset.k === "rank"; } draw(); }));
    tbl.querySelectorAll("tr[data-s]").forEach((tr) => (tr.onclick = () => { location.hash = `#/s/${encodeURIComponent(tr.dataset.s)}/overview`; }));
  };
  draw();
}
