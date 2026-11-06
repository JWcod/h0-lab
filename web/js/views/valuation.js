import { ZH } from "../i18n/index.js";
import { esc, num, pct, price, big, mult, isNum } from "../fmt.js";
import { chart, base, axis, C, PALETTE } from "../charts.js";

function heatCell(v, px) {
  if (!isNum(v)) return `<td class="n">—</td>`;
  const r = v / px - 1;
  const a = Math.min(0.55, Math.abs(r) * 0.9);
  const bg = r >= 0 ? `rgba(62,224,197,${a})` : `rgba(255,138,92,${a})`;
  return `<td class="n" style="background:${bg}">${price(v)}<div style="font-size:10px;color:var(--text)">${pct(r, 0, true)}</div></td>`;
}

export function render(el, R, ctx) {
  const v = R.valuation, q = R.quote, m = R.meta, d = v.dcf, mu = v.multiples || {};
  const px = q.price;
  el.innerHTML = ZH`
  <div class="grid g3">
    <div class="card span2"><h3>估值足球場圖 <span class="sub">粗條 = 各方法在預設假設下的區間,圓點 = 中心值;實線 = 現價</span></h3><div id="ff" class="chart mid"></div></div>
    <div class="card"><h3>估值計算機</h3>
      <div class="note" style="line-height:1.8">這裡提供 DCF、蒙地卡羅、反向 DCF、本益比河流、EPV、同業倍數等工具。每個工具的假設都標了預設值的來源,DCF 與本益比法可以在下方自己調整。
        H0 Lab 不把各方法綜合成單一公平價,也不提供買進或賣出價位;結果只是依假設推算的數字,不是預測,也不是建議。</div>
      <div class="kv" style="margin-top:10px">
        <span class="k">公司類型</span><span class="v">${esc(v.company_type_label || "—")}</span>
        <span class="k">現價</span><span class="v">${price(px)} ${esc(m.currency)}</span>
      </div>
      ${v.cyclical?.cyclical ? ZH`<div class="note watch" style="margin-top:10px">⚠ 景氣循環股:${esc(v.cyclical.reason || "")}。DCF 預設改用循環平均利潤率;本益比河流在循環高峰或谷底容易失真。</div>` : ""}
    </div>
  </div>
  <div class="card" style="margin-top:14px"><h3>各估值方法</h3><div class="tbl-wrap"><table>
    <tr><th>方法</th><th class="n">低</th><th class="n">中心值</th><th class="n">高</th><th class="n">相對現價</th></tr>
    ${(v.methods || []).map((x) => `<tr><td>${esc(x.label)}</td><td class="n">${price(x.low)}</td><td class="n"><b>${price(x.value)}</b></td><td class="n">${price(x.high)}</td>
      <td class="n ${x.upside > 0 ? "pos" : "neg"}">${pct(x.upside, 1, true)}</td></tr>`).join("")}
  </table></div><div class="note" style="margin-top:6px">每個方法各自用預設假設計算,彼此可能差很多;差異本身就是要研究的地方。</div></div>

  ${d && d.base ? dcfHTML(R, ctx) : ZH`<div class="card" style="margin-top:14px"><h3>DCF</h3><div class="note">${esc(d?.note || ZH`這家公司不適合用 DCF,或資料不足(例如金融股、自由現金流為負)。`)} 你可以改看上方其他估值方法。</div></div>`}

  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>本益比河流圖 ${ctx.hint("pe_band")} <span class="sub">${v.pe_band ? ZH`近 ${v.pe_band.years} 年本益比分位數 × 近四季 EPS` : ""}</span></h3>
      ${v.river ? `<div id="river" class="chart mid"></div>` : ZH`<div class="note">本益比歷史資料不足,畫不出河流圖</div>`}
      ${v.pe_band ? ZH`<div class="kv" style="margin-top:8px">
        <span class="k">目前本益比</span><span class="v">${num(v.pe_band.current_pe, 1)}x(高於過去 ${pct(v.pe_band.current_pct_rank, 0)} 的時間)</span>
        <span class="k">5 年分位 P10 / P25 / P50 / P75 / P90</span><span class="v">${["10", "25", "50", "75", "90"].map((k) => num(v.pe_band.percentiles[k], 1)).join(" / ")}</span>
        <span class="k">使用 EPS</span><span class="v">${num(v.pe_band.eps_used, 2)}(${esc(v.pe_band.eps_basis)})</span>
        <span class="k">5 年本益比中位數 × EPS(預設假設)</span><span class="v">${price(v.pe_band.value)}</span></div>` : ""}
    </div>
    <div class="card"><h3>其他估值觀點</h3>
      ${v.epv ? ZH`<div class="section-title" style="margin-top:0">EPV 零成長獲利價值 ${ctx.hint("epv")}</div><div class="kv">
        <span class="k">正規化 EBIT</span><span class="v">${big(v.epv.ebit_norm, m.market)}</span>
        <span class="k">EPV 每股</span><span class="v">${price(v.epv.value)}</span>
        <span class="k">現價隱含的「成長溢價」</span><span class="v">${pct(v.epv.growth_premium, 0, true)}</span></div>
        <div class="note" style="margin-top:6px">EPV 假設公司永遠不成長。現價比 EPV 高越多,代表你為未來成長付的錢越多。</div>` : ""}
      ${v.pb_roe ? ZH`<div class="section-title">P/B–ROE(金融股)</div><div class="kv">
        <span class="k">ROE(3 年平均)</span><span class="v">${pct(v.pb_roe.roe)}</span><span class="k">股權成本</span><span class="v">${pct(v.pb_roe.coe)}</span>
        <span class="k">合理 P/B</span><span class="v">${num(v.pb_roe.fair_pb, 2)}x</span><span class="k">每股淨值</span><span class="v">${price(v.pb_roe.bvps)}</span></div>` : ""}
      <div class="section-title">估值倍數</div><div class="kv">
        <span class="k">本益比(近四季)</span><span class="v">${mult(mu.pe_ttm)}</span><span class="k">預估本益比</span><span class="v">${mult(mu.forward_pe)}</span>
        <span class="k">PEG(用成長 ${pct(mu.peg_growth_used, 0)})</span><span class="v">${num(mu.peg, 2)}</span>
        <span class="k">EV/EBITDA</span><span class="v">${mult(mu.ev_ebitda)}</span><span class="k">EV/EBIT</span><span class="v">${mult(mu.ev_ebit)}</span>
        <span class="k">EV/營收</span><span class="v">${mult(mu.ev_sales, 2)}</span><span class="k">股價營收比</span><span class="v">${mult(mu.ps, 2)}</span>
        <span class="k">股價淨值比</span><span class="v">${mult(mu.pb, 2)}</span><span class="k">FCF 殖利率</span><span class="v">${pct(mu.fcf_yield, 2)}</span>
        <span class="k">盈餘殖利率</span><span class="v">${pct(mu.earnings_yield, 2)}</span><span class="k">企業價值 EV</span><span class="v">${big(mu.ev, m.market)}</span></div>
    </div>
  </div>

  ${v.pe_band && isNum(v.pe_band.eps_used) ? ZH`<div class="card" style="margin-top:14px"><h3>本益比計算機 <span class="sub">每股價值 = EPS × 本益比;兩個值都可以自己改</span>
    <span class="right"><button class="btn" id="peReset">回到預設假設</button></span></h3>
    <div class="grid g2"><div>
      <div style="margin-bottom:12px"><label class="big-label" for="peEps">EPS</label>
        <input class="u-input" id="peEps" type="number" step="any" value="${v.pe_band.eps_used}" style="width:100%">
        <div class="note" style="font-size:11px">預設值來源:${esc(v.pe_band.eps_basis || "")}</div></div>
      <div><div class="flex"><label class="big-label" style="margin:0" for="pePe">本益比</label><span class="sp"></span><b class="num" id="pePeV"></b></div>
        <input type="range" id="pePe" min="1" max="${Math.max(80, Math.ceil((v.pe_band.percentiles?.["50"] ?? 0) * 1.5))}" step="0.5" value="${v.pe_band.percentiles?.["50"] ?? v.pe_band.current_pe ?? 15}" style="width:100%">
        <div class="note" style="font-size:11px">預設值來源:這家公司近 ${v.pe_band.years} 年本益比的中位數(${num(v.pe_band.percentiles?.["50"], 1)}x)</div></div>
    </div><div><div class="big-label">你的假設下的每股價值</div><div class="flex"><span id="peVal" class="num" style="font-size:30px;font-weight:800"></span><span id="peUp" class="num" style="font-size:18px"></span></div>
      <div class="note" style="margin-top:6px">本益比法假設市場會用你填的倍數看這家公司的獲利;只是依假設推算,不是預測。</div></div></div></div>` : ""}

  <div class="card" style="margin-top:14px"><h3>同業比較 <span class="sub">${esc(R.extra?.peer_source || (m.market === "TW" ? ZH`同產業依規模挑選` : ""))}</span></h3>
    ${peersTable(R)}</div>
  ${m.market === "TW" && R.extra?.tw_industry?.table ? ZH`<div class="card" style="margin-top:14px"><h3>產業本益比分布 <span class="sub">${esc(R.extra.tw_industry.industry)} · ${R.extra.tw_industry.n} 檔(證交所/櫃買中心)</span></h3>
    <div class="grid g3"><div class="span2"><div id="indpe" class="chart short"></div></div><div class="kv">
      <span class="k">產業本益比中位數</span><span class="v">${num(R.extra.tw_industry.pe_median, 1)}x</span>
      <span class="k">本公司本益比</span><span class="v">${num(R.extra.tw_industry.self?.pe, 1)}x</span>
      <span class="k">產業淨值比中位數</span><span class="v">${num(R.extra.tw_industry.pb_median, 2)}x</span>
      <span class="k">本公司淨值比</span><span class="v">${num(R.extra.tw_industry.self?.pb, 2)}x</span>
      <span class="k">產業殖利率中位數</span><span class="v">${num(R.extra.tw_industry.dy_median, 2)}%</span>
      <span class="k">本公司殖利率</span><span class="v">${num(R.extra.tw_industry.self?.dy, 2)}%</span></div></div></div>` : ""}`;

  football(el.querySelector("#ff"), v, px);
  if (d && d.base) dcfCharts(el, R);
  if (v.river) riverChart(el.querySelector("#river"), v.river);
  peCalc(el, v.pe_band, px);
  if (m.market === "TW" && R.extra?.tw_industry?.table) industryHist(el.querySelector("#indpe"), R.extra.tw_industry);
}

