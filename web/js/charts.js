// ECharts 共用設定。顏色跟著目前主題(深色 / 淺色)從 CSS 變數讀,每次畫圖時重新讀,切換主題後重畫就會更新。
import { chartNum } from "./fmt.js";

const E = window.echarts;
const instances = new Set();

// 讀 :root 上的 CSS 變數;讀不到(例如在 node 測試裡)就用深色預設值
function cssVar(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch (e) { return fallback; }
}
export const isLight = () => { try { return getComputedStyle(document.documentElement).colorScheme.trim() === "light"; } catch (e) { return false; } };

// 系列顏色:前兩色是圖表主色(藍 --link、青 --brand2,從 token 讀),其餘深色用亮色、淺色用較深的同色系(白底上線條要看得清楚)
const PAL_DARK = ["#9b8cff", "#ffd166", "#ff8a5c", "#f472b6", "#a3e635", "#fda4af", "#3ee0c5"];
const PAL_LIGHT = ["#5b3fd0", "#8a6100", "#c03d00", "#b8206e", "#4d7c0f", "#b4232f", "#007a66"];
export const PALETTE = ["#8ab4ff", "#22d3ee", ...PAL_DARK];
function syncPalette() {
  const src = [cssVar("--link", "#8ab4ff"), cssVar("--brand2", "#22d3ee"), ...(isLight() ? PAL_LIGHT : PAL_DARK)];
  src.forEach((c, i) => { PALETTE[i] = c; });
}

// 各頁寫死的深色系列色(亮色線條),淺色模式下換成同色系的深色版;只換完全相同的色碼與 rgba,其他不動
const TO_LIGHT = { "#5ab0ff": "#0060df", "#9b8cff": "#5b3fd0", "#3ee0c5": "#007a66", "#ffd166": "#8a6100", "#ff8a5c": "#c03d00", "#f472b6": "#b8206e",
  "#7dd3fc": "#0077a8", "#a3e635": "#4d7c0f", "#fda4af": "#b4232f", "#ffa94d": "#a03c00", "#ff7b6b": "#c0000f",
  "#d9e6ff": "#1d2b4a", "#4b6aa8": "#2f4f8f" };
const RGB_TO_LIGHT = { "90,176,255": "0,96,223", "62,224,197": "0,122,102", "255,209,102": "138,97,0", "255,138,92": "192,61,0",
  "155,140,255": "91,63,208", "255,169,77": "160,60,0", "125,211,252": "0,119,168", "255,123,107": "192,0,15", "185,210,255": "29,43,74" };
function lightColor(v) {
  const k = v.toLowerCase();
  if (TO_LIGHT[k]) return TO_LIGHT[k];
  const m = k.match(/^rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)$/);
  if (m && RGB_TO_LIGHT[`${m[1]},${m[2]},${m[3]}`]) return `rgba(${RGB_TO_LIGHT[`${m[1]},${m[2]},${m[3]}`]},${m[4]})`;
  return v;
}
function toLight(o, depth = 0) {
  if (depth > 12 || o === null) return o;
  if (typeof o === "string") return o.startsWith("#") || o.startsWith("rgba(") ? lightColor(o) : o;
  if (Array.isArray(o)) { for (let i = 0; i < o.length; i++) if (o[i] !== null && typeof o[i] !== "number") o[i] = toLight(o[i], depth + 1); return o; }
  if (typeof o === "object" && Object.getPrototypeOf(o) === Object.prototype) {
    for (const k of Object.keys(o)) {
      if (k === "color" && typeof o[k] === "function") {   // 用函式決定的顏色(例:足球場圖)也要換淺色
        const f = o[k];
        o[k] = (...args) => { const r = f(...args); return typeof r === "string" ? lightColor(r) : r; };
      } else o[k] = toLight(o[k], depth + 1);
    }
  }
  return o;
}

// C 的每個顏色都是 getter:畫圖當下讀主題
const VARS = { text: ["--text", "#f4f7ff"], text2: ["--text2", "#d9e6ff"], label: ["--label", "#b9d2ff"], line: ["--line", "#233457"],
  grid: ["--chart-grid", "#1b2a4a"], accent: ["--accent", "#5ab0ff"], accent2: ["--accent2", "#9b8cff"], pos: ["--pos", "#3ee0c5"],
  neg: ["--neg", "#ff8a5c"], watch: ["--watch", "#ffd166"], tip: ["--chrome-glass", "rgba(10,13,20,.72)"],
  sep: ["--sep", "#273145"], link: ["--link", "#8ab4ff"], brand2: ["--brand2", "#22d3ee"], blue: ["--blue", "#4f7cff"], panel2: ["--panel2", "#1b2231"] };
export const C = {};
for (const [k, [name, fb]] of Object.entries(VARS)) Object.defineProperty(C, k, { get: () => cssVar(name, fb), enumerable: true });
const FONT = () => cssVar("--font", "-apple-system, PingFang TC, sans-serif");
const MONO = () => cssVar("--mono", "SF Mono, ui-monospace, Menlo, monospace");
const reduceMotion = () => { try { return matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; } };

// #rrggbb → rgba(…, a);給選取範圍、十字線標籤這種半透明底用(來源仍是 token)
function alpha(hex, a) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

export function upDown() {
  const cs = getComputedStyle(document.body);
  return { up: cs.getPropertyValue("--up").trim() || "#ff5f6d", down: cs.getPropertyValue("--down").trim() || "#2fe08a" };
}

// 圖表主題:細軸線、虛線格線、數字軸用等寬字、提示框毛玻璃。各頁傳進來的設定優先,主題只補沒設的部分。
const TIP_CSS = "backdrop-filter: blur(14px) saturate(150%); -webkit-backdrop-filter: blur(14px) saturate(150%); border-radius: 10px;"
  + " box-shadow: var(--shadow); font-variant-numeric: tabular-nums; line-height: 1.55;";
