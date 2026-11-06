import { ZH } from "../i18n/index.js";
import { esc, num, pct, isNum, ratingName } from "../fmt.js";
import { chart, base, axis, C } from "../charts.js";
import * as UI from "../ui.js";
import { productText } from "../modelapi.js";

const PL = { quality: ZH`品質`, growth: ZH`成長`, valuation: ZH`估值`, health: ZH`財務安全`, momentum: ZH`技術動能`, lt_score: ZH`長期綜合分`, st_score: ZH`短期綜合分`, upside: ZH`模型估值差距` };

// 舊版規則的開發期回測:完整保留,收在研究紀錄頁下方的可展開區塊(打開才讀取)。只有整體統計,沒有任何個股目前的等級
export async function renderLegacy(el, state) {
  const mk = state.btMarket || "TW";
  el.innerHTML = ZH`<div class="loading"><div class="spinner"></div>讀取回測結果…</div>`;
  const j = await (await fetch(`/api/research/backtest?market=${mk}`)).json();
  const R = j.current, B0 = j.baseline;
  const notice = noticeCard(j);
  if (!R) {
    el.innerHTML = ZH`${notice}<div class="card" style="margin-top:14px"><h3>歷史回測
      <span class="right flex">${toggle(mk)}</span></h3><div class="note">${esc(j.withheld || ZH`還沒有開發期的回測結果。`)}</div></div>`;
    bindToggle(el, state);
    return;
  }
  const lt = R.long_term["12m"], st = R.short_term["3m"];
  el.innerHTML = ZH`${notice}
  <div class="card" style="margin-top:14px"><h3>歷史回測<span class="sub">用「現在這套模型」回到過去,每個月底只用當時拿得到的資料判斷,再對答案</span>
    <span class="right flex">${toggle(mk)}</span></h3>
    <div class="note">只含開發期(${esc(R.holdout_start)} 之前)· 期間 ${esc(R.start)} 起每月底 · ${R.n_stocks} 檔 · ${R.n_judgments.toLocaleString()} 次判斷 · 模型 ${esc(R.model_version)} · 計算時間 ${esc((R.computed || "").replace("T", " "))}</div>
  </div>

  <div class="grid g2" style="margin-top:14px">
    ${scoreCard(ZH`長期模型等級(之後 12 個月)`, lt, B0?.long_term?.["12m"], ZH`等級 A、B → 之後 12 個月高於同股票池中位數算「對」;D、E → 低於算「對」`)}
    ${scoreCard(ZH`短期分數等級(之後 3 個月)`, st, B0?.short_term?.["3m"], ZH`分數偏高 → 之後 3 個月高於中位數算「對」;分數偏低 → 低於算「對」。回測只有價格類資料(沒有法人籌碼、預估修正)`)}
  </div>

  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>逐年對錯</h3><div class="tbl-wrap"><table>
      <tr><th>年</th><th class="n">長期 對 / 錯</th><th class="n">長期命中</th><th class="n">短期 對 / 錯</th><th class="n">短期命中</th></tr>
      ${R.by_year.map((y) => `<tr><td>${y.year}</td><td class="n">${y.lt_right} / ${y.lt_wrong}</td><td class="n ${y.lt_hit > 0.5 ? "pos" : y.lt_hit < 0.5 ? "neg" : ""}">${isNum(y.lt_hit) ? pct(y.lt_hit, 1) : ZH`開發期內沒有完整 12 個月結果`}</td>
        <td class="n">${y.st_right} / ${y.st_wrong}</td><td class="n ${y.st_hit > 0.5 ? "pos" : y.st_hit < 0.5 ? "neg" : ""}">${pct(y.st_hit, 1)}</td></tr>`).join("")}
    </table></div></div>
    <div class="card"><h3>依當時等級分組:之後 12 個月表現</h3><div class="tbl-wrap"><table>
      <tr><th>當時等級</th><th class="n">次數</th><th class="n">跑贏中位數比例</th><th class="n">平均相對報酬</th><th class="n">平均絕對報酬</th></tr>
      ${R.by_rating.map((b) => `<tr><td>${esc(ratingName(b.rating))}</td><td class="n">${b.n}</td><td class="n">${pct(b.win, 0)}</td><td class="n ${b.avg_x12 > 0 ? "pos" : "neg"}">${pct(b.avg_x12, 1, true)}</td><td class="n">${pct(b.avg_r12, 1, true)}</td></tr>`).join("")}
    </table></div><div class="note" style="margin-top:6px">理想:等級越高,跑贏比例越高。平均報酬會被少數報酬極高的股票拉高,看「跑贏比例」比較公平。</div></div>
  </div>

  <div class="card" style="margin-top:14px"><h3>模型規則的歷史回測 <span class="sub">每月底調整、等權重、扣交易成本 ${pct(R.portfolio.cost, 1)};對照「股票池全部等權」與大盤</span></h3>
    <div class="grid g3">
      <div class="span2"><div id="pf" class="chart mid"></div></div>
      <div><div class="tbl-wrap"><table><tr><th>策略</th><th class="n">年化</th><th class="n">最大回撤</th><th class="n">夏普</th></tr>
        ${[["ex_sell", ZH`排除等級 D、E(其餘等權)`], ["model", ZH`只含等級 A、B`], ["model_st", ZH`等級 A、B + 短期分數不偏低`], ["universe", ZH`股票池全部等權`], ["bench", mk === "TW" ? ZH`加權指數` : "S&P 500"]]
          .map(([k, n]) => { const s = R.portfolio.stats[k] || {}; return `<tr><td>${n}</td><td class="n">${pct(s.cagr, 1)}</td><td class="n">${pct(s.max_dd, 0)}</td><td class="n">${num(s.sharpe, 2)}</td></tr>`; }).join("")}
      </table></div><div class="note" style="margin-top:8px">${mk === "TW"
        ? ZH`台股(舊版基準,是包含樣本外測試期的全期結果,僅供對照,不是本頁的開發期數字):「排除等級 D、E」與全部等權的年化差距 +1.6%,95% 區間 −2.0% ~ +5.5%,沒有證實有效;只含最高等級的(−4.1%,區間 −13.8% ~ +5.1%)也沒有證實有效。「勝過大盤」主要來自存活者股票池與大盤不含股利,不是模型能力。`
        : ZH`美股(舊版基準,是包含樣本外測試期的全期結果,僅供對照,不是本頁的開發期數字):這段期間模型沒有優勢,排除低等級 ≈ 全部等權(年化差距 −0.1%,95% 區間 −1.4% ~ +1.2%);長期分數的 IC 是負的,美股等級目前只能當參考。`}</div></div>
    </div></div>

  <div class="card" style="margin-top:14px"><h3>各面向的預測力 <span class="sub">每個月橫斷面 Spearman IC 的平均。這個 t 值沒有處理每月重疊的 12 個月報酬,會高估顯著性;正式檢定的方法見本頁上方「怎麼判斷有沒有用」</span></h3>
    <div class="tbl-wrap"><table><tr><th>面向</th><th class="n">3 個月 IC</th><th class="n">t</th><th class="n">12 個月 IC</th><th class="n">t</th></tr>
    ${Object.keys(PL).map((k) => { const a = R.pillar_ic.find((x) => x.key === k && x.horizon === "3m"), b = R.pillar_ic.find((x) => x.key === k && x.horizon === "12m"); if (!a && !b) return "";
      return `<tr><td>${PL[k]}</td><td class="n ${a?.ic > 0 ? "pos" : "neg"}">${num(a?.ic, 3)}</td><td class="n">${num(a?.t, 2)}</td><td class="n ${b?.ic > 0 ? "pos" : "neg"}">${num(b?.ic, 3)}</td><td class="n">${num(b?.t, 2)}</td></tr>`; }).join("")}
    </table></div></div>

  <div class="card" style="margin-top:14px"><h3>⚠ 這個回測的限制(請一定要讀)</h3><ul class="note" style="line-height:1.9;margin:0">
    <li><b>存活者偏誤</b>:股票池是「現在」的權值股,過去下市或被剔除的公司不在裡面。</li>
    <li><b>缺少的資料</b>:分析師預估與目標價、法人籌碼、新聞情緒、同業倍數拿不到歷史值,這些面向在回測中缺席;DCF 的成長率只能用過去 3 年 CAGR(網站上會混合分析師預估)。</li>
    <li><b>公布延遲</b>:台股季報假設季末 45 天(年報 90 天)、月營收次月 10 日、美股年報年度結束 75 天後才可用。</li>
    <li><b>樣本內調整</b>:舊版規則裡有兩條是看過更早版本的結果後才加入的(有文獻依據,且前後半段都檢查過),仍有過度配適的可能;之後要用新資料持續驗證。</li>
    <li><b>有效樣本很小</b>:每月判斷一次、看之後 12 個月,相鄰月份的答案幾乎重疊,5 年多只有約 4.7 個獨立期間;命中率略高於 50% 不代表模型有效(舊版基準,是包含樣本外測試期的全期結果,僅供對照:台股 12 個月 58.8% 的 95% 區間是 52.7% ~ 66.0%)。</li>
    <li>命中率 > 50% 不代表報酬一定比較好:少數報酬極高的股票影響很大。</li>
  </ul></div>`;
  bindToggle(el, state);
  const c = R.portfolio.curve;
  chart(el.querySelector("#pf"), {
    ...base({ legend: { textStyle: { color: C.text2, fontSize: 11 }, top: 0 }, grid: { left: 50, right: 16, top: 40, bottom: 30 } }),
    xAxis: { type: "category", data: c.date, ...axis(), boundaryGap: false },
    yAxis: { type: "value", ...axis(), axisLabel: { color: C.label, formatter: (x) => x.toFixed(1) + "x" } },
    series: [["ex_sell", ZH`排除等級 D、E`, "#3ee0c5", 2.4], ["model", ZH`只含等級 A、B`, "#5ab0ff", 1.6], ["universe", ZH`全部等權`, "#ffd166", 1.6], ["bench", ZH`大盤`, "#d9e6ff", 1.4]]
      .map(([k, n, col, w]) => ({ name: n, type: "line", data: c[k], symbol: "none", lineStyle: { color: col, width: w, type: k === "bench" ? "dashed" : "solid" } })),
  });
}

