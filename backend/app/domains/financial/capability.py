"""能力边界：明确"系统不支持的指标"（单一事实来源）

为什么要这份清单：查询返回空时，用户看到的是"这家公司没有该指标"，而真实原因可能是
"本系统没实现"。两者混为一谈会让人误判公司数据质量——所以不支持必须显式声明。

规则（三条同时成立才算"不支持"）：
1. 没有映射源（东财三表拿不到）
2. 没有 formula（不能派生）
3. 在下面的清单里登记了原因

`tests/test_capability.py` 会检查：宽表每个指标列都必须落在
「有映射源 / 有 formula / 在本清单里」三者之一，否则测试失败——
新增列时如果忘了处置，CI 会直接拦住。
"""
from __future__ import annotations

UNSUPPORTED_METRICS: dict[str, str] = {
    "bps": "需要总股本，东财三表不提供",
    "ocf_per_share": "需要总股本",
    "dividend_per_share": "需要分红方案数据",
    "employee_count": "需要年报/公告文本",
    "roe_weighted": "需加权平均净资产（分红/增发时点），三表无法精确还原",
    "roe_avg": "需跨期平均净资产，单期公式引擎不支持",
    "revenue_cagr_3y": "需跨期复合增速，单期公式引擎不支持",
    "interest_coverage": "需要利息费用（利润表只给财务费用净额）",
    "capital_adequacy_ratio": "银行专项指标，东财三表不提供",
    "net_interest_margin": "银行专项指标",
    "non_performing_loan_ratio": "银行专项指标",
    "provision_coverage": "银行专项指标",
    "interest_income": "银行专项指标",
}


def is_unsupported(metric_code: str) -> bool:
    return metric_code in UNSUPPORTED_METRICS


def reason(metric_code: str) -> str:
    return UNSUPPORTED_METRICS.get(metric_code, "")
