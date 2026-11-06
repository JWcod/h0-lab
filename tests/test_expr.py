"""運算式語言(lab/models/expr.py)單元測試:正確計算、None 傳遞、除以 0、拒絕語法與惡意輸入(不連網)。"""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.models.expr import (
    MAX_LEN,
    MAX_NODES,
    CompiledExpr,
    ExprError,
    compile_expr,
)

VARS = {"a", "b", "c", "x", "pe", "roe", "close", "market_cap", "fcf"}


def ev(text, env=None, allowed=VARS):
    return compile_expr(text, allowed).evaluate(env or {})


# ---------------------------------------------------------------- 正確計算

@pytest.mark.parametrize("text, env, want", [
    ("1 + 2 * 3", {}, 7.0),
    ("(1 + 2) * 3", {}, 9.0),
    ("10 / 4", {}, 2.5),
    ("7 - 10", {}, -3.0),
    ("-a", {"a": 2}, -2.0),
    ("+a", {"a": 2}, 2.0),
    ("--a", {"a": 2}, 2.0),
    ("a ** 2", {"a": 3}, 9.0),
    ("a ** -1", {"a": 4}, 0.25),
    ("a ** 0.5", {"a": 9}, 3.0),
    ("a ** 4", {"a": 2}, 16.0),
    ("-2 ** 2", {}, -4.0),          # Python 優先順序:-(2 ** 2)
    ("fcf / market_cap * 100", {"fcf": 50, "market_cap": 1000}, 5.0),
    ("1e3 + 0.5", {}, 1000.5),
])
def test_arithmetic(text, env, want):
    assert ev(text, env) == pytest.approx(want)


def test_result_is_float():
    r = ev("a + 1", {"a": 1})
    assert isinstance(r, float) and r == 2.0


@pytest.mark.parametrize("text, want", [
    ("a < b", 1.0), ("a <= b", 1.0), ("a > b", 0.0), ("a >= b", 0.0),
    ("a == 1", 1.0), ("a != 1", 0.0),
    ("0 < a < b", 1.0), ("0 < b < a", 0.0),
    ("a < b and b < c", 1.0), ("a > b and b < c", 0.0),
    ("a > b or b < c", 1.0), ("a > b or b > c", 0.0),
    ("not a", 0.0), ("not (a > b)", 1.0), ("not 0", 1.0),
    ("a and 0", 0.0), ("a or 0", 1.0),
])
def test_boolean_results_are_one_or_zero(text, want):
    r = ev(text, {"a": 1, "b": 2, "c": 3})
    assert isinstance(r, float) and r == want


def test_conditional():
    assert ev("a if a > 0 else 0", {"a": 5}) == 5.0
    assert ev("a if a > 0 else 0", {"a": -5}) == 0.0
    assert ev("1 if pe < 15 else (0.5 if pe < 25 else 0)", {"pe": 20}) == 0.5


@pytest.mark.parametrize("text, want", [
    ("abs(-3)", 3.0),
    ("min(3, 1, 2)", 1.0),
    ("max(3, 1, 2)", 3.0),
    ("min(a, b)", 1.0),
    ("log(1)", 0.0),
    ("sqrt(16)", 4.0),
    ("clip(5, 0, 3)", 3.0),
    ("clip(-5, 0, 3)", 0.0),
    ("clip(2, 0, 3)", 2.0),
    ("safe_div(6, 3)", 2.0),
    ("coalesce(a, 9)", 1.0),
])
def test_functions(text, want):
    assert ev(text, {"a": 1, "b": 2}) == pytest.approx(want)


def test_log_is_natural_log():
    assert ev("log(x)", {"x": math.e}) == pytest.approx(1.0)


def test_variables_collected():
    ce = compile_expr("safe_div(fcf, market_cap) + coalesce(roe, 0) * 2", VARS)
    assert isinstance(ce, CompiledExpr)
    assert ce.variables == {"fcf", "market_cap", "roe"}
    assert compile_expr("1 + 2", VARS).variables == set()


def test_compiled_expr_is_reusable():
    ce = compile_expr("a * 2", VARS)
    assert ce.evaluate({"a": 1}) == 2.0
    assert ce.evaluate({"a": 3}) == 6.0
    assert ce.evaluate({"a": None}) is None


def test_int_and_bool_inputs_become_float():
    assert ev("a + b", {"a": 1, "b": True}) == 2.0


# ---------------------------------------------------------------- None 傳遞