function tooltipStyle() {
  return {
    backgroundColor: C.tip, borderColor: C.sep, borderWidth: 1, padding: [8, 12],
    textStyle: { color: C.text, fontSize: 12, fontFamily: FONT() }, extraCssText: TIP_CSS, valueFormatter: chartNum,
  };
}
function pointerLabel() { return { backgroundColor: C.panel2, color: C.text, borderColor: C.sep, borderWidth: 1, fontFamily: MONO(), fontSize: 11, padding: [3, 6] }; }
function axisTheme(value) {
  return {
    axisLine: { show: !value, lineStyle: { color: C.line } },
    axisTick: { show: false, lineStyle: { color: C.line } },
    axisLabel: { color: C.label, fontSize: 11, fontFamily: MONO() },
    splitLine: { show: value, lineStyle: { color: C.grid, type: [3, 3] } },
    splitArea: { show: false },
    nameTextStyle: { color: C.label, fontSize: 11 },
  };
}
function theme() {
  const t = tooltipStyle();
  return {
    color: PALETTE, backgroundColor: "transparent",
    textStyle: { color: C.text, fontFamily: FONT() },
    title: { textStyle: { color: C.text, fontWeight: 650 }, subtextStyle: { color: C.text2 } },
    legend: { textStyle: { color: C.text2, fontSize: 11.5 }, icon: "roundRect", itemWidth: 12, itemHeight: 4, itemGap: 14,
      inactiveColor: C.line, pageTextStyle: { color: C.label }, pageIconColor: C.link, pageIconInactiveColor: C.line },
    tooltip: { ...t, axisPointer: { lineStyle: { color: C.label, type: [4, 3], width: 1 }, crossStyle: { color: C.label, type: [4, 3] },
      shadowStyle: { color: alpha(C.panel2, 0.45) }, label: pointerLabel() } },
    axisPointer: { lineStyle: { color: C.label, type: [4, 3], width: 1 }, label: pointerLabel() },
    categoryAxis: axisTheme(false), timeAxis: axisTheme(false), valueAxis: axisTheme(true), logAxis: axisTheme(true),
    line: { lineStyle: { width: 1.6 }, symbolSize: 4, emphasis: { lineStyle: { width: 2 } } },
    bar: { barMaxWidth: 28 },
    markLine: { label: { color: C.text2, fontFamily: MONO(), fontSize: 11 }, lineStyle: { color: C.label, type: [4, 3] } },
    dataZoom: { borderColor: C.line, backgroundColor: "transparent", fillerColor: alpha(C.blue, 0.14), textStyle: { color: C.label, fontFamily: MONO(), fontSize: 10.5 },
      dataBackground: { lineStyle: { color: C.line }, areaStyle: { color: alpha(C.blue, 0.08) } },
      selectedDataBackground: { lineStyle: { color: C.link }, areaStyle: { color: alpha(C.blue, 0.18) } },
      handleStyle: { color: C.panel2, borderColor: C.link }, moveHandleStyle: { color: C.line }, brushStyle: { color: alpha(C.blue, 0.14) } },
    gauge: { title: { color: C.text2 }, detail: { color: C.text, fontFamily: MONO() } },
  };
}

export function axis(extra = {}) {
  return {
    axisLine: { lineStyle: { color: C.line } },
    axisTick: { show: false, lineStyle: { color: C.line } },
    axisLabel: { color: C.label, fontSize: 11, fontFamily: MONO() },
    splitLine: { lineStyle: { color: C.grid, type: [3, 3] } },
    nameTextStyle: { color: C.label, fontSize: 11 },
    ...extra,
  };
}

export function base(extra = {}) {
  syncPalette();
  return {
    backgroundColor: "transparent",
    color: PALETTE,
    textStyle: { color: C.text, fontFamily: FONT() },
    grid: { left: 56, right: 24, top: 36, bottom: 36 },
    legend: { textStyle: { color: C.text2, fontSize: 11.5 }, top: 4, icon: "roundRect", itemWidth: 12, itemHeight: 4, itemGap: 14 },
    tooltip: { trigger: "axis", ...tooltipStyle(), axisPointer: { lineStyle: { color: C.label, type: [4, 3] }, crossStyle: { color: C.label } } },
    animationDuration: 320, animationDurationUpdate: 200, animationEasing: "cubicOut",
    ...extra,
  };
}

export function chart(el, option) {
  if (!el || !E) return null;
  syncPalette();
  if (isLight()) toLight(option);
  if (reduceMotion()) option.animation = false;
  // 線的顏色只寫在 lineStyle 時,圖例與提示框的小圓點會用系列預設色,和線對不上;這裡補成同一色
  for (const s of [].concat(option.series || [])) {
    const lc = s && s.type === "line" && s.lineStyle && s.lineStyle.color;
    if (typeof lc === "string" && !(s.itemStyle && s.itemStyle.color)) s.itemStyle = { ...(s.itemStyle || {}), color: lc };
  }
  E.registerTheme("h0", theme());   // 每次重讀 token,切換主題後重畫就跟著換
  const old = E.getInstanceByDom(el);
  if (old) { old.dispose(); instances.delete(old); }
  const c = E.init(el, "h0", { renderer: "canvas" });
  c.setOption(option);
  instances.add(c);
  return c;
}

export function disposeAll() {
  for (const c of instances) { try { c.dispose(); } catch (e) { /* noop */ } }
  instances.clear();
}

window.addEventListener("resize", () => { for (const c of instances) c.resize(); });

export const fmtAxisPct = (v) => (v * 100).toFixed(0) + "%";
