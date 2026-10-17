"""pytest 配置：评测用例默认跳过，`pytest --eval` 才跑

原因：评测（L3/L2）读真实数据库，比业务单测慢且依赖数据状态；
默认测试要保持"快且无外部依赖"。
"""
import pytest


def pytest_addoption(parser):
    parser.addoption("--eval", action="store_true", default=False,
                     help="运行标记为 eval 的评测用例（需要真实数据库）")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--eval"):
        return
    skip = pytest.mark.skip(reason="评测用例，需 pytest --eval 运行")
    for item in items:
        if "eval" in item.keywords:
            item.add_marker(skip)