function scoreCard(title, x, x0, note) {
  const good = x.hit > 0.5;
  return ZH`<div class="card"><h3>${title}</h3>
    <div class="flex" style="gap:24px;align-items:flex-end">
      <div><div class="big-label">判斷正確</div><div class="num pos" style="font-size:34px;font-weight:800">${x.right.toLocaleString()}</div></div>
      <div><div class="big-label">判斷錯誤</div><div class="num neg" style="font-size:34px;font-weight:800">${x.wrong.toLocaleString()}</div></div>
      <div><div class="big-label">命中率</div><div class="num ${good ? "pos" : "neg"}" style="font-size:34px;font-weight:800">${pct(x.hit, 1)}</div></div>
      <span class="sp"></span><span class="pill ${good ? "pos" : "neg"}">${good ? ZH`對 > 錯(未經統計檢定)` : ZH`對 ≤ 錯(未經統計檢定)`}</span></div>
    <div style="margin:12px 0;height:12px;border-radius:6px;overflow:hidden;background:rgba(255,138,92,.6)"><div style="height:100%;width:${(x.hit * 100).toFixed(1)}%;background:var(--pos)"></div></div>
    <div class="kv"><span class="k">高等級的命中率</span><span class="v">${pct(x.bull_hit, 1)}(${x.n_bull} 次)</span>
      <span class="k">低等級的命中率</span><span class="v">${isNum(x.bear_hit) ? pct(x.bear_hit, 1) : "—"}(${x.n_bear} 次)</span>
      ${x0 ? ZH`<span class="k">調整前的規則</span><span class="v">${x0.right} 對 / ${x0.wrong} 錯 · ${pct(x0.hit, 1)}</span>` : ""}</div>
    <div class="note" style="margin-top:8px">${esc(note)}</div></div>`;
}

