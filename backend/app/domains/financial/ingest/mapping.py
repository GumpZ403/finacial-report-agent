"""东财三表映射与归一化（纯函数，无 IO、不联网）

职责：把东方财富接口的英文列名翻译成内部 metric_code，并把值归一化（元 / 小数）。
- 三张映射表：EM_BALANCE_MAP / EM_PROFIT_MAP / EM_CASHFLOW_MAP
- 报告期解析：`2024-03-31` / `20240331` -> (2024Q1, Q1)
- 代码转换：6 位代码 -> 东财 symbol（SH/SZ/BJ）

为什么单独成模块：这部分是**纯函数**，映射规则改动频繁、需要能离线单测；
把它和网络采集拆开后，改映射不必碰适配器，也不必注入假 akshare 才能测。
"""
from __future__ import annotations

from datetime import datetime

from loguru import logger

from app.domains.financial.repository import normalize_code

EM_BALANCE_MAP = {
    "TOTAL_ASSETS": "total_assets",            # 总资产（元）
    "TOTAL_LIABILITIES": "total_liabilities",  # 总负债（元）
    "TOTAL_EQUITY": "total_equity",            # 净资产=股东权益合计（含少数股东，元）
    "INVENTORY": "inventory",                  # 存货（元）
    "NOTE_ACCOUNTS_RECE": "receivable",        # 应收票据及应收账款（元；字典别名口径）
    "GOODWILL": "goodwill",                    # 商誉（元）
    # 2026-09-26 ③ formula 支撑科目（东财确有其列，供严格派生；值==metric_code）
    "MONETARYFUNDS": "monetary_funds",              # 货币资金（元）→ cash_ratio 分子
    "TOTAL_CURRENT_ASSETS": "total_current_assets",  # 流动资产合计（元）→ current_ratio/quick_ratio
    "TOTAL_CURRENT_LIAB": "total_current_liab",      # 流动负债合计（元）→ current/quick/cash_ratio 分母
    "TOTAL_PARENT_EQUITY": "total_parent_equity",    # 归母净资产（元）→ roe 分母
    "NOTE_ACCOUNTS_PAYABLE": "accounts_payable",     # 应付票据及应付账款（元）→ payable_turnover 分母
    "FIXED_ASSET": "fixed_asset",                    # 固定资产（元）→ fixed_asset_turnover 分母
    # 同比（百分数 → ÷100 存小数）
    "TOTAL_ASSETS_YOY": "total_assets_yoy",    # 总资产同比
    "TOTAL_EQUITY_YOY": "equity_yoy",          # 净资产同比
}

# 利润表（累计口径）
EM_PROFIT_MAP = {
    "TOTAL_OPERATE_INCOME": "revenue",             # 营业总收入（元）
    "OPERATE_COST": "operate_cost",                # 营业成本（元，真口径，非营业总成本）
    "NETPROFIT": "net_profit",                     # 净利润（含少数股东，元）
    "PARENT_NETPROFIT": "net_profit_parent",       # 归母净利润（元）
    "DEDUCT_PARENT_NETPROFIT": "net_profit_deducted",  # 扣非归母净利润（元）
    "TOTAL_OPERATE_COST": "total_operating_cost",  # 营业总成本（元，含税金及附加与期间费用）
    # 2026-09-26 ③ formula 支撑科目（东财确有其列，供严格派生）
    "OPERATE_PROFIT": "operating_profit",          # 营业利润（元）→ operating_margin 分子
    "INCOME_TAX": "income_tax",                    # 所得税费用（元）→ effective_tax_rate 分子
    "TOTAL_PROFIT": "total_profit",                # 利润总额（元）→ effective_tax_rate 分母
    "FINANCE_EXPENSE": "finance_expense",          # 财务费用（元）→ 三费分析（原生科目）
    "SALE_EXPENSE": "sale_expense",                # 销售费用（元）→ 三费分析（原生科目）
    "MANAGE_EXPENSE": "manage_expense",            # 管理费用（元）→ 三费分析（原生科目）
    "BASIC_EPS": "eps",                            # 基本每股收益（元/股，接口直给）
    # 同比（百分数 → ÷100 存小数）
    "TOTAL_OPERATE_INCOME_YOY": "revenue_yoy",     # 营收同比
    "PARENT_NETPROFIT_YOY": "net_profit_yoy",      # 归母净利润同比（字典语义=归属母公司）
    # 银行别名（2026-09-26）：银行利润表无 TOTAL_OPERATE_INCOME，
    # 营业收入列名为 OPERATE_INCOME、营业支出为 OPERATE_EXPENSE；
    # 因 _em_rows 首值优先，工商企业不受影响（TOTAL_* 优先）
    "OPERATE_INCOME": "revenue",
    "OPERATE_INCOME_YOY": "revenue_yoy",
    "OPERATE_EXPENSE": "total_operating_cost",
}

