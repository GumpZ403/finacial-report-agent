"""评测用例（`pytest --eval`）：守住"取数正确性"这条底线

L3 真值来自长表（financial_metrics），被测代码读宽表（financial_core）——
所以它同时校验了长宽镜像一致性、期次匹配与单位映射。
"""
import pytest

from scripts.eval.run_l3 import evaluate, load_baseline


@pytest.mark.eval
def test_l3_not_below_baseline():
    base = load_baseline()
    assert base, "缺少基线，先跑 run_l3 --write-baseline"
    r = evaluate()
    assert r["missing"] == 0, f"取不到数的用例: {r['failures']}"
    assert r["pass_rate"] >= base["pass_rate"] - 1e-4, r


@pytest.mark.eval
def test_l3_unit_mapping():
    r = evaluate(limit=50)
    assert r["unit_ok_rate"] >= 0.99, r
