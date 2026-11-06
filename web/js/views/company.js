import { ZH } from "../i18n/index.js";
import { esc, num, pct, price, big, isNum, safeUrl } from "../fmt.js";

export function render(el, R, ctx) {
  const m = R.meta, q = R.quote, x = R.extra || {};
  const web = safeUrl(m.website);
  el.innerHTML = ZH`
  <div class="grid g3">
    <div class="card span2"><h3>公司簡介 <span class="sub">Yahoo Finance(英文原文)</span></h3>
      <div class="note" style="font-size:13.5px;line-height:1.75;color:var(--text)">${esc(m.summary || ZH`目前沒有公司簡介`)}</div></div>
    <div class="card"><h3>基本資料</h3><div class="kv">
      <span class="k">代號</span><span class="v">${esc(m.symbol)}</span>
      <span class="k">交易所</span><span class="v">${esc(m.exchange || "")}</span>
      <span class="k">產業</span><span class="v">${esc(m.industry_tw || m.industry || "")}</span>
      <span class="k">類股</span><span class="v">${esc(m.sector || "")}</span>
      <span class="k">國家 / 城市</span><span class="v">${esc([m.country, m.city].filter(Boolean).join(" / "))}</span>
      <span class="k">員工數</span><span class="v">${isNum(m.employees) ? m.employees.toLocaleString() : "—"}</span>
      <span class="k">報價幣別 / 財報幣別</span><span class="v">${esc(m.currency)} / ${esc(m.financial_currency)}</span>
      <span class="k">市值</span><span class="v">${big(q.market_cap, m.market)}</span>
      <span class="k">流通股數</span><span class="v">${big(q.shares, "US")}</span>
      <span class="k">平均成交量</span><span class="v">${big(q.avg_volume, "US")}</span>
      ${web ? ZH`<span class="k">網站</span><span class="v"><a href="${esc(web)}" target="_blank" rel="noopener noreferrer">${esc(web.replace(/^https?:\/\//, ""))}</a></span>` : ""}
    </div></div>
  </div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>經營團隊</h3>${m.officers?.length ? ZH`<div class="tbl-wrap"><table><tr><th>姓名</th><th>職稱</th><th class="n">年齡</th><th class="n">年薪</th></tr>
      ${m.officers.map((o) => `<tr><td>${esc(o.name)}</td><td style="white-space:normal">${esc(o.title)}</td><td class="n">${o.age || ""}</td><td class="n">${o.pay ? big(o.pay, "US") : ""}</td></tr>`).join("")}</table></div>` : ZH`<div class="note">目前沒有經營團隊資料</div>`}</div>
    <div class="card"><h3>${m.market === "TW" ? ZH`股利政策(FinMind)` : ZH`配息紀錄`}</h3>${dividends(R)}</div>
  </div>
  ${x.filings?.length ? ZH`<div class="card" style="margin-top:14px"><h3>SEC 申報文件 <span class="sub">10-K 年報、10-Q 季報、8-K 重大事件、Form 4 內部人</span></h3><div class="tbl-wrap"><table>
    <tr><th>日期</th><th>表單</th><th>說明</th><th>連結</th></tr>
    ${x.filings.map((f) => `<tr><td>${esc(f.date)}</td><td>${esc(f.form)}</td><td style="white-space:normal">${esc(f.desc || "")}</td><td>${safeUrl(f.url) ? ZH`<a href="${esc(f.url)}" target="_blank" rel="noopener noreferrer">開啟</a>` : ""}</td></tr>`).join("")}
  </table></div></div>` : ""}
  <div class="card" style="margin-top:14px"><h3>資料來源狀態 <span class="sub">這次分析每個資料來源是否成功(耗時 ${R.meta.elapsed_sec} 秒);有失敗的話,稍後按「重新分析」再試</span></h3><div class="tbl-wrap"><table>
    <tr><th>來源</th><th>項目</th><th>狀態</th><th class="n">筆數</th><th>說明</th></tr>
    ${R.data_status.map((s) => `<tr><td>${esc(s.source)}</td><td>${esc(s.item)}</td><td class="${s.ok ? "pos" : "neg"}">${s.ok ? ZH`成功` : ZH`失敗或無資料`}</td><td class="n">${isNum(s.n) ? s.n : ""}</td><td style="white-space:normal;font-size:12px">${esc(s.error || "")}</td></tr>`).join("")}
  </table></div></div>`;
}

function dividends(R) {
  const x = R.extra || {};
  if (R.meta.market === "TW" && x.dividends?.length) {
    return ZH`<div class="tbl-wrap"><table><tr><th>所屬期間</th><th class="n">現金股利</th><th class="n">股票股利</th><th>除息日</th><th>發放日</th></tr>
      ${x.dividends.slice(0, 16).map((d) => `<tr><td>${esc(d.year)}</td><td class="n">${num(d.cash, 2)}</td><td class="n">${num(d.stock, 2)}</td><td>${esc(d.ex_date || "")}</td><td>${esc(d.pay_date || "")}</td></tr>`).join("")}</table></div>`;
  }
  const h = x.dividend_history || [];
  if (!h.length) return ZH`<div class="note">沒有配息紀錄</div>`;
  return ZH`<div class="tbl-wrap"><table><tr><th>除息日</th><th class="n">每股股利</th></tr>${h.slice().reverse().slice(0, 16).map((d) => `<tr><td>${esc(d.date)}</td><td class="n">${num(d.amount, 4)}</td></tr>`).join("")}</table></div>`;
}
