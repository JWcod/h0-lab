// 因子研究:主畫面只放「只用開發期」的說明與因子數、每個因子一列;
// 統計數字、對模型的意涵、限制收進細節面板;原本的表格與兩張圖完整保留在下方「完整內容」。
// 用 PIT 價格庫;7 個因子一起做 Holm 校正(p 值用 Newey–West fixed-b)。
// 校正後顯著也只是開發期上的統計關係,沒有經過正式檢定與樣本外檢驗,一律不算證實,不用打勾或「通過」字樣(規則 8)。
import { ZH } from "../i18n/index.js";
import { esc, num, pct, isNum } from "../fmt.js";
import { chart, base, axis, C, PALETTE } from "../charts.js";
import * as UI from "../ui.js";
import * as Setup from "../setup.js";

const UNIS = [["tw_large40", ZH`台股權值 40`], ["us_large40", ZH`美股大型 40`], ["tw_pit50", ZH`台股前 50(含之後下市)`]];
let renderSeq = 0;   // 切股票池很快時,只有最後一次的回應可以畫,不然 A 的資料會顯示在 B 的按鈕下
const READ_NOTE = ZH`判讀:7 個因子一起做 Holm 多重檢定校正(p 值用 Newey–West,考慮自相關)。校正後 p < 0.05 才算「顯著」,但這只是開發期上的統計關係,沒有經過正式檢定與樣本外檢驗,仍不算證實有效。台股前 50 是每個時點當時的名單,含之後下市的公司;權值 40 與美股 40 是今天的名單,有存活者偏誤。`;

