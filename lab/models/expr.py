"""自訂指標的運算式語言(層級 C,規格見 docs/模型客製化規格.md §3)。

用法:
    ce = compile_expr("safe_div(fcf, market_cap) * 100", {"fcf", "market_cap"})
    ce.variables            # frozenset({'fcf', 'market_cap'})
    ce.evaluate({"fcf": 50.0, "market_cap": 1000.0})   # 5.0

安全設計:
- 只用標準函式庫 `ast.parse(text, mode="eval")` 解析,再逐節點做**白名單**檢查;白名單以外的節點、
  名稱、函式一律在編譯時丟 `ExprError`(繁體中文訊息,指出哪個名稱或語法不允許)。
- 計算時自己走訪 AST,**完全不使用 eval / exec / compile**;運算式碰不到任何 Python 物件,
  只看得到呼叫端傳進來的數字。

計算語意:
- 所有數值以 float 計算;布林結果(比較、and / or / not)轉成 1.0 / 0.0,條件判斷以「不等於 0」為真。
- 任何輸入是 None → 結果 None(該指標「不可得」);`coalesce` 是唯一會跳過 None 的函式。
- 除以 0、log(x ≤ 0)、sqrt(負數)、0 的負次方、負數的非整數次方、溢位或任何非有限值(inf / nan)→ None。
- 傳進來的變數若不是數字、是 inf / nan、或根本沒傳,一律視為 None。
- `and` / `or` 兩邊都會計算(不短路),任一邊 None → None;條件式 `a if c else b` 只計算被選到的那一邊。
"""
from __future__ import annotations

import ast
import math
from collections.abc import Callable, Mapping

MAX_LEN = 500       # 運算式長度上限(字元)
MAX_NODES = 200     # AST 節點上限(ast.walk 的所有節點,含運算子與 Load 等語境節點)
POW_LIMIT = 4.0     # `**` 的指數只能是常數,且在 −4 到 4 之間

Number = float | None


class ExprError(ValueError):
    """運算式不合法(語法錯誤或用了白名單以外的東西)。訊息為繁體中文,可直接顯示給使用者。"""


# ---------------------------------------------------------------- 白名單函式

def _fn_abs(x: float) -> Number:
    return abs(x)


def _fn_min(*xs: float) -> Number:
    return min(xs)


def _fn_max(*xs: float) -> Number:
    return max(xs)


def _fn_log(x: float) -> Number:
    return math.log(x) if x > 0 else None


def _fn_sqrt(x: float) -> Number:
    return math.sqrt(x) if x >= 0 else None


def _fn_clip(x: float, lo: float, hi: float) -> Number:
    if lo > hi:
        return None
    return min(max(x, lo), hi)


def _fn_safe_div(a: float, b: float) -> Number:
    return None if b == 0 else a / b


# 名稱 → (函式, 最少參數數, 最多參數數(None = 不限), 說明)。coalesce 另外處理(要看得到 None)。
_FUNCS: dict[str, tuple[Callable[..., Number] | None, int, int | None, str]] = {
    "abs": (_fn_abs, 1, 1, "abs(x)"),
    "min": (_fn_min, 2, None, "min(a, b, ...)"),
    "max": (_fn_max, 2, None, "max(a, b, ...)"),
    "log": (_fn_log, 1, 1, "log(x)"),
    "sqrt": (_fn_sqrt, 1, 1, "sqrt(x)"),
    "clip": (_fn_clip, 3, 3, "clip(x, lo, hi)"),
    "safe_div": (_fn_safe_div, 2, 2, "safe_div(a, b)"),
    "coalesce": (None, 1, None, "coalesce(a, b, ...)"),
}

ALLOWED_FUNCTIONS: frozenset[str] = frozenset(_FUNCS)

_BINOPS: dict[type, str] = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "**"}
_CMPOPS: dict[type, Callable[[float, float], bool]] = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}

# 常見的不允許語法 → 中文說明(其他節點用通用訊息)
_NODE_DESC: dict[str, str] = {
    "Attribute": "屬性存取(.)",
    "Subscript": "索引([])",
    "Slice": "切片",
    "Lambda": "lambda",
    "ListComp": "串列推導式",
    "SetComp": "集合推導式",
    "DictComp": "字典推導式",
    "GeneratorExp": "產生器推導式",
    "NamedExpr": "海象運算子(:=)",
    "JoinedStr": "f-string",
    "FormattedValue": "f-string",
    "TemplateStr": "t-string",
    "List": "串列",
    "Tuple": "元組",
    "Set": "集合",
    "Dict": "字典",
    "Starred": "星號展開(*)",
    "Await": "await",
    "Yield": "yield",
    "YieldFrom": "yield",
}
_OP_DESC: dict[type, str] = {
    ast.Mod: "%(取餘數)", ast.FloorDiv: "//(整數除法)", ast.MatMult: "@(矩陣乘法)",
    ast.LShift: "<<", ast.RShift: ">>", ast.BitOr: "|", ast.BitAnd: "&", ast.BitXor: "^",
    ast.Invert: "~", ast.Is: "is", ast.IsNot: "is not", ast.In: "in", ast.NotIn: "not in",
}


