// 方法與資料:主畫面是章節清單與幾個關鍵數字;每章全文收進細節面板(內容與舊版相同,不刪);
// 指標字典留在頁面下方,電腦用表格、手機用清單(點開看公式、意義、判斷、出處)。
import { ZH } from "../i18n/index.js";
import { esc } from "../fmt.js";
import * as UI from "../ui.js";

const PN = { quality: ZH`品質`, valuation: ZH`估值`, growth: ZH`成長`, health: ZH`財務安全`, revisions: ZH`預期修正`, flows: ZH`籌碼`, momentum: ZH`技術動能`, sentiment: ZH`消息`, regime: ZH`大盤環境` };

// 章節全文(純文字;openDetail 會跳脫)
const FLOW = [
  [ZH`建立模型`, ZH`用精靈自己選一個因子範本(沒有預設、不推薦),看過權重、調整你在意的訊號,存成你自己的模型。H0 Lab 不提供內建評等。`],
  [ZH`選股掃描`, ZH`每週(或收盤後)掃一次台股 / 美股清單,用你自己的模型排名,挑 3–5 家有興趣的。`],
  [ZH`深入研究`, ZH`點進公司,先看資料頁與「研究檢查清單」,再到估值計算機調整假設(特別是成長率與利潤率)。`],
  [ZH`挑戰假設`, ZH`看敏感度表 —— 如果只要成長率差一點,結論就翻盤,代表風險高。看不懂的數字按旁邊的 ?。`],
  [ZH`加入觀察清單`, ZH`想持續追蹤的公司按 ☆,之後一起掃描。`],
  [ZH`研究筆記`, ZH`檢查清單的最後三項(一句話說明、研究假設、資料來源)要自己查證並寫下來。`],
  [ZH`誠實驗證`, ZH`在模型工作台用開發期回測檢驗你的模型;嘗試次數會被記錄,樣本外測試每個模型系列只能看一次。`],
  [ZH`因子研究`, ZH`想知道某個量化訊號在台股 / 美股有沒有用,先來這裡看歷史證據。`],
];
const PIPE = ZH`資料(Yahoo、FinMind、SEC、證交所、集保、Google News)→ 指標計算(全部用程式算,不用 AI 算數)→ 八大面向評分 → 估值模型 → 長短期模型等級 → 規則式見解。這是開發時研究基準模型的流程,用來產生研究紀錄;網站不顯示它的個股結果,你的模型用同一套指標與評分引擎。`;
const PRINCIPLES = [
  ZH`公布的因子會衰退(McLean & Pontiff 2016:發表後報酬平均下降 58%),所以權重偏向證據最穩的因子。`,
  ZH`品質要和價值一起看,避免價值陷阱。`,
  ZH`分析師目標價要打折(Bradshaw 等 2013)。`,
  ZH`看公平價「區間」與「不確定性」,不看單點。`,
  ZH`每個面向分開評分,才知道是哪一面判斷錯。`,
];
const SCORING = ZH`每個指標用分段線性函數轉成 0–100(門檻是初版設定的假設值),有同業資料時混合同業百分位(絕對 60% + 相對 40%)。`;
const OLD_RULE = ZH`價格 ≤ 模型估值 × (1 − 安全邊際) → 模型等級 A;≤ × (1 − 安全邊際/2) → 等級 B;±安全邊際/2 內 → 等級 C;以上對稱為等級 D、E。安全邊際由不確定性決定(低 15%、中 25%、高 35%、很高 45%)。長期分數 < 40 降一級;有財務紅旗最高只到等級 C。`;
const DATA = [
  ["Yahoo Finance", ZH`股價、財報、分析師預估與目標價(台股、美股)`],
  ["FinMind", ZH`台股財報、月營收、法人籌碼、融資融券、股利(免費額度約每小時 280 次)`],
  ["SEC EDGAR", ZH`美股官方申報財報(只用判斷日以前申報的版本)`],
  [ZH`證交所、櫃買中心、集保`, ZH`全市場本益比與淨值比、加權報酬指數、股權分散表、重大訊息`],
  [ZH`Google News 等新聞`, ZH`新聞標題,用詞典法判斷消息情緒`],
];
const V2_RULE = ZH`長期模型等級改用長期分數在正式股票池(台股前 50 時點名單、美股大型 40)最近一個回測判斷日的固定分位:後 10% 為 E、次 20% 為 D、中間 40% 為 C、次 20% 為 B、前 10% 為 A,分位規則和回測相同(切點來自回測的分數分布;回測沒有分析師與同業資料,網站的長期分數和回測不完全可比);公平價與安全邊際照樣顯示,但不決定等級。沒有公平價時不評等;還沒有分位切點檔時暫時沿用舊規則,並在等級理由中標示。`;

