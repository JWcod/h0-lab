// 預測追蹤:主畫面只放快照數、可評分判斷數、方向命中率(附樣本數與「樣本少不要下結論」);
// 模型學習、判斷紀錄、用法說明收進細節面板;原本的表格完整保留在下方「完整內容」(手機改成清單)。
// 方向對錯用文字「對 / 錯」,不用打勾符號(規則 8);美股畫面上一律不評等,方向判斷用的是模型內部等級,已標示。
import { ZH } from "../i18n/index.js";
import { esc, num, pct, price, isNum, shownRating, shownSignal, ratingName } from "../fmt.js";
import * as UI from "../ui.js";

const LBL = { quality: ZH`品質`, growth: ZH`成長`, valuation: ZH`估值`, health: ZH`財務安全`, momentum: ZH`技術動能`, flows: ZH`籌碼`, revisions: ZH`預期修正`,
  sentiment: ZH`消息情緒`, regime: ZH`大盤環境`, lt_score: ZH`長期綜合分`, st_score: ZH`短期綜合分` };
const THIN_N = 30;   // 少於這個數的可評分判斷,命中率只標「樣本不足」
const dirTxt = (r) => (r.direction_ok === true ? ZH`對` : r.direction_ok === false ? ZH`錯` : ZH`未到期`);
const USE_NOTE = ZH`這些是即時追蹤紀錄(唯一真正沒看過的樣本):樣本累積後用來檢驗事先登記的研究假設;看完結果直接改權重就是資料窺探,所以任何改動都要先事先登記、再通過正式檢定。`;

function pillarRead(p) {
  if (!(isNum(p.t) && Math.abs(p.t) >= 2)) return ZH`尚無結論`;
  return p.ic > 0 ? ZH`偏正向(未證實,需更多樣本)` : ZH`偏反向(未證實,需更多樣本)`;
}

// 原本的「模型學習」表格,保留在完整內容
function learnHTML(L) {
  if (!L) return "";
  return ZH`<div class="card"><h3>模型學習:各面向分數與之後報酬的關係 <span class="sub">樣本 ${esc(L.n)} 檔(每檔取最早一筆已過 ${esc(L.min_days)} 天的快照);同時檢驗多個面向、沒有做多重檢定校正,|t| ≥ 2 不能算證實有效</span></h3>
    <div class="note" style="margin-bottom:8px">${esc(L.message || "")}</div>
    ${L.pillars?.length ? ZH`<div class="grid g2"><div class="tbl-wrap"><table><tr><th>面向</th><th class="n">IC(分數 vs 之後超額報酬)</th><th class="n">t 值</th><th class="n">樣本</th><th>解讀</th></tr>
      ${L.pillars.map((p) => `<tr><td>${esc(LBL[p.key] || p.key)}</td><td class="n ${p.ic > 0 ? "pos" : "neg"}">${num(p.ic, 3)}</td><td class="n">${num(p.t, 2)}</td><td class="n">${esc(p.n)}</td>
        <td>${pillarRead(p) === ZH`尚無結論` ? ZH`<span class="note">尚無結論</span>` : `<span class="${p.ic > 0 ? "pos" : "neg"}">${esc(pillarRead(p))}</span>`}</td></tr>`).join("")}</table></div>
      <div class="tbl-wrap"><table><tr><th>當時等級(模型內部)</th><th class="n">檔數</th><th class="n">平均超額報酬</th><th class="n">跑贏大盤比例</th></tr>
      ${(L.buckets || []).map((b) => `<tr><td>${esc(ratingName(b.rating))}</td><td class="n">${esc(b.n)}</td><td class="n ${b.avg_excess > 0 ? "pos" : "neg"}">${pct(b.avg_excess, 1, true)}</td><td class="n">${pct(b.win, 0)}</td></tr>`).join("")}</table>
      <div class="note" style="margin-top:6px">理想狀況:等級越高,平均超額報酬越高(單調)。</div></div></div>` : ""}
  </div>`;
}

