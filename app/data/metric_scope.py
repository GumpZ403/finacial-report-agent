# -*- coding: utf-8 -*-
"""行业指标模板适用范围（方案 v1.1 §3「行业模板」）

方案要求 `company_master.industry_template ∈ {general, financial, other}` 驱动指标集：
financial 模板下毛利率、存货/应收等经营性指标无意义，须置为 N/A。

此前该字段从未被任何代码读取，导致招商银行被填入 51% 的"毛利率"并进入 RAG 文本。
本模块把"哪些指标在哪个模板下不适用"显式声明出来。

N/A 的存储方式（本次约定）：
- **不适用的指标照常入库**（保留可审计的原始数据，不丢信息）；
- 消费方（textify / 质检 / 同行对比 / 评分）必须经 `is_applicable()` 过滤，
  不得把 N/A 指标写进文本、参与对比或计入完整性分母。

维护说明：金融模板的 N/A 清单是数据层的显式声明，后续可改为从
`metric_dict.industry_scope` 派生（当字典扩展出完整金融指标集时）。
"""
from __future__ import annotations

TEMPLATE_GENERAL = "general"
TEMPLATE_FINANCIAL = "financial"
TEMPLATE_OTHER = "other"

DEFAULT_TEMPLATE = TEMPLATE_GENERAL
VALID_TEMPLATES = frozenset({TEMPLATE_GENERAL, TEMPLATE_FINANCIAL, TEMPLATE_OTHER})

# 金融业（银行/保险/券商）无意义的经营性科目：
#   - 第一组：经营循环类（存货/应收应付/周转）——金融业无此口径
#   - 第二组（2026-09-26 实测招行/兴业/平安三表列名）：银行无"财务/销售/管理费用"、
#     "货币资金"、"应付账款"科目；银行资产负债表不按流动/非流动划分，
#     故 total_current_* 及以其为分母的流动比率类均不适用
_FINANCIAL_NOT_APPLICABLE = frozenset({
    # 经营循环类
    "operate_cost",            # 金融业无"营业成本"口径（东财给出的是营业支出近似值）
    "gross_profit",
    "gross_margin",
    "inventory",
    "inventory_turnover",
    "inventory_days",
    "receivable",
    "receivable_turnover",
    "receivable_days",
    "payable_turnover",
    "payable_days",
    "cash_conversion_cycle",
    # 银行科目结构类（2026-09-26）
    "finance_expense",          # 银行利润表无"财务费用"（利息支出单独列示）
    "sale_expense",             # 银行无"销售费用"
    "manage_expense",           # 银行无"管理费用"（业务及管理费为另一科目，未映射）
    "accounts_payable",         # 银行资产负债表无"应付账款"
    "monetary_funds",           # 银行无"货币资金"（现金及存放央行款项为另一科目）
    "total_current_assets",     # 银行资产负债表不划分流动资产
    "total_current_liab",       # 银行资产负债表不划分流动负债
    "current_ratio",            # 流动比率（分母 total_current_liab 银行不适用）
    "quick_ratio",              # 速动比率
    "cash_ratio",               # 现金比率
    "current_asset_turnover",   # 流动资产周转率
    "quick_ratio_conservative", # 保守速动比率
})

_NOT_APPLICABLE_BY_TEMPLATE: dict[str, frozenset[str]] = {
    TEMPLATE_GENERAL: frozenset(),
    TEMPLATE_FINANCIAL: _FINANCIAL_NOT_APPLICABLE,
    TEMPLATE_OTHER: frozenset(),
}


def normalize_template(template: str | None) -> str:
    """模板名归一化：None/空/未知 → general（兜底）"""
    t = (template or "").strip().lower()
    return t if t in VALID_TEMPLATES else DEFAULT_TEMPLATE


def not_applicable_metrics(template: str | None) -> frozenset[str]:
    """该模板下不适用的指标集合"""
    return _NOT_APPLICABLE_BY_TEMPLATE[normalize_template(template)]


def is_applicable(metric_code: str, template: str | None) -> bool:
    """指标在给定模板下是否适用"""
    return metric_code not in not_applicable_metrics(template)


def filter_applicable(metric_codes, template: str | None) -> list[str]:
    """过滤出该模板下适用的指标（保持原顺序）"""
    na = not_applicable_metrics(template)
    return [c for c in metric_codes if c not in na]