export async function render(el, state) {
  el.innerHTML = ZH`<div class="loading"><div class="spinner"></div>讀取方法與資料…</div>`;
  let params = null;
  try { params = await (await fetch("/api/params")).json(); } catch (e) { /* noop */ }
  if (!state.dictList.length) {
    try { const j = await (await fetch("/api/dictionary")).json(); state.dictList = j.items; } catch (e) { /* noop */ }
  }
  const pw = params?.pillar_weights || {};
  const W = [[ZH`長期分數權重`, pw.long_term], [ZH`短期分數權重(美股)`, pw.short_term_us], [ZH`短期分數權重(台股)`, pw.short_term_tw]];
  const D = {
    flow: { title: ZH`建議使用流程`, sections: [{ items: FLOW.map(([t, d], i) => ({ tag: String(i + 1), t, d })) }] },
    pipe: { title: ZH`從資料到等級的流程`, desc: ZH`開源專案裡的模型白皮書有完整說明。`, sections: [{ p: [PIPE] }] },
    data: { title: ZH`資料來源`, desc: ZH`只用免費、公開的資料;每個來源都有快取,來源暫時失敗時會改用快取或標示缺少的資料。`,
      sections: [{ items: DATA.map(([t, d]) => ({ t, d })) }] },
    principles: { title: ZH`設計原則`, sections: [{ items: PRINCIPLES.map((t, i) => ({ tag: `(${i + 1})`, t })) }] },
    scoring: { title: ZH`評分方式`, sections: [{ p: [SCORING] }] },
    v2: { title: ZH`長期模型等級規則`, desc: ZH`目前使用的等級規則。美股畫面上一律不評等。(研究假設代號 P2-H10)`, sections: [{ p: [V2_RULE] }, { h: ZH`備用規則(還沒有分位切點檔時暫時沿用)`, p: [OLD_RULE] }] },
    weights: { title: ZH`面向權重`, desc: ZH`模型 ${params?.version || "—"}。權重寫在模型參數檔;任何改動都要先事先登記研究假設、通過正式檢定。`,
      sections: W.map(([h, w]) => ({ h, rows: Object.entries(w || {}).map(([k, v]) => [PN[k] || k, String(v)]) })) },
  };
  el.innerHTML = ZH`<div class="u-ov">
    <h1 class="u-largetitle">方法與資料</h1>
    <p class="u-sub" style="margin:-6px 0 14px">看懂模型怎麼打分數、等級怎麼來、資料從哪裡來,以及每個指標的意思。</p>
    <div class="u-grid">
      <button type="button" class="u-card u-kpi" data-d="pipe"><span class="u-label">模型版本</span><span class="u-verdict"><b class="u-num">${esc(String(params?.version || "—").replace(/^JW-MF\s*/i, "").replace(/-dev$/, ""))}</b></span>
        <span class="u-sub">開發時的研究基準模型 · 點開看從資料到等級的流程</span></button>
      <button type="button" class="u-card u-kpi" data-d="v2"><span class="u-label">長期模型等級方式</span><span class="u-verdict"><b>固定分位</b></span>
        <span class="u-sub">長期分數在股票池的分位:前 10% / 20% / 40% / 20% / 10%</span></button>
      <button type="button" class="u-card u-kpi" data-jump="dict"><span class="u-label">指標字典</span><span class="u-verdict"><b class="u-num">${state.dictList.length} 個</b></span>
        <span class="u-sub">公式、意義、判斷、出處</span></button>
    </div>
    <h2 class="u-label u-list-h">章節</h2>
    <ul class="u-list">
      ${UI.row({ title: ZH`建議使用流程`, sub: ZH`建立模型 → 掃描 → 研究 → 挑戰假設 → 誠實驗證`, data: { d: "flow" } })}
      ${UI.row({ title: ZH`從資料到等級的流程`, sub: ZH`資料、指標、面向、估值、等級`, data: { d: "pipe" } })}
      ${UI.row({ title: ZH`資料來源`, sub: ZH`${DATA.length} 個免費公開來源`, data: { d: "data" } })}
      ${UI.row({ title: ZH`設計原則`, sub: ZH`${PRINCIPLES.length} 條`, data: { d: "principles" } })}
      ${UI.row({ title: ZH`評分方式`, sub: ZH`指標轉成 0–100`, data: { d: "scoring" } })}
      ${UI.row({ title: ZH`長期模型等級規則`, sub: ZH`固定分位與備用規則`, data: { d: "v2" } })}
      ${UI.row({ title: ZH`面向權重`, sub: ZH`長期、短期(美股 / 台股)`, data: { d: "weights" } })}
      ${UI.row({ title: ZH`指標字典`, sub: ZH`${state.dictList.length} 個指標,在下方`, data: { jump: "dict" } })}
    </ul>
    <h2 class="u-label u-list-h" id="dict">指標字典</h2>
    <div class="u-card">
      <input id="dq" class="u-input" type="search" aria-label="搜尋指標" placeholder="搜尋指標…">
      <ul class="u-list u-mobile-only" id="dl" style="margin:0"></ul>
      <div class="tbl-wrap u-desk-only"><table id="dt"></table></div>
    </div>
    <p class="u-disclaimer">這頁說明模型怎麼算,不代表模型有效,也不構成投資建議;研究結果請看「研究紀錄」頁。開源版不提供內建模型的個股評等,分數只來自你自己的模型。</p>
  </div>`;
  el.querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => { const x = D[b.dataset.d]; if (x) UI.openDetail(x); }));
  el.querySelectorAll("[data-jump]").forEach((b) => (b.onclick = () => el.querySelector("#" + b.dataset.jump)?.scrollIntoView({ behavior: "smooth", block: "start" })));
  const dt = el.querySelector("#dt"), dl = el.querySelector("#dl");
  let shown = [];
  const draw = (q) => {
    shown = state.dictList.filter((d) => !q || (d.name + d.category + d.formula + d.meaning).toLowerCase().includes(q.toLowerCase()));
    dt.innerHTML = ZH`<tr><th>類別</th><th>指標</th><th>公式</th><th>意義</th><th>判斷</th><th>出處</th></tr>` +
      shown.map((d) => `<tr><td>${esc(d.category)}</td><td><b>${esc(d.name)}</b></td><td style="white-space:normal;font-family:var(--mono);font-size:12px;min-width:220px">${esc(d.formula)}</td>
        <td style="white-space:normal;min-width:200px">${esc(d.meaning)}</td><td style="white-space:normal;min-width:200px">${esc(d.judge)}</td><td style="white-space:normal;font-size:12px">${esc(d.source)}</td></tr>`).join("");
    dl.innerHTML = shown.map((d, i) => UI.row({ title: d.name, sub: d.category, data: { i: String(i) } })).join("")
      || ZH`<li class="u-row u-sub">沒有符合的指標</li>`;
    dl.querySelectorAll("[data-i]").forEach((b) => (b.onclick = () => { const d = shown[+b.dataset.i]; if (!d) return;
      UI.openDetail({ title: d.name, desc: d.category, sections: [{ h: ZH`公式`, p: [d.formula] }, { h: ZH`意義`, p: [d.meaning] }, { h: ZH`判斷`, p: [d.judge] }, { h: ZH`出處`, p: [d.source] }] }); }));
  };
  draw("");
  el.querySelector("#dq").oninput = (e) => draw(e.target.value);
}
