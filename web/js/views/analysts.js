import { ZH } from "../i18n/index.js";
import { esc, num, pct, price, big, isNum } from "../fmt.js";
import { chart, base, axis, C } from "../charts.js";

const P = { "0q": ZH`本季`, "+1q": ZH`下季`, "0y": ZH`今年度`, "+1y": ZH`明年度` };

export function render(el, R, ctx) {
  const x = R.expectations, tg = x.targets || {}, q = R.quote, m = R.meta;
  el.innerHTML = ZH`
  <div class="grid g3">
    <div class="card"><h3>分析師目標價 ${ctx.hint("analyst_adj")} <span class="sub">${isNum(tg.n) ? tg.n + ZH` 位分析師` : ""}</span></h3>
      <div id="tgt" class="chart short"></div>
      <div class="kv"><span class="k">中位數 / 平均</span><span class="v">${price(tg.median)} / ${price(tg.mean)}</span>
        <span class="k">最高 / 最低</span><span class="v">${price(tg.high)} / ${price(tg.low)}</span>
        <span class="k">隱含空間(中位數)</span><span class="v ${tg.implied_upside > 0 ? "pos" : "neg"}">${pct(tg.implied_upside, 1, true)}</span></div>
      <div class="note" style="margin-top:8px">研究顯示分析師目標價平均偏樂觀約 15%,看隱含空間時要把這點算進去。</div></div>
    <div class="card"><h3>分析師評等分布 <span class="sub">第三方資料;5 = 最正面,1 = 最負面</span></h3><div id="rec" class="chart short"></div>
      <div class="kv"><span class="k">目前平均評等</span><span class="v">${num(x.rec_score, 2)}(${esc(x.rec_key || "")})</span>
        <span class="k">3 個月變化</span><span class="v">${isNum(x.rec_score_chg_3m) ? (x.rec_score_chg_3m > 0 ? "+" : "") + x.rec_score_chg_3m.toFixed(2) : "—"}</span></div></div>
    <div class="card"><h3>財報驚喜 ${ctx.hint("surprise")}</h3><div id="sur" class="chart short"></div>
      <div class="kv"><span class="k">近 4 季平均驚喜</span><span class="v">${pct(x.surprise_avg_4q, 1, true)}</span>
        <span class="k">近 4 季打敗預期</span><span class="v">${isNum(x.beats_4q) ? x.beats_4q + " / 4" : "—"}</span>
        <span class="k">下次財報</span><span class="v">${esc(x.next_earnings || "—")}${isNum(x.days_to_earnings) ? ZH`(${x.days_to_earnings} 天後)` : ""}</span></div></div>
  </div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>EPS 與營收預估</h3><div class="tbl-wrap"><table>
      <tr><th>期間</th><th class="n">EPS 預估</th><th class="n">低 – 高</th><th class="n">去年同期</th><th class="n">成長</th><th class="n">分析師數</th></tr>
      ${Object.entries(x.eps_estimates || {}).map(([k, r]) => `<tr><td>${P[k]}</td><td class="n">${num(r.avg, 2)}</td><td class="n">${num(r.low, 2)} – ${num(r.high, 2)}</td><td class="n">${num(r.yearAgoEps, 2)}</td><td class="n">${pct(r.growth, 1, true)}</td><td class="n">${num(r.numberOfAnalysts, 0)}</td></tr>`).join("")}
      <tr><th>期間</th><th class="n">營收預估</th><th class="n">低 – 高</th><th class="n">去年同期</th><th class="n">成長</th><th class="n">分析師數</th></tr>
      ${Object.entries(x.rev_estimates || {}).map(([k, r]) => `<tr><td>${P[k]}</td><td class="n">${big(r.avg, m.market)}</td><td class="n">${big(r.low, m.market)} – ${big(r.high, m.market)}</td><td class="n">${big(r.yearAgoRevenue, m.market)}</td><td class="n">${pct(r.growth, 1, true)}</td><td class="n">${num(r.numberOfAnalysts, 0)}</td></tr>`).join("")}
    </table></div><div class="note" style="margin-top:6px">未來 12 個月 EPS:${num(x.ntm_eps, 2)}(${esc(x.ntm_basis || "")})</div></div>
    <div class="card"><h3>預估修正 ${ctx.hint("eps_revision")} <span class="sub">共識 EPS 的變化</span></h3><div class="tbl-wrap"><table>
      <tr><th>期間</th><th class="n">目前共識</th><th class="n">7 日</th><th class="n">30 日</th><th class="n">90 日</th><th class="n">30 日上修 / 下修</th></tr>
      ${Object.entries(x.eps_trend || {}).map(([k, r]) => { const rv = (x.eps_revisions || {})[k] || {}; return `<tr><td>${P[k]}</td><td class="n">${num(r.current, 2)}</td>
        ${["chg_7d", "chg_30d", "chg_90d"].map((c) => `<td class="n ${r[c] > 0 ? "pos" : r[c] < 0 ? "neg" : ""}">${pct(r[c], 1, true)}</td>`).join("")}
        <td class="n">${num(rv.up30, 0)} / ${num(rv.down30, 0)}</td></tr>`; }).join("")}
    </table></div>
    ${x.rating_changes_90d ? ZH`<div class="section-title">近 90 天評等動作</div><div class="kv">
      <span class="k">升評 / 降評 / 新覆蓋</span><span class="v">${x.rating_changes_90d.up} / ${x.rating_changes_90d.down} / ${x.rating_changes_90d.init}</span>
      <span class="k">目標價上調 / 下調</span><span class="v">${x.rating_changes_90d.pt_raises} / ${x.rating_changes_90d.pt_lowers}</span></div>` : ""}</div>
  </div>
  ${x.earnings_moves ? ZH`<div class="card" style="margin-top:14px"><h3>過去財報公布前後的股價反應 <span class="sub">前一日收盤 → 隔一日收盤</span></h3>
    <div class="flex" style="margin-bottom:8px"><span class="chip">平均波動 ±${pct(x.earnings_moves.avg_abs, 1)}</span><span class="chip">最大 ${pct(x.earnings_moves.max_abs, 1)}</span>
      <span class="chip">上漲 ${x.earnings_moves.ups} / ${x.earnings_moves.n} 次</span></div>
    <div class="flex">${x.earnings_moves.moves.map((mv) => `<span class="chip">${esc(mv.date)} <b class="${mv.move > 0 ? "up" : "down"}">${pct(mv.move, 1, true)}</b></span>`).join("")}</div>
    <div class="note" style="margin-top:6px">用途:了解財報前後的歷史波動。若平均波動 ±8%,代表歷史上財報前後的價格變動約這個幅度。</div></div>` : ""}
  ${x.rating_changes_list?.length ? ZH`<div class="card" style="margin-top:14px"><h3>最近評等與目標價變動</h3><div class="tbl-wrap"><table>
    <tr><th>日期</th><th>券商</th><th>評等</th><th>動作</th><th class="n">目標價</th><th class="n">原目標價</th></tr>
    ${x.rating_changes_list.map((r) => `<tr><td>${esc(r.date)}</td><td>${esc(r.firm)}</td><td>${esc(r.from && r.from !== r.to ? r.from + " → " : "")}${esc(r.to)}</td>
      <td>${esc({ up: ZH`升評`, down: ZH`降評`, main: ZH`維持`, init: ZH`新覆蓋`, reit: ZH`重申` }[r.action] || r.action || "")} ${esc({ Raises: ZH`· 調高目標`, Lowers: ZH`· 調降目標`, Maintains: "", Announces: ZH`· 公布目標` }[r.pt_action] || "")}</td>
      <td class="n">${r.pt ? price(r.pt) : "—"}</td><td class="n">${r.pt_prev ? price(r.pt_prev) : "—"}</td></tr>`).join("")}
  </table></div></div>` : ""}`;

  if (isNum(tg.low) && isNum(tg.high)) {
    chart(el.querySelector("#tgt"), {
      ...base({ grid: { left: 20, right: 20, top: 30, bottom: 30 }, tooltip: { show: false } }),
      xAxis: { type: "value", min: Math.min(tg.low, q.price) * 0.92, max: Math.max(tg.high, q.price) * 1.05, ...axis() },
      yAxis: { type: "category", data: [""], show: false },
      series: [{ type: "bar", stack: "a", data: [tg.low], itemStyle: { color: "transparent" }, silent: true },
        { type: "bar", stack: "a", data: [tg.high - tg.low], barWidth: 22, itemStyle: { color: "rgba(90,176,255,.35)", borderRadius: 6 } },
        { type: "scatter", data: [[tg.median, ""]], symbolSize: 14, itemStyle: { color: C.accent2 },
          markLine: { symbol: "none", label: { color: C.text, formatter: (p) => p.name + " " + price(p.value) },
            data: [{ name: ZH`現價`, xAxis: q.price, lineStyle: { color: C.text, width: 2 } }, { name: ZH`中位數`, xAxis: tg.median, lineStyle: { color: C.accent2, type: "dashed" } }] } }],
    });
  }
  const rec = (x.recommendations || []).slice().reverse();
  if (rec.length) {
    const cols = [["strongBuy", ZH`最正面`, "#3ee0c5"], ["buy", ZH`正面`, "#7dd3fc"], ["hold", ZH`中立`, "#ffd166"], ["sell", ZH`負面`, "#ffa94d"], ["strongSell", ZH`最負面`, "#ff7b6b"]];
    chart(el.querySelector("#rec"), {
      ...base({ grid: { left: 36, right: 10, top: 34, bottom: 26 } }),
      xAxis: { type: "category", data: rec.map((r) => ({ "0m": ZH`本月`, "-1m": ZH`1 月前`, "-2m": ZH`2 月前`, "-3m": ZH`3 月前` }[r.period] || r.period)), ...axis() },
      yAxis: { type: "value", ...axis() },
      series: cols.map(([k, n, c]) => ({ name: n, type: "bar", stack: "r", data: rec.map((r) => r[k]), itemStyle: { color: c } })),
    });
  }
  const su = (x.surprises || []).slice().reverse();
  if (su.length) {
    chart(el.querySelector("#sur"), {
      ...base({ grid: { left: 44, right: 10, top: 30, bottom: 26 } }),
      xAxis: { type: "category", data: su.map((s) => s.date.slice(0, 7)), ...axis() },
      yAxis: { type: "value", scale: true, ...axis() },
      series: [{ name: ZH`預估`, type: "scatter", data: su.map((s) => s.estimate), symbolSize: 9, itemStyle: { color: "transparent", borderColor: C.label, borderWidth: 2 } },
        { name: ZH`實際`, type: "scatter", data: su.map((s) => s.actual), symbolSize: 10, itemStyle: { color: (p) => (su[p.dataIndex].surprise >= 0 ? C.pos : C.neg) } }],
    });
  }
}
