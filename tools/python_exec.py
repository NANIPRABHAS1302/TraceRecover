"""A safe evaluator for Python expressions with no imports or side effects."""

from __future__ import annotations

import ast
import operator
from typing import Any


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_SAFE_FUNCTIONS = {
    "abs": abs,
    "len": len,
    "max": max,
    "min": min,
    "sum": sum,
}


def _evaluate(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float, str, bool, type(None)):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [_evaluate(element) for element in node.elts]
        if isinstance(node, ast.List):
            return values
        if isinstance(node, ast.Tuple):
            return tuple(values)
        return set(values)
    if isinstance(node, ast.Dict):
        return {_evaluate(key): _evaluate(value) for key, value in zip(node.keys, node.values)}
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and (
            not isinstance(right, (int, float)) or abs(right) > 10
        ):
            raise ValueError("Exponent magnitude must be at most 10.")
        result = _BINARY_OPERATORS[type(node.op)](left, right)
        if isinstance(result, (str, list, tuple)) and len(result) > 10000:
            raise ValueError("Expression result is too large.")
        return result
    if isinstance(node, ast.Subscript):
        return _evaluate(node.value)[_evaluate(node.slice)]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function = _SAFE_FUNCTIONS.get(node.func.id)
        if function is None or node.keywords:
            raise ValueError("Only abs, len, max, min, and sum calls are allowed.")
        return function(*[_evaluate(argument) for argument in node.args])
    raise ValueError("Only safe literals, arithmetic, indexing, and approved functions are allowed.")


def execute(code: str) -> Any:
    """Evaluate one restricted Python expression without using eval or exec."""
    if not isinstance(code, str) or not code.strip():
        raise ValueError("Python expression must be a non-empty string.")
    if len(code) > 1000:
        raise ValueError("Python expression is too long.")
    try:
        tree = ast.parse(code, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 100:
            raise ValueError("Python expression is too complex.")
        return _evaluate(tree.body)
    except (SyntaxError, ArithmeticError, TypeError, IndexError, KeyError) as exc:
        raise ValueError(f"Could not evaluate safe Python expression: {exc}") from exc