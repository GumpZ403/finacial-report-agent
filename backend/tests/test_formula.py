"""公式求值（formula.py）：纯函数，不需要数据库

拆出独立模块的目的就是这些用例能直接写——以前只能靠 L0 校验间接覆盖。
"""
import pytest

from app.domains.financial.formula import safe_eval_formula


class TestBasicArithmetic:
    def test_simple_division(self):
        assert safe_eval_formula("net_profit / total_equity", {"net_profit": 10, "total_equity": 40}) == 0.25

    def test_operator_precedence(self):
        assert safe_eval_formula("a + b * c", {"a": 1, "b": 2, "c": 3}) == 7.0

    def test_parentheses(self):
        assert safe_eval_formula("(a + b) * c", {"a": 1, "b": 2, "c": 3}) == 9.0

    def test_unary_minus(self):
        assert safe_eval_formula("-a + 1", {"a": 2}) == -1.0

    def test_integer_and_float_constants(self):
        assert safe_eval_formula("a * 100", {"a": 0.5}) == 50.0


class TestMissingDependency:
    def test_missing_name_returns_none(self):
        """依赖科目缺失 → None（不猜、不写占位值）"""
        assert safe_eval_formula("a / b", {"a": 1}) is None

    def test_none_value_returns_none(self):
        assert safe_eval_formula("a + 1", {"a": None}) is None

    def test_empty_formula_returns_none(self):
        assert safe_eval_formula("", {}) is None
        assert safe_eval_formula("   ", {"a": 1}) is None


class TestGuards:
    def test_zero_division_returns_none(self):
        assert safe_eval_formula("a / b", {"a": 1, "b": 0}) is None

    def test_nan_and_inf_rejected(self):
        assert safe_eval_formula("a / 0.0", {"a": 0.0}) is None
        assert safe_eval_formula("a * 1e308 * 1e308", {"a": 1.0}) is None

    def test_unsafe_name_rejected(self):
        assert safe_eval_formula("eval + 1", {"eval": 1}) is None

    def test_unsafe_call_rejected(self):
        for expr in ("open(a)", "__import__(a)", "exec(a)"):
            assert safe_eval_formula(expr, {"a": 1, "open": 1, "exec": 1}) is None

    def test_bad_syntax_returns_none(self):
        assert safe_eval_formula("a +", {"a": 1}) is None

    def test_unsupported_node_returns_none(self):
        # 比较运算不在白名单
        assert safe_eval_formula("a > 1", {"a": 1}) is None


class TestAllowedCalls:
    @pytest.mark.parametrize("expr,values,expect", [
        ("abs(a)", {"a": -2}, 2.0),
        ("min(a, b)", {"a": 1, "b": 3}, 1.0),
        ("max(a, b)", {"a": 1, "b": 3}, 3.0),
        ("round(a)", {"a": 2.6}, 3.0),
    ])
    def test_whitelisted_functions(self, expr, values, expect):
        assert safe_eval_formula(expr, values) == expect
