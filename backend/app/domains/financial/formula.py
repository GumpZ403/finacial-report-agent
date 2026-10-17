"""公式求值（纯函数，无 IO）

`metric_dict.formula` 是受控算术表达式（只允许 + - * / 括号、科目名、abs/min/max/round），
用 AST 白名单求值，**不使用 eval**。依赖科目缺失时返回 None —— 不猜、不插值。

独立成模块的原因：这段逻辑与数据库无关，拆出来可以直接写单测，
不必再靠 L0 校验间接验证。
"""
from __future__ import annotations

import ast
import operator

from loguru import logger

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_UNSAFE_NAMES = {"eval", "exec", "open", "compile", "__import__", "globals", "locals"}
_SAFE_CALLS = {"abs": abs, "min": min, "max": max, "round": round}


class _FormulaEvaluator(ast.NodeVisitor):
    """只允许四则运算与白名单函数的公式求值（不用裸 eval）"""

    def __init__(self, values: dict[str, float | None]):
        self.values = values

    def eval(self, expr: str):
        tree = ast.parse(expr, mode="eval")
        return self.visit(tree.body)

    def visit_Constant(self, node: ast.Constant):  # type: ignore[override]
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"公式含非法常量: {node.value!r}")

    def visit_Name(self, node: ast.Name):  # type: ignore[override]
        if node.id in _UNSAFE_NAMES:
            raise ValueError(f"公式含禁用名称: {node.id}")
        value = self.values.get(node.id)
        if value is None:
            raise KeyError(node.id)
        return float(value)

    def visit_BinOp(self, node: ast.BinOp):  # type: ignore[override]
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"公式含不支持运算符: {type(node.op).__name__}")
        return op(self.visit(node.left), self.visit(node.right))

    def visit_UnaryOp(self, node: ast.UnaryOp):  # type: ignore[override]
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"公式含不支持一元运算符: {type(node.op).__name__}")
        return op(self.visit(node.operand))

    def visit_Call(self, node: ast.Call):  # type: ignore[override]
        if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_CALLS:
            raise ValueError("公式只允许 abs/min/max/round")
        return _SAFE_CALLS[node.func.id](*[self.visit(a) for a in node.args])

    def generic_visit(self, node):  # type: ignore[override]
        raise ValueError(f"公式含不支持语法: {type(node).__name__}")


def safe_eval_formula(expr: str, values: dict[str, float | None]) -> float | None:
    """按 metric_dict.formula 求值；依赖缺失/除零/非法语法 → None（不写占位值）"""
    if not expr or not expr.strip():
        return None
    try:
        val = _FormulaEvaluator(values).eval(expr)
    except KeyError:
        return None                                   # 依赖指标缺失
    except ZeroDivisionError:
        return None                                   # 分母为 0
    except (ValueError, SyntaxError, TypeError, OverflowError) as e:
        logger.warning(f"派生公式无法求值（已跳过）: {expr!r} → {e}")
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf
        return None
    return f