function toggle(mk) {
  return ZH`<span class="toggle" id="mk"><button data-k="TW" class="${mk === "TW" ? "on" : ""}">台股權值 40</button><button data-k="US" class="${mk === "US" ? "on" : ""}">美股大型 40</button></span>`;
}

function bindToggle(el, state) {
  el.querySelectorAll("#mk button").forEach((b) => (b.onclick = () => { state.btMarket = b.dataset.k; renderLegacy(el, state); }));
}

// 網站不提供重跑回測;說明用高對比的一般文字色,不用灰字或透明度
function noticeCard(j) {
  return ZH`<div class="card" style="border:1px solid var(--watch, #ffd166)"><h3>這裡只顯示開發期的回測</h3>
    <div style="color:var(--text, #ffffff);line-height:1.8">${esc(j.notice || ZH`網站不提供重跑回測;這裡只顯示開發期的結果。`)}<br>
    本頁不是投資建議,歷史回測結果不代表未來報酬。</div></div>`;
}

// ---------- 研究紀錄頁:開發時研究模型的整體統計與事先登記假設的結果;不顯示任何個股目前的內建評等 ----------
const MK = { TW: ZH`台股`, US: ZH`美股` };
const HZ = { lt_12m: ZH`長期 12 個月`, lt_6m: ZH`長期 6 個月`, st_1m: ZH`短期 1 個月` };
const sgn = (v, d = 3) => (isNum(v) ? (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(d) : "—");
const ciTxt = (c) => (Array.isArray(c) && c.length === 2 ? `${sgn(c[0])} ~ ${sgn(c[1])}` : "—");
const INTRO = ZH`開發時的研究模型在過去資料上的整體統計,以及事先登記的研究假設結果。只用開發期(2024-09 以前)的資料,樣本外測試期的成績不在這裡顯示;這裡不提供任何個股的評等。`;
const DISCLAIM = ZH`研究紀錄是歷史資料的統計檢定,不代表未來報酬,也不構成投資建議。`;
const SUM = "display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;cursor:pointer;font-weight:600;color:var(--text)";

function gateStatus(g) {
  if (g.change_type === "add" && g.passed) return ["proven", ZH`加特徵:證明更好(含多重檢定校正)`];
  if (g.change_type === "fix" && !g.passed) return ["worse", ZH`修正:沒有通過「不更差」的門檻`];
  if (g.change_type === "fix") return ["none", ZH`修正:不更差,已採用;沒有證實更好`];
  return ["none", ZH`加特徵:沒有證明更好,沒有採用`];
}

// 舊版回測收在可展開區塊:第一次打開才讀取與畫圖(圖表在隱藏的容器裡量不到寬度)
function legacyBox() {
  return ZH`<details class="u-card" id="legacyWrap" style="margin-top:14px">
    <summary style="${SUM}">▸ 舊版規則的歷史回測(開發期,只供參考)<span class="u-sub" style="font-weight:400;flex-basis:100%">點開看逐年對錯、投資組合模擬與限制</span></summary>
    <div id="legacyBt" style="margin-top:12px"></div></details>`;
}
function bindLegacy(el, state) {
  const w = el.querySelector("#legacyWrap");
  let done = false;
  w.addEventListener("toggle", () => { if (w.open && !done) { done = true; renderLegacy(el.querySelector("#legacyBt"), state); } });
}

export async function render(el, state) {
  el.innerHTML = ZH`<div class="loading"><div class="spinner"></div>讀取驗證摘要…</div>`;
  let j = {};
  try { j = await (await fetch("/api/research/evidence")).json(); } catch (e) { j = { message: ZH`讀取失敗,請重新整理頁面再試一次。` }; }
  const E = j.evidence;
  const head = ZH`<h1 class="u-largetitle">研究紀錄</h1><p class="u-sub" style="margin:-6px 0 14px">${esc(INTRO)}</p>`;
  if (!E) {
    el.innerHTML = `<div class="u-ov">${head}<div class="u-card"><div class="u-sub">${esc(j.message || ZH`還沒有驗證摘要。`)}</div></div>${legacyBox()}
      <p class="u-disclaimer">${esc(DISCLAIM)}</p></div>`;
    return bindLegacy(el, state);
  }
  const P = E.primary || {};
  const allZero = Object.values(P).every((m) => Object.values(m).every((x) => !Array.isArray(x.ci95) || (x.ci95[0] <= 0 && x.ci95[1] >= 0)));
  const gates = E.gates || [];
  const nFam = new Set(gates.map((g) => g.family)).size;
  const nAdd = gates.filter((g) => g.change_type === "add").length, nAddOk = gates.filter((g) => g.change_type === "add" && g.passed).length;
  const nFixOk = gates.filter((g) => g.change_type === "fix" && g.passed).length;
  const card = (m) => {
    const x = (P[m] || {}).lt_12m || {};
    const st = UI.ciStatus(x.ci95?.[0], x.ci95?.[1]);
    return ZH`<button type="button" class="u-card u-kpi" data-d="mk-${m}"><span class="u-label">${MK[m] || m} · 長期 12 個月 IC</span>
      <span class="u-verdict"><b class="u-num">${sgn(x.ic)}</b>${UI.evidence(st.status, st.long)}</span>
      ${UI.ciBar(x.ic, x.ci95?.[0], x.ci95?.[1], { range: [-0.15, 0.15], label: ZH`${MK[m] || m} 長期 12 個月 IC` })}
      <span class="u-sub">95% 區間 ${ciTxt(x.ci95)} · ${x.n_months ?? "—"} 個月</span></button>`;
  };
  const D = {};
  for (const m of Object.keys(P)) {
    D["mk-" + m] = { title: ZH`${MK[m] || m}:主要指標`, desc: ZH`IC 是分數排名和之後報酬排名的相關;用重疊世代的月 IC 加 Newey–West 95% 區間。只用開發期。`,
      sections: [{ rows: Object.entries(P[m]).map(([k, x]) => [HZ[k] || k, `${sgn(x.ic)}(${ciTxt(x.ci95)})`]) },
        { items: Object.entries(P[m]).map(([k, x]) => { const s = UI.ciStatus(x.ci95?.[0], x.ci95?.[1]); return { t: HZ[k] || k, ev: s.status, d: s.long }; }) }] };
  }
  D.gates = { title: ZH`每個改進的結果`, desc: ZH`${nFam} 個事先登記的研究假設做過正式檢定(共 ${gates.length} 個設定,開發期)。修正型只要求不更差;加特徵型要證明更好。`,
    sections: [{ items: gates.map((g) => { const [ev, why] = gateStatus(g); return {
      tag: g.family, t: `${g.change_type === "add" ? ZH`加特徵` : ZH`修正`} · ${g.markets} · ${g.primary || ""}`, ev,
      d: ZH`差異 ${sgn(g.diff)}(95% 區間 ${ciTxt(g.ci95)},${g.n_months ?? "—"} 個月)。${why}。` }; }) },
      { h: ZH`沒有做正式檢定的假設`, items: (E.prereg || []).filter((r) => !gates.some((g) => g.family === r.family)).map((r) => ({
        tag: r.family, t: ZH`狀態:${r.status}`, ev: r.status === ZH`探索性` ? "unval" : "none",
        d: r.status === ZH`探索性` ? ZH`依事先定好的規則降級為探索性,不做正式檢定。` : ZH`用事先登記的其他判準檢驗(例:機率校準用 Brier 分數比較),或只能在樣本外測試期檢驗。` })) }] };
  D.prereg = { title: ZH`事先登記的研究假設`, desc: ZH`改模型之前先寫下要測什麼、怎麼判斷;沒有登記的嘗試一律算探索性。`,
    sections: [{ items: (E.prereg || []).map((r) => ({ tag: r.status, t: `${r.change_type === "add" ? ZH`加特徵` : ZH`修正`} · ${(r.markets || []).join("、")} · ${r.primary}`, d: ZH`代號 ${r.family}` })) }] };
  D.method = { title: ZH`怎麼判斷有沒有用`, sections: [{ p: [...(E.notes || []).map(productText),
    ZH`樣本外測試期(${E.holdout_start} 以後的股價)只能透過正式程序計算,每個研究階段最多一次;目前用了 ${E.holdout_used} 次。這頁不顯示任何樣本外成績。`,
    ZH`模型 ${productText(E.model).replace(/-dev\b/, "")} · 資料版本 ${E.data_vintage} · 產生時間 ${E.generated}`] }] };
  el.innerHTML = ZH`<div class="u-ov">
    ${head}
    <button type="button" class="u-card u-kpi" data-d="method"><span class="u-label">模型能預測報酬嗎?</span>
      <span class="u-verdict"><b class="u-kpi-hero">${allZero ? ZH`目前沒有證實有效` : ZH`部分指標區間不含 0`}</b></span>
      <span class="u-sub">${allZero ? ZH`所有主要指標的 95% 區間都包含 0` : ZH`看細節:區間不含 0 但沒有經過多重檢定校正,仍不算證實`}</span></button>
    <div class="u-grid" style="margin-top:14px">${Object.keys(P).map(card).join("")}</div>
    <div class="u-grid" style="margin-top:14px">
      <button type="button" class="u-card u-kpi" data-d="gates"><span class="u-label">嘗試過的改進</span><span class="u-verdict"><b class="u-num">${gates.length}</b></span>
        <span class="u-sub">${nFam} 個研究假設 · 加特徵 ${nAdd} 個設定,證實更好 ${nAddOk} 個;修正通過 ${nFixOk} 個</span></button>
      <button type="button" class="u-card u-kpi" data-d="method"><span class="u-label">樣本外測試用了</span><span class="u-verdict"><b class="u-num">${esc(E.holdout_used)} 次</b></span>
        <span class="u-sub">每個研究階段最多 1 次;成績不在網站顯示</span></button>
    </div>
    <h2 class="u-label u-list-h">細節</h2>
    <ul class="u-list">
      ${UI.row({ title: ZH`每個改進的結果`, sub: ZH`${gates.length} 個設定的正式檢定`, data: { d: "gates" } })}
      ${UI.row({ title: ZH`事先登記的研究假設`, sub: ZH`${(E.prereg || []).length} 個`, data: { d: "prereg" } })}
      ${UI.row({ title: ZH`怎麼判斷有沒有用`, sub: ZH`統計方法、樣本外測試規則`, data: { d: "method" } })}
    </ul>
    ${legacyBox()}
    <p class="u-disclaimer">${esc(DISCLAIM)}</p>
  </div>`;
  el.querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => { const x = D[b.dataset.d]; if (x) UI.openDetail(x); }));
  bindLegacy(el, state);
}
