// 模擬投資組合:主畫面只放各策略報酬 vs 大盤、持股數;規則、持股、交易明細收進細節面板;
// 原本的淨值圖與表格完整保留在下方「完整內容」。報酬只是虛擬帳戶的紀錄,不代表未來報酬(規則 8)。
import { ZH } from "../i18n/index.js";
import { esc, num, pct, price, big, isNum } from "../fmt.js";
import { chart, base, axis, C } from "../charts.js";
import * as UI from "../ui.js";

const KEYS = ["TW", "TW_ex", "TW_risk", "US", "US_ex", "US_risk"];
const mkName = (m) => (m === "TW" ? ZH`台股` : ZH`美股`);
const benchName = (m) => (m === "TW" ? ZH`加權指數` : "S&P 500");
const bName = (b) => b.bench_name || benchName(b.market);   // C 帳本用含息大盤(0050 / SPY)
const DISCLAIM = ZH`這裡只做研究與模擬,不接任何券商或交易所、不會下真單。模擬帳戶的報酬只是模型規則在虛擬帳戶的紀錄,不代表未來報酬,也不構成投資建議。`;

const SIDE_BUY = "\u8cb7\u9032";    // 帳本裡的原值(private/paper.json),只拿來判斷方向,不顯示

export async function render(el, state) {
  el.innerHTML = ZH`<div class="loading"><div class="spinner"></div>讀取模擬帳戶…</div>`;
  let j;
  try { j = await (await fetch("/api/paper")).json(); } catch (e) { el.innerHTML = ZH`<div class="err">讀取模擬投資組合失敗。請重新整理頁面再試一次;一直失敗的話,請看伺服器視窗的錯誤訊息。</div>`; return; }
  const books = KEYS.filter((k) => j.books?.[k]).map((k) => [k, j.books[k]]);
  const D = {
    rules: { title: ZH`策略規則`, desc: ZH`三個模擬策略並行,依模型內部等級在虛擬帳戶裡模擬交易。每次選股掃描(台股權值 40 / 美股大型 40)完成時自動更新。`,
      sections: [{ h: ZH`策略 A:精選`, items: (j.rules || []).map((r) => ({ t: r })) },
        { h: ZH`策略 B:排除法(並行觀察用,沒有證實有效)`, items: (j.rules_ex || []).map((r) => ({ t: r })) },
        { h: ZH`策略 C:風控版(A 的選股 + 投組風控,參考用,沒有經過回測驗證)`, items: (j.rules_risk || []).map((r) => ({ t: r })) }] },
    about: { title: ZH`怎麼看這頁`, sections: [{ p: [
      ZH`每個策略一張卡片:大數字是模擬帳戶從開始到現在的報酬率,下面是同期大盤報酬與目前持股數。點卡片看帳戶價值、現金比例、持股與最近交易。`,
      ZH`理由欄的等級是「模型內部等級」:美股在其他畫面一律不評等,這裡為了執行模擬規則才使用內部等級。模擬持股是規則的結果,不是對個股的建議。`,
      ZH`模擬報酬沒有經過統計檢驗,期間短、樣本少,不能用來判斷模型有沒有用;模型有沒有用請看「研究紀錄」頁。`,
      DISCLAIM] }] },
  };
  for (const [k, b] of books) D["bk-" + k] = bookDetail(b);

  const card = ([k, b]) => ZH`<button type="button" class="u-card u-kpi" data-d="bk-${esc(k)}">
      <span class="u-label">${esc(mkName(b.market))} · ${esc(b.strategy || k)}</span>
      <span class="u-verdict"><b class="u-num ${b.return > 0 ? "u-up" : b.return < 0 ? "u-down" : ""}">${esc(pct(b.return, 2, true))}</b>${UI.evidence("unval", ZH`模擬紀錄,沒有經過統計檢驗,不代表未來報酬`)}</span>
      <span class="u-sub">同期${esc(bName(b))}${b.bench_since ? ZH`(自 ${esc(b.bench_since)} 起)` : ""}${b.bench_gap ? ZH`(序列曾中斷)` : ""} ${esc(pct(b.bench_return, 2, true))} · 持股 ${b.positions.length} 檔 · 開始於 ${esc(b.start || "—")}</span></button>`;

  el.innerHTML = ZH`<div class="u-ov">
    <h1 class="u-largetitle">模擬投資組合</h1>
    <p class="u-sub" style="margin:-6px 0 14px">用虛擬資金照模型規則自動模擬交易,看規則實際跑起來的樣子;全部是模擬紀錄,不碰真錢、不下真單,不是對個股的建議。</p>
    ${books.length ? `<div class="u-grid">${books.map(card).join("")}</div>`
      : ZH`<div class="u-card"><div class="u-sub">還沒有模擬投資組合紀錄。到「選股掃描」跑一次掃描,完成後會自動建立並更新。</div></div>`}
    <h2 class="u-label u-list-h">細節</h2>
    <ul class="u-list">
      ${UI.row({ title: ZH`策略規則`, sub: ZH`精選、排除法、風控版三種規則`, data: { d: "rules" } })}
      ${UI.row({ title: ZH`怎麼看這頁`, sub: ZH`數字的意思與限制`, data: { d: "about" } })}
    </ul>
    ${books.length ? ZH`<h2 class="u-label u-list-h">完整內容(淨值圖、持股與交易表)</h2>
    <p class="u-sub u-hscroll-hint">表格可以左右滑動。</p>
    <div class="u-full">${books.map(([k, b]) => bookHTML(k, b)).join("")}</div>` : ""}
    <p class="u-disclaimer">${esc(DISCLAIM)}</p>
  </div>`;
  el.querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => { const x = D[b.dataset.d]; if (x) UI.openDetail(x); }));

  for (const [k, b] of books) {
    const elc = el.querySelector(`#eq-${k}`);
    if (!elc || !b.equity.length) continue;
    const b0 = b.equity.find((e) => isNum(e.bench))?.bench;
    chart(elc, {
      ...base({ grid: { left: 60, right: 20, top: 36, bottom: 30 } }),
      xAxis: { type: "category", data: b.equity.map((e) => e.date), ...axis(), boundaryGap: false },
      yAxis: { type: "value", scale: true, ...axis(), axisLabel: { color: C.label, formatter: (x) => (x * 100 - 100).toFixed(0) + "%" } },
      series: [{ name: ZH`模擬帳戶`, type: "line", data: b.equity.map((e) => e.value / b.initial), lineStyle: { color: C.accent, width: 2 }, symbolSize: 5 },
        { name: bName(b), type: "line", data: b.equity.map((e) => (b0 && e.bench ? e.bench / b0 : null)), lineStyle: { color: C.label, type: "dashed" }, symbolSize: 4 }],
    });
  }
}

