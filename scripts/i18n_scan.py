"""掃描前後端的中文字串:給 tests/test_i18n_static.py 與 scripts/i18n_scan.py --missing 用(不連網)。

- JS:小型 tokenizer 找出字串與模板字面值(含巢狀 ${}),用和 web/js/i18n/index.js 相同的規則切出要翻譯的文字段。
- Python:用 ast 找出字串與 f-string,{…} 換成 {0}、{1},docstring 不算。
"""
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CJK = re.compile(r"[㐀-鿿]")
NEEDS = re.compile(r"[　-〿㐀-鿿＀-￯]")
TAG = re.compile(r"(</?[a-zA-Z!][^<>]*>)")
ATTR = re.compile(r"""=(["'])([^"']*)\1""")
MARK = re.compile(r"(\d+)")
REGEX_PREV = set("(,=:[!&|?{};\n+-*%<>~^")
REGEX_KW = ("return", "typeof", "case", "of", "in", "void", "delete")
NOT_TAG = {*REGEX_KW, "", "else", "await", "yield", "default", "throw", "new"}

JSON_FILES = ["lab/models/templates/*.json", "lab/models/*.json"]
# 掃描範圍外:展示版假資料、設計系統頁、字典本身
JS_SKIP = {"web/js/dev/wizard-mock.js", "web/js/design.js", "web/js/i18n/catalog.en.js",
           "web/js/update.js", "web/js/update_text.js", "web/js/settings.js"}  # 更新介面自帶中英文(update_text.js)
PY_FILES = ["app/server.py", "lab/agents.py", "lab/paper.py", "lab/portfolio.py", "lab/report.py", "lab/scan.py", "lab/snapshots.py",
            "lab/setup_status.py", "lab/metric_dictionary.py", "lab/analysis/*.py", "lab/valuation/*.py", "lab/models/*.py",
            "lab/research/model_eval.py"]


@dataclass
class Lit:
    start: int                      # 開頭引號位置
    end: int                        # 結尾引號之後
    kind: str                       # '"' "'" "`"
    parts: list[str] = field(default_factory=list)    # 靜態片段(未解碼的原始文字)
    exprs: list[tuple[int, int]] = field(default_factory=list)
    tag: str = ""


def _scan(src: str, i: int, end_on_brace: bool, out: list[Lit]) -> int:
    """掃描程式碼直到檔尾(或對應的 })。回傳停下來的位置。"""
    depth = 0
    prev = "\n"
    word = ""
    n = len(src)
    while i < n:
        c = src[i]
        if c in " \t\r":
            i += 1
            continue
        if c == "/" and src[i + 1:i + 2] == "/":
            i = src.find("\n", i)
            i = n if i < 0 else i
            continue
        if c == "/" and src[i + 1:i + 2] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == "/" and (prev in REGEX_PREV or word in REGEX_KW):
            j, cls = i + 1, False
            while j < n and (src[j] != "/" or cls):
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    cls = True
                elif src[j] == "]":
                    cls = False
                j += 1
            i, prev, word = j + 1, "/", ""
            continue
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(Lit(i, j + 1, c, [src[i + 1:j]]))
            i, prev, word = j + 1, '"', ""
            continue
        if c == "`":
            lit = Lit(i, 0, "`", tag="" if word in NOT_TAG else word)
            out.append(lit)
            j, seg = i + 1, i + 1
            while src[j] != "`":
                if src[j] == "\\":
                    j += 2
                elif src[j:j + 2] == "${":
                    lit.parts.append(src[seg:j])
                    k = _scan(src, j + 2, True, out)
                    lit.exprs.append((j + 2, k))
                    j = seg = k + 1
                else:
                    j += 1
            lit.parts.append(src[seg:j])
            lit.end = j + 1
            i, prev, word = j + 1, '"', ""
            continue
        if end_on_brace:
            if c == "{":
                depth += 1
            elif c == "}":
                if depth == 0:
                    return i
                depth -= 1
        if c.isalnum() or c in "_$" or ord(c) > 127:
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$" or ord(src[j]) > 127):
                j += 1
            word, prev, i = src[i:j], "a", j
            continue
        prev, word = c, ""
        i += 1
    return i


def js_literals(src: str) -> list[Lit]:
    out: list[Lit] = []
    _scan(src, 0, False, out)
    return out


