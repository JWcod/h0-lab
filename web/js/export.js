// 把報告整理成 Markdown(可直接放進 Obsidian 等筆記軟體),只在瀏覽器端產生下載,不經過伺服器
import { ZH } from "./i18n/index.js";
import { pct, price, big, num, isNum } from "./fmt.js";

// 開源版不提供內建模型的評等、八面向分數與綜合公平價(API 也不回傳),匯出只放資料、估值工具結果、見解與筆記。
export function toMarkdown(R) {
  const m = R.meta, v = R.verdict || {}, val = R.valuation || {}, mu = val.multiples || {};
  const L = [];
  L.push(ZH`# ${m.name}(${m.symbol})研究筆記`, "");
  L.push(ZH`- 分析時間:${m.generated_at.replace("T", " ")}`);
  L.push(ZH`- 股價:${price(R.quote.price)} ${m.currency} · 市值 ${big(R.quote.market_cap, m.market)} · 資料完整度 ${v.confidence?.label || "—"} ${v.confidence?.score ?? "—"}`);
  if (v.red_flags?.length) L.push(ZH`- 財務紅旗:${v.red_flags.join(";")}`);
  L.push("", ZH`## 估值工具(預設假設)`, "", ZH`| 方法 | 價值 | 相對現價 |`, "|---|---|---|");
  for (const x of val.methods || []) if (isNum(x.value)) L.push(`| ${x.label} | ${price(x.value)} | ${pct(x.upside, 1, true)} |`);
  if (isNum(val.dcf?.implied_growth)) L.push("", ZH`反向 DCF:以現價反推,市場隱含的起始營收成長約 ${pct(val.dcf.implied_growth)}。`);
  L.push("", ZH`## 關鍵數據`, "");
  const p = R.fundamentals.profitability, g = R.fundamentals.growth, h = R.fundamentals.health;
  L.push(ZH`- 本益比 ${num(mu.pe_ttm, 1)} / 預估本益比 ${num(mu.forward_pe, 1)} / EV/EBITDA ${num(mu.ev_ebitda, 1)} / FCF 殖利率 ${pct(mu.fcf_yield, 1)}`);
  L.push(ZH`- ROIC ${pct(p.roic)} / ROE ${pct(p.roe)} / 毛利率 ${pct(p.gross_margin)} / 營益率 ${pct(p.operating_margin)}`);
  L.push(ZH`- 營收年增 ${pct(g.revenue_yoy)} / EPS 年增 ${pct(g.eps_yoy)} / 營收 3 年 CAGR ${pct(g.revenue_cagr_3y)}`);
  L.push(ZH`- Altman Z ${num(h.altman_z, 2)} / Piotroski ${R.fundamentals.piotroski?.score ?? "—"} / 淨負債/EBITDA ${num(h.net_debt_ebitda, 2)}`);
  L.push("", ZH`## 見解`, "");
  for (const i of R.insights || []) L.push(`- [${i.tone === "pos" ? "+" : i.tone === "neg" ? "−" : "·"}] **${i.title}**${i.detail ? ":" + i.detail : ""}`);
  L.push("", ZH`## 研究檢查清單`, "");
  for (const c of R.checklist || []) L.push(`- [${c.status === "pass" ? "x" : " "}] ${c.text}${c.detail ? "(" + c.detail + ")" : ""}`);
  if (R.notes) {
    L.push("", ZH`## 研究筆記`, "", ZH`**研究假設**:${R.notes.thesis || ""}`, "", ZH`**假設不成立的條件**:${R.notes.sell_rules || ""}`, "",
      ZH`**追蹤重點**:${R.notes.watch || ""}`, "", R.notes.free || "");
  }
  L.push("", ZH`> 由 H0 Lab 自動產生。估值工具的結果依預設假設計算,不是綜合公平價;內容不構成投資建議。`);
  return L.join("\n");
}

export function download(R) {
  const md = toMarkdown(R);
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${R.meta.symbol}_${R.meta.name}_${R.meta.generated_at.slice(0, 10)}.md`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