export async function render(el, state) {
  const seq = ++renderSeq;
  const key = UNIS.some(([k]) => k === state.labKey) ? state.labKey : "tw_large40";
  el.innerHTML = ZH`<div class="u-ov">
    <h1 class="u-largetitle">因子研究</h1>
    <p class="u-sub" style="margin:-6px 0 14px">檢驗常見的量化因子(動能、低波動、反轉等)和下個月報酬有沒有關係,幫你判斷哪些訊號值得放進模型。只用開發期(2024-09 以前)的資料。</p>
    <div class="u-seg u-seg-wrap" id="uni" role="group" aria-label="股票池">${UNIS.map(([k, n]) => `<button type="button" data-k="${esc(k)}" aria-pressed="${key === k}">${esc(n)}</button>`).join("")}</div>
    <div id="labBody"><div class="loading"><div class="spinner"></div>計算中(讀取歷史價格,第一次約 10–30 秒)…</div></div>
    <p class="u-disclaimer">因子實驗是歷史資料的統計檢定,不代表未來報酬,也不構成投資建議。</p>
  </div>`;
  el.querySelectorAll("#uni button").forEach((b) => (b.onclick = () => { state.labKey = b.dataset.k; render(el, state); }));
  const showBody = (html) => {   // 已切到別的股票池或別頁就不畫
    const box = seq === renderSeq ? el.querySelector("#labBody") : null;
    if (box) { box.innerHTML = html; Setup.bindCopy(box); }
  };
  const market = key.startsWith("us_") ? "US" : "TW";
  if (key === "tw_pit50") {   // 快速資料算這個池會是不完整的名單,卻標成含下市股,所以先擋
    const st = await Setup.refreshStatus();
    const info = st?.research?.[market];
    if (info && !(info.universes || []).includes(key)) { showBody(Setup.researchMissingHTML(st, market, "", false, key)); return; }
  }
  let j;
  try {
    const r = await fetch(`/api/research/factors?universe=${encodeURIComponent(key)}`);
    j = await r.json();
    if (!r.ok) throw new Error(j.detail || ZH`計算失敗`);
  } catch (e) {
    if (!Setup.isResearchMissing(e.message)) { showBody(`<div class="err">${esc(e.message)}</div>`); return; }
    showBody(Setup.researchMissingHTML(await Setup.refreshStatus(), market, "", false, key));
    return;
  }
  if (seq !== renderSeq) return;
  const F = j.factors || [];
  const nSig = F.filter((f) => f.holm_sig).length;
  const uniName = (UNIS.find(([k]) => k === key) || [key, key])[1];

  const D = {
    dev: { title: ZH`只用開發期`, desc: ZH`結果月份截到 2024-09;2024-10 以後是樣本外測試期,這頁不顯示。`,
      rows: [[ZH`股票池`, uniName], [ZH`期間`, j.period || "—"], [ZH`月數`, ZH`${j.n_months ?? "—"} 個月`], [ZH`股票數`, ZH`${j.n_stocks ?? "—"} 檔`]],
      sections: [{ h: ZH`怎麼算`, p: [ZH`每月底依因子排序,看下個月的報酬。IC = 因子排名與下月報酬排名的相關係數(Spearman)。`,
        ZH`前 1/3 − 後 1/3 = 每月做多前 1/3、放空後 1/3 的平均報酬(未計交易成本)。`, READ_NOTE] }] },
    mt: { title: ZH`多重檢定校正`, desc: ZH`同一份資料一次檢定很多因子,總會有幾個看起來顯著;Holm 校正把這個運氣成分扣掉。`,
      rows: [[ZH`方法`, "Holm–Bonferroni"], [ZH`一起校正的因子數`, ZH`${j.multiple_testing?.m ?? "—"} 個`], [ZH`顯著水準`, "0.05"],
        [ZH`p 值`, ZH`Newey–West fixed-b 雙尾`], [ZH`校正後顯著`, ZH`${nSig} 個`]],
      sections: [{ p: [ZH`校正後顯著 ≠ 證實有效:這些因子是長年公開、之前也看過同一份資料的價格因子(資料窺探),而且沒有經過正式檢定與樣本外檢驗。`] }] },
    read: { title: ZH`怎麼讀這頁`, sections: [{ p: [READ_NOTE] }] },
    impl: { title: ZH`對目前模型的意涵`, desc: ZH`系統只提出建議,不會自動改權重;任何權重改動都要先事先登記研究假設、通過正式檢定才會採用。`,
      sections: [{ items: MAP.map(([k, item, w]) => { const f = F.find((x) => x.key === k); if (!f) return null;
        return { tag: ZH`權重 ${w}`, t: `${f.label} → ${item}`, d: ZH`這次證據:${verdictTxt(f)}。建議:${suggestTxt(f, w)}。` }; }).filter(Boolean) }] },
    caveats: { title: ZH`這個實驗的限制`, sections: [{ items: (j.caveats || []).map((c) => ({ t: c })) }] },
  };
  for (const f of F) {
    D["f-" + f.key] = { title: f.label, desc: f.formula || "",
      rows: [[ZH`平均 IC`, num(f.ic_mean, 3)], [ZH`IC t 值`, num(f.ic_t, 2)], [ZH`IC > 0 比例`, pct(f.ic_hit, 0)], [ZH`ICIR(年化)`, num(f.icir, 2)],
        ["Newey–West t", num(f.nw_t, 2)], [ZH`p 值(未校正)`, num(f.nw_p, 3)], [ZH`Holm 校正後 p`, num(f.holm_p, 3)],
        [ZH`前 1/3 − 後 1/3(月)`, pct(f.ls_mean, 2, true)], [ZH`年化多空`, pct(f.ls_ann, 1, true)], [ZH`開發期最後 36 月 IC`, num(f.recent_ic, 3)], [ZH`開發期最後 36 月 t`, num(f.recent_t, 2)]],
      sections: [{ h: ZH`證據`, p: [`${verdictTxt(f)}。`, ZH`校正後顯著也只是開發期上的統計關係,不算證實有效。`] }] };
  }

  const body = el.querySelector("#labBody");
  if (!body) return;   // 資料回來之前使用者已經切到別頁
  body.innerHTML = ZH`
    <div class="u-grid">
      <button type="button" class="u-card u-kpi" data-d="dev"><span class="u-label">只用開發期</span><span class="u-verdict"><b class="u-num">${esc(j.n_months ?? "—")} 個月</b></span>
        <span class="u-sub">${esc(j.period || "")} · ${esc(j.n_stocks ?? "—")} 檔 · 截到 2024-09</span></button>
      <button type="button" class="u-card u-kpi" data-d="read"><span class="u-label">檢驗的因子</span>
        <span class="u-verdict"><b class="u-num">${F.length} 個</b>${UI.evidence("none", ZH`Holm 校正後顯著也只是開發期上的統計關係,不算證實有效`)}</span>
        <span class="u-sub">Holm 校正後顯著 ${nSig} 個,仍不算證實</span></button>
    </div>
    <h2 class="u-label u-list-h">各因子(依 Newey–West t 值排序)</h2>
    <ul class="u-list">${F.map((f) => UI.row({ title: f.label, sub: ZH`平均 IC ${num(f.ic_mean, 3)} · t ${num(f.nw_t, 2)} · 校正後 p ${num(f.holm_p, 2)} · ${verdictTxt(f)}`, data: { d: "f-" + f.key } })).join("")}</ul>
    <h2 class="u-label u-list-h">細節</h2>
    <ul class="u-list">
      ${UI.row({ title: ZH`對目前模型的意涵`, sub: ZH`各因子和模型裡對應指標的建議`, data: { d: "impl" } })}
      ${UI.row({ title: ZH`這個實驗的限制`, sub: ZH`${(j.caveats || []).length} 項`, data: { d: "caveats" } })}
      ${UI.row({ title: ZH`多重檢定校正`, sub: ZH`Holm,${j.multiple_testing?.m ?? "—"} 個因子一起校正`, data: { d: "mt" } })}
      ${UI.row({ title: ZH`怎麼讀這頁`, sub: ZH`IC、t 值、多重檢定`, data: { d: "read" } })}
    </ul>
    <h2 class="u-label u-list-h">完整內容(表格與圖)</h2>
    <p class="u-sub u-hscroll-hint">表格可以左右滑動。</p>
    <div class="u-full">
    <div class="card"><h3>因子檢定結果 <span class="sub">${esc(j.period)} · ${esc(j.n_months)} 個月 · ${esc(j.n_stocks)} 檔 · 依 Newey–West t 值排序 · 只用開發期(結果月份截到 2024-09)</span></h3>
      <div class="tbl-wrap"><table>
        <tr><th>因子</th><th>公式</th><th class="n">平均 IC</th><th class="n">IC t 值</th><th class="n">NW t</th><th class="n">Holm 校正後 p</th><th class="n">IC>0 比例</th><th class="n">ICIR(年化)</th>
          <th class="n">前 1/3 − 後 1/3(月)</th><th class="n">年化多空</th><th class="n">開發期最後 36 月 IC</th><th class="n">開發期最後 36 月 t</th><th>證據</th></tr>
        ${F.map((f) => `<tr><td><b>${esc(f.label)}</b></td><td style="white-space:normal;font-size:12px;min-width:200px">${esc(f.formula)}</td>
          <td class="n ${f.ic_mean > 0 ? "pos" : "neg"}">${num(f.ic_mean, 3)}</td><td class="n">${num(f.ic_t, 2)}</td><td class="n">${num(f.nw_t, 2)}</td><td class="n">${num(f.holm_p, 3)}</td><td class="n">${pct(f.ic_hit, 0)}</td><td class="n">${num(f.icir, 2)}</td>
          <td class="n ${f.ls_mean > 0 ? "pos" : "neg"}">${pct(f.ls_mean, 2, true)}</td><td class="n">${pct(f.ls_ann, 1, true)}</td>
          <td class="n">${num(f.recent_ic, 3)}</td><td class="n">${num(f.recent_t, 2)}</td><td>${verdict(f)}</td></tr>`).join("")}
      </table></div>
      <div class="note" style="margin-top:8px">${esc(READ_NOTE)}</div></div>
    <div class="card"><h3>對目前模型的意涵 <span class="sub">系統只提出建議,不會自動改權重;改動要先事先登記研究假設、通過正式檢定</span></h3>
      <div class="tbl-wrap"><table><tr><th>因子</th><th>模型裡對應的指標</th><th class="n">目前權重(技術動能面向內)</th><th>這次證據</th><th>建議</th></tr>
      ${MAP.map(([k, item, w]) => { const f = F.find((x) => x.key === k); if (!f) return ""; const s = suggest(f, w);
        return `<tr><td>${esc(f.label)}</td><td>${esc(item)}</td><td class="n">${w}</td><td>${verdict(f)}</td><td style="white-space:normal;min-width:220px">${s}</td></tr>`; }).join("")}
      </table></div></div>
    <div class="grid g2">
      <div class="card"><h3>多空累積報酬 <span class="sub">每月做多前 1/3、放空後 1/3(未計交易成本)</span></h3><div id="cum" class="chart mid"></div></div>
      <div class="card"><h3>滾動 12 個月平均 IC <span class="sub">看因子效果是否隨時間衰退</span></h3><div id="ric" class="chart mid"></div></div>
    </div>
    <div class="card" style="margin-top:14px"><h3>這個實驗的限制</h3><ul class="note" style="line-height:1.9">
      ${(j.caveats || []).map((c) => `<li>${esc(c)}</li>`).join("")}</ul></div>
    </div>`;
  body.querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => { const x = D[b.dataset.d]; if (x) UI.openDetail(x); }));
  const dates = j.dates;
  chart(body.querySelector("#cum"), {
    ...base({ legend: { textStyle: { color: C.text2, fontSize: 11 }, top: 0 }, grid: { left: 50, right: 20, top: 46, bottom: 30 } }),
    xAxis: { type: "category", data: dates, ...axis(), boundaryGap: false },
    yAxis: { type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => x.toFixed(1) + "x" } },
    series: F.map((f, i) => ({ name: f.label, type: "line", data: f.cum_ls, symbol: "none", lineStyle: { width: 1.6, color: PALETTE[i % PALETTE.length] } })),
  });
  chart(body.querySelector("#ric"), {
    ...base({ legend: { textStyle: { color: C.text2, fontSize: 11 }, top: 0 }, grid: { left: 50, right: 20, top: 46, bottom: 30 } }),
    xAxis: { type: "category", data: dates, ...axis(), boundaryGap: false },
    yAxis: { type: "value", ...axis() },
    series: F.map((f, i) => ({ name: f.label, type: "line", data: f.rolling_ic, symbol: "none", lineStyle: { width: 1.4, color: PALETTE[i % PALETTE.length] },
      markLine: i === 0 ? { symbol: "none", silent: true, label: { show: false }, data: [{ yAxis: 0, lineStyle: { color: C.label } }] } : undefined })),
  });
}

