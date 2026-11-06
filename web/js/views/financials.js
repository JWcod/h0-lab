import { ZH } from "../i18n/index.js";
import { esc, num, pct, big, isNum } from "../fmt.js";
import { chart, base, axis, C } from "../charts.js";

const LABEL = {
  revenue: ZH`營收`, gross_profit: ZH`毛利`, operating_expenses: ZH`營業費用`, operating_income: ZH`營業利益`, pretax_income: ZH`稅前淨利`, tax: ZH`所得稅`,
  net_income: ZH`淨利(歸屬母公司)`, eps_diluted: "EPS", ebitda: "EBITDA", rnd: ZH`研發費用`, sga: ZH`管銷費用`, da: ZH`折舊攤銷`,
  interest_expense: ZH`利息費用`, sbc: ZH`股票酬勞`,
  cfo: ZH`營業現金流`, capex: ZH`資本支出`, fcf: ZH`自由現金流`, dividends_paid: ZH`發放股利`, buybacks: ZH`買回庫藏股`,
  total_assets: ZH`總資產`, current_assets: ZH`流動資產`, cash: ZH`現金`, cash_sti: ZH`現金與短期投資`, receivables: ZH`應收帳款`, inventory: ZH`存貨`,
  ppe: ZH`不動產廠房設備`, goodwill_intangibles: ZH`商譽與無形資產`, total_liabilities: ZH`總負債`, current_liabilities: ZH`流動負債`,
  total_debt: ZH`有息負債`, payables: ZH`應付帳款`, equity: ZH`股東權益`, retained_earnings: ZH`保留盈餘`, shares_out: ZH`流通股數`,
};
const GROUPS = [
  [ZH`損益表`, ["revenue", "gross_profit", "operating_expenses", "rnd", "sga", "operating_income", "pretax_income", "tax", "net_income", "eps_diluted", "ebitda", "da", "interest_expense", "sbc"]],
  [ZH`資產負債表`, ["total_assets", "current_assets", "cash", "cash_sti", "receivables", "inventory", "ppe", "goodwill_intangibles", "total_liabilities", "current_liabilities", "total_debt", "payables", "equity", "retained_earnings", "shares_out"]],
  [ZH`現金流量表`, ["cfo", "capex", "fcf", "dividends_paid", "buybacks"]],
];

function statementTable(rows, market) {
  if (!rows || !rows.length) return ZH`<div class="note">這個期間沒有財報資料,可以切換其他期間看看</div>`;
  const cols = rows.slice().reverse();
  const ratios = [
    [ZH`毛利率`, (r) => (isNum(r.gross_profit) && r.revenue ? r.gross_profit / r.revenue : null)],
    [ZH`營業利益率`, (r) => (isNum(r.operating_income) && r.revenue ? r.operating_income / r.revenue : null)],
    [ZH`淨利率`, (r) => (isNum(r.net_income) && r.revenue ? r.net_income / r.revenue : null)],
    [ZH`FCF 利潤率`, (r) => (isNum(r.fcf) && r.revenue ? r.fcf / r.revenue : null)],
    [ZH`負債比`, (r) => (isNum(r.total_liabilities) && r.total_assets ? r.total_liabilities / r.total_assets : null)],
  ];
  let html = ZH`<div class="tbl-wrap"><table><tr><th>項目</th>${cols.map((c) => `<th class="n">${esc(c.period.slice(0, 7))}</th>`).join("")}</tr>`;
  for (const [g, keys] of GROUPS) {
    const ks = keys.filter((k) => cols.some((c) => isNum(c[k])));
    if (!ks.length) continue;
    html += `<tr><td colspan="${cols.length + 1}" style="color:var(--accent);font-weight:650;padding-top:12px">${g}</td></tr>`;
    for (const k of ks) {
      html += `<tr><td class="lbl">${LABEL[k] || k}</td>${cols.map((c) => `<td class="n">${k === "eps_diluted" ? num(c[k], 2) : k === "shares_out" ? big(c[k], "US") : big(c[k], market)}</td>`).join("")}</tr>`;
    }
  }
  html += ZH`<tr><td colspan="${cols.length + 1}" style="color:var(--accent);font-weight:650;padding-top:12px">比率</td></tr>`;
  for (const [n, f] of ratios) html += `<tr><td class="lbl">${n}</td>${cols.map((c) => `<td class="n">${pct(f(c))}</td>`).join("")}</tr>`;
  return html + `</table></div>`;
}

