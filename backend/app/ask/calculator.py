"""Arithmetic for answers, so a derived figure (a difference, a ratio, a change) comes from a tool and can be checked.

Only numbers, + - * / ( ), unary minus and round(x, n). No names, no calls other than round, no exponent, so it
cannot run code or be used to exhaust the machine.
"""
from __future__ import annotations

import ast
import re
import operator

from .semantic import QueryError

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
MAX = 1e18


def evaluate(expression: str) -> float:
    # Thousands separators go ("1,200"); a comma between arguments stays ("round(2/3, 2)").
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", expression).replace("$", "").replace("%", "").strip()
    if not text or len(text) > 300:
        raise QueryError("Give an arithmetic expression of up to 300 characters")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise QueryError(f"'{expression}' is not an arithmetic expression") from None

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Div) and b == 0:
                raise QueryError("Division by zero")
            v = OPS[type(n.op)](a, b)
            if abs(v) > MAX:
                raise QueryError("That figure is too large")
            return v
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "round" and not n.keywords and 1 <= len(n.args) <= 2:
            digits = int(ev(n.args[1])) if len(n.args) == 2 else 0
            return round(ev(n.args[0]), max(0, min(digits, 6)))
        raise QueryError("Only numbers, + - * / ( ) and round(x, n) are allowed")

    v = ev(tree)
    return round(v, 6)
