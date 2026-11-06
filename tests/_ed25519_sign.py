"""測試專用的 Ed25519 簽章(RFC 8032 §5.1.6)。正式環境沒有這段:App 只驗證,發版用 cryptography 簽。"""
from __future__ import annotations

import hashlib

from lab import ed25519 as E


def _encode(pt: tuple) -> bytes:
    x, y, z, _ = pt
    zi = pow(z, E.P - 2, E.P)
    x, y = x * zi % E.P, y * zi % E.P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _expand(seed: bytes) -> tuple[int, bytes]:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    return _encode(E._mul(_expand(seed)[0], E._B))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(seed)
    pub = _encode(E._mul(a, E._B))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % E.L
    big_r = _encode(E._mul(r, E._B))
    k = int.from_bytes(hashlib.sha512(big_r + pub + msg).digest(), "little") % E.L
    return big_r + ((r + k * a) % E.L).to_bytes(32, "little")
