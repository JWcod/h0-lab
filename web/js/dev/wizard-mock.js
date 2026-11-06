// 開發用假資料(只在開發時看畫面用;預設關閉,見 web/js/wizardapi.js 的 MOCK_KEY)。
// 這裡的範本、訊號、排名、回測數字全部是假的,不是任何真實資料或模型輸出。
// 形狀照 docs/上手精靈規格.md §3.3、§4.1、§5、§8 寫,S1 / S3 後端完成後以後端為準。

const PILLARS = ["quality", "growth", "valuation", "health", "momentum", "flows", "revisions", "sentiment"];

const TEMPLATES = [
  { id: "sample", order: 1, markets: ["TW", "US"], title: "範例模型(綜合)", for_whom: "不知道選什麼的人", focus: "品質、成長、估值、財務安全各佔四分之一",
    ignores: "不特別偏重哪一個面向", rationale: "照現行範例模型的設定。", sources: ["JW-MF v2.0"], requires_vars: [],
    lt: { quality: 25, growth: 25, valuation: 25, health: 25 } },
  { id: "value", order: 2, markets: ["TW", "US"], title: { "zh-TW": "價值" }, for_whom: { "zh-TW": "想找便宜貨的人" }, focus: { "zh-TW": "估值為主、財務安全次之" },
    ignores: { "zh-TW": "不看短期漲跌" }, rationale: { "zh-TW": "便宜的股票長期平均表現較好的文獻很多,但可能等很久。" }, sources: ["Fama–French (1992)", "Graham"], requires_vars: [],
    lt: { valuation: 50, health: 30, quality: 20 } },
  { id: "quality", order: 3, markets: ["TW", "US"], title: "品質護城河", for_whom: "想抱好公司的人", focus: "品質為主、財務安全次之", ignores: "不看短期漲跌",
    rationale: "獲利能力高、穩定的公司。", sources: ["Novy-Marx (2013)", "Asness 等 (2019)"], requires_vars: [], lt: { quality: 55, health: 30, valuation: 15 } },
  { id: "growth", order: 4, markets: ["TW", "US"], title: "成長", for_whom: "追營收、獲利成長的人", focus: "成長為主、品質次之", ignores: "估值看得比較輕",
    rationale: "營收與獲利成長。", sources: ["Lakonishok 等 (1994)"], requires_vars: [], lt: { growth: 55, quality: 30, valuation: 15 } },
  { id: "momentum", order: 5, markets: ["TW", "US"], title: "趨勢動能", for_whom: "順勢操作的人", focus: "技術動能為主", ignores: "不看估值",
    rationale: "過去 12 個月漲多的股票之後一段時間傾向延續。", sources: ["Jegadeesh–Titman (1993)"], requires_vars: [], lt: { momentum: 70, quality: 30 } },
  { id: "flows_tw", order: 6, markets: ["TW"], title: "籌碼", for_whom: "看法人、大戶動向的人", focus: "籌碼為主、動能次之", ignores: "估值、品質看得比較輕",
    rationale: "台股法人籌碼實證研究。", sources: ["台股法人籌碼實證研究"], requires_vars: [], lt: { flows: 60, momentum: 40 } },
  { id: "dividend", order: 7, markets: ["TW", "US"], title: "存股高股息", for_whom: "要穩定現金流的人", focus: "殖利率、配息穩定、財務安全", ignores: "不看短期漲跌",
    rationale: "需要新的股利資料。", sources: [], requires_vars: ["dividend_yield_ttm", "div_paid_years_5y"], lt: { health: 100 } },
  { id: "lowvol", order: 8, markets: ["TW", "US"], title: "穩健低波動", for_whom: "怕大跌的人", focus: "低波動、低 beta、財務安全", ignores: "不看動能",
    rationale: "低波動股票長期風險調整後報酬不差。", sources: ["Frazzini–Pedersen (2014)"], requires_vars: ["vol_1y", "beta_2y"], lt: { health: 100 } },
];

