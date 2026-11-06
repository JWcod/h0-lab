import { ZH, KEY, dyn } from "../i18n/index.js";
import { esc, num, pct, price, big, isNum } from "../fmt.js";
import { chart, base, axis, C, upDown } from "../charts.js";

export function render(el, R, ctx) {
  const t = R.technical;
  if (!t.available) { el.innerHTML = ZH`<div class="card note">股價資料不足,算不出技術指標。你可以稍後按「重新分析」再試一次。</div>`; return; }
  const lv = t.levels || {}, sigs = t.signals || [];
  // 英文模式下 API 已經把方向翻成英文,兩種都要認
  const dirCls = (x) => ([KEY`多`, dyn(KEY`多`)].includes(x) ? "pos" : [KEY`空`, dyn(KEY`空`)].includes(x) ? "neg" : "watch");
  el.innerHTML = ZH`
  <div class="card"><h3>K 線圖 <span class="sub">滑鼠滾輪縮放、拖曳平移</span>
    <span class="right flex"><span class="toggle" id="ovl"><button data-k="ma" class="on">均線</button><button data-k="bb">布林</button><button data-k="sr" class="on">支撐壓力</button></span>
    <span class="toggle" id="ind"><button data-k="rsi" class="on">RSI</button><button data-k="macd">MACD</button><button data-k="kd">KD</button></span></span></h3>
    <div id="kline" class="chart tall"></div></div>
  <div class="grid g3" style="margin-top:14px">
    <div class="card"><h3>技術訊號 <span class="sub">指標條件的統計描述,不是買賣建議</span></h3>${sigs.length ? `<div class="insights">${sigs.map((s) => `<div class="ins ${dirCls(s.direction)}"><div class="tone"></div><div><div class="t">${esc(dyn(s.name))} <span class="cat">${esc(dyn(s.direction))}</span></div><div class="d">${esc(s.detail)}</div></div></div>`).join("")}</div>` : ZH`<div class="note">目前沒有符合條件的技術訊號</div>`}
      <div class="section-title">趨勢狀態</div><div class="kv">
        <span class="k">狀態</span><span class="v">${esc(t.trend.label)}</span>
        ${["ma20", "ma50", "ma200"].map((k) => ZH`<span class="k">相對 ${k.toUpperCase()}</span><span class="v">${pct(t.price_vs_ma[k], 1, true)}</span>`).join("")}
        <span class="k">MA200 斜率(20 日)</span><span class="v">${pct(t.ma200_slope_20d, 2, true)}</span>
        ${t.ma_cross ? ZH`<span class="k">均線交叉</span><span class="v">${t.ma_cross.type === "golden" ? ZH`黃金交叉` : ZH`死亡交叉`} ${esc(t.ma_cross.date)}</span>` : ""}
      </div></div>
    <div class="card"><h3>指標數值</h3><div class="kv">
      <span class="k">RSI(14)${ctx.hint("rsi")}</span><span class="v">${num(t.rsi14, 1)}</span>
      <span class="k">MACD / 訊號 / 柱${ctx.hint("macd")}</span><span class="v">${num(t.macd.line, 2)} / ${num(t.macd.signal, 2)} / ${num(t.macd.hist, 2)}</span>
      <span class="k">K / D${ctx.hint("kd")}</span><span class="v">${num(t.kd.k, 1)} / ${num(t.kd.d, 1)}</span>
      <span class="k">布林 %B</span><span class="v">${num(t.bollinger.pct_b, 2)}</span>
      <span class="k">布林寬度</span><span class="v">${pct(t.bollinger.bandwidth)}${t.bollinger.squeeze ? ZH`(壓縮)` : ""}</span>
      <span class="k">ATR(14)${ctx.hint("atr")}</span><span class="v">${price(t.atr14)}(${pct(t.atr_pct, 2)})</span>
      <span class="k">52 週高 / 低</span><span class="v">${price(t.high_52w)} / ${price(t.low_52w)}</span>
      <span class="k">距 52 週高點${ctx.hint("pct_from_52w_high")}</span><span class="v">${pct(t.pct_from_52w_high)}</span>
      <span class="k">52 週區間位置</span><span class="v">${pct(t.pos_in_52w_range, 0)}</span>
      <span class="k">距歷史高點</span><span class="v">${pct(t.drawdown_from_ath)}</span>
      <span class="k">量比(5 日 / 20 日)</span><span class="v">${num(t.vol_ratio_5_20, 2)}</span>
      <span class="k">量比(20 日 / 60 日)</span><span class="v">${num(t.vol_ratio_20_60, 2)}</span>
      <span class="k">20 日均成交值</span><span class="v">${big(t.avg_turnover_20d, R.meta.market)}</span>
    </div></div>
    <div class="card"><h3>支撐與壓力 <span class="sub">近一年擺動高低點聚類;只是歷史價位的統計,不是交易價位</span></h3><div class="kv">
      ${(lv.resistance || []).slice().reverse().map((x) => ZH`<span class="k neg">壓力(觸碰 ${x.touches} 次)</span><span class="v neg">${price(x.price)} (${pct(x.price / t.price - 1, 1, true)})</span>`).join("")}
      <span class="k"><b>現價</b></span><span class="v"><b>${price(t.price)}</b></span>
      ${(lv.support || []).map((x) => ZH`<span class="k pos">支撐(觸碰 ${x.touches} 次)</span><span class="v pos">${price(x.price)} (${pct(x.price / t.price - 1, 1, true)})</span>`).join("")}
      ${!(lv.resistance || []).length && !(lv.support || []).length ? ZH`<span class="k">近一年沒有明顯的擺動高低點</span><span class="v">—</span>` : ""}
    </div></div>
  </div>
  <div class="grid g3" style="margin-top:14px">
    <div class="card"><h3>報酬率</h3><table><tr><th>期間</th><th class="n">本股</th><th class="n">相對大盤</th></tr>
      ${[["1w", ZH`1 週`], ["1m", ZH`1 個月`], ["3m", ZH`3 個月`], ["6m", ZH`6 個月`], ["ytd", ZH`今年以來`], ["1y", ZH`1 年`], ["3y", ZH`3 年`], ["5y", ZH`5 年`]].map(([k, n]) =>
        `<tr><td class="lbl">${n}</td><td class="n ${t.returns[k] > 0 ? "up" : "down"}">${pct(t.returns[k], 1, true)}</td><td class="n">${t.relative && k in t.relative ? pct(t.relative[k], 1, true) : ""}</td></tr>`).join("")}
      <tr><td class="lbl">12-1 月動能</td><td class="n">${pct(t.mom_12_1, 1, true)}</td><td></td></tr></table></div>
    <div class="card"><h3>風險</h3><div class="kv">
      <span class="k">年化波動 20 日 / 60 日 / 1 年</span><span class="v">${pct(t.vol_20d, 0)} / ${pct(t.vol_60d, 0)} / ${pct(t.vol_1y, 0)}</span>
      <span class="k">下檔波動(1 年)</span><span class="v">${pct(t.downside_vol_1y, 0)}</span>
      <span class="k">最大回撤 1 年 / 5 年${ctx.hint("max_dd")}</span><span class="v">${pct(t.max_dd_1y)} / ${pct(t.max_dd_5y)}</span>
      <span class="k">VaR 95%(1 日)${ctx.hint("var95")}</span><span class="v">${pct(t.var95_1d, 2)}</span>
      <span class="k">CVaR 95%(1 日)</span><span class="v">${pct(t.cvar95_1d, 2)}</span>
      <span class="k">Beta 原始 / 調整${ctx.hint("beta")}</span><span class="v">${num(t.beta_raw, 2)} / ${num(t.beta_adj, 2)}</span>
      <span class="k">與大盤相關係數</span><span class="v">${num(t.corr_bench, 2)}</span>
      <span class="k">偏態 / 峰態(1 年)</span><span class="v">${num(t.skew_1y, 2)} / ${num(t.kurt_1y, 2)}</span>
    </div></div>
    <div class="card"><h3>月份季節性 <span class="sub">過去 10 年各月平均報酬</span></h3><div id="seas" class="chart short"></div></div>
  </div>
  ${t.backtest && t.backtest.stats ? ZH`<div class="grid g3" style="margin-top:14px">
    <div class="card span2"><h3>簡單規則的歷史回測 <span class="sub">${esc(t.backtest.note)};來回成本 ${pct(t.backtest.cost_roundtrip, 2)}</span></h3><div id="bt" class="chart mid"></div></div>
    <div class="card"><h3>歷史回測統計</h3><div class="tbl-wrap"><table><tr><th>策略</th><th class="n">年化</th><th class="n">夏普</th><th class="n">最大回撤</th><th class="n">持有比例</th></tr>
      ${Object.entries(t.backtest.stats).map(([k, s]) => `<tr><td style="white-space:normal">${esc(dyn(k))}</td><td class="n">${pct(s.cagr, 1)}</td><td class="n">${num(s.sharpe, 2)}</td><td class="n">${pct(s.max_dd, 0)}</td><td class="n">${isNum(s.time_in_market) ? pct(s.time_in_market, 0) : "100%"}</td></tr>`).join("")}
    </table></div>
    <div class="section-title">歷史條件統計(之後 60 個交易日)</div>
    <table><tr><th>狀態</th><th class="n">平均報酬</th><th class="n">勝率</th><th class="n">天數</th></tr>
      ${Object.entries(t.backtest.conditional || {}).map(([k, s]) => `<tr><td>${esc(dyn(k))}</td><td class="n">${pct(s.avg_fwd60, 1, true)}</td><td class="n">${pct(s.win_rate, 0)}</td><td class="n">${s.n}</td></tr>`).join("")}</table>
    <div class="note" style="margin-top:8px">這是這檔股票自己的歷史,樣本有限,也是樣本內結果。只是統計描述,不是預測,也不是對這檔股票的建議。</div></div></div>` : ""}`;

  const opts = { ma: true, bb: false, sr: true, ind: "rsi" };
  const draw = () => drawK(el.querySelector("#kline"), t, opts);
  draw();
  el.querySelectorAll("#ovl button").forEach((b) => (b.onclick = () => { opts[b.dataset.k] = !opts[b.dataset.k]; b.classList.toggle("on", opts[b.dataset.k]); draw(); }));
  el.querySelectorAll("#ind button").forEach((b) => (b.onclick = () => { opts.ind = b.dataset.k; el.querySelectorAll("#ind button").forEach((x) => x.classList.toggle("on", x === b)); draw(); }));

  if (t.seasonality?.length) {
    chart(el.querySelector("#seas"), {
      ...base({ grid: { left: 44, right: 10, top: 16, bottom: 28 } }),
      tooltip: { trigger: "axis", formatter: (p) => { const s = t.seasonality[p[0].dataIndex]; return ZH`${s.month} 月:平均 ${pct(s.avg)},勝率 ${pct(s.win_rate, 0)}(${s.n} 年)`; } },
      xAxis: { type: "category", data: t.seasonality.map((s) => s.month + ZH`月`), ...axis() },
      yAxis: { type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => (x * 100).toFixed(0) + "%" } },
      series: [{ type: "bar", data: t.seasonality.map((s) => ({ value: s.avg, itemStyle: { color: s.avg > 0 ? "rgba(62,224,197,.75)" : "rgba(255,138,92,.75)" } })) }],
    });
  }
  if (t.backtest?.curves) {
    const cv = t.backtest.curves;
    const names = Object.keys(cv).filter((k) => k !== "date");
    chart(el.querySelector("#bt"), {
      ...base({ legend: { textStyle: { color: C.text2, fontSize: 11 }, top: 0 }, grid: { left: 50, right: 20, top: 40, bottom: 30 } }),
      xAxis: { type: "category", data: cv.date, ...axis(), boundaryGap: false },
      yAxis: { type: "log", ...axis(), axisLabel: { color: C.label, formatter: (x) => x.toFixed(1) + "x" } },
      series: names.map((n, i) => ({ name: dyn(n), type: "line", data: cv[n], symbol: "none", lineStyle: { width: n === KEY`持有不動` ? 2.4 : 1.5 } })),
    });
  }
}

