"""純 Python Ed25519 驗簽:RFC 8032 §7.1 測試向量與嚴格模式。"""
from __future__ import annotations

import hashlib

import pytest

from _ed25519_sign import public_key, sign
from lab.ed25519 import L, verify

RFC_VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]
# RFC 8032 TEST SHA(abc)
ABC_SEED = "833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42"
ABC_PUB = "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf"
ABC_SIG = ("dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b58909351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704")


@pytest.mark.parametrize("seed,pub,msg,sig", RFC_VECTORS)
def test_rfc8032_vectors(seed, pub, msg, sig):
    assert verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes.fromhex(sig))
    assert public_key(bytes.fromhex(seed)).hex() == pub     # 測試用簽章器也對得上 RFC
    assert sign(bytes.fromhex(seed), bytes.fromhex(msg)).hex() == sig


def test_rfc8032_sha_abc():
    msg = hashlib.sha512(b"abc").digest()
    assert verify(bytes.fromhex(ABC_PUB), msg, bytes.fromhex(ABC_SIG))
    assert not verify(bytes.fromhex(ABC_PUB), msg + b"x", bytes.fromhex(ABC_SIG))


def _good():
    seed, pub, msg, sig = RFC_VECTORS[2]
    return bytes.fromhex(pub), bytes.fromhex(msg), bytes.fromhex(sig)


def test_any_bit_flip_rejected():
    pub, msg, sig = _good()
    for i in (0, 31, 32, 63):
        bad = bytearray(sig)
        bad[i] ^= 1
        assert not verify(pub, msg, bytes(bad))
    bad_pub = bytearray(pub)
    bad_pub[0] ^= 1
    assert not verify(bytes(bad_pub), msg, sig)
    assert not verify(pub, msg + b"\0", sig)


def test_malleable_signature_rejected():
    """S + L 在數學上也驗得過,嚴格模式要拒絕。"""
    pub, msg, sig = _good()
    s = int.from_bytes(sig[32:], "little") + L
    assert not verify(pub, msg, sig[:32] + s.to_bytes(32, "little"))


def test_non_canonical_point_rejected():
    """y >= p 的非正規編碼。"""
    _, msg, sig = _good()
    noncanonical = (2**255 - 19 + 1).to_bytes(32, "little")
    assert not verify(noncanonical, msg, sig)
    assert not verify(_good()[0], msg, noncanonical + sig[32:])


@pytest.mark.parametrize("pub,sig", [(b"", b""), (b"x" * 31, b"y" * 64), (b"x" * 32, b"y" * 63), (None, None), ("a" * 32, "b" * 64)])
def test_bad_lengths_and_types_return_false(pub, sig):
    assert verify(pub, b"m", sig) is False