const CATALOG = [
  ["roic", "quality", "用錢賺錢的效率", "公司每投入 100 元資本,一年能賺回多少。", "數字越高越好", "all", true],
  ["gross_margin", "quality", "毛利率", "賣東西扣掉成本後還剩多少,看產品有沒有議價能力。", "數字越高越好", "all", true],
  ["fcf_margin", "quality", "自由現金流率", "營收裡真正變成現金、可以自由運用的比例。", "數字越高越好", "all", true],
  ["rev_growth", "growth", "營收成長", "近一年營收比前一年多了多少。", "數字越高越好", "all", true],
  ["eps_growth", "growth", "獲利成長", "每股盈餘比前一年多了多少。", "數字越高越好", "all", true],
  ["mrev_yoy", "growth", "月營收年增", "最近 3 個月營收和去年同期比。", "數字越高越好", "TW", true],
  ["pe_pct", "valuation", "本益比在歷史的位置", "現在的本益比在自己過去 5 年裡算便宜還是貴。", "數字越低越好", "all", true],
  ["fcf_yield", "valuation", "自由現金流殖利率", "用現在的股價買下整家公司,每年能拿回多少現金。", "數字越高越好", "all", true],
  ["debt_ebitda", "health", "負債壓力", "要幾年的本業獲利才還得完淨負債。", "數字越低越好", "all", true],
  ["current_ratio", "health", "短期償債能力", "一年內要付的錢,手上的流動資產夠不夠。", "接近 2 最好", "all", true],
  ["ret_12_1", "momentum", "過去一年漲幅", "扣掉最近一個月,過去 12 個月漲了多少。", "數字越高越好", "all", true],
  ["vs_ma200", "momentum", "相對 200 日線", "股價比長期平均線高還是低。", "數字越高越好", "all", true],
  ["inst_net_20d", "flows", "法人近 20 日買賣超", "外資、投信、自營商最近一個月的淨買賣。", "數字越高越好", "TW", false],
  ["big_holder", "flows", "大戶持股變化", "持有 400 張以上的股東持股比例變化。", "數字越高越好", "TW", false],
  ["eps_rev", "revisions", "分析師預期修正", "最近分析師把獲利預估往上還是往下調。", "數字越高越好", "all", false],
  ["news_tone", "sentiment", "新聞語氣", "最近新聞標題偏正面還是偏負面。", "數字越高越好", "all", false],
].map(([key, pillar, plain_name, one_liner, good_when, mk, backtestable]) => ({
  key, pillar, plain_name: { "zh-TW": plain_name }, one_liner: { "zh-TW": one_liner }, good_when: { "zh-TW": good_when },
  markets: mk === "all" ? ["TW", "US"] : [mk], backtestable,
}));

// 範本裡各訊號的原權重(假的):面向有用到的訊號給 2 或 3,其他 0
function tplModel(tpl, market) {
  const metrics = CATALOG.filter((c) => c.markets.includes(market)).map((c, i) => ({
    key: c.key, pillar: c.pillar, market: c.markets.length === 2 ? "all" : market, enabled: true,
    label: c.plain_name["zh-TW"], weight: (tpl.lt[c.pillar] || 0) > 0 ? 2 + (i % 2) : 0,
    value: { var: c.key }, score: { type: "linear", bad: 0, good: 1 },
  }));
  return {
    schema: "h0-model/1", id: `tpl-${tpl.id}`, family_id: `tpl-${tpl.id}`, name: loc0(tpl.title), based_on: `template:${tpl.id}`,
    notes: "", pillar_weights: { long_term: { ...tpl.lt }, short_term_tw: { momentum: 50, flows: 50 }, short_term_us: { momentum: 100 } },
    short_term_thresholds: { bullish: 60, bearish: 40 }, metrics,
  };
}
const loc0 = (v) => (typeof v === "string" ? v : v["zh-TW"]);
const delay = (ms) => new Promise((r) => setTimeout(r, ms));
const MULT = { off: 0, normal: 1, high: 2 };

