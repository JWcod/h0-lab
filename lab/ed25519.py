"""Ed25519 驗簽(RFC 8032 §5.1.7,純 Python)。

標準函式庫沒有 Ed25519,更新器又不能新增執行依賴,所以這裡只實作「驗證」(沒有私鑰、不會簽章,不需要防時序攻擊)。
嚴格模式:公鑰、R 的座標必須是正規編碼(y < p),S 必須 < L,防簽章可塑性;驗證式用不乘餘因子的 [S]B == R + [k]A。
測試向量見 tests/test_ed25519.py(RFC 8032 §7.1)。
"""
from __future__ import annotations

import hashlib

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
_SQRT_M1 = pow(2, (P - 1) // 4, P)

# 基點 B(延伸座標 X, Y, Z, T)
_BY = 4 * pow(5, P - 2, P) % P


def _recover_x(y: int, sign: int) -> int | None:
    """由 y 與 x 的最低位還原 x(RFC 8032 §5.1.3);不在曲線上回傳 None。"""
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * _SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if x & 1 != sign:
        x = P - x
    return x


_BX = _recover_x(_BY, 0)
_B = (_BX, _BY, 1, _BX * _BY % P)
_IDENTITY = (0, 1, 1, 0)


def _add(a: tuple, b: tuple) -> tuple:
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    aa = (y1 - x1) * (y2 - x2) % P
    bb = (y1 + x1) * (y2 + x2) % P
    cc = 2 * t1 * t2 * D % P
    dd = 2 * z1 * z2 % P
    e, f, g, h = bb - aa, dd - cc, dd + cc, bb + aa
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(s: int, pt: tuple) -> tuple:
    q = _IDENTITY
    while s > 0:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _equal(a: tuple, b: tuple) -> bool:
    x1, y1, z1, _ = a
    x2, y2, z2, _ = b
    return (x1 * z2 - x2 * z1) % P == 0 and (y1 * z2 - y2 * z1) % P == 0


def _decode_point(s: bytes) -> tuple | None:
    if len(s) != 32:
        return None
    n = int.from_bytes(s, "little")
    sign = n >> 255
    y = n & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """簽章正確回傳 True;任何格式問題一律回傳 False,不丟例外。"""
    if not (isinstance(public_key, (bytes, bytearray)) and isinstance(signature, (bytes, bytearray))):
        return False
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decode_point(bytes(public_key))
    r = _decode_point(bytes(signature[:32]))
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    k = int.from_bytes(hashlib.sha512(bytes(signature[:32]) + bytes(public_key) + bytes(message)).digest(), "little") % L
    return _equal(_mul(s, _B), _add(r, _mul(k, a)))