def _where(node: ast.AST) -> str:
    col = getattr(node, "col_offset", None)
    return f"(第 {col + 1} 個字元)" if isinstance(col, int) else ""


def _is_number_const(node: ast.AST) -> bool:
    return (isinstance(node, ast.Constant) and isinstance(node.value, int | float)
            and not isinstance(node.value, bool))


# ---------------------------------------------------------------- 編譯(白名單檢查)

class _Checker:
    """遞迴檢查 AST,只放行白名單節點,並收集用到的變數。"""

    def __init__(self, allowed_vars: set[str] | frozenset[str]):
        self.allowed_vars = frozenset(allowed_vars)
        self.variables: set[str] = set()

    def check(self, node: ast.AST) -> None:
        if isinstance(node, ast.Constant):
            self._check_const(node)
        elif isinstance(node, ast.Name):
            self._check_name(node)
        elif isinstance(node, ast.BinOp):
            op = type(node.op)
            if op not in _BINOPS:
                raise ExprError(f"不允許的運算子 {_OP_DESC.get(op, op.__name__)}{_where(node)};"
                                "只能用 + - * / **")
            if op is ast.Pow:
                self._check_pow_exponent(node.right)
            self.check(node.left)
            self.check(node.right)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, ast.USub | ast.UAdd | ast.Not):
                raise ExprError(f"不允許的運算子 {_OP_DESC.get(type(node.op), type(node.op).__name__)}"
                                f"{_where(node)}")
            self.check(node.operand)
        elif isinstance(node, ast.BoolOp):
            for v in node.values:
                self.check(v)
        elif isinstance(node, ast.Compare):
            for op in node.ops:
                if type(op) not in _CMPOPS:
                    raise ExprError(f"不允許的比較 {_OP_DESC.get(type(op), type(op).__name__)}{_where(node)};"
                                    "只能用 < <= > >= == !=")
            self.check(node.left)
            for c in node.comparators:
                self.check(c)
        elif isinstance(node, ast.IfExp):
            self.check(node.test)
            self.check(node.body)
            self.check(node.orelse)
        elif isinstance(node, ast.Call):
            self._check_call(node)
        else:
            name = type(node).__name__
            desc = _NODE_DESC.get(name, f"語法 {name}")
            raise ExprError(f"不允許使用{desc}{_where(node)}")

    def _check_const(self, node: ast.Constant) -> None:
        v = node.value
        if isinstance(v, str):
            raise ExprError(f"不允許字串 {v!r}{_where(node)};運算式只能用數字與變數")
        if not _is_number_const(node):
            raise ExprError(f"不允許的常數 {v!r}{_where(node)};運算式只能用數字與變數")
        try:
            f = float(v)
        except OverflowError:
            raise ExprError(f"數字太大{_where(node)}") from None
        if not math.isfinite(f):
            raise ExprError(f"數字太大或不是有限值{_where(node)}")

    def _check_name(self, node: ast.Name) -> None:
        name = node.id
        if name in self.allowed_vars:
            self.variables.add(name)
            return
        if name in _FUNCS:
            raise ExprError(f"函式 {name} 要用呼叫的方式寫,例如 {_FUNCS[name][3]}{_where(node)}")
        raise ExprError(f"不認得的名稱「{name}」{_where(node)};只能用基礎變數清單裡的變數")

    def _check_pow_exponent(self, node: ast.AST) -> None:
        e = node
        sign = 1.0
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.USub | ast.UAdd):
            sign = -1.0 if isinstance(e.op, ast.USub) else 1.0
            e = e.operand
        if not _is_number_const(e):
            raise ExprError(f"** 的指數只能是常數數字(−4 到 4){_where(node)}")
        self._check_const(e)
        val = sign * float(e.value)
        if not -POW_LIMIT <= val <= POW_LIMIT:
            raise ExprError(f"** 的指數 {val:g} 超出範圍,只能在 −4 到 4 之間{_where(node)}")

    def _check_call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name):
            raise ExprError(f"只能呼叫白名單函式({'、'.join(sorted(_FUNCS))}){_where(node)}")
        name = node.func.id
        if name not in _FUNCS:
            raise ExprError(f"不允許的函式「{name}」{_where(node)};"
                            f"只能用 {'、'.join(sorted(_FUNCS))}")
        if node.keywords:
            raise ExprError(f"函式 {name} 不接受關鍵字參數(名稱=值){_where(node)}")
        _, lo, hi, usage = _FUNCS[name]
        n = len(node.args)
        for a in node.args:
            if isinstance(a, ast.Starred):
                raise ExprError(f"不允許使用星號展開(*){_where(a)}")
        if n < lo or (hi is not None and n > hi):
            raise ExprError(f"函式 {name} 的參數數目不對(給了 {n} 個){_where(node)};用法:{usage}")
        for a in node.args:
            self.check(a)


