# -*- coding: utf-8 -*-
"""
指标字典种子脚本（方案 v1.1 附录 B + 东财三表扩展）
- 写入 metric_dict（含东财原生科目 + 派生公式）
- 幂等：已存在的 metric_code 更新、别名 upsert
- 公式只基于东财三表真实科目（见 app/data/datasource.py 映射表），无依据的不编造
用法：.venv/Scripts/python.exe scripts/seed_metric_dict.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from app.data.repository import get_db
from app.data.schema import MetricDict

# (code, name_cn, aliases, unit_type, direction, formula, industry_scope, is_core占位, description)
# 公式说明：
#   - ""         = 原生科目，接口直给（东财三表），无公式
#   - "a / b"    = 派生指标，公式基于东财三表科目；期末口径简化（周转类未用平均余额）
#   - 无源指标（dividend_per_share/employee_count/roe_weighted 等）保持 formula="" 且无映射，留空
SEED = [
    # ---- 规模与盈利 ----
    ("revenue", "营业收入", ["营收", "收入", "营业总收入", "主营业务收入"], "amount", "positive", "", "general", 1, "营业收入（元），东财利润表 TOTAL_OPERATE_INCOME"),
    ("operate_cost", "营业成本", ["营业成本"], "amount", "negative", "", "general", 1,
     "营业成本（元），东财利润表 OPERATE_COST（真口径，实测茅台毛利率 89.7% 吻合）"),
    ("gross_profit", "毛利", ["毛利润"], "amount", "positive", "revenue - operate_cost", "general", 1, "毛利 = 营业收入 - 营业成本（派生）"),
    ("gross_margin", "毛利率", ["销售毛利率", "毛利率(销售)"], "ratio", "positive", "gross_profit / revenue", "general", 1, "毛利率 = 毛利/营收（派生，小数 0.25=25%）"),
    ("net_profit", "净利润", ["归母净利润", "扣非净利润", "利润总额"], "amount", "positive", "", "general", 1, "净利润（元，含少数股东），东财利润表 NETPROFIT"),
    ("net_margin", "净利率", ["销售净利率"], "ratio", "positive", "net_profit / revenue", "general", 1, "净利率 = 净利润/营收（派生，小数）"),
    ("roe", "净资产收益率", ["ROE", "加权ROE"], "ratio", "positive", "net_profit_parent / total_parent_equity", "general", 1, "ROE = 归母净利/归母净资产（派生，小数；期末口径）"),
    ("roa", "总资产收益率", ["ROA", "资产收益率"], "ratio", "positive", "net_profit / total_assets", "general", 1, "ROA = 净利润/总资产（派生，小数）"),
    # ---- 成长 ----
    ("revenue_yoy", "营收同比增长率", ["营业收入同比", "营收增速", "收入同比"], "ratio", "positive", "", "general", 1, "营收同比（小数），东财利润表 TOTAL_OPERATE_INCOME_YOY ÷100"),
    ("net_profit_yoy", "净利润同比增长率", ["净利润同比", "利润增速", "净利同比"], "ratio", "positive", "", "general", 1, "归母净利同比（小数），东财利润表 PARENT_NETPROFIT_YOY ÷100"),
    ("revenue_cagr_3y", "营收三年复合增速", ["3年复合增速", "CAGR3"], "ratio", "positive", "", "general", 1, "跨期复合增速需跨报告期计算，formula 引擎单期求值不支持 → v1 无源，保持空"),
    # ---- 现金流 ----
    ("operating_cash_flow", "经营活动现金流净额", ["经营现金流", "经营现金净流量", "OCF"], "amount", "positive", "", "general", 1, "经营现金流净额（元），东财现金流量表 NETCASH_OPERATE"),
    ("investing_cash_flow", "投资活动现金流净额", ["投资现金流"], "amount", "neutral", "", "general", 1, "投资现金流净额（元），东财现金流量表 NETCASH_INVEST"),
    ("financing_cash_flow", "筹资活动现金流净额", ["筹资现金流"], "amount", "neutral", "", "general", 1, "筹资现金流净额（元），东财现金流量表 NETCASH_FINANCE"),
    ("ocf_to_net_profit", "经营现金流/净利润", ["净现比", "盈利现金比率"], "ratio", "positive", "operating_cash_flow / net_profit", "general", 1, "净现比 = 经营现金流/净利润（派生）"),
    ("free_cash_flow", "自由现金流", ["FCF"], "amount", "positive", "operating_cash_flow - capex", "general", 1, "FCF = 经营现金流 - 资本开支（派生；资本开支=购建固定资产等支出）"),
    # ---- 营运效率 ----
    ("inventory", "存货", ["存货余额"], "amount", "neutral", "", "general", 1, "存货（元），东财资产负债表 INVENTORY"),
    ("inventory_turnover", "存货周转率", ["存货周转次数"], "times", "positive", "operate_cost / inventory", "general", 1, "存货周转率 = 营业成本/存货（派生，期末口径简化）"),
    ("receivable", "应收账款", ["应收票据及应收账款"], "amount", "neutral", "", "general", 1, "应收票据及应收账款（元），东财资产负债表 NOTE_ACCOUNTS_RECE"),
    ("receivable_turnover", "应收账款周转率", ["应收周转率", "应收周转次数"], "times", "positive", "revenue / receivable", "general", 1, "应收周转率 = 营收/应收（派生，期末口径简化）"),
    ("payable_turnover", "应付账款周转率", ["应付周转率"], "times", "positive", "operate_cost / accounts_payable", "general", 1, "应付周转率 = 营业成本/应付（派生，期末口径简化）"),
    ("cash_conversion_cycle", "现金转换周期", ["现金循环周期", "CCC"], "days", "negative",
     "inventory_days + receivable_days - payable_days", "general", 1, "现金转换周期 = 存货天数+应收天数-应付天数（派生，天）"),
    # ---- 偿债与结构 ----
    ("total_assets", "总资产", ["资产总计", "资产总额"], "amount", "neutral", "", "general", 1, "总资产（元），东财资产负债表 TOTAL_ASSETS"),
    ("total_liabilities", "总负债", ["负债合计", "负债总额"], "amount", "negative", "", "general", 1, "总负债（元），东财资产负债表 TOTAL_LIABILITIES"),
    ("total_equity", "股东权益", ["净资产", "所有者权益", "归母权益"], "amount", "positive", "", "general", 1, "股东权益合计（含少数股东，元），东财资产负债表 TOTAL_EQUITY"),
    ("debt_ratio", "资产负债率", ["负债率"], "ratio", "negative", "total_liabilities / total_assets", "general", 1, "资产负债率 = 总负债/总资产（派生，小数）"),
    ("current_ratio", "流动比率", [], "ratio", "positive", "total_current_assets / total_current_liab", "general", 1, "流动比率 = 流动资产/流动负债（派生，倍）"),
    ("quick_ratio", "速动比率", ["酸性测试比率"], "ratio", "positive", "(total_current_assets - inventory) / total_current_liab", "general", 1, "速动比率 = (流动资产-存货)/流动负债（派生，倍；未扣预付等）"),
    ("interest_coverage", "利息保障倍数", ["利息覆盖率", "已获利息倍数"], "times", "positive", "", "general", 1, "东财三表无可靠利息费用列（财务费用含利息收入混项），v1 无源，保持空"),
    # ---- 每股/其他 ----
    ("eps", "每股收益", ["EPS", "基本每股收益", "摊薄每股收益"], "per_share", "positive", "", "general", 1, "基本每股收益（元/股），东财利润表 BASIC_EPS"),
    ("bps", "每股净资产", ["BPS", "每股账面价值"], "per_share", "positive", "", "general", 1, "BPS 需归母净资产/总股本，股本无可靠源 → v1 无源，保持空"),
    ("dividend_per_share", "每股股利", ["每股分红", "每股股息", "DPS"], "per_share", "positive", "", "general", 1, "东财三表无每股股利 → v1 无源，保持空"),
    ("employee_count", "员工人数", ["在职员工", "员工总数"], "count", "neutral", "", "general", 1, "东财三表无员工数据 → v1 无源，保持空"),
    # ---- 长表扩展指标（历史批次，部分已有东财原生源）----
    ("net_profit_parent", "归母净利润", ["归属于母公司股东的净利润", "归母净利"], "amount", "positive", "", "general", 1, "归母净利润（元），东财利润表 PARENT_NETPROFIT"),
    ("net_profit_deducted", "扣非净利润", ["扣除非经常性损益后的净利润", "扣非净利"], "amount", "positive", "", "general", 1, "扣非归母净利润（元），东财利润表 DEDUCT_PARENT_NETPROFIT"),
    ("total_operating_cost", "营业总成本", ["营业总成本"], "amount", "negative", "", "general", 1, "营业总成本（元），东财利润表 TOTAL_OPERATE_COST（含税金及附加+期间费用+减值）"),
    ("goodwill", "商誉", ["商誉余额"], "amount", "neutral", "", "general", 1, "商誉（元），东财资产负债表 GOODWILL"),
    ("operate_cost_ratio", "主营业务成本率", ["成本率", "销售成本率"], "ratio", "negative", "", "general", 0,
     "新浪专用派生中间量，新浪已退役 → deprecated，无源"),
    ("roe_weighted", "加权净资产收益率", ["加权ROE"], "ratio", "positive", "", "general", 1, "东财三表无加权 ROE → v1 无源，保持空"),
    ("inventory_days", "存货周转天数", ["存货周转期"], "days", "negative", "365 / inventory_turnover", "general", 1, "存货周转天数 = 365/存货周转率（派生）"),
    ("receivable_days", "应收账款周转天数", ["应收周转天数"], "days", "negative", "365 / receivable_turnover", "general", 1, "应收周转天数 = 365/应收周转率（派生）"),
    ("payable_days", "应付账款周转天数", ["应付周转天数"], "days", "neutral", "365 / payable_turnover", "general", 1, "应付周转天数 = 365/应付周转率（派生）"),
    ("ocf_per_share", "每股经营现金流", ["每股经营性现金流"], "per_share", "positive", "", "general", 1, "每股经营现金流需股本，股本无可靠源 → v1 无源，保持空"),
    ("roe_avg", "净资产收益率(平均)", ["ROE_平均"], "ratio", "positive", "", "general", 1, "平均口径 ROE 需期初期末均值 → v1 无源，保持空"),
    ("roic", "投入资本回报率", ["ROIC"], "ratio", "positive", "", "general", 1, "ROIC 需有息负债+权益等，东财三表可近似但口径复杂 → v1 无源，保持空"),
    ("operating_margin", "营业利润率", ["营业利润率"], "ratio", "positive", "operating_profit / revenue", "general", 1, "营业利润率 = 营业利润/营收（派生，小数）"),
    ("effective_tax_rate", "所得税/利润总额", ["实际税率"], "ratio", "neutral", "income_tax / total_profit", "general", 1, "实际税率 = 所得税费用/利润总额（派生，小数）"),
    ("ocf_to_revenue", "经营活动净现金/销售收入", ["经营现金流对营收比"], "ratio", "positive", "operating_cash_flow / revenue", "general", 1, "经营现金流对营收比（派生，倍）"),
    ("equity_multiplier", "权益乘数", ["财务杠杆"], "times", "neutral", "total_assets / total_equity", "general", 1, "权益乘数 = 总资产/股东权益（派生，倍）"),
    ("debt_to_equity", "产权比率", ["负债/股东权益"], "times", "negative", "total_liabilities / total_equity", "general", 1, "产权比率 = 总负债/股东权益（派生，倍）"),
    ("cash_ratio", "现金比率", ["现金类资产/流动负债"], "ratio", "positive", "monetary_funds / total_current_liab", "general", 1, "现金比率 = 货币资金/流动负债（派生，倍；未含交易性金融资产）"),
    ("quick_ratio_conservative", "保守速动比率", [], "times", "positive", "", "general", 1, "保守速动比率需更严口径，v1 无源，保持空"),
    ("total_asset_turnover", "总资产周转率", ["资产周转率"], "times", "positive", "revenue / total_assets", "general", 1, "总资产周转率 = 营收/总资产（派生，次；期末口径）"),
    ("current_asset_turnover", "流动资产周转率", [], "times", "positive", "revenue / total_current_assets", "general", 1, "流动资产周转率 = 营收/流动资产（派生，次；期末口径）"),
    ("fixed_asset_turnover", "固定资产周转率", [], "times", "positive", "revenue / fixed_asset", "general", 1, "固定资产周转率 = 营收/固定资产（派生，次；期末口径）"),
    ("total_assets_yoy", "总资产增长率", ["资产增速"], "ratio", "positive", "", "general", 1, "总资产同比（小数），东财资产负债表 TOTAL_ASSETS_YOY ÷100"),
    ("equity_yoy", "净资产增长率", ["净资产增速"], "ratio", "positive", "", "general", 1, "净资产同比（小数），东财资产负债表 TOTAL_EQUITY_YOY ÷100"),
    # ---- 东财三表原生科目（2026-09-26 新增：严格来自接口列，见 datasource.py 映射表）----
    ("operating_profit", "营业利润", ["营业利润"], "amount", "positive", "", "general", 1, "营业利润（元），东财利润表 OPERATE_PROFIT"),
    ("total_profit", "利润总额", ["利润总额"], "amount", "positive", "", "general", 1, "利润总额（元），东财利润表 TOTAL_PROFIT"),
    ("income_tax", "所得税费用", ["所得税"], "amount", "negative", "", "general", 1, "所得税费用（元），东财利润表 INCOME_TAX"),
    ("finance_expense", "财务费用", ["财务费用"], "amount", "negative", "", "general", 1, "财务费用（元），东财利润表 FINANCE_EXPENSE"),
    ("sale_expense", "销售费用", ["销售费用"], "amount", "negative", "", "general", 1, "销售费用（元），东财利润表 SALE_EXPENSE"),
    ("manage_expense", "管理费用", ["管理费用"], "amount", "negative", "", "general", 1, "管理费用（元），东财利润表 MANAGE_EXPENSE"),
    ("monetary_funds", "货币资金", ["货币资金"], "amount", "neutral", "", "general", 1, "货币资金（元），东财资产负债表 MONETARYFUNDS"),
    ("total_current_assets", "流动资产合计", ["流动资产"], "amount", "neutral", "", "general", 1, "流动资产合计（元），东财资产负债表 TOTAL_CURRENT_ASSETS"),
    ("total_current_liab", "流动负债合计", ["流动负债"], "amount", "negative", "", "general", 1, "流动负债合计（元），东财资产负债表 TOTAL_CURRENT_LIAB"),
    ("total_parent_equity", "归母净资产", ["归母权益", "归属于母公司股东权益"], "amount", "positive", "", "general", 1, "归母净资产（元），东财资产负债表 TOTAL_PARENT_EQUITY"),
    ("accounts_payable", "应付票据及应付账款", ["应付账款", "应付票据及应付"], "amount", "negative", "", "general", 1, "应付票据及应付账款（元），东财资产负债表 NOTE_ACCOUNTS_PAYABLE"),
    ("fixed_asset", "固定资产", ["固定资产净额"], "amount", "neutral", "", "general", 1, "固定资产（元），东财资产负债表 FIXED_ASSET"),
    ("capex", "资本开支", ["购建固定资产支出", "购建长期资产"], "amount", "negative", "", "general", 1, "资本开支 = 购建固定资产/无形资产等支付的现金（元），东财现金流量表 CONSTRUCT_LONG_ASSET"),
    # ---- 金融行业模板（financial）----
    ("interest_income", "利息净收入", ["净利息收入"], "amount", "positive", "", "financial", 0, "金融业：利息净收入（元）"),
    ("net_interest_margin", "净息差", ["NIM"], "ratio", "positive", "", "financial", 0, "净息差（小数）"),
    ("non_performing_loan_ratio", "不良贷款率", ["不良率", "NPL"], "ratio", "negative", "", "financial", 0, "不良率（小数）"),
    ("provision_coverage", "拨备覆盖率", ["拨备"], "ratio", "positive", "", "financial", 0, "拨备覆盖率（小数）"),
    ("capital_adequacy_ratio", "资本充足率", ["资本充足率"], "ratio", "positive", "", "financial", 0, "资本充足率（小数）"),
]


def main():
    db = get_db()
    n_metric = 0
    with db.get_session() as s:
        for code, name_cn, aliases, unit_type, direction, formula, scope, _is_core, desc in SEED:
            m = s.get(MetricDict, code)
            if m is None:
                m = MetricDict(metric_code=code)
                s.add(m)
                n_metric += 1
            m.name_cn = name_cn
            m.aliases = aliases
            m.unit_type = unit_type
            m.direction = direction
            m.formula = formula
            m.industry_scope = scope
            m.description = desc
            m.deprecated = 0
        s.commit()
    logger.info(f"指标字典 seed 完成: {len(SEED)} 指标, 新增 {n_metric} 条字典（别名已并入 aliases JSON）")


if __name__ == "__main__":
    main()
