"""API 輸出的語言:中文是原文,英文在輸出時查 web/js/i18n/catalog.en.js(前後端共用同一份目錄)。

計算、快取與資料檔全部維持中文,不影響評分與回測;舊快照、舊掃描檔也能翻譯。
文字走三層:整句完全相符 → 帶 {n} 的句型(佔位符遞迴翻譯)→ 依句號、分號切開逐句翻譯。翻不到就保留原文。
不接任何翻譯 API;數字全部是 Python 算好的,這裡只換文字。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CATALOG_FILE = Path(__file__).resolve().parent.parent / "web" / "js" / "i18n" / "catalog.en.js"
CJK = re.compile(r"[㐀-鿿]")
NEEDS = re.compile(r"[　-〿㐀-鿿＀-￯]")
PLACEHOLDER = re.compile(r"\{\w+\}")
TAIL = {"。": ".", "；": ";", "，": ",", "：": ":", "！": "!", "？": "?", ";": ";", ",": ",", ":": ":"}
FULLWIDTH = str.maketrans("\uff08\uff09\uff1a\uff0c\uff1b\uff01\uff1f", "():,;!?")
GROUP = r"([^\u3002;\uff1b]+?)"     # 佔位符不跨句,否則句尾的 {0} 會把後面整段吃掉
STRIP = " ,;:。；，：！？"
# 子句邊界:標點之後(數字裡的逗號不算)、左括號之前
BOUNDARY = re.compile(r"(?<=[;:。；：，！？])|(?<=,)(?!\d)|(?=\()")
# 使用者或外部來源的文字,不翻譯
SKIP_KEYS = {"notes", "subject", "summary_raw", "side", "symbol", "name_en", "website", "officers", "url", "link", "source_url"}
PUNCT = [("：", ": "), ("，", ", "), ("；", "; "), ("。", ". "), ("、", ", "), ("（", " ("), ("）", ")"),
         ("？", "? "), ("！", "! "), ("「", "\""), ("」", "\""), ("『", "\""), ("』", "\""), ("～", "~"), ("　", " ")]


@lru_cache(maxsize=1)
def _catalog() -> dict[str, str]:
    text = CATALOG_FILE.read_text(encoding="utf-8")
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


@lru_cache(maxsize=1)
def _norm() -> dict[str, str]:
    """句尾標點不算在鍵裡:後端常把幾段文字接起來,切開後要查得到。"""
    return {k.strip(STRIP): v.strip(" ,;:.") for k, v in _catalog().items()}


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[re.Pattern, list[str], str, tuple[str, ...]]]:
    out = []
    for zh, en in _norm().items():
        names = PLACEHOLDER.findall(zh)
        if not names:
            continue
        chunks = PLACEHOLDER.split(zh)
        rx = "".join(re.escape(c) + (GROUP if i < len(names) else "") for i, c in enumerate(chunks))
        out.append((re.compile(rx + r"\Z", re.S), names, en, tuple(c for c in chunks if c)))
    out.sort(key=lambda x: -sum(len(c) for c in x[3]))     # 文字越多的句型越優先
    return out


def _punct(s: str) -> str:
    for a, b in PUNCT:
        s = s.replace(a, b)
    return s


def _data_rule(core: str) -> str | None:
    """資料本身帶的格式:民國季別、集保持股級距。"""
    m = re.fullmatch(r"(\d{2,3})\u5e74", core)
    if m:
        return str(int(m.group(1)) + 1911)
    m = re.fullmatch(r"(\d{2,3})年第(\d)季", core)
    if m:
        return f"{int(m.group(1)) + 1911} Q{m.group(2)}"
    m = re.fullmatch(r"([\d,]+)(?:-([\d,]+))?(張|股)(以上)?", core)
    if m:
        unit = "lots" if m.group(3) == "張" else "shares"
        return f"{m.group(1)}{'-' + m.group(2) if m.group(2) else ''}{'+' if m.group(4) else ''} {unit}"
    m = re.fullmatch(r"(.+?) ([A-F][+-]?)", core)     # 面向名稱 + 成績
    if m and CJK.search(m.group(1)):
        head = translate(m.group(1))
        if not CJK.search(head):
            return f"{head} {m.group(2)}"
    return None


def _match(core: str) -> str | None:
    core = core.translate(FULLWIDTH)     # 目錄的鍵用半形標點;資料檔裡可能是全形
    cat = _catalog()
    if core in cat:
        return cat[core]
    norm = _norm()
    if core in norm:
        return norm[core]
    got = _data_rule(core)
    if got is not None:
        return got
    for rx, names, en, chunks in _patterns():
        if not all(c in core for c in chunks):
            continue
        m = rx.match(core)
        if m:
            out = en
            for name, val in zip(names, m.groups()):
                out = out.replace(name, translate(val))
            return out
    for sep, joiner in (("、", ", "), (" · ", " · ")):    # 清單
        if sep in core:
            items = [translate(x) for x in core.split(sep)]
            if not any(CJK.search(x) for x in items):
                return joiner.join(items)
    return None


def _segments(core: str) -> str:
    """把接起來的文字切成子句,由長到短查目錄;查不到的子句保留原文。"""
    parts = [p for p in BOUNDARY.split(core) if p]
    n = len(parts)
    out: list[str] = []
    i = 0
    while i < n:
        for j in range(n, i, -1):
            chunk = "".join(parts[i:j])
            body = chunk.rstrip(STRIP)
            end = chunk[len(body):].strip()
            got = _match(body) if body and (j - i < n or body != core) else None
            if got is not None:
                out.append(got + TAIL.get(end[:1], ""))
                i = j
                break
        else:
            piece = parts[i].rstrip(STRIP)
            end = parts[i][len(piece):].strip()
            out.append((piece if CJK.search(piece) else _punct(piece)).strip() + TAIL.get(end[:1], ""))
            i += 1
    return " ".join(x for x in out if x).replace(" )", ")").replace(" (", " (")


@lru_cache(maxsize=20000)
def translate(s: str) -> str:
    if not NEEDS.search(s):
        return s
    core = s.strip()
    lead, trail = s[:s.index(core)] if core else "", s[s.index(core) + len(core):] if core else ""
    got = _match(core)
    if got is not None:
        return lead + got + trail
    if not CJK.search(core):
        return lead + _punct(core).strip() + trail
    return lead + _segments(core) + trail


def exact(key: str) -> bool:
    """前端只查整段:目錄裡要有這個鍵。"""
    return key in _catalog()


def covered(key: str) -> bool:
    """靜態檢查用:後端文字翻完不得留下中文。"""
    return exact(key) or not CJK.search(translate(key))


def localize(obj, key: str | None = None):
    """遞迴翻譯 JSON 值。字典的鍵不動(前端用中文鍵取資料);{"zh-TW": …} 補上 "en"。"""
    if isinstance(obj, str):
        return obj if key in SKIP_KEYS else translate(obj)
    if isinstance(obj, list):
        return [localize(x, key) for x in obj]
    if isinstance(obj, dict):
        if "zh-TW" in obj and isinstance(obj["zh-TW"], str):
            return {**obj, "en": obj.get("en") or translate(obj["zh-TW"])}
        skip = set(SKIP_KEYS)
        if "link" in obj and "title" in obj:        # 新聞、公告:標題和來源是外部文字;事件分類照翻
            skip |= {"title", "publisher", "pos_words", "neg_words"}
        if ("symbol" in obj or "code" in obj) and key != "benchmark":    # 公司:名稱和簡介是外部文字
            skip |= {"name", "summary"}
        if "subject" in obj:                        # 官方公告原文
            skip |= {"desc", "clause"}
        return {k: (v if k in skip else localize(v, k)) for k, v in obj.items()}
    return obj


def lang_of(request) -> str:
    return "en" if request.cookies.get("h0lab_lang") == "en" else "zh-TW"
