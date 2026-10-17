"""能力边界不变量：宽表每一列都必须被"处置过"

这是 M0 的验收标准落成代码：
每个指标列必须落在「有映射源 / 有 formula / 在 capability.UNSUPPORTED_METRICS 里」之一。
新增列时如果忘了处置，这个测试会直接失败——而不是等到用户来问"为什么查不到"。
"""
from app.domains.financial.capability import UNSUPPORTED_METRICS
from app.domains.financial.ingest.mapping import (
    EM_BALANCE_MAP, EM_CASHFLOW_MAP, EM_PROFIT_MAP,
)
from app.domains.financial.models import FinancialCore
from scripts.seed_metric_dict import SEED

BASE_COLUMNS = {"period_id", "company_id", "period"}


def _core_columns() -> set[str]:
    return set(FinancialCore.__table__.columns.keys()) - BASE_COLUMNS


def _mapped() -> set[str]:
    return (set(EM_BALANCE_MAP.values()) | set(EM_PROFIT_MAP.values())
            | set(EM_CASHFLOW_MAP.values()))


def _derived() -> set[str]:
    """有公式的指标（formula 非空）"""
    return {row[0] for row in SEED if row[5]}


class TestCapabilityInvariants:
    def test_every_core_column_is_classified(self):
        unclassified = _core_columns() - _mapped() - _derived() - set(UNSUPPORTED_METRICS)
        assert not unclassified, f"未处置的宽表列（无映射/无公式/未声明不支持）: {sorted(unclassified)}"

    def test_mapping_only_targets_existing_columns(self):
        """映射到了一个宽表里不存在的列 = 数据写不进去，等于白拉"""
        ghost = _mapped() - _core_columns()
        assert not ghost, f"映射目标不存在于 FinancialCore: {sorted(ghost)}"

    def test_unsupported_has_no_source(self):
        """不支持清单里的指标不能同时又声称有映射或公式（自相矛盾）"""
        assert not (set(UNSUPPORTED_METRICS) & _mapped()), "不支持清单里出现了有映射源的指标"
        assert not (set(UNSUPPORTED_METRICS) & _derived()), "不支持清单里出现了有公式的指标"

    def test_unsupported_metrics_have_reasons(self):
        empty = [c for c, r in UNSUPPORTED_METRICS.items() if not r.strip()]
        assert not empty, f"不支持指标缺原因: {empty}"

    def test_derived_columns_exist(self):
        ghost = _derived() - _core_columns()
        assert not ghost, f"公式算出的指标没有宽表列: {sorted(ghost)}"

    def test_capability_covers_expected_size(self):
        """能力边界别缩水：目前支持 86 个有源指标 + 13 个明确不支持"""
        assert len(_core_columns()) >= 100
        assert len(UNSUPPORTED_METRICS) == 13