function football(el, v, px) {
  const ms = (v.methods || []).filter((x) => isNum(x.value));
  if (!ms.length) return;
  const names = ms.map((x) => x.label.replace(/\s*[(\uFF08][^)\uFF09]*[)\uFF09]/g, ""));
  const lows = ms.map((x) => x.low);
  const highs = ms.map((x) => x.high);
  const vals = ms.map((x) => x.value);
  const all = [...lows, ...highs, px].filter(isNum);
  const xmin = Math.min(...all) * 0.9, xmax = Math.max(...all) * 1.05;
  chart(el, {
    ...base({ grid: { left: 150, right: 40, top: 34, bottom: 48 } }),
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: C.tip, borderColor: C.line, textStyle: { color: C.text },
      formatter: (p) => { const i = p[0].dataIndex; return ZH`${names[i]}<br>低 ${price(lows[i])} / 中 ${price(vals[i])} / 高 ${price(highs[i])}`; } },
    xAxis: { type: "value", min: Math.floor(xmin), max: Math.ceil(xmax), ...axis(), axisLabel: { showMinLabel: false, showMaxLabel: false, margin: 24 } },
    yAxis: { type: "category", data: names, inverse: true, ...axis({ axisLabel: { color: C.text2, fontSize: 12 } }) },
    series: [
      { type: "bar", stack: "r", data: lows, itemStyle: { color: "transparent" }, silent: true },
      { type: "bar", stack: "r", data: highs.map((h, i) => h - lows[i]), barWidth: 16,
        itemStyle: { color: "rgba(90,176,255,.55)", borderRadius: 4 } },
      { type: "scatter", data: vals, symbolSize: 12, itemStyle: { color: C.text, borderColor: C.accent2, borderWidth: 2 }, z: 5,
        markLine: { symbol: "none", label: { color: C.text, formatter: (p) => p.name + " " + price(p.value) },
          data: [{ name: ZH`現價`, xAxis: px, lineStyle: { color: C.text, width: 2 }, label: { position: "start", color: C.text } }] } },
    ],
  });
}