function drawK(el, t, o) {
  const cd = t.chart;
  const { up, down } = upDown();
  const series = [
    { name: ZH`K 線`, type: "candlestick", data: cd.ohlc, xAxisIndex: 0, yAxisIndex: 0,
      itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down } },
  ];
  if (o.ma) {
    [["MA20", cd.ma20, "#ffd166"], ["MA60", cd.ma60, "#5ab0ff"], ["MA200", cd.ma200, "#9b8cff"], ["MA240", cd.ma240, "#f472b6"]]
      .forEach(([n, d, c]) => series.push({ name: n, type: "line", data: d, symbol: "none", lineStyle: { width: 1.1, color: c }, itemStyle: { color: c } }));
  }
  if (o.bb) {
    series.push({ name: ZH`布林上軌`, type: "line", data: cd.bb_up, symbol: "none", lineStyle: { width: 1, type: "dashed", color: "#7dd3fc" } });
    series.push({ name: ZH`布林下軌`, type: "line", data: cd.bb_dn, symbol: "none", lineStyle: { width: 1, type: "dashed", color: "#7dd3fc" } });
  }
  if (o.sr && t.levels) {
    series[0].markLine = { symbol: "none", silent: true, label: { color: C.text, fontSize: 10, position: "insideEndTop", formatter: (p) => p.name },
      data: [...(t.levels.support || []).map((x) => ({ name: ZH`支撐 ` + price(x.price), yAxis: x.price, lineStyle: { color: "#3ee0c5", type: "dotted" } })),
        ...(t.levels.resistance || []).map((x) => ({ name: ZH`壓力 ` + price(x.price), yAxis: x.price, lineStyle: { color: "#ff8a5c", type: "dotted" } }))] };
  }
  series.push({ name: ZH`成交量`, type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: cd.volume.map((v, i) => ({ value: v, itemStyle: { color: (cd.ohlc[i][1] >= cd.ohlc[i][0] ? up : down) + "99" } })) });
  if (o.ind === "rsi") {
    series.push({ name: "RSI", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: cd.rsi, symbol: "none", lineStyle: { color: "#ffd166", width: 1.2 },
      markLine: { symbol: "none", silent: true, label: { show: false }, data: [{ yAxis: 70, lineStyle: { color: "#ff8a5c", type: "dashed" } }, { yAxis: 30, lineStyle: { color: "#3ee0c5", type: "dashed" } }] } });
  } else if (o.ind === "macd") {
    series.push({ name: ZH`MACD 柱`, type: "bar", xAxisIndex: 2, yAxisIndex: 2, data: cd.macd_hist.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? up : down } })) });
    series.push({ name: "MACD", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: cd.macd, symbol: "none", lineStyle: { color: "#5ab0ff", width: 1.2 } });
    series.push({ name: ZH`訊號`, type: "line", xAxisIndex: 2, yAxisIndex: 2, data: cd.macd_signal, symbol: "none", lineStyle: { color: "#ffd166", width: 1.2 } });
  } else {
    series.push({ name: "K", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: cd.k, symbol: "none", lineStyle: { color: "#5ab0ff", width: 1.2 } });
    series.push({ name: "D", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: cd.d, symbol: "none", lineStyle: { color: "#ffd166", width: 1.2 },
      markLine: { symbol: "none", silent: true, label: { show: false }, data: [{ yAxis: 80, lineStyle: { color: "#ff8a5c", type: "dashed" } }, { yAxis: 20, lineStyle: { color: "#3ee0c5", type: "dashed" } }] } });
  }
  const n = cd.dates.length;
  chart(el, {
    ...base({ legend: { textStyle: { color: C.text2, fontSize: 11 }, top: 0, data: series.filter((s) => s.type === "line" && s.xAxisIndex !== 2).map((s) => s.name) } }),
    tooltip: { trigger: "axis", axisPointer: { type: "cross" }, backgroundColor: C.tip, borderColor: C.line, textStyle: { color: C.text, fontSize: 12 } },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [{ left: 60, right: 30, top: 34, height: "56%" }, { left: 60, right: 30, top: "66%", height: "10%" }, { left: 60, right: 30, top: "80%", height: "13%" }],
    xAxis: [0, 1, 2].map((i) => ({ type: "category", data: cd.dates, gridIndex: i, ...axis(), axisLabel: { show: i === 2, color: C.label }, boundaryGap: true })),
    yAxis: [{ scale: true, gridIndex: 0, ...axis() }, { scale: true, gridIndex: 1, ...axis(), axisLabel: { show: false }, splitLine: { show: false } },
      { scale: true, gridIndex: 2, ...axis(), splitNumber: 2 }],
    dataZoom: [{ type: "inside", xAxisIndex: [0, 1, 2], start: Math.max(0, 100 - (250 / n) * 100), end: 100 },
      { type: "slider", xAxisIndex: [0, 1, 2], bottom: 4, height: 14, textStyle: { color: C.label }, borderColor: C.line }],
    series,
  });
}