def compile_expr(text: str, allowed_vars: set[str] | frozenset[str]) -> CompiledExpr:
    """解析並檢查運算式,回傳可重複計算的 `CompiledExpr`;不合法時丟 `ExprError`(繁體中文訊息)。

    `allowed_vars`:這個運算式可以讀的基礎變數名稱。函式名稱不需要列在裡面。
    """
    if not isinstance(text, str):
        raise ExprError("運算式必須是文字")
    if len(text) > MAX_LEN:
        raise ExprError(f"運算式太長({len(text)} 字元),上限 {MAX_LEN} 字元")
    if not text.strip():
        raise ExprError("運算式是空的")
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as e:
        pos = f"(第 {e.offset} 個字元)" if e.offset else ""
        raise ExprError(f"語法錯誤{pos}:請檢查括號、運算子是否完整") from None
    except (ValueError, RecursionError, MemoryError):
        raise ExprError("運算式無法解析(含有不允許的字元或巢狀太深)") from None
    n_nodes = sum(1 for _ in ast.walk(tree))
    if n_nodes > MAX_NODES:
        raise ExprError(f"運算式太複雜({n_nodes} 個節點),上限 {MAX_NODES} 個")
    checker = _Checker(allowed_vars)
    checker.check(tree.body)
    return CompiledExpr(text, tree.body, frozenset(checker.variables))


# ---------------------------------------------------------------- 計算

def _fin(x: float | None) -> Number:
    """非有限值(inf / nan)一律視為不可得。"""
    if x is None or not math.isfinite(x):
        return None
    return x


def _as_number(v: object) -> Number:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, int | float):
        try:
            return _fin(float(v))
        except OverflowError:
            return None
    return None


def _binop(op: type, a: float, b: float) -> Number:
    try:
        if op is ast.Add:
            r = a + b
        elif op is ast.Sub:
            r = a - b
        elif op is ast.Mult:
            r = a * b
        elif op is ast.Div:
            if b == 0:
                return None
            r = a / b
        else:  # ast.Pow:指數已在編譯時限制為 −4 到 4 的常數
            r = math.pow(a, b)
    except (ZeroDivisionError, OverflowError, ValueError):
        return None
    return _fin(r)


class CompiledExpr:
    """編譯好的運算式。`variables` 是用到的變數;`evaluate(env)` 回傳 float 或 None。"""

    __slots__ = ("_tree", "text", "variables")

    def __init__(self, text: str, tree: ast.expr, variables: frozenset[str]):
        self.text = text
        self.variables = variables
        self._tree = tree

    def __repr__(self) -> str:
        return f"CompiledExpr({self.text!r})"

    def evaluate(self, env: Mapping[str, float | None]) -> Number:
        """用 `env`(變數名 → 數值或 None)計算。缺少的變數視為 None。不會丟例外。"""
        return self._eval(self._tree, env)

    def _eval(self, node: ast.expr, env: Mapping[str, float | None]) -> Number:
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return _as_number(env.get(node.id))
        if isinstance(node, ast.BinOp):
            a = self._eval(node.left, env)
            b = self._eval(node.right, env)
            if a is None or b is None:
                return None
            return _binop(type(node.op), a, b)
        if isinstance(node, ast.UnaryOp):
            x = self._eval(node.operand, env)
            if x is None:
                return None
            if isinstance(node.op, ast.Not):
                return 0.0 if x != 0 else 1.0
            return -x if isinstance(node.op, ast.USub) else x
        if isinstance(node, ast.BoolOp):
            vals = [self._eval(v, env) for v in node.values]
            if any(v is None for v in vals):
                return None
            truth = [v != 0 for v in vals]
            ok = all(truth) if isinstance(node.op, ast.And) else any(truth)
            return 1.0 if ok else 0.0
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, env)
            rights = [self._eval(c, env) for c in node.comparators]
            if left is None or any(r is None for r in rights):
                return None
            for op, r in zip(node.ops, rights, strict=True):
                if not _CMPOPS[type(op)](left, r):
                    return 0.0
                left = r
            return 1.0
        if isinstance(node, ast.IfExp):
            c = self._eval(node.test, env)
            if c is None:
                return None
            return self._eval(node.body if c != 0 else node.orelse, env)
        if isinstance(node, ast.Call):
            name = node.func.id  # type: ignore[attr-defined]  # 編譯時已確認是白名單函式名
            args = [self._eval(a, env) for a in node.args]
            if name == "coalesce":
                return next((a for a in args if a is not None), None)
            if any(a is None for a in args):
                return None
            fn = _FUNCS[name][0]
            try:
                return _fin(fn(*args))  # type: ignore[misc]
            except (ZeroDivisionError, OverflowError, ValueError):
                return None
        # 編譯時已擋掉其他節點;走到這裡代表內部錯誤
        raise ExprError(f"內部錯誤:未預期的節點 {type(node).__name__}")
