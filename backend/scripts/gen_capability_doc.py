# -*- coding: utf-8 -*-
"""生成《指标能力清单》：从库 + 映射 + 指标字典自动汇总

输出 docs/指标能力清单.md，回答三个问题：
1. 现在支持哪些指标（有数据、填充率多少）
2. 哪些是派生指标（公式是什么、可信度如何）
3. 哪些明确不支持（以及原因）——避免"查不到"和"没实现"混为一谈

用法：..\\.venv\\Scripts\\python.exe scripts\\gen_capability_doc.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from app.domains.financial.ingest.mapping import (
    EM_BALANCE_MAP, EM_CASHFLOW_MAP, EM_PROFIT_MAP,
)
from app.domains.financial.repository import get_db

OUT = Path(__file__).resolve().parents[2] / "docs" / "指标能力清单.md"

# 明确不支持（来自 M0 处置：无映射源、无公式、需外部数据/专项数据）
from app.domains.financial.capability import UNSUPPORTED_METRICS as UNSUPPORTED_REASON  # noqa: E402

REPORT_GROUPS = [
    ("资产负债表", ["total_assets", "total_liabilities", "total_equity", "total_parent_equity",
                     "total_current_assets", "total_current_liab", "total_non_current_assets",
                     "total_non_current_liab", "monetary_funds", "receivable", "notes_receivable",
                     "prepayments", "inventory", "fixed_asset", "construction_in_progress",
                     "intangible_asset", "right_of_use_asset", "goodwill", "long_term_equity_invest",
                     "accounts_payable", "notes_payable", "contract_liab", "advance_receipts",
                     "short_term_loan", "long_term_loan", "bonds_payable", "lease_liab",
                     "noncurrent_liab_due_1y", "deferred_income", "minority_interest"]),
    ("利润表", ["revenue", "operate_cost", "total_operating_cost", "gross_profit", "tax_surcharge",
                "sale_expense", "manage_expense", "research_expense", "finance_expense",
                "operating_profit", "total_profit", "income_tax", "net_profit",
                "net_profit_parent", "net_profit_deducted", "eps", "investment_income",
                "fair_value_change", "asset_impairment", "credit_impairment", "other_income",
                "non_operating_income", "non_operating_expense"]),
    ("现金流量表", ["operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
                    "cash_from_sales", "cash_for_goods", "cash_for_staff", "cash_for_tax",
                    "cash_from_borrowing", "cash_for_debt_repay", "cash_for_dividend",
                    "cash_begin", "cash_end", "capex", "free_cash_flow"]),
]


def main():
    db = get_db()
    with db.get_session() as s:
        from sqlalchemy import text as sa_text
        rows = s.execute(sa_text("SELECT metric_code, name_cn, unit_type, formula, "
                                 "industry_scope FROM metric_dict ORDER BY metric_code")).all()
        dicts = {r[0]: {"name": r[1], "unit": r[2], "formula": r[3], "scope": r[4]} for r in rows}
        n_company = s.execute(sa_text("SELECT COUNT(*) FROM company_master")).scalar()
        n_period = s.execute(sa_text("SELECT COUNT(*) FROM financial_periods")).scalar()
        n_metric = s.execute(sa_text("SELECT COUNT(*) FROM financial_metrics")).scalar()
        n_rows = s.execute(sa_text("SELECT COUNT(*) FROM financial_core")).scalar()
        distinct = {r[0] for r in s.execute(sa_text(
            "SELECT DISTINCT metric_code FROM financial_metrics"))}
        fills = {}
        for code in distinct:
            n = s.execute(sa_text(f'SELECT COUNT(*) FROM financial_core WHERE "{code}" IS NOT NULL')).scalar()
            fills[code] = (int(n or 0), n_rows)

    mapped = set(EM_BALANCE_MAP.values()) | set(EM_PROFIT_MAP.values()) | set(EM_CASHFLOW_MAP.values())
    derived = {c for c, m in dicts.items() if m["formula"]}
    missing = sorted(set(dicts) - mapped - derived)

    lines = []
    lines.append("# 指标能力清单\n")
    lines.append(f"> 自动生成：`scripts/gen_capability_doc.py`（{datetime.now():%Y-%m-%d %H:%M}）\n")
    lines.append("> 用途：回答\"这个系统能算什么\"。**不支持 ≠ 没有数据**——两者必须区分。\n")
    lines.append("## 1. 数据规模\n")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 公司 | {n_company} |")
    lines.append(f"| 报告期 | {n_period} |")
    lines.append(f"| 指标行（长表） | {n_metric:,} |")
    lines.append(f"| 宽表行 | {n_rows} |")
    lines.append(f"| 有数据的指标种类 | {len(distinct)} |")
    lines.append(f"| 映射到的原始科目 | {len(mapped)} |")
    lines.append(f"| 派生指标 | {len(derived)} |")
    lines.append(f"| 明确不支持 | {len(UNSUPPORTED_REASON)} |")

    lines.append("\n## 2. 支持的科目（按报表）\n")
    lines.append("| 报表 | 指标 | 中文名 | 填充率 |")
    lines.append("|---|---|---|---|")
    for group, codes in REPORT_GROUPS:
        for code in codes:
            if code not in fills:
                continue
            n, total = fills[code]
            meta = dicts.get(code, {})
            rate = f"{n / total * 100:.0f}%" if total else "-"
            lines.append(f"| {group} | `{code}` | {meta.get('name', '')} | {rate} |")

    lines.append("\n## 3. 派生指标（公式，单期求值）\n")
    lines.append("| 指标 | 中文名 | 公式 | 填充率 |")
    lines.append("|---|---|---|---|")
    for code in sorted(derived):
        n, total = fills.get(code, (0, n_rows))
        rate = f"{n / total * 100:.0f}%"
        lines.append(f"| `{code}` | {dicts[code]['name']} | `{dicts[code]['formula']}` | {rate} |")

    lines.append("\n## 4. 明确不支持（查不到时请按此回答，不要说\"公司没有\"）\n")
    lines.append("| 指标 | 中文名 | 原因 |")
    lines.append("|---|---|---|")
    for code, reason in UNSUPPORTED_REASON.items():
        lines.append(f"| `{code}` | {dicts.get(code, {}).get('name', code)} | {reason} |")

    if missing:
        lines.append("\n## 5. 待归类（字典有、但既无映射也无公式）\n")
        lines.append(", ".join(f"`{c}`" for c in missing))
        lines.append("\n> 这些应在下一轮处置：补公式或明确标记为不支持。")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"能力清单已生成: {OUT}（{len(lines)} 行）")
    print(f"支持 {len(distinct) - len(missing)} / 不支持 {len(UNSUPPORTED_REASON)} / 待归类 {len(missing)}")


if __name__ == "__main__":
    main()