function recordDetail(r) {
  return { title: `${r.name || r.symbol}(${r.symbol})`, desc: ZH`快照日期 ${r.date || "—"}。方向判斷用的是模型內部等級。`,
    rows: [[ZH`長期模型等級`, shownRating(r) || "—"], [ZH`短期`, shownSignal(r) || "—"], [ZH`當時價格`, price(r.price)],
      [ZH`公平價`, price(r.fair_value)], [ZH`長期分數`, num(r.lt_score, 0)], [ZH`至今報酬`, pct(r.ret, 1, true)], [ZH`同期大盤`, pct(r.bench_ret, 1, true)],
      [ZH`超額報酬`, pct(r.excess, 1, true)], [ZH`方向(模型內部等級)`, dirTxt(r)]],
    sections: [{ go: [{ label: ZH`看這家公司`, hash: `#/s/${encodeURIComponent(r.symbol)}/overview` }] }] };
}

export async function render(el, state) {
  el.innerHTML = ZH`<div class="loading"><div class="spinner"></div>整理預測紀錄中…</div>`;
  let j;
  try { j = await (await fetch("/api/review")).json(); } catch (e) { el.innerHTML = ZH`<div class="err">讀取預測紀錄失敗。請重新整理頁面再試一次;一直失敗的話,請看伺服器視窗的錯誤訊息。</div>`; return; }
  const s = j.summary || {}, L = j.learning, rows = j.rows || [];
  const thin = !(s.n_judged >= THIN_N);
  const F = j.forward || { horizons: [] };
  const f1 = (F.horizons || [])[0] || {};
  const ciTxt = (ci, d = 0) => (ci ? ZH`95% 區間 ${pct(ci[0], d)} ~ ${pct(ci[1], d)}` : ZH`樣本不足,不給區間`);
  const D = {
    hit: { title: ZH`方向命中率怎麼算`, desc: ZH`目前 ${s.n_judged ?? 0} 筆可評分的判斷。`,
      rows: [[ZH`快照數`, String(s.n_snapshots ?? 0)], [ZH`可評分的判斷`, String(s.n_judged ?? 0)], [ZH`方向命中率`, isNum(s.hit_rate) ? pct(s.hit_rate, 0) : "—"]],
      sections: [{ p: [ZH`模型內部等級 A、B → 之後高於大盤算對;D、E → 低於大盤算對;C 不評分。快照要過 20 天以上才評分。`,
        ZH`樣本少時不要下結論:少於 ${THIN_N} 筆標「樣本不足」;就算樣本夠,命中率也沒有附信賴區間、沒有經過正式檢定,不算證實有效。`,
        ZH`每次分析自動存一份快照(同一天同一檔覆蓋)。`] }] },
    learn: { title: ZH`模型學習:各面向分數與之後報酬`, desc: L ? ZH`樣本 ${L.n} 檔(每檔取最早一筆已過 ${L.min_days} 天的快照)。` : "",
      sections: L ? [{ p: [L.message || "", ZH`同時檢驗多個面向、沒有做多重檢定校正,|t| ≥ 2 也不能算證實有效。`].filter(Boolean) },
        { h: ZH`各面向(IC = 分數和之後超額報酬的相關)`, items: (L.pillars || []).map((p) => ({ t: LBL[p.key] || p.key, ev: isNum(p.n) && p.n < 15 ? "thin" : null,
          d: ZH`IC ${num(p.ic, 3)} · t 值 ${num(p.t, 2)} · 樣本 ${p.n ?? "—"} · ${pillarRead(p)}` })) },
        { h: ZH`依當時等級分組(模型內部等級)`, rows: (L.buckets || []).map((b) => [ZH`${ratingName(b.rating)}(${b.n} 檔)`, ZH`平均超額 ${pct(b.avg_excess, 1, true)} · 跑贏 ${pct(b.win, 0)}`]),
          p: (L.buckets || []).length ? [ZH`理想狀況:等級越高,平均超額報酬越高(單調)。`] : [] }] : [{ p: [ZH`樣本還不夠,沒有學習結果。`] }] },
    fwd: { title: ZH`即時追蹤紀錄`, desc: ZH`${F.start || "—"} 以後的正式紀錄才算(之前的快照產生在模型開發期間,結果可能已經被看過)。`,
      rows: [[ZH`即時追蹤快照(每檔每月一筆)`, ZH`${F.n_snapshots ?? 0} 筆`], [ZH`樣本足夠的門檻`, ZH`${F.min_n ?? "—"} 筆`]],
      sections: [...(F.horizons || []).map((h) => ({ h: ZH`${h.label}後`,
        rows: [[ZH`到期筆數`, ZH`${h.n_matured} 筆(其中美股 ${h.n_us} 筆)`], [ZH`方向可評分(台股、不是等級 C)`, ZH`${h.n_dir} 筆`],
          [ZH`方向命中率`, isNum(h.hit_rate) ? pct(h.hit_rate, 0) : "—"], [ZH`命中率區間(Wilson)`, ciTxt(h.hit_ci)],
          [ZH`台股平均超額報酬(對加權指數)`, isNum(h.mean_excess) ? pct(h.mean_excess, 1, true) : "—"], [ZH`台股超額報酬區間`, ciTxt(h.excess_ci, 1)],
          [ZH`美股平均超額報酬(對 S&P 500)`, isNum(h.us_mean_excess) ? pct(h.us_mean_excess, 1, true) : "—"]] })),
        { h: ZH`限制`, p: [ZH`同一個月很多檔股票一起漲跌,彼此不獨立;3 個月與 12 個月期每月取一筆,前後期間會重疊(12 個月期最多重疊 11 個月)。區間是當成獨立樣本算的,會偏窄。價格報酬沒有含股利。`,
          ZH`美股畫面上不評等,所以只計超額報酬,不算方向命中率。`,
          ZH`目前即時追蹤的資料很少,不得據此下結論;要檢驗的假設必須事先登記。`] }] },
    use: { title: ZH`這些紀錄怎麼用`, sections: [{ p: [USE_NOTE, ZH`美股在畫面上一律不評等;方向對錯欄用的是模型內部等級,只供預測追蹤。`] }] },
  };
  rows.forEach((r, i) => { D["r" + i] = recordDetail(r); });

  el.innerHTML = ZH`<div class="u-ov">
    <h1 class="u-largetitle">預測追蹤</h1>
    <p class="u-sub" style="margin:-6px 0 14px">每次分析都會自動記下模型當時的判斷,到期後對答案,讓你看到模型實際準不準。</p>
    <div class="u-grid">
      <button type="button" class="u-card u-kpi" data-d="hit"><span class="u-label">快照數</span><span class="u-verdict"><b class="u-num">${esc(s.n_snapshots ?? 0)}</b></span>
        <span class="u-sub">每次分析存一份</span></button>
      <button type="button" class="u-card u-kpi" data-d="hit"><span class="u-label">可評分的判斷</span><span class="u-verdict"><b class="u-num">${esc(s.n_judged ?? 0)}</b></span>
        <span class="u-sub">過 20 天以上、不是等級 C</span></button>
      <button type="button" class="u-card u-kpi" data-d="hit"><span class="u-label">方向命中率(相對大盤)</span>
        <span class="u-verdict"><b class="u-num">${isNum(s.hit_rate) ? esc(pct(s.hit_rate, 0)) : "—"}</b>${thin ? UI.evidence("thin", ZH`只有 ${s.n_judged ?? 0} 筆,樣本少不要下結論`) : UI.evidence("unval", ZH`沒有信賴區間、沒有經過正式檢定,不算證實`)}</span>
        <span class="u-sub">樣本 ${esc(s.n_judged ?? 0)} 筆 · 樣本少不要下結論</span></button>
      <button type="button" class="u-card u-kpi" data-d="fwd"><span class="u-label">即時追蹤(${esc(F.start || "—")} 起)</span>
        <span class="u-verdict"><b class="u-num">${esc(F.n_snapshots ?? 0)} 筆</b>${f1.thin === false ? UI.evidence("unval", ZH`即時追蹤紀錄,沒有經過正式檢定,不算證實`) : UI.evidence("thin", ZH`1 個月到期可評分 ${f1.n_dir ?? 0} 筆,少於 ${F.min_n ?? 30} 筆不給區間`)}</span>
        <span class="u-sub">1 個月到期 ${esc(f1.n_matured ?? 0)} 筆 · 命中率 ${isNum(f1.hit_rate) ? esc(pct(f1.hit_rate, 0)) : "—"}</span></button>
    </div>
    <h2 class="u-label u-list-h">細節</h2>
    <ul class="u-list">
      ${UI.row({ title: ZH`即時追蹤紀錄`, sub: ZH`1 / 3 / 12 個月到期的命中率與超額報酬`, data: { d: "fwd" } })}
      ${UI.row({ title: ZH`模型學習`, sub: L ? ZH`各面向分數和之後報酬的關係 · 樣本 ${L.n} 檔` : ZH`樣本還不夠`, data: { d: "learn" } })}
      ${UI.row({ title: ZH`判斷紀錄`, sub: ZH`${rows.length} 筆快照,在下方完整內容`, data: { jump: "records" } })}
      ${UI.row({ title: ZH`這些紀錄怎麼用`, sub: ZH`即時追蹤紀錄與資料窺探`, data: { d: "use" } })}
    </ul>
    <h2 class="u-label u-list-h">完整內容</h2>
    <div class="u-full">
      ${learnHTML(L)}
      <div class="card" id="records"><h3>判斷紀錄</h3>
        <p class="u-sub u-hscroll-hint">表格可以左右滑動;點清單看單筆細節。</p>
        ${rows.length ? `<ul class="u-list u-mobile-only">${rows.map((r, i) => UI.row({ title: `${r.name || r.symbol}`,
          sub: ZH`${r.date || ""} · 方向 ${dirTxt(r)} · 超額 ${pct(r.excess, 1, true)}`, data: { d: "r" + i } })).join("")}</ul>` : ""}
        <div class="tbl-wrap u-desk-only"><table>
        <tr><th>日期</th><th>公司</th><th>長期模型等級</th><th>短期</th><th class="n">當時價格</th><th class="n">公平價</th><th class="n">長期分數</th><th class="n">至今報酬</th><th class="n">大盤</th><th class="n">超額</th><th>方向(模型內部等級)</th></tr>
        ${rows.map((r) => `<tr><td>${esc(r.date)}</td><td><a href="#/s/${encodeURIComponent(r.symbol)}/overview">${esc(r.name)}</a> <span class="num">${esc(r.symbol)}</span></td>
          <td>${esc(shownRating(r))}</td><td>${esc(shownSignal(r))}</td><td class="n">${price(r.price)}</td><td class="n">${price(r.fair_value)}</td><td class="n">${num(r.lt_score, 0)}</td>
          <td class="n ${r.ret > 0 ? "up" : r.ret < 0 ? "down" : ""}">${pct(r.ret, 1, true)}</td><td class="n">${pct(r.bench_ret, 1, true)}</td><td class="n ${r.excess > 0 ? "pos" : r.excess < 0 ? "neg" : ""}">${pct(r.excess, 1, true)}</td>
          <td>${r.direction_ok === true ? ZH`<span class="pos">對</span>` : r.direction_ok === false ? ZH`<span class="neg">錯</span>` : ZH`<span class="note">未到期</span>`}</td></tr>`).join("") || ZH`<tr><td colspan="11" class="note">還沒有快照。分析任何一家公司就會自動記錄。</td></tr>`}
        </table></div>
        ${rows.length ? "" : ZH`<div class="note u-mobile-only">還沒有快照。分析任何一家公司就會自動記錄。</div>`}
        <div class="note" style="margin-top:8px">${esc(USE_NOTE)}</div></div>
    </div>
    <p class="u-disclaimer">預測追蹤是模型過去判斷的紀錄,不代表未來報酬,也不構成投資建議。</p>
  </div>`;
  el.querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => { const x = D[b.dataset.d]; if (x) UI.openDetail(x); }));
  el.querySelectorAll("[data-jump]").forEach((b) => (b.onclick = () => el.querySelector("#" + b.dataset.jump)?.scrollIntoView({ behavior: "smooth", block: "start" })));
}