@pytest.mark.parametrize("text", [
    "a + 1", "1 - a", "a * 2", "a / 2", "2 / a", "a ** 2", "-a", "not a",
    "a < 1", "1 < a < 3", "a and 1", "1 or a",
    "abs(a)", "min(a, 1)", "max(1, a)", "log(a)", "sqrt(a)", "clip(a, 0, 1)", "clip(1, a, 2)",
    "safe_div(a, 1)", "safe_div(1, a)",
    "1 if a > 0 else 2",   # 條件是 None
    "a if 1 else 2",       # 被選到的分支是 None
])
def test_none_propagates(text):
    assert ev(text, {"a": None}) is None


def test_missing_variable_is_none():
    assert ev("a + 1", {}) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), "12", [1], object()])
def test_non_finite_or_non_numeric_input_is_none(bad):
    assert ev("a + 1", {"a": bad}) is None


def test_conditional_only_evaluates_chosen_branch():
    # 沒被選到的分支是 None 或除以 0,不影響結果
    assert ev("1 if b > 0 else a", {"a": None, "b": 1}) == 1.0
    assert ev("1 if b > 0 else 1 / 0", {"b": 1}) == 1.0


def test_coalesce_skips_none():
    assert ev("coalesce(a, b, 7)", {"a": None, "b": None}) == 7.0
    assert ev("coalesce(a, b)", {"a": None, "b": 2}) == 2.0
    assert ev("coalesce(a, b)", {"a": None, "b": None}) is None
    assert ev("coalesce(a / b, 0)", {"a": 1, "b": 0}) == 0.0   # 除以 0 的 None 也會被跳過
    assert ev("coalesce(a, 5)", {"a": 0}) == 0.0               # 0 不是 None,不跳過


# ---------------------------------------------------------------- 除以 0 與非有限值

@pytest.mark.parametrize("text, env", [
    ("1 / 0", {}),
    ("a / b", {"a": 1, "b": 0}),
    ("a / b", {"a": 0, "b": 0}),
    ("safe_div(a, 0)", {"a": 1}),
    ("log(0)", {}),
    ("log(-1)", {}),
    ("sqrt(-1)", {}),
    ("0 ** -1", {}),
    ("a ** 0.5", {"a": -8}),            # 負數的非整數次方
    ("a * a", {"a": 1e200}),            # 溢位成 inf
    ("a ** 4", {"a": 1e100}),           # 溢位
    ("a + b", {"a": 1.7e308, "b": 1.7e308}),
    ("clip(1, 3, 0)", {}),              # lo > hi
])
def test_invalid_math_gives_none(text, env):
    assert ev(text, env) is None


def test_negative_integer_power_ok():
    assert ev("a ** 3", {"a": -2}) == -8.0


def test_evaluate_never_raises_on_data():
    ce = compile_expr("log(a) + sqrt(b) / c ** -2 + safe_div(a, b) * (a if b else c)", VARS)
    for v in [0, -1, 1, None, float("nan"), float("inf"), 1e308, -1e308, 5e-324]:
        for w in [0, -1, 1, None, 1e308]:
            r = ce.evaluate({"a": v, "b": w, "c": v})
            assert r is None or math.isfinite(r)


# ---------------------------------------------------------------- 拒絕的語法

@pytest.mark.parametrize("text, keyword", [
    ("a.real", "屬性"),
    ("a[0]", "索引"),
    ("'abc'", "字串"),
    ("\"x\" + 'y'", "字串"),
    ("lambda: 1", "lambda"),
    ("[i for i in a]", "推導式"),
    ("{i for i in a}", "推導式"),
    ("{i: 1 for i in a}", "推導式"),
    ("(i for i in a)", "推導式"),
    ("(y := 1)", "海象"),
    ("f'{a}'", "f-string"),
    ("[1, 2]", "串列"),
    ("(1, 2)", "元組"),
    ("{1: 2}", "字典"),
    ("{1, 2}", "集合"),
    ("a % 2", "%"),
    ("a // 2", "//"),
    ("a @ b", "@"),
    ("a | b", "|"),
    ("a & b", "&"),
    ("a ^ b", "^"),
    ("a << 1", "<<"),
    ("~a", "~"),
    ("a is b", "is"),
    ("a in b", "in"),
    ("None", "常數"),
    ("True", "常數"),
    ("1j", "常數"),
    ("b'x'", "常數"),
    ("...", "常數"),
    ("unknown_var + 1", "unknown_var"),
    ("abs", "呼叫"),
    ("foo(1)", "foo"),
    ("a(1)", "a"),
    ("max(a, b, key=c)", "關鍵字"),
    ("min(*a)", "星號"),
    ("abs(1, 2)", "參數數目"),
    ("min(1)", "參數數目"),
    ("clip(1, 2)", "參數數目"),
    ("coalesce()", "參數數目"),
    ("a ** b", "指數"),
    ("a ** 5", "指數"),
    ("a ** -4.5", "指數"),
    ("a ** (1 + 1)", "指數"),
    ("1e999", "數字"),
    ("a = 1", "語法錯誤"),
    ("a += 1", "語法錯誤"),
    ("import os", "語法錯誤"),
    ("1 +", "語法錯誤"),
    ("a; b", "語法錯誤"),
    ("", "空的"),
    ("   ", "空的"),
])
def test_rejected(text, keyword):
    with pytest.raises(ExprError) as ei:
        compile_expr(text, VARS)
    assert keyword in str(ei.value)


