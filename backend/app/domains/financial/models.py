"""
数据模型定义（SQLAlchemy 2.0 ORM）。

约定：
- financial_metrics（长表）是唯一真相；financial_core（宽表）是镜像，只允许镜像长表已有值
- 比率在库内存小数（unit=ratio），金额单位为元（unit=CNY）；语义以 metric_dict.unit_type 为准
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime,
    ForeignKey, JSON, UniqueConstraint, Index,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


# ============ 财报表模型 ============

# 公司主表：全部财报数据的根（公司档案 + 行业归属）
class CompanyMaster(Base):
    __tablename__ = "company_master"

    company_id = Column(Integer, primary_key=True, autoincrement=True)  # 主键，自增（代理主键，全库外键指向它）
    code = Column(String(20), unique=True, nullable=False)              # 统一 6 位代码，不含交易所后缀（如 300750）
    name = Column(String(200), nullable=False)                          # 公司全称/简称
    name_aliases = Column(JSON, default=list)                           # 别名列表（如 ["宁德时代","CATL"]，供 NL 匹配）
    industry_l1 = Column(String(100), default="")                       # 一级行业（金融/制造/消费…）
    industry_l2 = Column(String(100), default="")                       # 二级行业（电池/白酒…），同行组划分依据
    industry_template = Column(String(20), default="general")           # 行业指标模板: general/financial（决定指标适用性）
    exchange = Column(String(20), default="")                           # 交易所: SSE/SZSE/BSE（展示层补后缀用）
    listing_date = Column(String(20), default="")                       # 上市日期（字符串，不参与日期计算）
    currency = Column(String(10), default="CNY")                        # 货币单位
    status = Column(String(20), default="active")                       # 状态: active/inactive（软停用）


# 报告期表：公司 × 归一化期次（如 2024Q3），是长表/宽表挂载的轴
class FinancialPeriod(Base):
    __tablename__ = "financial_periods"

    period_id = Column(Integer, primary_key=True, autoincrement=True)   # 主键，自增
    company_id = Column(Integer, ForeignKey("company_master.company_id"),
                        nullable=False, index=True)                     # 所属公司（外键）
    period = Column(String(20), nullable=False)                         # 归一化期次标识，如 2024Q3
    period_end = Column(String(20), default="")                         # 报告期末日期（如 2024-09-30）
    period_type = Column(String(10), default="FY")                      # 期次类型: FY/H1/Q1/Q3
    is_single_quarter = Column(Integer, default=0)                      # 0=累计口径, 1=单季口径
    report_scope = Column(String(20), default="consolidated")           # 合并口径: consolidated/parent
    audited = Column(Integer, default=0)                                # 是否审计
    source = Column(String(20), default="akshare")                      # 数据源
    source_version = Column(String(50), default="")                     # 数据源版本
    raw_payload_id = Column(Integer, nullable=True)                     # 关联原始落盘 payload（回放溯源）
    fetched_at = Column(String(30), default="")                         # 拉取时间
    fetch_status = Column(String(80), default="ok")                     # 采集完整性: ok / partial:<失败表>；partial 时该期数据不全，不得当作完整数据

    __table_args__ = (
        UniqueConstraint("company_id", "period", "report_scope", name="uq_report_period"),  # 同公司同期次同口径唯一
    )


# 指标长表（EAV，唯一真相）：一行 = 某报告期 × 某指标；任意接口格式在此消解为统一三元组
class FinancialMetric(Base):
    __tablename__ = "financial_metrics"

    period_id = Column(Integer, ForeignKey("financial_periods.period_id"),
                       primary_key=True, index=True)                    # 所属报告期（复合主键，外键）
    metric_code = Column(String(50), primary_key=True)                  # 指标代码（复合主键，关联 metric_dict）
    value = Column(Float, nullable=True)                                # 指标值（比率存小数，金额单位为元）
    unit = Column(String(12), default="")                               # 单位语义: CNY/ratio/times/days/per_share/count
    is_estimated = Column(Integer, default=0)                           # 1=公式派生/回算未通过（非源数据）
    source = Column(String(20), default="")                             # 取值来源: eastmoney/sina/derived/manual
    updated_at = Column(String(30), default="")                         # 该值最后一次**实际变化**的时间（未变不推进）

    __table_args__ = (
        Index("ix_metrics_metric_code", "metric_code"),                 # 按指标查全部公司加速
        Index("ix_metrics_source", "source"),                           # 按来源筛选加速
    )


# 核心宽表（镜像）：一行 = 一个报告期，一列 = 一个指标；只镜像长表已有值，缺值 NULL
class FinancialCore(Base):
    __tablename__ = "financial_core"

    period_id = Column(Integer, ForeignKey("financial_periods.period_id"), primary_key=True)  # 主键=报告期（1:1）
    company_id = Column(Integer, index=True)                            # 冗余镜像列（便于直接按公司过滤）
    period = Column(String(20), index=True)                            # 冗余镜像列

    # —— 规模与盈利 ——
    revenue = Column(Float, nullable=True)                              # 营业总收入（元）
    operate_cost = Column(Float, nullable=True)                         # 营业成本（元）
    gross_profit = Column(Float, nullable=True)                         # 毛利润（元）
    gross_margin = Column(Float, nullable=True)                         # 毛利率（小数）
    net_profit = Column(Float, nullable=True)                           # 净利润（元）
    net_margin = Column(Float, nullable=True)                           # 净利率（小数）
    roe = Column(Float, nullable=True)                                  # 净资产收益率（小数）
    roa = Column(Float, nullable=True)                                  # 总资产收益率（小数）

    # —— 成长 ——
    revenue_yoy = Column(Float, nullable=True)                          # 营收同比增长率（小数）
    net_profit_yoy = Column(Float, nullable=True)                       # 净利润同比增长率（小数）
    revenue_cagr_3y = Column(Float, nullable=True)                      # 营收 3 年复合增速（小数）

    # —— 现金流 ——
    operating_cash_flow = Column(Float, nullable=True)                  # 经营活动现金流净额（元）
    investing_cash_flow = Column(Float, nullable=True)                  # 投资活动现金流净额（元）
    financing_cash_flow = Column(Float, nullable=True)                  # 筹资活动现金流净额（元）
    ocf_to_net_profit = Column(Float, nullable=True)                    # 经营现金流/净利润（倍，净现比）
    free_cash_flow = Column(Float, nullable=True)                       # 自由现金流（元）

    # —— 营运效率 ——
    inventory = Column(Float, nullable=True)                            # 存货（元）
    inventory_turnover = Column(Float, nullable=True)                   # 存货周转率（次）
    receivable = Column(Float, nullable=True)                           # 应收账款（元）
    receivable_turnover = Column(Float, nullable=True)                  # 应收账款周转率（次）
    payable_turnover = Column(Float, nullable=True)                     # 应付账款周转率（次）
    cash_conversion_cycle = Column(Float, nullable=True)                # 现金转换周期（天）

    # —— 偿债与结构 ——
    total_assets = Column(Float, nullable=True)                         # 总资产（元）
    total_liabilities = Column(Float, nullable=True)                    # 总负债（元）
    total_equity = Column(Float, nullable=True)                         # 净资产（元）
    debt_ratio = Column(Float, nullable=True)                           # 资产负债率（小数）
    current_ratio = Column(Float, nullable=True)                        # 流动比率（倍）
    quick_ratio = Column(Float, nullable=True)                          # 速动比率（倍）
    interest_coverage = Column(Float, nullable=True)                    # 利息保障倍数（倍）

    # —— 每股 / 其他 ——
    eps = Column(Float, nullable=True)                                  # 每股收益（元/股）
    bps = Column(Float, nullable=True)                                  # 每股净资产（元/股）
    dividend_per_share = Column(Float, nullable=True)                   # 每股股利（元/股）
    employee_count = Column(Float, nullable=True)                       # 员工人数

    # —— 长表扩展指标（提升为宽表列，供文本/对比直接使用）——
    net_profit_parent = Column(Float, nullable=True)                    # 归母净利润（元）
    roe_weighted = Column(Float, nullable=True)                         # 净资产收益率（加权，小数）
    inventory_days = Column(Float, nullable=True)                       # 存货周转天数（天）
    receivable_days = Column(Float, nullable=True)                      # 应收账款周转天数（天）
    payable_days = Column(Float, nullable=True)                         # 应付账款周转天数（天）

    # —— 2026-09 扩展批次（akshare 实测新增指标）——
    total_operating_cost = Column(Float, nullable=True)                 # 营业总成本（东财"营业成本"行口径，元）
    net_profit_deducted = Column(Float, nullable=True)                  # 扣非净利润（元）
    goodwill = Column(Float, nullable=True)                             # 商誉（元）
    ocf_per_share = Column(Float, nullable=True)                        # 每股经营现金流（元/股）
    roe_avg = Column(Float, nullable=True)                              # 净资产收益率（平均口径，小数）
    roic = Column(Float, nullable=True)                                 # 投入资本回报率（小数）
    operating_margin = Column(Float, nullable=True)                     # 营业利润率（小数）
    effective_tax_rate = Column(Float, nullable=True)                   # 有效税率 = 所得税/利润总额（小数）
    ocf_to_revenue = Column(Float, nullable=True)                       # 经营现金流/销售收入（倍）
    equity_multiplier = Column(Float, nullable=True)                    # 权益乘数 = 总资产/净资产（倍）
    debt_to_equity = Column(Float, nullable=True)                       # 产权比率 = 负债/净资产（倍）
    cash_ratio = Column(Float, nullable=True)                           # 现金比率（倍）
    quick_ratio_conservative = Column(Float, nullable=True)             # 保守速动比率（倍）
    total_asset_turnover = Column(Float, nullable=True)                 # 总资产周转率（次）
    current_asset_turnover = Column(Float, nullable=True)               # 流动资产周转率（次）
    fixed_asset_turnover = Column(Float, nullable=True)                 # 固定资产周转率（次）
    total_assets_yoy = Column(Float, nullable=True)                     # 总资产同比增长率（小数）
    equity_yoy = Column(Float, nullable=True)                           # 净资产同比增长率（小数）

    # —— 金融专用指标（全量对齐 metric_dict，原 is_core=0 批次）——
    capital_adequacy_ratio = Column(Float, nullable=True)               # 资本充足率（小数，金融专用）
    interest_income = Column(Float, nullable=True)                      # 利息收入（元，金融专用）
    net_interest_margin = Column(Float, nullable=True)                  # 净息差（小数，金融专用）
    non_performing_loan_ratio = Column(Float, nullable=True)            # 不良贷款率（小数，金融专用）
    operate_cost_ratio = Column(Float, nullable=True)                   # 营业成本率（小数）
    provision_coverage = Column(Float, nullable=True)                   # 拨备覆盖率（小数，金融专用）

    # —— 东财三表原生科目（2026-09-26 新增，严格来自接口列；供公式派生+三费分析）——
    operating_profit = Column(Float, nullable=True)                     # 营业利润（元）
    total_profit = Column(Float, nullable=True)                         # 利润总额（元）
    income_tax = Column(Float, nullable=True)                           # 所得税费用（元）
    finance_expense = Column(Float, nullable=True)                      # 财务费用（元）
    sale_expense = Column(Float, nullable=True)                         # 销售费用（元）
    manage_expense = Column(Float, nullable=True)                       # 管理费用（元）
    monetary_funds = Column(Float, nullable=True)                       # 货币资金（元）
    total_current_assets = Column(Float, nullable=True)                 # 流动资产合计（元）
    total_current_liab = Column(Float, nullable=True)                   # 流动负债合计（元）
    total_parent_equity = Column(Float, nullable=True)                  # 归母净资产（元）
    accounts_payable = Column(Float, nullable=True)                     # 应付票据及应付账款（元）
    fixed_asset = Column(Float, nullable=True)                          # 固定资产（元）
    capex = Column(Float, nullable=True)                                # 资本开支（元）
    short_term_loan = Column(Float, nullable=True)                      # 短期借款（元）
    long_term_loan = Column(Float, nullable=True)                       # 长期借款（元）
    bonds_payable = Column(Float, nullable=True)                        # 应付债券（元）
    lease_liab = Column(Float, nullable=True)                           # 租赁负债（元）
    noncurrent_liab_due_1y = Column(Float, nullable=True)               # 一年内到期的非流动负债（元）
    notes_payable = Column(Float, nullable=True)                        # 应付票据及应付账款（元）
    contract_liab = Column(Float, nullable=True)                        # 合同负债（元）
    advance_receipts = Column(Float, nullable=True)                     # 预收款项（元）
    notes_receivable = Column(Float, nullable=True)                     # 应收票据（元）
    prepayments = Column(Float, nullable=True)                          # 预付款项（元）
    construction_in_progress = Column(Float, nullable=True)             # 在建工程（元）
    intangible_asset = Column(Float, nullable=True)                     # 无形资产（元）
    right_of_use_asset = Column(Float, nullable=True)                   # 使用权资产（元）
    long_term_equity_invest = Column(Float, nullable=True)              # 长期股权投资（元）
    total_non_current_assets = Column(Float, nullable=True)             # 非流动资产合计（元）
    total_non_current_liab = Column(Float, nullable=True)               # 非流动负债合计（元）
    minority_interest = Column(Float, nullable=True)                    # 少数股东权益（元）
    deferred_income = Column(Float, nullable=True)                      # 递延收益（元）
    interest_bearing_debt = Column(Float, nullable=True)                # 有息负债（短期借款+长期借款+应付债券+一年内到期，派生）
    research_expense = Column(Float, nullable=True)                     # 研发费用（元）
    tax_surcharge = Column(Float, nullable=True)                        # 税金及附加（元）
    investment_income = Column(Float, nullable=True)                    # 投资收益（元）
    fair_value_change = Column(Float, nullable=True)                    # 公允价值变动收益（元）
    asset_impairment = Column(Float, nullable=True)                     # 资产减值损失（元）
    credit_impairment = Column(Float, nullable=True)                    # 信用减值损失（元）
    other_income = Column(Float, nullable=True)                         # 其他收益（元）
    non_operating_income = Column(Float, nullable=True)                 # 营业外收入（元）
    non_operating_expense = Column(Float, nullable=True)                # 营业外支出（元）
    cash_from_sales = Column(Float, nullable=True)                      # 销售商品、提供劳务收到的现金（元）
    cash_for_goods = Column(Float, nullable=True)                       # 购买商品、接受劳务支付的现金（元）
    cash_for_staff = Column(Float, nullable=True)                       # 支付给职工以及为职工支付的现金（元）
    cash_for_tax = Column(Float, nullable=True)                         # 支付的各项税费（元）
    cash_from_borrowing = Column(Float, nullable=True)                  # 取得借款收到的现金（元）
    cash_for_debt_repay = Column(Float, nullable=True)                  # 偿还债务支付的现金（元）
    cash_for_dividend = Column(Float, nullable=True)                    # 分配股利、利润或偿付利息支付的现金（元）
    cash_begin = Column(Float, nullable=True)                           # 期初现金及现金等价物余额（元）
    cash_end = Column(Float, nullable=True)                             # 期末现金及现金等价物余额（元）



# 指标字典：语义来源（中文名/别名/公式/单位类型/行业范围），L0 回算与归一化的依据
class MetricDict(Base):
    __tablename__ = "metric_dict"

    metric_code = Column(String(50), primary_key=True)                  # 指标代码（主键，如 gross_margin）
    name_cn = Column(String(100), nullable=False)                       # 中文名（如 毛利率）
    aliases = Column(JSON, default=list)                                # 别名列表（如 ["销售毛利率","毛利率(销售)"]）
    unit_type = Column(String(20), default="ratio")                     # 单位语义: ratio/amount/days/per_share
    value_scale = Column(Float, default=1.0)                            # 展示换算因子（如金额元→亿元的缩放）
    direction = Column(String(20), default="neutral")                   # 方向: positive/negative/neutral（用于异常语义）
    formula = Column(String(200), default="")                           # 派生公式（可执行表达式，回算依据）
    industry_scope = Column(String(20), default="general")              # 适用行业模板: general/financial
    description = Column(Text, default="")                              # 指标说明
    deprecated = Column(Integer, default=0)                             # 是否弃用


# 同行组：离线预计算的"同行"关系（industry_l2 + 规模分位），供同行画像；不由 LLM 现编
class PeerGroup(Base):
    __tablename__ = "peer_groups"

    group_id = Column(Integer, primary_key=True, autoincrement=True)    # 主键，自增
    company_id = Column(Integer, ForeignKey("company_master.company_id"),
                        nullable=False, index=True)                     # 成员公司（外键）
    group_name = Column(String(200), default="")                        # 组名（如 "电池-千亿以上"）
    basis = Column(String(100), default="")                             # 分组依据（如 industry_l2 + revenue_decile）
    updated_at = Column(String(30), default="")                         # 更新时间


# 异常规则表：规则化异常检测的阈值配置（阈值可调、可版本化）
class AnomalyRule(Base):
    __tablename__ = "anomaly_rules"

    rule_id = Column(String(50), primary_key=True)                      # 规则代码（主键，如 gross_margin_drop）
    name = Column(String(200), default="")                              # 规则名称
    metric_code = Column(String(50), default="")                        # 检测的目标指标
    compare = Column(String(20), default="yoy")                         # 对比方式: yoy/qoq/vs_peer_median
    operator = Column(String(20), default="lt")                         # 比较符: lt/gt/abs_gt
    threshold = Column(Float, default=0.0)                              # 阈值
    severity_map = Column(JSON, default=dict)                           # 幅度→级别映射（如 {"1.5":"high"}）
    industry_scope = Column(String(20), default="all")                  # 适用行业
    seasonality = Column(String(200), default="")                       # 季节性调整说明
    enabled = Column(Integer, default=1)                                # 是否启用


# 原始接口响应登记表（只增不改）：指向 storage/raw/ 下原样落盘文件，供映射变更后回放重算
class RawFinancialPayload(Base):
    __tablename__ = "raw_financial_payload"

    payload_id = Column(Integer, primary_key=True, autoincrement=True)  # 主键，自增
    company_id = Column(Integer, nullable=True, index=True)             # 公司 ID（落盘时可能未建档，允许空）
    company_code = Column(String(20), default="", index=True)           # 统一 6 位代码（回链主键）
    source = Column(String(20), default="akshare")                      # 数据源
    api_name = Column(String(100), default="")                          # 接口名
    params = Column(JSON, default=dict)                                 # 请求参数
    payload_path = Column(String(300), default="")                      # storage/raw/ 下文件路径
    checksum = Column(String(64), default="")                           # 文件校验和
    fetched_at = Column(String(30), default="")                         # 拉取时间


# 数据质量日志：L0 回算/校验结果的记录（open→fixed/accepted 闭环），评测体系的数据源
class DataQualityLog(Base):
    __tablename__ = "data_quality_log"

    id = Column(Integer, primary_key=True, autoincrement=True)          # 主键，自增
    period_id = Column(Integer, nullable=True)                          # 关联报告期
    metric_code = Column(String(50), default="")                        # 关联指标
    check_name = Column(String(50), default="")                         # 检查类型: recompute_mismatch/unit_inconsistent/yoy_break
    level = Column(String(10), default="warn")                          # 级别: error/warn/info（info=整轮汇总）
    detail = Column(Text, default="")                                   # 明细（差异描述）
    status = Column(String(20), default="open")                         # 状态: open/fixed/accepted
    checked_at = Column(String(30), default="")                         # 检查时间
