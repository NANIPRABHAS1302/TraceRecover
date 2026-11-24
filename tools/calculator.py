"""Restricted arithmetic calculator; it never evaluates Python source."""

from __future__ import annotations

import ast
import operator
from typing import TypeAlias


Number: TypeAlias = int | float
_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _evaluate(node: ast.AST) -> Number:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 10:
            raise ValueError("Exponent magnitude must be at most 10.")
        return _BINARY_OPERATORS[type(node.op)](left, right)
    raise ValueError("Only numeric arithmetic expressions are allowed.")


def calculate(expression: str) -> Number:
    """Evaluate a bounded numeric expression using an explicit AST allowlist."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("Expression must be a non-empty string.")
    if len(expression) > 200:
        raise ValueError("Expression is too long.")
    try:
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 80:
            raise ValueError("Expression is too complex.")
        result = _evaluate(tree.body)
    except (SyntaxError, ArithmeticError, OverflowError) as exc:
        raise ValueError(f"Invalid arithmetic expression: {exc}") from exc
    if isinstance(result, float) and not (float("-inf") < result < float("inf")):
        raise ValueError("Expression result must be finite.")
    return result