function dcfHTML(R, ctx) {
  const d = R.valuation.dcf, w = R.valuation.wacc, inp = d.inputs, m = R.meta, px = R.quote.price;
  const s = d.sensitivity;
  return ZH`
  <div class="grid g3" style="margin-top:14px">
    <div class="card"><h3>DCF 假設 ${ctx.hint("dcf")}</h3><div class="kv">
      <span class="k">近四季營收</span><span class="v">${big(inp.revenue_ttm, m.market)}</span>
      <span class="k">起始成長率</span><span class="v">${pct(inp.g_start)}</span>
      <span class="k" style="grid-column:span 2;font-size:11px">└ ${esc(inp.g_start_basis || "")}${isNum(inp.g_start_raw) && Math.abs(inp.g_start_raw - inp.g_start) > 1e-6 ? ZH`(原始 ${pct(inp.g_start_raw)},已限制在 −10%~40%)` : ""}</span>
      <span class="k">FCFF 利潤率</span><span class="v">${pct(inp.fcff_margin)}</span>
      <span class="k" style="grid-column:span 2;font-size:11px">└ ${esc(inp.margin_basis || "")}</span>
      <span class="k">預測期</span><span class="v">${inp.years} 年(成長線性收斂;參數檔預設)</span>
      <span class="k">永續成長率</span><span class="v">${pct(inp.g_terminal)}</span>
      <span class="k">淨負債(負 = 淨現金)</span><span class="v">${big(inp.net_debt, m.market)}</span>
      <span class="k">股數</span><span class="v">${big(inp.shares, "US")}</span>
      <span class="k">企業價值 EV</span><span class="v">${big(d.ev, m.market)}</span>
      <span class="k">終值占比</span><span class="v">${pct(d.tv_share)}</span>
      <span class="k"><b>DCF 每股價值</b></span><span class="v"><b>${price(d.base)}</b></span>
    </div></div>
    <div class="card"><h3>折現率 WACC ${ctx.hint("wacc")}</h3><div class="kv">
      <span class="k">利率基準</span><span class="v">${esc(w.rate_basis || "")}</span>
      <span class="k">無風險利率</span><span class="v">${pct(w.rf, 2)}${w.rate_basis === ZH`台幣利率` ? ZH`(參數)` : ZH`(美 10 年債)`}</span>
      <span class="k">Beta(調整後)</span><span class="v">${num(w.beta, 2)}</span>
      <span class="k">成熟市場 ERP</span><span class="v">${pct(w.erp, 2)}</span>
      ${w.crp ? ZH`<span class="k">台股額外風險溢酬</span><span class="v">${pct(w.crp, 2)}</span>` : ""}
      <span class="k">股權成本(下限 ${pct(w.coe_floor, 1)})</span><span class="v">${pct(w.cost_of_equity, 2)}</span>
      <span class="k">稅前債務成本</span><span class="v">${pct(w.cost_of_debt, 2)}</span>
      <span class="k">稅率</span><span class="v">${pct(w.tax, 1)}</span>
      <span class="k">股權權重</span><span class="v">${pct(w.weight_equity, 1)}</span>
      <span class="k"><b>WACC</b></span><span class="v"><b>${pct(w.wacc, 2)}</b>${Math.abs(w.wacc - w.wacc_raw) > 1e-6 ? ZH`(原始 ${pct(w.wacc_raw, 2)})` : ""}</span>
    </div>
      <div class="section-title">反向 DCF ${ctx.hint("reverse_dcf")}</div>
      <div class="note">以現價 ${price(px)} 反推,市場隱含的起始營收成長率約 <b class="num">${pct(d.implied_growth)}</b>(預設假設 ${pct(inp.g_start)})。</div>
    </div>
    <div class="card"><h3>蒙地卡羅分布 ${ctx.hint("monte_carlo")} <span class="sub">${d.mc ? d.mc.n + ZH` 次模擬` : ""}</span></h3>
      ${d.mc ? ZH`<div id="mc" class="chart short"></div><div class="kv">
        <span class="k">P10 / P50 / P90</span><span class="v">${price(d.mc.p10)} / ${price(d.mc.p50)} / ${price(d.mc.p90)}</span>
        <span class="k">現價低於模擬值的機率</span><span class="v" id="mcprob"></span></div>` : ZH`<div class="note">這次沒有模擬結果</div>`}
    </div>
  </div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>敏感度:WACC × 永續成長率 <span class="sub">每格 = 每股價值;顏色 = 相對現價</span></h3><div class="tbl-wrap"><table>
      <tr><th>WACC \\ g</th>${s.g_term.map((g) => `<th class="n">${pct(g, 1)}</th>`).join("")}</tr>
      ${s.grid.map((row, i) => `<tr><td class="lbl">${pct(s.wacc[i], 1)}</td>${row.map((c) => heatCell(c, px)).join("")}</tr>`).join("")}</table></div></div>
    <div class="card"><h3>敏感度:FCFF 利潤率 × 起始成長率</h3><div class="tbl-wrap"><table>
      <tr><th>利潤率 \\ 成長</th>${s.g0s.map((g) => `<th class="n">${pct(g, 0)}</th>`).join("")}</tr>
      ${s.grid_margin_growth.map((row, i) => `<tr><td class="lbl">${pct(s.margins[i], 0)}</td>${row.map((c) => heatCell(c, px)).join("")}</tr>`).join("")}</table></div></div>
  </div>
  <div class="card" style="margin-top:14px"><h3>DCF 計算機:自己調整假設 <span class="sub">拖動滑桿看你的假設下每股價值是多少;每個預設值都標了來源</span>
    <span class="right"><button class="btn" id="dcfReset">回到預設假設</button></span></h3>
    <div class="grid g2">
      <div id="sliders"></div>
      <div><div class="big-label">你的假設下的每股價值</div><div class="flex"><span id="myVal" class="num" style="font-size:30px;font-weight:800"></span><span id="myUp" class="num" style="font-size:18px"></span></div>
        <div class="note" id="myNote" style="margin-top:6px"></div>
        <div class="note" style="margin-top:10px">提示:先問自己「這家公司未來 10 年平均能成長多少?利潤率會比現在高還是低?」價值對哪個假設最敏感,那就是你該研究的重點。結果只是依假設推算,不是預測。</div></div>
    </div></div>
  <div class="card" style="margin-top:14px"><h3>10 年現金流預測</h3><div id="dcfpath" class="chart short"></div><div class="tbl-wrap"><table>
    <tr><th>年</th>${d.path.map((p) => `<th class="n">${p.year}</th>`).join("")}</tr>
    <tr><td class="lbl">成長率</td>${d.path.map((p) => `<td class="n">${pct(p.growth, 1)}</td>`).join("")}</tr>
    <tr><td class="lbl">營收</td>${d.path.map((p) => `<td class="n">${big(p.revenue, m.market, 1)}</td>`).join("")}</tr>
    <tr><td class="lbl">FCFF</td>${d.path.map((p) => `<td class="n">${big(p.fcff, m.market, 1)}</td>`).join("")}</tr>
    <tr><td class="lbl">折現值</td>${d.path.map((p) => `<td class="n">${big(p.pv, m.market, 1)}</td>`).join("")}</tr></table></div></div>`;
}