def test_function_names_are_not_variables_even_if_allowed_list_is_empty():
    with pytest.raises(ExprError):
        compile_expr("a + 1", set())
    assert compile_expr("abs(-1)", set()).evaluate({}) == 1.0


def test_non_string_input_rejected():
    with pytest.raises(ExprError):
        compile_expr(123, VARS)  # type: ignore[arg-type]


def test_error_message_points_to_position():
    with pytest.raises(ExprError) as ei:
        compile_expr("a + foo", VARS)
    assert "foo" in str(ei.value) and "第 5 個字元" in str(ei.value)


def test_expr_error_is_value_error():
    assert issubclass(ExprError, ValueError)


# ---------------------------------------------------------------- 惡意輸入

@pytest.mark.parametrize("text", [
    "__import__('os')",
    "__import__('os').system('echo hi')",
    "().__class__",
    "().__class__.__bases__[0].__subclasses__()",
    "open('x')",
    "eval('1')",
    "exec('1')",
    "compile('1', 'x', 'eval')",
    "getattr(a, '__class__')",
    "globals()",
    "locals()",
    "vars()",
    "dir()",
    "breakpoint()",
    "__builtins__",
    "type(a)",
    "abs.__self__",
    "(lambda: 1)()",
    "[x for x in ().__class__.__mro__]",
    "a if __import__('os') else b",
    "max(a, __import__('os'))",
    "a\nimport os",
    "a\x00",
])
def test_malicious_inputs_rejected(text):
    with pytest.raises(ExprError):
        compile_expr(text, VARS)


def test_dunder_names_rejected_even_if_listed_as_allowed():
    # 就算呼叫端誤把 __import__ 列進變數,它也只會被當成數字變數讀 env,不會變成可呼叫的東西
    with pytest.raises(ExprError):
        compile_expr("__import__('os')", VARS | {"__import__"})
    assert compile_expr("__import__ + 1", VARS | {"__import__"}).evaluate({"__import__": __import__}) is None


def test_too_long_rejected():
    text = "+".join(["1"] * 400)   # 799 字元
    assert len(text) > MAX_LEN
    with pytest.raises(ExprError, match="太長"):
        compile_expr(text, VARS)


def test_exactly_max_len_is_allowed_if_small_enough():
    text = "1" + " " * (MAX_LEN - 1)
    assert len(text) == MAX_LEN
    assert compile_expr(text, VARS).evaluate({}) == 1.0


def test_too_many_nodes_rejected():
    text = "+".join(["a"] * 80)    # 每個 a 有 Name + Load,加上 BinOp 與 Add,遠超過 200
    assert len(text) <= MAX_LEN
    with pytest.raises(ExprError, match="太複雜"):
        compile_expr(text, VARS)


def test_node_limit_boundary_value():
    assert MAX_NODES == 200
    compile_expr("+".join(["1"] * 40), VARS)   # 40 個常數 + 39 個 BinOp/Add + Expression,< 200


@pytest.mark.parametrize("text", [
    "(" * 240 + "1" + ")" * 240,       # 深層括號(長度 481)
    "-" * 499 + "1",                    # 深層一元負號
    "not " * 120 + "1",
    "abs(" * 99 + "1" + ")" * 99,
    "[" * 249 + "]" * 249,
])
def test_deep_nesting_rejected(text):
    assert len(text) <= MAX_LEN
    with pytest.raises(ExprError):
        compile_expr(text, VARS)


def test_huge_int_literal_rejected():
    with pytest.raises(ExprError):
        compile_expr("9" * 400, VARS)


def test_module_does_not_call_eval_exec_compile():
    # 靜態檢查:expr.py 裡不得出現 eval / exec / compile / __import__ 等呼叫(ast.parse 只解析,不執行)
    import ast

    import lab.models.expr as mod

    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    banned = {"eval", "exec", "compile", "__import__", "setattr", "open"}
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    called |= {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not (called & banned), called & banned