# 现金流量表（累计口径）
EM_CASHFLOW_MAP = {
    "NETCASH_OPERATE": "operating_cash_flow",   # 经营活动现金流净额（元）
    "NETCASH_INVEST": "investing_cash_flow",    # 投资活动现金流净额（元）
    "NETCASH_FINANCE": "financing_cash_flow",   # 筹资活动现金流净额（元）
    "CONSTRUCT_LONG_ASSET": "capex",            # 资本开支=购建固定资产/无形资产等支付的现金（元）
    # 同比：三表科目 _YOY 无对应 metric_code（operating_cash_flow_yoy 等未入字典），暂不映射
}


def _em_symbol(code: str) -> str:
    """6 位裸代码 → 东财接口所需的交易所前缀代码（6=沪 SH / 0、3=深 SZ / 4、8=北 BJ）"""
    code = normalize_code(code)
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("0", "3")):
        return f"SZ{code}"
    return f"BJ{code}"


def _period_from_end(period_end: str) -> tuple[str, str]:
    """'2024-03-31' 或 '20240331' → ('2024Q1', 'Q1')；'2024-12-31' → ('2024FY', 'FY')"""
    end = str(period_end).strip()
    if len(end) == 8 and end.isdigit():  # '20260630'
        end = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
    parts = end.split("-")
    if len(parts) != 3:
        raise ValueError(f"无法解析报告期: {period_end!r}")
    y, m, _d = parts
    mm = int(m)
    if mm == 12:
        return f"{y}FY", "FY"
    if mm == 6:
        return f"{y}H1", "H1"
    if mm == 3:
        return f"{y}Q1", "Q1"
    if mm == 9:
        return f"{y}Q3", "Q3"
    return f"{y}Q{mm // 3 + 1}", f"Q{mm // 3 + 1}"


def _normalize_end(value) -> str | None:
    """报告期列/值 → 'YYYY-MM-DD'；无法解析返回 None"""
    s = str(value).strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


# 永久性错误：接口签名变了 / 代码 bug，重试没有意义

def _is_na(v) -> bool:
    if v is None:
        return True
    try:
        return v != v                          # NaN
    except Exception:
        return False


def _jsonable(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)
def _to_frame(payload) -> list[dict]:
    """{columns, rows} / 行列表 / DataFrame → 行字典列表（回放无需真 DataFrame）"""
    if payload is None:
        return []
    if isinstance(payload, dict):
        return payload.get("rows") or []
    if isinstance(payload, list):
        return payload
    try:
        return [dict(r) for r in payload.to_dict(orient="records")]
    except Exception:
        return []

def em_rows(payload, col_map: dict[str, str]) -> dict[str, dict[str, float]]:
    """单张东财表 → {period_end: {metric_code: value}}

    - 行 = 报告期（REPORT_DATE），列 = 科目 / _YOY
    - 金额列原样（东财单位为元）；_YOY 列 ÷100 存小数
    - 只映射 col_map 声明的列，其余忽略
    """
    rows: dict[str, dict[str, float]] = {}
    for rec in _to_frame(payload):
        end = _normalize_end(rec.get("REPORT_DATE", ""))
        if end is None:
            continue
        for col, code in col_map.items():
            if col not in rec:
                continue
            value = rec.get(col)
            if _is_na(value):
                continue
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            if col.endswith("_YOY"):       # 东财同比列为百分数 → 小数
                v = v / 100.0
            # 首值优先：工商 TOTAL_OPERATE_INCOME 优先于银行 OPERATE_INCOME 别名，
            # 后者仅作银行 fallback（银行利润表无 TOTAL_OPERATE_INCOME 列）
            rows.setdefault(end, {}).setdefault(code, v)
    return rows