const MAP = [
  ["mom_12_1", ZH`12-1 個月動能`, 2], ["high_52w", ZH`距 52 週高點`, 1.5], ["trend_200", ZH`股價相對 200 日線`, 1.5],
  ["low_vol", ZH`(未使用)`, 0], ["reversal_1m", ZH`(未使用;RSI 有部分反轉概念)`, 0], ["volume_surge", ZH`(訊號列表:爆量)`, 0],
];

// 建議與證據:同一套判斷,分別輸出純文字(細節面板、清單)與表格用的 HTML(只含固定字串)
function suggestKind(f, w) {
  const strong = f.holm_sig === true;
  const recentOk = isNum(f.recent_t) && Math.abs(f.recent_t) >= 1.5 && Math.sign(f.recent_ic) === Math.sign(f.ic_mean);
  if (w > 0) {
    if (f.ic_mean > 0 && (strong || recentOk)) return ["pos", ZH`維持或小幅提高`, ZH`(方向一致)`];
    if (f.ic_mean < 0 && strong) return ["neg", ZH`考慮降低或反向`, ZH`(這個股票池裡方向相反)`];
    return ["watch", ZH`維持,但證據不足`, ZH`,等更大樣本 / 更長期間再決定`];
  }
  if (strong && recentOk) return ["watch", ZH`值得研究是否加入`, ZH`(${f.ic_mean > 0 ? ZH`正向` : ZH`反向`}且近期仍有效)`];
  return ["note", ZH`不需要動`, ""];
}
const suggestTxt = (f, w) => { const [, a, b] = suggestKind(f, w); return a + b; };
function suggest(f, w) { const [c, a, b] = suggestKind(f, w); return `<span class="${c}">${esc(a)}</span>${esc(b)}`; }

function verdictKind(f) {
  if (!isNum(f.nw_t)) return ["", "—"];
  const full = f.holm_sig === true, recent = isNum(f.recent_t) && Math.abs(f.recent_t) >= 1.5 && Math.sign(f.recent_ic) === Math.sign(f.ic_mean);
  if (full && recent) return ["pos", f.ic_mean > 0 ? ZH`偏正向(未證實)` : ZH`偏反向(未證實)`];
  if (full) return ["watch", ZH`校正後顯著、近期轉弱(未證實)`];
  if (recent) return ["watch", ZH`近期方向一致、校正後不顯著`];
  return ["note", ZH`證據不足`];
}
const verdictTxt = (f) => verdictKind(f)[1];
function verdict(f) { const [c, t] = verdictKind(f); return c ? `<span class="${c}">${esc(t)}</span>` : esc(t); }
