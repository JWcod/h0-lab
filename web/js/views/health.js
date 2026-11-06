import { ZH, dyn } from "../i18n/index.js";
import { esc, num, pct, big, mult, isNum } from "../fmt.js";
import { chart, base, axis, C } from "../charts.js";

export function render(el, R, ctx) {
  const F = R.fundamentals, p = F.profitability, h = F.health, e = F.efficiency || {}, s = F.shareholder || {}, st = F.stability || {};
  const pf = F.piotroski, bm = F.beneish;
  const z = h.altman_z;
  const zone = !isNum(z) ? "—" : z > 2.99 ? ZH`<span class="pos">安全區</span>` : z >= 1.81 ? ZH`<span class="watch">灰色區</span>` : ZH`<span class="neg">危險區</span>`;
  el.innerHTML = ZH`
  <div class="grid g3">
    <div class="card"><h3>Piotroski F-score ${ctx.hint("piotroski")} <span class="right">${pf ? pf.period + ZH` 年度` : ""}</span></h3>
      ${pf ? `<div style="font-size:30px;font-weight:800" class="num">${pf.score} <span style="font-size:16px;color:var(--label)">/ 9</span></div>
      <table style="margin-top:8px">${pf.checks.map((c) => `<tr><td><span class="check ${c.pass ? "ok" : "no"}">${c.pass ? "✓" : "✗"}</span></td><td>${esc(c.name)}</td></tr>`).join("")}</table>` : ZH`<div class="note">需要至少 3 年年報才能計算</div>`}
    </div>
    <div class="card"><h3>Altman Z-score ${ctx.hint("altman_z")}</h3>
      <div style="font-size:30px;font-weight:800" class="num">${num(z, 2)} <span style="font-size:15px">${zone}</span></div>
      ${h.altman_parts ? `<table style="margin-top:8px">${Object.entries(h.altman_parts).map(([k, v]) => `<tr><td class="lbl">${esc(dyn(k))}</td><td class="n">${num(v, 3)}</td></tr>`).join("")}</table>` : ZH`<div class="note">金融股不適用這個指標</div>`}
      <div class="note" style="margin-top:8px">原始模型針對製造業,輕資產或科技公司參考即可。</div>
      <details class="note"><summary style="cursor:pointer">公式</summary>Z = 1.2·X1 + 1.4·X2 + 3.3·X3 + 0.6·X4 + 1.0·X5</details>
    </div>
    <div class="card"><h3>Beneish M-score ${ctx.hint("beneish")} <span class="right">${bm ? bm.period + ZH` 年度` : ""}</span></h3>
      ${bm ? `<div style="font-size:30px;font-weight:800" class="num">${num(bm.m, 2)} <span style="font-size:15px" class="${bm.flag ? "neg" : "pos"}">${bm.flag ? ZH`高於 −1.78:需留意` : ZH`低於 −1.78:正常`}</span></div>
      <table style="margin-top:8px">${Object.entries(bm.vars).map(([k, v]) => `<tr><td class="lbl">${esc(dyn(k))}</td><td class="n">${num(v, 3)}</td></tr>`).join("")}</table>
      ${bm.high_growth_caveat ? ZH`<div class="note watch" style="margin-top:6px">營收成長 > 30%(SGI 高),模型容易誤判,僅供參考。</div>` : ""}` : ZH`<div class="note">資料不足,算不出 M-score</div>`}
    </div>
  </div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>季度毛利率 / 營益率趨勢</h3><div id="gmq" class="chart"></div></div>
    <div class="card"><h3>年度利潤率與 ROE</h3><div id="roe" class="chart"></div></div>
  </div>
  <div class="grid g4" style="margin-top:14px">
    ${box(ZH`獲利能力`, [["ROIC", pct(p.roic), "roic"], ["ROE", pct(p.roe), "roe"], ["ROA", pct(p.roa), "roa"], [ZH`毛利/總資產`, pct(p.gp_assets), "gp_assets"],
      [ZH`毛利率`, pct(p.gross_margin), "gross_margin"], [ZH`營益率`, pct(p.operating_margin), "operating_margin"], [ZH`淨利率`, pct(p.net_margin), "net_margin"],
      [ZH`FCF 利潤率`, pct(p.fcf_margin), "fcf_margin"], [ZH`有效稅率`, pct(p.tax_rate), null]], ctx)}
    ${box(ZH`獲利品質與穩定`, [[ZH`現金流/淨利`, mult(p.cfo_to_ni, 2), "cfo_to_ni"], [ZH`應計比率`, pct(p.accruals), "accruals"],
      [ZH`毛利率 10 年平均`, pct(st.gm_mean_10y), null], [ZH`毛利率 10 年標準差`, pct(st.gm_std_10y), "gm_std_10y"], [ZH`近 10 年虧損年數`, isNum(st.loss_years_10y) ? st.loss_years_10y + " / " + st.years_covered : "—", null]], ctx)}
    ${box(ZH`償債與槓桿`, [[ZH`流動比率`, mult(h.current_ratio, 2), "current_ratio"], [ZH`速動比率`, mult(h.quick_ratio, 2), null], [ZH`負債/權益`, mult(h.debt_to_equity, 2), null],
      [ZH`負債/資產`, pct(h.liabilities_to_assets), null], [ZH`淨負債`, big(h.net_debt, R.meta.market), null], [ZH`淨負債/EBITDA`, mult(h.net_debt_ebitda, 2), "net_debt_ebitda"],
      [ZH`利息保障倍數`, isNum(h.interest_coverage) && h.interest_coverage > 900 ? ZH`無負債` : mult(h.interest_coverage, 1), "interest_coverage"], [ZH`淨現金/市值`, pct(h.net_cash_to_mcap), null]], ctx)}
    ${box(ZH`營運效率與股東回饋`, [[ZH`資產周轉率`, mult(e.asset_turnover, 2), null], [ZH`應收天數 DSO`, num(e.dso, 0) + ZH` 天`, "dso"], [ZH`存貨天數 DIO`, num(e.dio, 0) + ZH` 天`, "dio"],
      [ZH`應付天數 DPO`, num(e.dpo, 0) + ZH` 天`, null], [ZH`現金循環 CCC`, num(e.ccc, 0) + ZH` 天`, "ccc"], [ZH`資本支出/營收`, pct(e.capex_to_revenue), null], [ZH`研發/營收`, pct(e.rnd_to_revenue), null],
      [ZH`股利發放率`, pct(s.payout_ratio), null], [ZH`股利/FCF`, pct(s.fcf_payout), null], [ZH`股東殖利率(股利+庫藏股)`, pct(s.shareholder_yield, 2), null],
      [ZH`股數年變化`, pct(s.share_change_yoy, 2, true), null]], ctx)}
  </div>`;
  const gq = p.gm_quarterly || [], oq = p.om_quarterly || [];
  if (gq.length) {
    chart(el.querySelector("#gmq"), {
      ...base(), xAxis: { type: "category", data: gq.map((x) => x.period.slice(0, 7)), ...axis() },
      yAxis: { type: "value", scale: true, ...axis(), axisLabel: { color: C.label, formatter: (x) => (x * 100).toFixed(0) + "%" } },
      series: [{ name: ZH`毛利率`, type: "line", data: gq.map((x) => x.value), lineStyle: { color: C.watch }, itemStyle: { color: C.watch }, symbolSize: 4 },
        { name: ZH`營益率`, type: "line", data: oq.map((x) => x.value), lineStyle: { color: "#f472b6" }, itemStyle: { color: "#f472b6" }, symbolSize: 4 }],
    });
  }
  const hs = p.history || {};
  const per = (hs.gross_margin || []).map((x) => x.period.slice(0, 4));
  chart(el.querySelector("#roe"), {
    ...base(), xAxis: { type: "category", data: per, ...axis() },
    yAxis: { type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => (x * 100).toFixed(0) + "%" } },
    series: [["gross_margin", ZH`毛利率`, C.watch], ["operating_margin", ZH`營益率`, "#f472b6"], ["net_margin", ZH`淨利率`, C.accent], ["fcf_margin", ZH`FCF 利潤率`, C.pos], ["roe", "ROE", C.accent2]]
      .map(([k, n, c]) => ({ name: n, type: "line", data: (hs[k] || []).map((x) => x.value), lineStyle: { color: c }, itemStyle: { color: c } })),
  });
}

function box(title, rows, ctx) {
  return `<div class="card"><h3>${esc(title)}</h3><div class="kv">${rows.map(([l, v, k]) => `<span class="k">${esc(l)}${k ? ctx.hint(k) : ""}</span><span class="v">${v}</span>`).join("")}</div></div>`;
}
