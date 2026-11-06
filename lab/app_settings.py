"""本機介面設定(private/settings.json):語言、研究工具告知是否看過。沒有帳號、不送到任何外部服務。"""
from __future__ import annotations

import json

from .config import PRIVATE_DIR

NOTICE_VERSION = 1          # 告知內容有實質修改時加 1,使用者要重新確認一次
ALLOWED = {"lang": ("zh-TW", "en")}


def _path():
    return PRIVATE_DIR / "settings.json"


def load() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"lang": d.get("lang") if d.get("lang") in ALLOWED["lang"] else None,
            "notice_ack": d.get("notice_ack") == NOTICE_VERSION, "notice_version": NOTICE_VERSION}


def update(body: dict) -> dict:
    try:
        cur = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cur = {}
    if "lang" in body:
        if body["lang"] not in ALLOWED["lang"]:
            raise ValueError("lang 只能是 zh-TW 或 en")
        cur["lang"] = body["lang"]
    if body.get("notice_ack") is True:
        cur["notice_ack"] = NOTICE_VERSION
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return load()
