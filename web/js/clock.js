// 市場時鐘(規格 §7):工具列顯示台股、美股目前是開盤中 / 盤前 / 盤後 / 已收盤。
// 只用電腦時間與一般交易時間計算(Intl 換算時區,不連網),不含國定假日與颱風假,畫面上會註明。
import { ZH } from "./i18n/index.js";
const MARKETS = [
  { id: "TW", name: ZH`台股`, tz: "Asia/Taipei", open: 9 * 60, close: 13 * 60 + 30 },
  { id: "US", name: ZH`美股`, tz: "America/New_York", open: 9 * 60 + 30, close: 16 * 60, pre: 4 * 60, post: 20 * 60 },
];

// 某時區的星期(0 = 週日)與當天分鐘數
function localParts(now, tz) {
  const f = new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const p = Object.fromEntries(f.formatToParts(now).map((x) => [x.type, x.value]));
  const dow = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(p.weekday);
  return { dow, min: (+p.hour % 24) * 60 + +p.minute };
}

const dur = (m) => (m >= 60 ? ZH`${Math.floor(m / 60)} 小時${m % 60 ? ZH` ${m % 60} 分` : ""}` : ZH`${m} 分`);

// 回傳 { state: "open"|"pre"|"post"|"closed", label, detail }
export function marketStatus(m, now = new Date()) {
  const { dow, min } = localParts(now, m.tz);
  const weekday = dow >= 1 && dow <= 5;
  if (weekday && min >= m.open && min < m.close) return { state: "open", label: ZH`開盤中`, detail: ZH`${dur(m.close - min)}後收盤` };
  if (weekday && m.pre !== undefined && min >= m.pre && min < m.open) return { state: "pre", label: ZH`盤前`, detail: ZH`${dur(m.open - min)}後開盤` };
  if (weekday && m.post !== undefined && min >= m.close && min < m.post) return { state: "post", label: ZH`盤後`, detail: ZH`今日已收盤` };
  if (weekday && min < m.open) return { state: "closed", label: ZH`未開盤`, detail: ZH`${dur(m.open - min)}後開盤` };
  return { state: "closed", label: ZH`已收盤`, detail: dow === 5 || dow === 6 || dow === 0 ? ZH`下個交易日開盤` : ZH`明天開盤` };
}

export function mount(box) {
  if (!box) return;
  const draw = () => {
    box.innerHTML = MARKETS.map((m) => {
      const s = marketStatus(m);
      return ZH`<span class="mk-clock is-${s.state}" title="${m.name}:${s.label},${s.detail}(依一般交易時間計算,不含國定假日)"><span class="mk-dot" aria-hidden="true"></span>${m.name} ${s.label}</span>`;
    }).join("");
  };
  draw();
  setInterval(draw, 30000);
}

export const _test = { MARKETS, localParts };
