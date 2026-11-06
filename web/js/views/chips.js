import { ZH, KEY, dyn } from "../i18n/index.js";
import { esc, num, pct, price, big, lots, isNum } from "../fmt.js";
import { chart, base, axis, C, upDown } from "../charts.js";

export function render(el, R, ctx) {
  const ch = R.chips;
  if (ch.market === "TW") return tw(el, R, ctx);
  return us(el, R, ctx);
}

function tw(el, R, ctx) {
  const ch = R.chips, inst = ch.institutional || {}, mg = ch.margin || {}, fh = ch.foreign_holding || {}, td = ch.tdcc, sbl = ch.sbl;
  el.innerHTML = ZH`
  <div class="card"><h3>三大法人買賣超 ${ctx.hint("inst_flow")} <span class="sub">單位:張(1 張 = 1,000 股)</span></h3>
    <div class="tbl-wrap"><table><tr><th>法人</th><th class="n">今日</th><th class="n">5 日</th><th class="n">20 日</th><th class="n">60 日</th><th class="n">20 日占股本</th><th class="n">連續天數</th></tr>
    ${[KEY`外資`, KEY`投信`, KEY`自營商`, KEY`合計`].map((g) => { const r = inst[g] || {}; return `<tr><td>${dyn(g)}</td>${["d1", "d5", "d20", "d60"].map((k) => `<td class="n ${r[k] > 0 ? "up" : r[k] < 0 ? "down" : ""}">${lots(r[k])}</td>`).join("")}
      <td class="n">${pct(r.d20_pct, 3, true)}</td><td class="n ${r.streak > 0 ? "up" : r.streak < 0 ? "down" : ""}">${isNum(r.streak) ? (r.streak > 0 ? ZH`連買 ` + r.streak : r.streak < 0 ? ZH`連賣 ` + -r.streak : "—") : "—"}</td></tr>`; }).join("")}
    </table></div><div id="instc" class="chart mid" style="margin-top:10px"></div></div>
  <div class="grid g3" style="margin-top:14px">
    <div class="card"><h3>融資融券 ${ctx.hint("margin")}</h3><div class="kv">
      <span class="k">融資餘額</span><span class="v">${num(mg.margin_balance, 0)} 張</span>
      <span class="k">融資 5 日 / 20 日變化</span><span class="v">${pct(mg.margin_chg_5d, 1, true)} / ${pct(mg.margin_chg_20d, 1, true)}</span>
      <span class="k">融資使用率</span><span class="v">${pct(mg.margin_usage, 2)}</span>
      <span class="k">融資占股本</span><span class="v">${pct(mg.margin_to_shares, 3)}</span>
      <span class="k">融券餘額</span><span class="v">${num(mg.short_balance, 0)} 張</span>
      <span class="k">券資比</span><span class="v">${pct(mg.short_margin_ratio, 2)}</span>
      ${sbl ? ZH`<span class="k">借券賣出餘額</span><span class="v">${num(sbl.balance / 1000, 0)} 張(${pct(sbl.to_shares, 3)} 股本)</span><span class="k">借券 20 日變化</span><span class="v">${pct(sbl.chg_20d, 1, true)}</span>` : ""}
    </div><div id="mgc" class="chart short" style="margin-top:8px"></div></div>
    <div class="card"><h3>外資持股比例</h3><div class="kv">
      <span class="k">目前</span><span class="v">${num(fh.ratio, 2)}%</span>
      <span class="k">20 / 60 / 250 日變化</span><span class="v">${["chg_20d", "chg_60d", "chg_250d"].map((k) => (isNum(fh[k]) ? (fh[k] > 0 ? "+" : "") + fh[k].toFixed(2) : "—")).join(" / ")} pp</span>
    </div><div id="fhc" class="chart short" style="margin-top:8px"></div></div>
    <div class="card"><h3>集保股權分散 ${ctx.hint("tdcc")} <span class="sub">${td ? ZH`最新 ${esc(td.history[td.history.length - 1].date)} · 已累積 ${td.weeks} 週` : ""}</span></h3>
      ${td ? ZH`<div class="kv">
        <span class="k">千張大戶持股比</span><span class="v">${num(td.big1000_pct, 2)}%${isNum(td.big1000_chg) ? ZH`(週 ${td.big1000_chg > 0 ? "+" : ""}${td.big1000_chg.toFixed(2)})` : ""}</span>
        <span class="k">400 張以上持股比</span><span class="v">${num(td.big400_pct, 2)}%</span>
        <span class="k">50 張以下散戶持股比</span><span class="v">${num(td.small50_pct, 2)}%</span>
        <span class="k">股東總人數</span><span class="v">${num(td.holders, 0)}${isNum(td.holders_chg) ? ZH`(週 ${pct(td.holders_chg, 2, true)})` : ""}</span></div>
        <div id="tdc" class="chart short" style="margin-top:8px"></div>
        ${td.weeks < 2 ? ZH`<div class="note">集保開放資料只提供最新一週。你每週打開一次,H0 Lab 會自動累積歷史,之後就能看趨勢。</div>` : ""}` : ZH`<div class="note">目前拿不到集保資料,稍後按「重新分析」再試</div>`}
    </div>
  </div>`;
  const s = ch.institutional_series;
  if (s) {
    const { up, down } = upDown();
    chart(el.querySelector("#instc"), {
      ...base({ grid: { left: 70, right: 60, top: 36, bottom: 30 } }),
      xAxis: { type: "category", data: s.dates, ...axis() },
      yAxis: [{ type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => (x / 1000).toFixed(0) + "k" } }, { type: "value", scale: true, ...axis(), splitLine: { show: false } }],
      series: [{ name: ZH`外資`, type: "bar", stack: "i", data: s[KEY`外資`], itemStyle: { color: "#5ab0ff" } }, { name: ZH`投信`, type: "bar", stack: "i", data: s[KEY`投信`], itemStyle: { color: "#ffd166" } },
        { name: ZH`自營商`, type: "bar", stack: "i", data: s[KEY`自營商`], itemStyle: { color: "#9b8cff" } },
        { name: ZH`收盤價`, type: "line", yAxisIndex: 1, data: s.close, symbol: "none", lineStyle: { color: C.text, width: 1.6 } }],
    });
  }
  if (ch.margin_series) {
    const m = ch.margin_series;
    chart(el.querySelector("#mgc"), {
      ...base({ grid: { left: 56, right: 50, top: 30, bottom: 26 } }),
      xAxis: { type: "category", data: m.dates, ...axis() },
      yAxis: [{ type: "value", scale: true, ...axis() }, { type: "value", scale: true, ...axis(), splitLine: { show: false } }],
      series: [{ name: ZH`融資`, type: "line", data: m.margin, symbol: "none", lineStyle: { color: "#ff8a5c" } }, { name: ZH`融券`, type: "line", yAxisIndex: 1, data: m.short, symbol: "none", lineStyle: { color: "#3ee0c5" } }],
    });
  }
  if (fh.series) {
    chart(el.querySelector("#fhc"), { ...base({ grid: { left: 50, right: 16, top: 16, bottom: 26 } }), xAxis: { type: "category", data: fh.series.dates, ...axis() },
      yAxis: { type: "value", scale: true, ...axis(), axisLabel: { color: C.label, formatter: (x) => x + "%" } },
      series: [{ type: "line", data: fh.series.ratio, symbol: "none", lineStyle: { color: "#5ab0ff", width: 2 }, areaStyle: { color: "rgba(90,176,255,.12)" } }] });
  }
  if (td) {
    const L = td.latest.filter((x) => x.level >= 1 && x.level <= 15);
    chart(el.querySelector("#tdc"), { ...base({ grid: { left: 70, right: 16, top: 10, bottom: 26 }, tooltip: { trigger: "axis" } }),
      xAxis: { type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => x + "%" } }, yAxis: { type: "category", data: L.map((x) => x.label), ...axis({ axisLabel: { color: C.label, fontSize: 10 } }) },
      series: [{ type: "bar", data: L.map((x) => ({ value: x.pct, itemStyle: { color: x.level >= 12 ? "#9b8cff" : x.level <= 8 ? "#ffd166" : "#5ab0ff" } })) }] });
  }
}

