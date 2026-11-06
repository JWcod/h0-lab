// 元件展示頁(web/design.html)的示範程式。CSP 不允許內嵌 script,所以獨立成檔。
import * as UI from "./ui.js";
const $ = (s) => document.querySelector(s);
$("#evs").innerHTML = Object.keys(UI.EVIDENCE).map((k) => UI.evidence(k)).join("");
const f0 = (v) => v.toLocaleString("en-US", { maximumFractionDigits: 0 });
$("#ranges").innerHTML = [
  ["區間內", { lo: 2129, mid: 2880, hi: 3474, price: 2440 }],
  ["低於區間", { lo: 2129, mid: 2880, hi: 3474, price: 1900 }],
  ["高於區間", { lo: 91, mid: 104, hi: 118, price: 140 }],
  ["不可得(負盈餘)", { lo: null, mid: null, hi: null, price: 95 }],
].map(([t, r]) => `<button class="u-card" data-d="${t}"><span class="u-label">${t}</span>${UI.rangeBar({ ...r, fmt: f0 })}</button>`).join("");
$("#rings").innerHTML = [["品質", "A", 0.86], ["成長", "A+", 0.93], ["估值", "C+", 0.48], ["安全", null, null]].map((r) => UI.ring(...r)).join("");
$("#cis").innerHTML = [
  ["含 0", 0.017, -0.016, 0.05, {}], ["整段 < 0", -0.03, -0.05, -0.01, {}], ["不含 0、未校正", 0.04, 0.01, 0.07, {}],
  ["不含 0、已校正", 0.04, 0.01, 0.07, { proven: true }], ["樣本不足", null, null, null, {}],
].map(([t, m, lo, hi, o]) => { const s = UI.ciStatus(lo, hi, o); return `<div style="display:flex;justify-content:space-between;gap:8px;align-items:center;margin-top:8px"><span>${t}</span>${UI.evidence(s.status, s.long)}</div>${UI.ciBar(m, lo, hi, { label: t })}`; }).join("");
$("#list").innerHTML = [
  UI.row({ title: "估值模型", data: { d: "估值模型" } }),
  UI.row({ title: "技術與籌碼", ev: "unval", data: { d: "技術與籌碼" } }),
  UI.row({ title: "<script>alert(1)</script>", sub: "跳脫測試:應該原樣顯示", data: { d: "跳脫" } }),
].join("");
$("#steps").innerHTML = UI.steps(["資料", "財報", "技術", "新聞", "估值", "結論"]);
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-d]");
  if (t) UI.openDetail({ title: t.dataset.d, desc: "細節面板:電腦在右側,手機從底部彈出。", rows: [["示意", "123"], ["區間", "1 – 2"]] });
  const th = e.target.closest("[data-theme]");
  if (th) {
    document.querySelectorAll("[data-theme]").forEach((b) => b.setAttribute("aria-pressed", String(b === th)));
    if (th.dataset.theme) document.documentElement.dataset.theme = th.dataset.theme; else delete document.documentElement.dataset.theme;
  }
});
$("#runSteps").onclick = () => {
  const root = $("#steps");
  [0, 1, 2, 3, 4, 5].forEach((i) => UI.setStep(root, i, "wait"));
  [0, 1, 2, 3, 4, 5].forEach((i) => {
    setTimeout(() => UI.setStep(root, i, "run"), i * 500);
    setTimeout(() => UI.setStep(root, i, i === 3 ? "fail" : "done"), i * 500 + 450);
  });
};
