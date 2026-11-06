"""特徵登記簿(共用介面,只有維護者改這個檔;各特徵寫在 lab/features/<面向>.py)。

規約:
- 特徵函式的簽名一律是 fn(view: PITView, entity: str) -> float | None,只能透過 view 拿資料
  (不准讀快取、讀磁碟、讀今天的參數),每個特徵都要有竄改測試(lab/pit/tamper.py)。
- 每個特徵登記:key、面向(pillar)、市場、資料來源、白話說明、方向(越大越好或越小越好)。
  指標字典、消融實驗、竄改測試都從這份登記簿自動產生。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

PILLARS = ("quality", "growth", "valuation", "health", "momentum", "flows", "revisions", "sentiment")


@dataclass(frozen=True)
class Feature:
    key: str
    pillar: str
    markets: tuple[str, ...]  # ("TW",)、("US",) 或 ("TW", "US")
    fn: Callable
    sources: tuple[str, ...] = ()
    label: str = ""
    higher_is_better: bool = True
    validated: bool = False  # 通過閘門前一律 False,不進評等(Q2)
    notes: str = field(default="", compare=False)


REGISTRY: dict[str, Feature] = {}


def register(key: str, pillar: str, markets=("TW", "US"), sources=(), label: str = "", higher_is_better: bool = True,
             notes: str = ""):
    if pillar not in PILLARS:
        raise ValueError(f"未知面向 {pillar}")

    def deco(fn):
        if key in REGISTRY:
            raise ValueError(f"特徵 {key} 重複登記")
        REGISTRY[key] = Feature(key, pillar, tuple(markets), fn, tuple(sources), label, higher_is_better, False, notes)
        return fn
    return deco
