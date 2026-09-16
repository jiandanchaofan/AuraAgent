"""A hand-written AST interpreter for arithmetic expressions — deliberately
NOT `eval()`/`exec()` with restricted globals.

Why this matters: a restricted-`eval` sandbox is a well-known bug class
(`().__class__.__bases__[0].__subclasses__()`, `__globals__`, format-string
tricks) precisely because `eval` still runs real Python bytecode and any
gap in the globals/builtins denylist is an escape hatch. This module never
calls `eval`/`exec` at all — `_eval_node()` is a recursive dispatcher that
only knows how to do the handful of things explicitly listed below. There
is no code path that reaches `ast.Attribute`, `ast.Subscript`, `ast.Name`
(as a value), `ast.Lambda`, comprehensions, or string literals — they all
fall through to the `UnsafeExpressionError` default case.
"""
from __future__ import annotations

import ast
import math
import operator
from typing import Callable

MAX_EXPRESSION_LENGTH = 200
MAX_EXPONENT = 1000  # guards against e.g. `2 ** 999999999` hanging/exhausting memory

ALLOWED_FUNCS: dict[str, Callable[..., float]] = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
}

_ALLOWED_BINOPS: dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
}

_ALLOWED_UNARYOPS: dict[type, Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class UnsafeExpressionError(Exception):
    """Raised when an expression uses syntax outside the safe whitelist."""


def evaluate(expression: str) -> float:
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise UnsafeExpressionError(f"Expression exceeds {MAX_EXPRESSION_LENGTH} characters.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"Invalid syntax: {exc}") from exc
    return _eval_node(tree.body)


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise UnsafeExpressionError(f"Unsupported constant: {node.value!r}")
        return node.value

    if isinstance(node, ast.BinOp):
        op_func = _ALLOWED_BINOPS.get(type(node.op))
        if op_func is None:
            raise UnsafeExpressionError(f"Unsupported operator: {type(node.op).__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise UnsafeExpressionError(f"Exponent too large (max {MAX_EXPONENT}).")
        return op_func(left, right)

    if isinstance(node, ast.UnaryOp):
        op_func = _ALLOWED_UNARYOPS.get(type(node.op))
        if op_func is None:
            raise UnsafeExpressionError(f"Unsupported unary operator: {type(node.op).__name__}")
        return op_func(_eval_node(node.operand))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCS:
            raise UnsafeExpressionError("Only sqrt, abs, round, min, max may be called.")
        if node.keywords:
            raise UnsafeExpressionError("Keyword arguments are not supported.")
        args = [_eval_node(arg) for arg in node.args]
        return ALLOWED_FUNCS[node.func.id](*args)

    raise UnsafeExpressionError(f"Unsupported expression element: {type(node).__name__}")