function us(el, R, ctx) {
  const ch = R.chips, sh = ch.short || {}, own = ch.ownership || {}, ins = ch.insiders;
  el.innerHTML = ZH`
  <div class="grid g3">
    <div class="card"><h3>放空 ${ctx.hint("short_interest")}</h3><div class="kv">
      <span class="k">放空占流通股</span><span class="v">${pct(sh.pct_float, 2)}</span>
      <span class="k">回補天數</span><span class="v">${num(sh.days_to_cover, 1)} 天</span>
      <span class="k">放空股數</span><span class="v">${big(sh.shares_short, "US")}</span>
      <span class="k">較上月變化</span><span class="v">${pct(sh.chg_vs_prior, 1, true)}</span></div></div>
    <div class="card"><h3>持股結構</h3><div class="kv">
      <span class="k">機構持股</span><span class="v">${pct(own.institutions, 1)}</span>
      <span class="k">內部人持股</span><span class="v">${pct(own.insiders, 2)}</span>
      <span class="k">前十大機構中增持比例</span><span class="v">${pct(ch.inst_net_adding, 0)}</span></div>
      <div id="own" class="chart mini"></div></div>
    <div class="card"><h3>內部人(近 6 個月)${ctx.hint("insider")}</h3>${ins ? ZH`<div class="kv">
      <span class="k">買入筆數 / 金額</span><span class="v pos">${ins.buy_count_6m} / ${big(ins.buy_value_6m, "US")}</span>
      <span class="k">賣出筆數 / 金額</span><span class="v neg">${ins.sell_count_6m} / ${big(ins.sell_value_6m, "US")}</span>
      <span class="k">買入者</span><span class="v" style="white-space:normal">${esc((ins.buyers || []).join(", ") || "—")}</span></div>
      <div class="note" style="margin-top:8px">內部人賣出多半與稅務或分散有關;這裡只是揭露資料的整理。</div>` : ZH`<div class="note">沒有內部人交易資料</div>`}</div>
  </div>
  ${ins && ins.recent?.length ? ZH`<div class="card" style="margin-top:14px"><h3>最近內部人交易</h3><div class="tbl-wrap"><table>
    <tr><th>日期</th><th>人員</th><th>職位</th><th>交易</th><th class="n">股數</th><th class="n">金額</th></tr>
    ${ins.recent.map((r) => `<tr><td>${esc(r.date)}</td><td>${esc(r.insider)}</td><td>${esc(r.position)}</td><td style="white-space:normal">${esc(r.text)}</td><td class="n">${num(r.shares, 0)}</td><td class="n">${big(r.value, "US")}</td></tr>`).join("")}
  </table></div></div>` : ""}
  ${ch.top_institutions?.length ? ZH`<div class="card" style="margin-top:14px"><h3>前十大機構股東</h3><div class="tbl-wrap"><table>
    <tr><th>機構</th><th class="n">持股比例</th><th class="n">股數</th><th class="n">市值</th><th class="n">季變化</th></tr>
    ${ch.top_institutions.map((r) => `<tr><td>${esc(r.holder)}</td><td class="n">${pct(r.pct, 2)}</td><td class="n">${big(r.shares, "US")}</td><td class="n">${big(r.value, "US")}</td><td class="n ${r.chg > 0 ? "pos" : r.chg < 0 ? "neg" : ""}">${pct(r.chg, 1, true)}</td></tr>`).join("")}
  </table></div></div>` : ""}`;
  if (isNum(own.institutions)) {
    const ins_ = own.insiders || 0, inst = own.institutions;
    chart(el.querySelector("#own"), { ...base({ tooltip: { trigger: "item" }, legend: { show: false } }),
      series: [{ type: "pie", radius: ["45%", "75%"], label: { color: C.text2, fontSize: 11 },
        data: [{ name: ZH`機構`, value: inst, itemStyle: { color: "#5ab0ff" } }, { name: ZH`內部人`, value: ins_, itemStyle: { color: "#ffd166" } }, { name: ZH`其他(散戶等)`, value: Math.max(0, 1 - inst - ins_), itemStyle: { color: "#9b8cff" } }] }] });
  }
}
