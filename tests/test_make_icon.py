"""App 圖示和 SVG 標誌是同一個圖形,而且 App 與網頁用的是定案版本。"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

import make_icon  # noqa: E402

PX = 512 / 824


def _paths(svg: str) -> list[tuple[str, str]]:
    """SVG 裡每個 path 的 (fill, d),照畫的順序。"""
    tags = re.findall(r"<path\b[^>]*>", svg)
    return [(re.search(r'fill=["\']([^"\']+)', t)[1].upper(), re.search(r' d=["\']([^"\']+)', t)[1]) for t in tags]


def _balanced(text: str, start: int) -> str:
    """從左括號後面開始,取到對應的右括號為止(gradient 可能跨行、裡面還有 rgba())。"""
    depth = 1
    for i in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        if depth == 0:
            return text[start:i]
    return text[start:]


def test_shapes_match_the_svg():
    assert make_icon._pixel(256, 300, PX)[:3] == make_icon.PAPER        # 分布中間
    assert make_icon._pixel(256, 80, PX)[:3] == make_icon.INK           # 峰頂上面是底色
    assert make_icon._pixel(400, 380, PX)[:3] == make_icon.TAIL         # 右尾
    assert make_icon._pixel(120, 380, PX)[:3] == make_icon.PAPER        # 左尾不上色
    assert make_icon._pixel(-40, 256, PX)[3] == 0                       # 圖示外面是透明的
    assert make_icon._pixel(3, 3, PX)[3] == 0                           # 圓角外面也是
    assert make_icon._pixel(256, 3, PX)[3] == 255                       # 邊的中間不透明


def test_tail_is_the_five_percent_rejection_region():
    from statistics import NormalDist
    assert abs(NormalDist().cdf(make_icon.TAIL_Z) - 0.95) < 0.001
    assert make_icon._pixel(make_icon.CENTER + 1.6 * make_icon.SIGMA, 380, PX)[:3] == make_icon.PAPER
    assert make_icon._pixel(make_icon.CENTER + 1.7 * make_icon.SIGMA, 380, PX)[:3] == make_icon.TAIL


def test_web_and_readme_use_the_chosen_logo():
    chosen = (ROOT / "web" / "img" / "h0-logo.svg").read_bytes()
    # 開發 repo 放在 oss/,公開 repo 沒有 oss/,README 用的圖在 .github/assets/
    readme_icon = ROOT / "oss" / "overlay" / ".github" / "assets" / "h0-lab-icon.svg"
    if not readme_icon.exists():
        readme_icon = ROOT / ".github" / "assets" / "h0-lab-icon.svg"
    assert readme_icon.read_bytes() == chosen
    brand = ROOT / "oss" / "brand" / "h0-logo-2c.svg"
    if brand.exists():
        assert brand.read_bytes() == chosen
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    favicon = unquote(index[index.index('<link rel="icon"'):].split('"')[3])
    assert _paths(favicon) == _paths(chosen.decode())   # favicon 和標誌是同一個圖,顏色、形狀、順序都一樣
    assert "/static/img/h0-logo.svg" in index and "E8583B" in index[:index.index("</head>")] and "22d3ee" not in index[:index.index("</head>")]


def test_interface_has_no_blue_cyan_gradient():
    cyan = ("--brand2", "#22d3ee", "#0891b2", "#3ee0c5")
    for css in (ROOT / "web" / "css").glob("*.css"):
        text = css.read_text(encoding="utf-8").lower()
        for m in re.finditer(r"gradient\(", text):
            body = _balanced(text, m.end())
            assert not any(c in body for c in cyan), f"{css.name}: {body[:80]}"