function hash(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
function rng(seed) { let x = seed || 1; return () => { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return ((x >>> 0) % 100000) / 100000; }; }
function gradeOf(s) { return s >= 85 ? "A+" : s >= 75 ? "A" : s >= 65 ? "B+" : s >= 55 ? "B" : s >= 45 ? "C+" : s >= 35 ? "C" : s >= 25 ? "D" : "F"; }

const STOCKS = {
  TW: ["2330 台積電", "2317 鴻海", "2454 聯發科", "2308 台達電", "2382 廣達", "2881 富邦金", "2882 國泰金", "2412 中華電", "2891 中信金", "3711 日月光投控",
    "2303 聯電", "1301 台塑", "2886 兆豐金", "2002 中鋼", "1216 統一", "2884 玉山金", "3008 大立光", "2357 華碩", "2885 元大金", "2892 第一金",
    "6505 台塑化", "2345 智邦", "3034 聯詠", "2379 瑞昱", "2395 研華", "4938 和碩", "1303 南亞", "5880 合庫金", "2880 華南金", "2603 長榮",
    "<script>測試</script>"].map((x) => { const [c, n] = x.split(" "); return { symbol: `${c}.TW`, name: n || c }; }),
  US: ["AAPL Apple", "MSFT Microsoft", "NVDA NVIDIA", "AMZN Amazon", "GOOGL Alphabet", "META Meta", "AVGO Broadcom", "TSLA Tesla", "BRK-B Berkshire", "JPM JPMorgan",
    "LLY Eli Lilly", "V Visa", "UNH UnitedHealth", "XOM Exxon", "MA Mastercard", "COST Costco", "HD Home Depot", "PG P&G", "JNJ J&J", "MU Micron",
    "ORCL Oracle", "ABBV AbbVie", "WMT Walmart", "NFLX Netflix", "KO Coca-Cola"].map((x) => { const i = x.indexOf(" "); return { symbol: x.slice(0, i), name: x.slice(i + 1) }; }),
};

export async function templates(market) {
  await delay(150);
  return { market, templates: TEMPLATES.filter((x) => x.markets.includes(market)).map(({ lt, ...x }) => ({ ...x, ready: !x.requires_vars.length })) };
}

export async function catalog(market) {
  await delay(150);
  return { market, items: CATALOG.filter((c) => c.markets.includes(market)) };
}

export async function preview(body) {
  await delay(250);
  const tpl = TEMPLATES.find((x) => x.id === body.template);
  if (!tpl) throw new Error(`找不到範本 ${body.template}`);
  if (tpl.requires_vars.length) throw new Error("這個範本還在準備中");
  const model = tplModel(tpl, body.market);
  const ch = body.choices || {};
  for (const m of model.metrics) {
    const lv = ch.signals?.[m.key];
    if (!lv) continue;
    m.weight = lv === "off" ? 0 : (m.weight || 1) * MULT[lv];
  }
  const lt = model.pillar_weights.long_term;
  for (const p of PILLARS) if (ch.pillars?.[p] && lt[p] !== undefined) lt[p] *= MULT[ch.pillars[p]];
  const tot = Object.values(lt).reduce((a, b) => a + b, 0);
  if (!tot) return { ok: false, error: "至少要看一個面向", model, ranking: null };
  for (const p of Object.keys(lt)) lt[p] = Math.round((lt[p] / tot) * 1000) / 1000;
  const sig = JSON.stringify([model.pillar_weights.long_term, model.metrics.map((m) => m.weight)]);
  const h = hash(sig);
  const r = rng(h);
  const rows = STOCKS[body.market].map((s) => { const sc = Math.round(r() * 1000) / 10; return { ...s, score: sc, grade: gradeOf(sc) }; })
    .sort((a, b) => b.score - a.score);
  return {
    ok: true, score_hash: h.toString(16).padStart(8, "0").slice(0, 12), model,
    ranking: { as_of: "2026-10-30", universe: body.market === "TW" ? "tw_pit50" : "us_large40", n: rows.length, rows },
  };
}

const saved = new Map();   // 假的「我的模型」(只在這個分頁的記憶體裡)
const trials = new Map();
export async function listModels() {
  await delay(80);
  return { models: [...saved.values()].map((m) => ({ id: m.id, name: m.name, family_id: m.id, source: "private", n_trials: trials.get(m.id)?.size })) };
}
export async function saveModel(model, opts = {}) {
  await delay(150);
  if (opts.auto_id) {
    model = { ...model, id: `${String(model.wizard?.template || "my-model").replace(/_/g, "-")}-20261102-${Math.random().toString(16).slice(2, 8)}` };
    if (!String(model.name || "").trim()) model.name = "我的範本";
  } else if (opts.create_only && saved.has(model.id)) throw new Error(`模型代號 ${model.id} 已經有人用了(含已封存的模型),請換一個`);
  if (!/^[a-z0-9][a-z0-9-]{0,47}$/.test(model.id || "")) throw new Error("模型代號只能用小寫英文、數字、連字號(最多 48 字)");
  saved.set(model.id, JSON.parse(JSON.stringify(model)));
  return { saved: { id: model.id, name: model.name, score_hash: hash(JSON.stringify(model.metrics)).toString(16).slice(0, 12) } };
}
export async function backtest(id, universe) {
  await delay(1200);
  const m = saved.get(id);
  if (!m) throw new Error(`找不到模型 ${id}`);
  const h = hash(JSON.stringify([m.pillar_weights, m.metrics.map((x) => x.weight)]));
  if (!trials.has(id)) trials.set(id, new Set());
  trials.get(id).add(h);
  const r = rng(h);
  const dates = [], top = [], pool = [], market = [];
  let a = 1, b = 1, c = 1, wins = 0, n = 0;
  for (let y = 2016; y <= 2024; y++) {
    for (let mo = 1; mo <= 12; mo++) {
      if (y === 2024 && mo > 9) break;
      const mk = (r() - 0.45) * 0.08, pl = mk + (r() - 0.5) * 0.02, tp = pl + (r() - 0.5) * 0.03;
      a *= 1 + tp; b *= 1 + pl; c *= 1 + mk; n++; if (tp > pl) wins++;
      dates.push(`${y}-${String(mo).padStart(2, "0")}`); top.push(a - 1); pool.push(b - 1); market.push(c - 1);
    }
  }
  const yrs = n / 12, cagr = (x) => Math.pow(1 + x, 1 / yrs) - 1;
  const mdd = (arr) => { let pk = 1, dd = 0; for (const v of arr) { pk = Math.max(pk, 1 + v); dd = Math.min(dd, (1 + v) / pk - 1); } return dd; };
  const ic = { ic: 0.012, ci95: [-0.01, 0.034], p: 0.29, p_bonferroni: Math.min(1, 0.29 * trials.get(id).size), verdict: "沒有證實有效",
    first_month: "2016-01-01", last_month: "2023-09-01", n_months: 93 };
  return {
    status: "開發集", universe, score_hash: h.toString(16).slice(0, 12), seconds: 1.2, n_trials: trials.get(id).size,
    multi_note: `這個模型族試了 ${trials.get(id).size} 個設定(假資料)`,
    metrics: { lt_12m: { mine: ic, shipped: { ...ic, ic: 0.008 }, mine_vs_shipped: { diff: 0.004, ci95: [-0.02, 0.028], n_months: 93, verdict: "和範例模型沒有差別" },
      random: { draws: 30, median: 0.0, p2_5: -0.03, p97_5: 0.031, mine_share_above: 0.7, note: "假資料只給分布" } } },
    holdout: { viewed: false, family_viewed: false, clean: true, label: "還沒看過" },
    disclaimer: "假資料:回測結果不代表未來報酬,非投資建議",
    plain: {
      start: dates[0], end: dates[dates.length - 1],
      curve: { dates, top, pool, market },
      stats: { top: { cagr: cagr(a - 1), max_drawdown: mdd(top) }, pool: { cagr: cagr(b - 1), max_drawdown: mdd(pool) }, market: { cagr: cagr(c - 1), max_drawdown: mdd(market) } },
      win_rate: wins / n, n_months: n,
      random: { median: cagr(b - 1), p2_5: cagr(b - 1) - 0.04, p97_5: cagr(b - 1) + 0.04 },
      cost_one_way: 0.003, ic_ci_includes_zero: true,
    },
  };
}