// 細節面板:全部是純文字(openDetail 會跳脫)
function bookDetail(b) {
  const m = b.market;
  return {
    title: `${mkName(m)} · ${b.strategy || ""}`,
    desc: ZH`起始 ${big(b.initial, m)} ${b.currency} · 開始於 ${b.start || "—"}。模擬紀錄,不代表未來報酬。`,
    rows: [[ZH`帳戶價值`, `${big(b.value, m)} ${b.currency}`], [ZH`報酬率`, pct(b.return, 2, true)], [ZH`同期${bName(b)}${b.bench_since ? ZH`(自 ${b.bench_since} 起)` : ""}`, pct(b.bench_return, 2, true)],
      ...(b.bench_gap ? [[ZH`大盤序列中斷`, ZH`${b.bench_gap.from} ~ ${b.bench_gap.to} 這段沒有計入`]] : []),
      [ZH`現金比例`, isNum(b.value) && b.value ? pct(b.cash / b.value, 0) : "—"], [ZH`持股數`, ZH`${b.positions.length} 檔`], [ZH`淨值紀錄`, ZH`${b.equity.length} 筆(每次掃描一筆)`]],
    sections: [
      { h: ZH`模擬持股(${b.positions.length})`, p: b.positions.length ? [] : [ZH`目前沒有模擬持股。`],
        items: b.positions.map((p) => ({ t: `${p.name || p.symbol}(${p.symbol})`,
          d: ZH`損益 ${pct(p.pnl_pct, 1, true)} · ${num(p.shares, 0)} 股 · 成本 ${price(p.cost)} · 現價 ${price(p.last_price)} · 市值 ${big(p.value, m)} · 模擬買入 ${p.entry_date || "—"}` })),
        go: b.positions.slice(0, 6).map((p) => ({ label: p.name || p.symbol, hash: `#/s/${encodeURIComponent(p.symbol)}/overview` })) },
      ...(b.risk_note ? [{ h: ZH`最近一次再平衡(${b.risk_note.date})`,
        rows: [[ZH`估計投組年化波動`, pct(b.risk_note.est_vol, 1)], [ZH`總曝險(其餘是現金)`, pct(b.risk_note.gross, 0)], [ZH`波動目標縮放`, pct(b.risk_note.scale, 0)]],
        p: (b.risk_note.notes || []).length ? b.risk_note.notes : [ZH`沒有觸發任何上限。`] }] : []),
      { h: ZH`最近模擬交易(最多 20 筆)`, p: b.trades.length ? [ZH`理由裡的等級是模型內部等級。`] : [ZH`還沒有交易。`],
        items: b.trades.slice(0, 20).map((t) => ({ tag: t.side, t: `${t.date} · ${t.name || t.symbol}`,
          d: ZH`${num(t.shares, 0)} 股 @ ${price(t.price)}${isNum(t.pnl) ? ZH` · 損益 ${big(t.pnl, m)}` : ""} · 理由:${t.reason || "—"}` })) },
    ],
  };
}