// 與後端 lab/valuation/dcf.py 的 dcf_value 同一公式
function dcfValue(rev0, g0, m, w, gt, years, netDebt, shares) {
  if (w - gt < 0.015 || shares <= 0) return null;
  let rev = rev0, pv = 0, fcff = 0;
  for (let i = 0; i < years; i++) {
    const g = years === 1 ? g0 : g0 + ((gt - g0) * i) / (years - 1);
    rev *= 1 + g; fcff = rev * m; pv += fcff / Math.pow(1 + w, i + 1);
  }
  const tv = (fcff * (1 + gt)) / (w - gt) / Math.pow(1 + w, years);
  return (pv + tv - netDebt) / shares;
}

function interactive(el, R) {
  const d = R.valuation.dcf, inp = d.inputs, px = R.quote.price;
  const defs = [["g_start", ZH`起始營收成長率`, -0.1, 0.6, 0.005, inp.g_start], ["fcff_margin", ZH`FCFF 利潤率`, -0.05, 0.6, 0.005, inp.fcff_margin],
    ["wacc", ZH`折現率 WACC`, 0.05, 0.15, 0.0025, inp.wacc], ["g_terminal", ZH`永續成長率`, 0.0, 0.04, 0.0025, inp.g_terminal]];
  const box = el.querySelector("#sliders");
  if (!box) return;
  const w = R.valuation.wacc || {};
  const src = {
    g_start: inp.g_start_basis || ZH`歷史營收成長與分析師預估`,
    fcff_margin: inp.margin_basis || ZH`歷史 FCFF 利潤率`,
    wacc: ZH`CAPM:無風險利率(${w.rate_basis || "—"})+ Beta × 股權風險溢酬,和債務成本依資本結構加權(見「折現率 WACC」卡)`,
    g_terminal: ZH`參數檔預設值(lab/model_params.json)`,
  };
  box.innerHTML = defs.map(([k, l, mn, mx, st, v]) => ZH`<div style="margin-bottom:12px"><div class="flex"><label class="big-label" style="margin:0" for="s-${k}">${l}</label><span class="sp"></span>
    <b class="num" id="v-${k}">${pct(v, 1)}</b><span class="note">(預設 ${pct(v, 1)})</span></div>
    <input type="range" id="s-${k}" min="${mn}" max="${mx}" step="any" value="${v}" aria-valuetext="${pct(v, 1)}" style="width:100%">
    <div class="note" style="font-size:11px">預設值來源:${esc(src[k] || "—")}</div></div>`).join("");
  const calc = () => {
    const val = Object.fromEntries(defs.map(([k]) => [k, parseFloat(el.querySelector("#s-" + k).value)]));
    defs.forEach(([k]) => {
      el.querySelector("#v-" + k).textContent = pct(val[k], 1);
      el.querySelector("#s-" + k).setAttribute("aria-valuetext", pct(val[k], 1));   // 螢幕閱讀器念百分比,不念小數
    });
    const v = dcfValue(inp.revenue_ttm, val.g_start, val.fcff_margin, val.wacc, val.g_terminal, inp.years, inp.net_debt, inp.shares);
    el.querySelector("#myVal").textContent = v ? price(v) : "—";
    const up = el.querySelector("#myUp");
    up.textContent = v ? pct(v / px - 1, 1, true) : "";
    up.className = "num " + (v && v > px ? "pos" : "neg");
    el.querySelector("#myNote").textContent = !v ? ZH`WACC 需要比永續成長率高至少 1.5%` : Math.abs(v / d.base - 1) < 0.005 ? ZH`和預設假設相同(預設假設的 DCF ${price(d.base)})` : ZH`預設假設的 DCF ${price(d.base)};你的假設下的價值比它${v > d.base ? ZH`更高` : ZH`更低`} ${pct(Math.abs(v / d.base - 1), 0)}。`;
  };
  defs.forEach(([k]) => (el.querySelector("#s-" + k).oninput = calc));
  el.querySelector("#dcfReset").onclick = () => { defs.forEach(([k, , , , , v]) => (el.querySelector("#s-" + k).value = v)); calc(); };
  calc();
}