export function render(el, R, ctx) {
  const F = R.financials, m = R.meta, t = F.ttm || {}, mr = R.fundamentals.month_revenue;
  const long = F.long && F.long.length ? F.long : F.annual;
  el.innerHTML = ZH`
  <div class="card"><h3>近四季(TTM)摘要 <span class="sub">${esc(t.basis || "")} · 截至 ${esc(t.period_end || "")} · 單位 ${esc(F.currency || m.currency)}${F.fx_to_quote ? ZH`(已換算為報價幣別,匯率 ${num(F.fx_to_quote, 4)})` : ""}</span></h3>
    <div class="stats">${[[ZH`營收`, t.revenue], [ZH`毛利`, t.gross_profit], [ZH`營業利益`, t.operating_income], [ZH`淨利`, t.net_income], ["EBITDA", t.ebitda],
      [ZH`營業現金流`, t.cfo], [ZH`資本支出`, t.capex], [ZH`自由現金流`, t.fcf], [ZH`現金與短投`, t.cash_sti ?? t.cash], [ZH`有息負債`, t.total_debt], [ZH`股東權益`, t.equity], [ZH`總資產`, t.total_assets]]
      .map(([l, v]) => `<div class="stat"><div class="l">${l}</div><div class="v">${big(v, m.market)}</div></div>`).join("")}
      <div class="stat"><div class="l">EPS(近四季)</div><div class="v">${num(t.eps_diluted, 2)}</div></div></div></div>

  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>長期營收與獲利 <span class="sub">${esc(F.long && F.long.length ? F.long_source : ZH`Yahoo 年報`)}</span></h3><div id="longc" class="chart mid"></div></div>
    <div class="card"><h3>季度營收與利潤率 <span class="sub">${esc(F.quarterly_source)}</span></h3><div id="qc" class="chart mid"></div></div>
  </div>
  ${mr ? ZH`<div class="card" style="margin-top:14px"><h3>月營收(台股)${ctx.hint("month_revenue")} <span class="sub">最新 ${esc(mr.latest_month)} · ${esc(mr.release_note)}</span></h3>
    <div class="grid g4" style="margin-bottom:10px">
      <div class="stat"><div class="l">單月營收</div><div class="v">${big(mr.latest, "TW")}</div></div>
      <div class="stat"><div class="l">年增 / 月增</div><div class="v">${pct(mr.yoy, 1, true)} / ${pct(mr.mom, 1, true)}</div></div>
      <div class="stat"><div class="l">近 3 月 / 12 月累計年增</div><div class="v">${pct(mr.yoy_3m, 1, true)} / ${pct(mr.yoy_12m, 1, true)}</div></div>
      <div class="stat"><div class="l">創新高 / 同月新高 / 連續年增</div><div class="v">${mr.record_high ? ZH`是` : ZH`否`} / ${mr.record_same_month ? ZH`是` : ZH`否`} / ${mr.yoy_streak}</div></div>
    </div><div id="mrc" class="chart"></div></div>` : ""}
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>資產負債結構 <span class="sub">年度</span></h3><div id="bsc" class="chart mid"></div></div>
    <div class="card"><h3>現金流去向 <span class="sub">年度</span></h3><div id="cfc" class="chart mid"></div></div>
  </div>
  <div class="card" style="margin-top:14px"><h3>財務報表
    <span class="right"><span class="toggle" id="stTog"><button data-k="annual" class="on">年度(Yahoo)</button><button data-k="quarterly">季度</button><button data-k="long">長期年度</button></span></span></h3>
    <div id="stmt"></div></div>`;

  // 長期
  const L = long;
  chart(el.querySelector("#longc"), {
    ...base({ grid: { left: 70, right: 56, top: 40, bottom: 30 } }),
    xAxis: { type: "category", data: L.map((r) => r.period.slice(0, 4)), ...axis() },
    yAxis: [{ type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => big(x, m.market, 0) } },
      { type: "value", ...axis(), splitLine: { show: false }, axisLabel: { color: C.label, formatter: (x) => (x * 100).toFixed(0) + "%" } }],
    series: [
      { name: ZH`營收`, type: "bar", data: L.map((r) => r.revenue), itemStyle: { color: "rgba(90,176,255,.55)" } },
      { name: ZH`淨利`, type: "bar", data: L.map((r) => r.net_income), itemStyle: { color: "rgba(155,140,255,.8)" } },
      { name: ZH`自由現金流`, type: "bar", data: L.map((r) => r.fcf), itemStyle: { color: "rgba(62,224,197,.7)" } },
      { name: ZH`毛利率`, type: "line", yAxisIndex: 1, data: L.map((r) => (isNum(r.gross_profit) && r.revenue ? r.gross_profit / r.revenue : null)), lineStyle: { color: C.watch }, itemStyle: { color: C.watch } },
      { name: ZH`營益率`, type: "line", yAxisIndex: 1, data: L.map((r) => (isNum(r.operating_income) && r.revenue ? r.operating_income / r.revenue : null)), lineStyle: { color: "#f472b6" }, itemStyle: { color: "#f472b6" } },
    ],
  });
  const Q = F.quarterly;
  chart(el.querySelector("#qc"), {
    ...base({ grid: { left: 70, right: 56, top: 40, bottom: 30 } }),
    xAxis: { type: "category", data: Q.map((r) => r.period.slice(0, 7)), ...axis() },
    yAxis: [{ type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => big(x, m.market, 0) } },
      { type: "value", ...axis(), splitLine: { show: false }, axisLabel: { color: C.label, formatter: (x) => (x * 100).toFixed(0) + "%" } }],
    series: [
      { name: ZH`營收`, type: "bar", data: Q.map((r) => r.revenue), itemStyle: { color: "rgba(90,176,255,.55)" } },
      { name: ZH`淨利`, type: "bar", data: Q.map((r) => r.net_income), itemStyle: { color: "rgba(155,140,255,.8)" } },
      { name: ZH`毛利率`, type: "line", yAxisIndex: 1, data: Q.map((r) => (isNum(r.gross_profit) && r.revenue ? r.gross_profit / r.revenue : null)), lineStyle: { color: C.watch }, itemStyle: { color: C.watch } },
      { name: ZH`營益率`, type: "line", yAxisIndex: 1, data: Q.map((r) => (isNum(r.operating_income) && r.revenue ? r.operating_income / r.revenue : null)), lineStyle: { color: "#f472b6" }, itemStyle: { color: "#f472b6" } },
    ],
  });
  if (mr) {
    const h = mr.history;
    chart(el.querySelector("#mrc"), {
      ...base({ grid: { left: 70, right: 56, top: 36, bottom: 30 } }),
      xAxis: { type: "category", data: h.map((x) => x.month), ...axis() },
      yAxis: [{ type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => big(x, "TW", 0) } },
        { type: "value", ...axis(), splitLine: { show: false }, axisLabel: { color: C.label, formatter: (x) => (x * 100).toFixed(0) + "%" } }],
      series: [{ name: ZH`月營收`, type: "bar", data: h.map((x) => x.revenue), itemStyle: { color: "rgba(90,176,255,.6)" } },
        { name: ZH`年增率`, type: "line", yAxisIndex: 1, data: h.map((x) => x.yoy), lineStyle: { color: C.watch }, itemStyle: { color: C.watch }, symbolSize: 4 }],
    });
  }
  const A = F.annual;
  chart(el.querySelector("#bsc"), {
    ...base({ grid: { left: 70, right: 20, top: 40, bottom: 30 } }),
    xAxis: { type: "category", data: A.flatMap((r) => [r.period.slice(0, 4) + ZH` 資產`, r.period.slice(0, 4) + ZH` 負債+權益`]), ...axis({ axisLabel: { color: C.label, fontSize: 10, interval: 0, rotate: 20 } }) },
    yAxis: { type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => big(x, m.market, 0) } },
    series: (() => {
      const mk = (name, f, color, side) => ({ name, type: "bar", stack: "s", itemStyle: { color }, data: A.flatMap((r) => (side === 0 ? [f(r), null] : [null, f(r)])) });
      const other = (r) => (r.total_assets ?? 0) - (r.cash_sti ?? r.cash ?? 0) - (r.receivables ?? 0) - (r.inventory ?? 0) - (r.ppe ?? 0) - (r.goodwill_intangibles ?? 0);
      return [mk(ZH`現金`, (r) => r.cash_sti ?? r.cash, "#3ee0c5", 0), mk(ZH`應收`, (r) => r.receivables, "#7dd3fc", 0), mk(ZH`存貨`, (r) => r.inventory, "#ffd166", 0),
        mk(ZH`廠房設備`, (r) => r.ppe, "#5ab0ff", 0), mk(ZH`商譽無形`, (r) => r.goodwill_intangibles, "#f472b6", 0), mk(ZH`其他資產`, other, "#4b6aa8", 0),
        mk(ZH`有息負債`, (r) => r.total_debt, "#ff8a5c", 1), mk(ZH`其他負債`, (r) => (r.total_liabilities ?? 0) - (r.total_debt ?? 0), "#ffa94d", 1), mk(ZH`股東權益`, (r) => r.equity, "#9b8cff", 1)];
    })(),
  });
  chart(el.querySelector("#cfc"), {
    ...base({ grid: { left: 70, right: 20, top: 40, bottom: 30 } }),
    xAxis: { type: "category", data: A.map((r) => r.period.slice(0, 4)), ...axis() },
    yAxis: { type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => big(x, m.market, 0) } },
    series: [{ name: ZH`營業現金流`, type: "bar", data: A.map((r) => r.cfo), itemStyle: { color: "#5ab0ff" } },
      { name: ZH`資本支出`, type: "bar", data: A.map((r) => r.capex), itemStyle: { color: "#ff8a5c" } },
      { name: ZH`股利`, type: "bar", data: A.map((r) => r.dividends_paid), itemStyle: { color: "#ffd166" } },
      { name: ZH`庫藏股`, type: "bar", data: A.map((r) => r.buybacks), itemStyle: { color: "#f472b6" } },
      { name: ZH`自由現金流`, type: "line", data: A.map((r) => r.fcf), lineStyle: { color: "#3ee0c5", width: 2 }, itemStyle: { color: "#3ee0c5" } }],
  });
  const st = el.querySelector("#stmt");
  const show = (k) => { st.innerHTML = statementTable(k === "annual" ? F.annual : k === "quarterly" ? F.quarterly : F.long, m.market); };
  show("annual");
  el.querySelectorAll("#stTog button").forEach((b) => (b.onclick = () => {
    el.querySelectorAll("#stTog button").forEach((x) => x.classList.toggle("on", x === b)); show(b.dataset.k);
  }));
}
