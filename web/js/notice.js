// 第一次開啟時顯示一次研究工具告知;按「我了解」後記在瀏覽器與本機設定(private/settings.json)。沒有帳號、不收資料。
import { ZH } from "./i18n/index.js";

const LS_KEY = "h0lab.notice";

function saveLocal(v) {
  try { localStorage.setItem(LS_KEY, String(v)); } catch (e) { /* 本機設定還有一份 */ }
}

function show(version) {
  const box = document.createElement("div");
  box.className = "u-notice-back";
  box.innerHTML = ZH`<div class="u-notice" role="dialog" aria-modal="true" aria-labelledby="noticeT">
    <h2 id="noticeT">使用前請先了解</h2>
    <ul>
      <li><b>研究用途</b>:H0 Lab 是開源的研究工具,用公開資料做統計分析、模型實驗與歷史回測。</li>
      <li><b>不是投資建議</b>:沒有內建評等;分數與等級只來自你自己建立的模型。分數、估值與回測都是模型輸出,不構成對任何證券的買賣建議。</li>
      <li><b>不是證券投資顧問</b>:本工具與開發者都不是證券投資顧問,也不提供個別化的投資意見。</li>
      <li><b>資料可能有誤</b>:資料來自免費公開來源,可能延遲、缺漏或錯誤,請自行查證。</li>
      <li><b>風險自負</b>:任何投資決定都由你自己判斷並承擔風險;過去表現不代表未來,也不保證任何報酬。</li>
    </ul>
    <p class="u-sub">所有計算都在你自己的電腦上,不需要帳號,也不會收集你的資料。</p>
    <button type="button" class="u-btn is-primary" id="noticeOk">我了解</button>
  </div>`;
  document.body.appendChild(box);
  const ok = box.querySelector("#noticeOk");
  ok.focus();
  ok.onclick = () => {
    saveLocal(version);
    box.remove();
    fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "invest-lab" },
      body: JSON.stringify({ notice_ack: true }) }).catch(() => {});
  };
}

export async function init() {
  let s = null;
  try { s = await (await fetch("/api/settings")).json(); } catch (e) { /* 離線備援:照樣依瀏覽器的紀錄判斷 */ }
  const version = s?.notice_version ?? 1;
  let local = null;
  try { local = localStorage.getItem(LS_KEY); } catch (e) { /* 看本機設定 */ }
  if (s?.notice_ack) { saveLocal(version); return; }
  if (local === String(version)) return;
  show(version);
}
