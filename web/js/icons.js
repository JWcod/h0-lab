// 全站線性圖示(24 格、1.6px 線寬、圓角端點,顏色跟 currentColor)。
// 規格 docs/介面商業化規格.md §5:取代 ◉ ≡ ✦ 這類文字符號。index.html 的文字符號留著當 JS 失敗時的備援,
// app.js 開機時把 [data-ic] 換成這裡的 SVG。圖示只有裝飾用途,一律 aria-hidden。
const P = {
  home: '<path d="M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-4.5v-6h-5v6H5a1 1 0 0 1-1-1z"/>',
  scan: '<path d="M4 6h16M7 12h10M10 18h4"/>',
  star: '<path d="m12 4 2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.4l-4.8 2.5.9-5.4-3.9-3.8 5.4-.8z"/>',
  wizard: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3M6.3 6.3l2.1 2.1M15.6 15.6l2.1 2.1M6.3 17.7l2.1-2.1M15.6 8.4l2.1-2.1"/><circle cx="12" cy="12" r="2.2"/>',
  models: '<path d="M5 4v16M12 4v16M19 4v16"/><circle cx="5" cy="9" r="2"/><circle cx="12" cy="15" r="2"/><circle cx="19" cy="7" r="2"/>',
  backtest: '<path d="M4 20V4M4 20h16"/><path d="m7 15 4-4 3 3 5-6"/>',
  lab: '<path d="M9 3h6M10 3v6l-5.2 9A2 2 0 0 0 6.5 21h11a2 2 0 0 0 1.7-3L14 9V3"/><path d="M7.5 15h9"/>',
  review: '<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.6"/><path d="M4 4v4.6h4.6M12 8v4l3 2"/>',
  paper: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M3 13h18"/>',
  method: '<path d="M4 5a1 1 0 0 1 1-1h5a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H5a1 1 0 0 1-1-1zM20 5a1 1 0 0 0-1-1h-5a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h5a1 1 0 0 0 1-1z"/>',
  colors: '<path d="M12 4v16"/><path d="m7 9-3 3 3 3M17 9l3 3-3 3"/>',
  theme: '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.4-4.4"/>',
  back: '<path d="m15 5-7 7 7 7"/>',
  forward: '<path d="m9 5 7 7-7 7"/>',
  settings: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l2.5 1.5"/>',
};

export function icon(name, size = 18) {
  const d = P[name];
  if (!d) return "";
  return `<svg class="ic-svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${d}</svg>`;
}

// 把頁面上 [data-ic="名稱"] 的元素換成 SVG(只處理已知名稱;未知的保留原本的文字符號)
export function hydrate(root = document) {
  root.querySelectorAll("[data-ic]").forEach((el) => {
    const svg = icon(el.dataset.ic);
    if (svg) el.innerHTML = svg;
  });
}
