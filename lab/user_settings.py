"""使用者在本機設定的值,寫進使用者自己的 .env(App 在使用者資料夾,原始碼安裝在專案根目錄)。

- 只接受白名單的鍵與驗證過的值(不能換行,避免寫入別的設定)。
- 值不回傳給前端,只回有沒有填。寫入 .env 只走 upsert_env 這一處;之後的設定頁呼叫 save() / public_state()。
- 檔案權限 0600,原子替換;其他行(含註解)原樣保留。
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from . import config, runtime

_EMAIL = re.compile(r"[^\s@<>\"']+@[^\s@<>\"']+\.[A-Za-z]{2,}")
_FORBIDDEN_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "localhost", "your-domain.tld",
                            "example", "invalid", "test")   # 後三個是 RFC 2606 保留的頂級網域
_TOKEN = re.compile(r"^[A-Za-z0-9._\-]{8,4096}$")


class SettingError(ValueError):
    pass


def clean_sec_user_agent(value: object) -> str:
    """SEC 要求宣告「名稱 + 聯絡信箱」。要有名字和真的信箱格式,佔位網域不收。"""
    if not isinstance(value, str):
        raise SettingError("sec_ua_format")
    v = " ".join(value.split())
    if not 5 <= len(v) <= 200 or not v.isprintable() or not v.isascii():
        raise SettingError("sec_ua_format")
    m = _EMAIL.search(v)
    if not m or not v.replace(m.group(0), "").strip():
        raise SettingError("sec_ua_format")
    for found in _EMAIL.findall(v):   # 每個信箱都查,不能靠前面放一個真的帶過去
        domain = found.rsplit("@", 1)[1].lower()
        if any(domain == d or domain.endswith("." + d) for d in _FORBIDDEN_EMAIL_DOMAINS):
            raise SettingError("sec_ua_placeholder")
    return v


def clean_finmind_token(value: object) -> str:
    """空字串代表清除。"""
    if value == "":
        return ""
    if not isinstance(value, str) or not _TOKEN.match(value.strip()):
        raise SettingError("finmind_token_format")
    return value.strip()


def upsert_env(path: Path, updates: dict[str, str]) -> None:
    """改寫 .env 的指定鍵:已有的那一行換掉(重複的鍵只留一行),沒有就加在最後;其他內容不動。"""
    lines: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").split("\n")     # 不用 splitlines:它會把 U+2028 這類字元也當換行
    except FileNotFoundError:
        pass
    if lines and not lines[-1]:
        lines.pop()
    done: set[str] = set()
    out = []
    for ln in lines:
        key = ln.split("=", 1)[0].strip() if "=" in ln and not ln.lstrip().startswith("#") else None
        if key not in updates:
            out.append(ln)
        elif key not in done:
            out.append(f"{key}={updates[key]}")
            done.add(key)
    out += [f"{k}={v}" for k, v in updates.items() if k not in done]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _apply_sec_ua(v: str) -> None:
    os.environ["SEC_USER_AGENT"] = v
    config.SEC_USER_AGENT = v
    config.SEC_UA_IS_DEFAULT = False
    runtime.sec_blocked = False
    from .sources import sec
    sec.HEADERS["User-Agent"] = v


def _apply_finmind(v: str) -> None:
    os.environ["FINMIND_TOKEN"] = v
    config.FINMIND_TOKEN = v
    from .sources import finmind
    if hasattr(finmind, "FINMIND_TOKEN"):
        finmind.FINMIND_TOKEN = v


def save(body: dict) -> dict:
    """body 可含 sec_user_agent、finmind_token、auto_check;先全部驗證,才一起寫入。"""
    updates: dict[str, str] = {}
    if "auto_check" in body and not isinstance(body["auto_check"], bool):
        raise SettingError("auto_check_format")
    if "sec_user_agent" in body:
        updates["SEC_USER_AGENT"] = clean_sec_user_agent(body["sec_user_agent"])
    if "finmind_token" in body:
        updates["FINMIND_TOKEN"] = clean_finmind_token(body["finmind_token"])
    if updates:
        upsert_env(runtime.env_file(), updates)
    if "SEC_USER_AGENT" in updates:
        _apply_sec_ua(updates["SEC_USER_AGENT"])
    if "FINMIND_TOKEN" in updates:
        _apply_finmind(updates["FINMIND_TOKEN"])
    if "auto_check" in body:
        from . import updater
        updater.set_auto_check(body["auto_check"])
    return public_state()


def folders() -> dict[str, str]:
    """使用者資料放在哪(設定頁顯示用)。"""
    out = {"data": str(config.DATA_DIR), "private": str(config.PRIVATE_DIR), "env_file": str(runtime.env_file())}
    if runtime.FROZEN:
        out["support"] = str(runtime.app_support_dir())
        out["logs"] = str(runtime.app_support_dir() / "logs")
    return out


def public_state() -> dict:
    from . import updater
    return {"auto_check": updater.auto_check_enabled(), "folders": folders(), "has_sec_user_agent": not config.SEC_UA_IS_DEFAULT, "has_finmind_token": bool(config.FINMIND_TOKEN),
            "sec_blocked": runtime.sec_blocked, "env_path": str(runtime.env_file()),
            "frozen": runtime.FROZEN}