function dcfCharts(el, R) {
  interactive(el, R);
  const d = R.valuation.dcf, px = R.quote.price;
  if (d.mc) {
    const h = d.mc.hist, mids = h.counts.map((_, i) => (h.edges[i] + h.edges[i + 1]) / 2);
    const total = h.counts.reduce((a, b) => a + b, 0);
    let above = 0;
    mids.forEach((x, i) => { if (x > px) above += h.counts[i]; });
    const pe = el.querySelector("#mcprob"); if (pe) pe.textContent = pct(above / total, 0);
    chart(el.querySelector("#mc"), {
      ...base({ grid: { left: 40, right: 16, top: 16, bottom: 30 }, tooltip: { trigger: "axis" } }),
      xAxis: { type: "category", data: mids.map((x) => price(x)), ...axis({ axisLabel: { color: C.label, fontSize: 10 } }) },
      yAxis: { type: "value", ...axis(), axisLabel: { show: false } },
      series: [{ type: "bar", data: h.counts.map((c, i) => ({ value: c, itemStyle: { color: mids[i] > px ? "rgba(62,224,197,.7)" : "rgba(255,138,92,.7)" } })), barCategoryGap: "8%" }],
    });
  }
  chart(el.querySelector("#dcfpath"), {
    ...base({ grid: { left: 70, right: 50, top: 30, bottom: 30 } }),
    xAxis: { type: "category", data: d.path.map((p) => "Y" + p.year), ...axis() },
    yAxis: [{ type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => big(x, R.meta.market, 0) } },
      { type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => (x * 100).toFixed(0) + "%" }, splitLine: { show: false } }],
    series: [{ name: "FCFF", type: "bar", data: d.path.map((p) => p.fcff), itemStyle: { color: "rgba(90,176,255,.6)" } },
      { name: ZH`折現值`, type: "bar", data: d.path.map((p) => p.pv), itemStyle: { color: "rgba(155,140,255,.75)" } },
      { name: ZH`成長率`, type: "line", yAxisIndex: 1, data: d.path.map((p) => p.growth), lineStyle: { color: C.watch } }],
  });
}