// 原本的完整卡片(淨值圖 + 持股表 + 交易表),保留在「完整內容」
function bookHTML(key, b) {
  const ccy = b.currency, m = b.market;
  return ZH`<div class="card"><h3>${mkName(m)} · ${esc(b.strategy || "")} <span class="sub">起始 ${big(b.initial, m)} ${esc(ccy)} · 開始於 ${esc(b.start || "")}</span></h3>
    <div class="grid g4" style="margin-bottom:10px">
      <div class="stat"><div class="l">帳戶價值</div><div class="v">${big(b.value, m)}</div></div>
      <div class="stat"><div class="l">報酬率</div><div class="v ${b.return >= 0 ? "pos" : "neg"}">${pct(b.return, 2, true)}</div></div>
      <div class="stat"><div class="l">同期${esc(bName(b))}</div><div class="v">${pct(b.bench_return, 2, true)}</div></div>
      <div class="stat"><div class="l">現金比例</div><div class="v">${isNum(b.value) && b.value ? pct(b.cash / b.value, 0) : "—"}</div></div>
    </div>
    ${b.equity.length > 1 ? `<div id="eq-${esc(key)}" class="chart short"></div>` : ZH`<div class="note">還沒有足夠的淨值紀錄(每次掃描記一筆)。</div>`}
    <div class="grid g2" style="margin-top:10px">
      <div><div class="section-title" style="margin-top:0">持股(${b.positions.length})</div>${b.positions.length ? ZH`<div class="tbl-wrap"><table>
        <tr><th>公司</th><th class="n">股數</th><th class="n">成本</th><th class="n">現價</th><th class="n">損益</th><th class="n">市值</th><th>模擬買入日</th></tr>
        ${b.positions.map((p) => `<tr><td><a href="#/s/${encodeURIComponent(p.symbol)}/overview">${esc(p.name || p.symbol)}</a></td><td class="n">${num(p.shares, 0)}</td>
          <td class="n">${price(p.cost)}</td><td class="n">${price(p.last_price)}</td><td class="n ${p.pnl_pct >= 0 ? "up" : "down"}">${pct(p.pnl_pct, 1, true)}</td>
          <td class="n">${big(p.value, m)}</td><td>${esc(p.entry_date)}</td></tr>`).join("")}</table></div>` : ZH`<div class="note">目前沒有模擬持股</div>`}</div>
      <div><div class="section-title" style="margin-top:0">最近交易</div>${b.trades.length ? ZH`<div class="tbl-wrap"><table>
        <tr><th>日期</th><th>動作</th><th>公司</th><th class="n">股數</th><th class="n">價格</th><th class="n">損益</th><th>理由(模型內部等級)</th></tr>
        ${b.trades.slice(0, 20).map((t) => `<tr><td>${esc(t.date)}</td><td class="${t.side === SIDE_BUY ? "pos" : "neg"}">${t.side === SIDE_BUY ? ZH`模擬買入` : ZH`模擬賣出`}</td><td>${esc(t.name || t.symbol)}</td>
          <td class="n">${num(t.shares, 0)}</td><td class="n">${price(t.price)}</td><td class="n">${isNum(t.pnl) ? big(t.pnl, m) : ""}</td><td style="white-space:normal;font-size:12px;min-width:200px">${esc(t.reason)}</td></tr>`).join("")}</table></div>`
        : ZH`<div class="note">還沒有交易</div>`}</div>
    </div></div>`;
}