def _cook(s: str) -> str:
    return re.sub(r"\\(u[0-9a-fA-F]{4}|.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1) if len(m.group(1)) == 1 else chr(int(m.group(1)[1:], 16))), s)


def run_key(text: str) -> str | None:
    """一段文字對應的目錄鍵;不需要查目錄(沒有中文字)回傳 None。"""
    core = text.strip()
    if not CJK.search(core):
        return None
    n = [-1]

    def mark(_m):
        n[0] += 1
        return "{%d}" % n[0]

    return MARK.sub(mark, core)


def lit_keys(lit: Lit) -> list[str]:
    src = lit.parts[0]
    for i, p in enumerate(lit.parts[1:]):
        src += f"{i}{p}"
    src = _cook(src)
    keys = []
    for i, p in enumerate(TAG.split(src)):
        chunks = [m.group(2) for m in ATTR.finditer(p)] if i % 2 else [p]
        for c in chunks:
            k = run_key(c)
            if k:
                keys.append(k)
    return keys


def js_files() -> list[Path]:
    fs = [p for p in sorted((ROOT / "web/js").rglob("*.js")) if p.relative_to(ROOT).as_posix() not in JS_SKIP]
    return fs


def js_keys() -> dict[str, list[str]]:
    """目錄鍵 → 出現的檔案。只收 ZH`…` 標籤的字面值與 zh-TW.js 的字典值。"""
    out: dict[str, list[str]] = {}
    for f in js_files():
        rel = f.relative_to(ROOT).as_posix()
        for lit in js_literals(f.read_text(encoding="utf-8")):
            if lit.tag == "ZH" or rel.endswith("i18n/zh-TW.js"):
                for k in lit_keys(lit):
                    out.setdefault(k, []).append(rel)
    return out


def js_untagged() -> list[tuple[str, int, str]]:
    """沒有經過 ZH 的中文字面值(檔案、行號、內容);zh-TW.js 除外。"""
    out = []
    for f in js_files():
        rel = f.relative_to(ROOT).as_posix()
        if rel.endswith("i18n/zh-TW.js"):
            continue
        src = f.read_text(encoding="utf-8")
        for lit in js_literals(src):
            if lit.tag not in ("ZH", "KEY") and any(CJK.search(p) for p in lit.parts):
                out.append((rel, src.count("\n", 0, lit.start) + 1, "".join(lit.parts)[:80]))
    return out


def html_keys() -> list[str]:
    src = (ROOT / "web/index.html").read_text(encoding="utf-8")
    body = re.sub(r"<!--.*?-->", "", src[src.index("<body"):], flags=re.S)
    keys = []
    for i, p in enumerate(TAG.split(body)):
        chunks = [m.group(2) for m in re.finditer(r"""(?:title|aria-label|placeholder)=(["'])([^"']*)\1""", p)] if i % 2 else [p]
        keys += [c.strip() for c in chunks if CJK.search(c)]
    return keys


# ---------------------------------------------------------------- Python
def _py_key(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip() if CJK.search(node.value) else None
    if isinstance(node, ast.JoinedStr):
        s, n = "", 0
        for v in node.values:
            if isinstance(v, ast.Constant):
                s += str(v.value)
            else:
                s += "{%d}" % n
                n += 1
        return s.strip() if CJK.search(s) else None
    return None


def py_keys(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and n.body \
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
            skip.add(id(n.body[0].value))
        if isinstance(n, ast.JoinedStr):
            skip.update(id(v) for v in n.values)
    # 相鄰字串在 ast 已合併;f-string 內的巢狀 FormattedValue 仍會被 walk 到自己的字串
    out = []
    for n in ast.walk(tree):
        if id(n) in skip:
            continue
        k = _py_key(n)
        if k:
            out.append((n.lineno, k))
    return out


def py_files() -> list[Path]:
    fs: list[Path] = []
    for pat in PY_FILES:
        fs += sorted(ROOT.glob(pat))
    return fs


def json_keys() -> list[tuple[str, str]]:
    """精靈範本等資料檔裡會顯示在畫面上的中文(檔案、內容)。"""
    out: list[tuple[str, str]] = []

    def walk(v, rel):
        if isinstance(v, str):
            if CJK.search(v):
                out.append((rel, v.strip()))
        elif isinstance(v, list):
            for x in v:
                walk(x, rel)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x, rel)

    for pat in JSON_FILES:
        for f in sorted(ROOT.glob(pat)):
            walk(json.loads(f.read_text(encoding="utf-8")), f.relative_to(ROOT).as_posix())
    return out


def missing() -> dict[str, str]:
    """沒有英文的介面文字 → 出現的位置。前端只查整段;後端可以用句型、逐句翻,但不得留下中文。"""
    sys.path.insert(0, str(ROOT))
    from lab import i18n
    miss = {k: where[0] for k, where in js_keys().items() if not i18n.exact(k)}
    miss.update({k: "web/index.html" for k in html_keys() if not i18n.exact(k)})
    for f in py_files():
        for ln, k in py_keys(f):
            if not i18n.covered(k):
                miss[k] = f"{f.relative_to(ROOT).as_posix()}:{ln}"
    for rel, k in json_keys():
        if not i18n.covered(k):
            miss[k] = rel
    return miss


if __name__ == "__main__":
    print(json.dumps(missing(), ensure_ascii=False, indent=0))