function peCalc(el, pb, px) {
  const eps = el.querySelector("#peEps"), pe = el.querySelector("#pePe");
  if (!eps || !pe) return;
  const calc = () => {
    const e = parseFloat(eps.value), m = parseFloat(pe.value);
    el.querySelector("#pePeV").textContent = isNum(m) ? m.toFixed(1) + "x" : "—";
    pe.setAttribute("aria-valuetext", isNum(m) ? m.toFixed(1) + "x" : "");
    const val = isNum(e) && isNum(m) ? e * m : null;
    el.querySelector("#peVal").textContent = isNum(val) ? price(val) : "—";
    const up = el.querySelector("#peUp");
    up.textContent = isNum(val) && px ? pct(val / px - 1, 1, true) : "";
    up.className = "num " + (isNum(val) && val > px ? "pos" : "neg");
  };
  eps.oninput = calc; pe.oninput = calc;
  el.querySelector("#peReset").onclick = () => { eps.value = pb.eps_used; pe.value = pb.percentiles?.["50"] ?? pb.current_pe ?? 15; calc(); };
  calc();
}

function riverChart(el, r) {
  const lv = ["10", "25", "50", "75", "90"];
  const cols = ["rgba(62,224,197,.25)", "rgba(125,211,252,.22)", "rgba(255,209,102,.20)", "rgba(255,169,77,.20)", "rgba(255,123,107,.20)"];
  const series = lv.map((k, i) => ({ name: `${r.pe_levels[k].toFixed(1)}x(P${k})`, type: "line", data: r["pe" + k], symbol: "none",
    lineStyle: { width: 1, color: cols[i].replace(/[\d.]+\)$/, "0.9)") } }));
  series.push({ name: ZH`股價`, type: "line", data: r.price, symbol: "none", lineStyle: { width: 2.2, color: C.text } });
  chart(el, { ...base({ legend: { textStyle: { color: C.text2, fontSize: 11 }, top: 0 }, grid: { left: 56, right: 20, top: 46, bottom: 30 } }),
    xAxis: { type: "category", data: r.dates, ...axis(), boundaryGap: false }, yAxis: { type: "value", scale: true, ...axis() }, series });
}

