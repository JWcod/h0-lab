"""產生 App 圖示的原始 PNG,只用標準函式庫。

圖形和 web/img/h0-logo.svg 同一份(viewBox 512):墨黑連續圓角底、紙白常態分布、朱紅右尾(z ≥ 1.645)。
macOS 圖示要留透明邊,所以放進 1024 畫布中央 824 的範圍(Apple 圖示格線)。用距離場算邊緣反鋸齒。
"""
from __future__ import annotations

import bisect
import math
import struct
import zlib
from pathlib import Path

INK, PAPER, TAIL = (0x14, 0x16, 0x1a), (0xf4, 0xf1, 0xea), (0xe8, 0x58, 0x3b)
CONTENT = 824 / 1024
LEFT, RIGHT, TOP, BASE = 52, 460, 116, 388       # 分布的範圍:±3σ、峰頂、底線
CENTER, SIGMA = (LEFT + RIGHT) / 2, (RIGHT - LEFT) / 6
TAIL_Z = 1.645


def _corner_table() -> tuple[list[float], list[float]]:
    """連續圓角(SVG 的 C460 0 512 52 512 158)折到第一象限、取 v ≥ u 那一半:u 遞增時的邊界 v。"""
    p0, p1, p2, p3 = (98, 256), (204, 256), (256, 204), (256, 98)
    us, vs = [], []
    for k in range(201):
        t = k / 400      # 只算到 t=0.5,也就是對角線
        m = 1 - t
        us.append(m**3 * p0[0] + 3 * m * m * t * p1[0] + 3 * m * t * t * p2[0] + t**3 * p3[0])
        vs.append(m**3 * p0[1] + 3 * m * m * t * p1[1] + 3 * m * t * t * p2[1] + t**3 * p3[1])
    return us, vs


_CORNER_U, _CORNER_V = _corner_table()


def _tile(x: float, y: float) -> float:
    """到底框的帶號距離(裡面是負的)。四個角對稱,所以折到一個象限再算。"""
    u, v = abs(x - 256), abs(y - 256)
    if u > v:
        u, v = v, u
    if u <= _CORNER_U[0]:
        return v - 256
    if u >= _CORNER_U[-1]:
        return math.hypot(u - _CORNER_U[-1], v - _CORNER_V[-1]) if v >= _CORNER_V[-1] else -min(_CORNER_U[-1] - u, _CORNER_V[-1] - v)
    i = bisect.bisect_right(_CORNER_U, u) - 1
    du = _CORNER_U[i + 1] - _CORNER_U[i]
    slope = (_CORNER_V[i + 1] - _CORNER_V[i]) / du
    edge = _CORNER_V[i] + slope * (u - _CORNER_U[i])
    return (v - edge) / math.hypot(1, slope)


def _curve_y(x: float) -> float:
    z = (x - CENTER) / SIGMA
    return BASE - (BASE - TOP) * math.exp(-z * z / 2)


def _mass(x: float, y: float) -> float:
    """到分布實心區域的帶號距離(曲線用一階近似,反鋸齒夠用)。"""
    z = (x - CENTER) / SIGMA
    slope = (BASE - TOP) * z * math.exp(-z * z / 2) / SIGMA
    return max((_curve_y(x) - y) / math.hypot(1, slope), y - BASE, LEFT - x, x - RIGHT)


def _mix(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(p + (q - p) * t for p, q in zip(a, b))


def _over(under: tuple, color: tuple, coverage: float) -> tuple:
    return _mix(under, color, min(max(coverage, 0.0), 1.0))


def _pixel(x: float, y: float, px: float) -> tuple:
    """x、y 是 SVG 座標;px 是一個像素等於幾個 SVG 單位。回傳 RGBA。"""
    alpha = min(max(0.5 - _tile(x, y) / px, 0.0), 1.0)
    if alpha == 0:
        return (0, 0, 0, 0)
    mass = _mass(x, y)
    color = _over(INK, PAPER, 0.5 - mass / px)
    color = _over(color, TAIL, 0.5 - max(mass, CENTER + TAIL_Z * SIGMA - x) / px)
    return (round(color[0]), round(color[1]), round(color[2]), round(alpha * 255))


def write_png(path: Path, size: int = 1024) -> None:
    content = size * CONTENT
    margin = (size - content) / 2
    px = 512 / content
    rows = bytearray()
    for j in range(size):
        rows.append(0)
        y = (j + 0.5 - margin) * px
        for i in range(size):
            rows += bytes(_pixel((i + 0.5 - margin) * px, y, px))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)