function peersTable(R) {
  const peers = R.valuation.peers || [];
  if (!peers.length) return ZH`<div class="note">找不到這家公司的同業資料</div>`;
  const m = R.meta, mu = R.valuation.multiples, p = R.fundamentals.profitability, g = R.fundamentals.growth;
  const self = { symbol: m.symbol, name: m.name + ZH`(本公司)`, market_cap: R.quote.market_cap, pe: mu.pe_ttm, forward_pe: mu.forward_pe,
    ev_ebitda: mu.ev_ebitda, ps: mu.ps, pb: mu.pb, gross_margin: p.gross_margin, operating_margin: p.operating_margin, roe: p.roe, revenue_growth: g.revenue_yoy, self: true };
  const rows = [self, ...peers];
  const med = (k) => { const xs = peers.map((x) => x[k]).filter(isNum).sort((a, b) => a - b); return xs.length ? xs[Math.floor(xs.length / 2)] : null; };
  const cols = [["market_cap", ZH`市值`, (x) => big(x, m.market)], ["pe", ZH`本益比`, (x) => mult(x)], ["forward_pe", ZH`預估本益比`, (x) => mult(x)],
    ["ev_ebitda", "EV/EBITDA", (x) => mult(x)], ["ps", "P/S", (x) => mult(x, 2)], ["pb", "P/B", (x) => mult(x, 2)],
    ["gross_margin", ZH`毛利率`, (x) => pct(x)], ["operating_margin", ZH`營益率`, (x) => pct(x)], ["roe", "ROE", (x) => pct(x)], ["revenue_growth", ZH`營收成長`, (x) => pct(x)]];
  return ZH`<div class="tbl-wrap"><table><tr><th>公司</th>${cols.map((c) => `<th class="n">${c[1]}</th>`).join("")}</tr>
    ${rows.map((r) => `<tr class="${r.self ? "hl" : ""}"><td>${esc(r.name)} <span class="num" style="color:var(--label)">${esc(r.symbol)}</span></td>${cols.map((c) => `<td class="n">${c[2](r[c[0]])}</td>`).join("")}</tr>`).join("")}
    <tr><td class="lbl">同業中位數</td>${cols.map((c) => `<td class="n">${c[0] === "market_cap" ? "" : c[2](med(c[0]))}</td>`).join("")}</tr></table></div>
    <div class="note" style="margin-top:6px">同業數值來自 Yahoo Finance,各公司口徑可能略有不同(例如近四季或年度)。</div>`;
}

function industryHist(el, ind) {
  const pes = ind.table.map((x) => x.pe).filter((x) => isNum(x) && x > 0 && x < 100);
  const bins = Array.from({ length: 20 }, (_, i) => i * 5);
  const counts = bins.map((b) => pes.filter((x) => x >= b && x < b + 5).length);
  const selfPe = ind.self?.pe;
  chart(el, {
    ...base({ grid: { left: 40, right: 16, top: 16, bottom: 30 } }),
    xAxis: { type: "category", data: bins.map((b) => `${b}-${b + 5}`), ...axis() }, yAxis: { type: "value", ...axis() },
    series: [{ type: "bar", data: counts.map((c, i) => ({ value: c, itemStyle: { color: isNum(selfPe) && selfPe >= bins[i] && selfPe < bins[i] + 5 ? C.accent2 : "rgba(90,176,255,.5)" } })) }],
  });
}
