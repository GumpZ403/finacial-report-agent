# -*- coding: utf-8 -*-
"""
L0 数据质量校验（方案 v1.1 §8.3，数据层的唯一实现）

检查项（只读 + 写 data_quality_log，不改业务数据）：
1. yoy_not_reproducible 同比可复现：revenue_yoy / net_profit_yoy 与上年同期值复算一致
2. unit_inconsistent    单位一致性：同一 metric_code 全库单位唯一，且与 metric_dict.unit_type 对齐
3. core_mirror_mismatch 长表-宽表一致：宽表必须逐列等于长表（宽表是派生物，漂移在此暴露）
4. coverage_zero / low  完整性：核心指标非空率，按行业模板统计并排除模板不适用指标

设计要点（对应方案 §4.3.5 / §4.3.6 的口径仲裁）：
- 同比复算基准：net_profit_yoy 是东财"归母净利润同比"，复算必须用 net_profit_parent（归母）；
  用 net_profit（含少数股东）会把少数股东损益占比大的公司（如万科）误报为同比差异。
- 覆盖率只统计"应有值"：模板不适用指标（metric_scope 金融 N/A）与 v1 无源留空指标
  （_EXPECTED_EMPTY）不进入分母，避免把"设计上留空"误报为数据缺失。
- 每轮运行写一条 check_name=l0_run / level=info 汇总行，通过率可审计（旧实现只写失败项）。

注：旧"公式回算一致性（recompute_mismatch）"检查已删除——入库派生值统一标记
source=derived，回算检查永远找不到非 derived 的公式指标（pass/fail 恒 0），属死检查。

用法：
    .venv/Scripts/python.exe -m app.data.quality             # 全库校验并写日志
    .venv/Scripts/python.exe -m app.data.quality --company 600519 300750  # 只校验指定公司
    .venv/Scripts/python.exe -m app.data.quality --keep-log   # 保留历史日志（默认清空重写）
"""
from __future__ import annotations

import json
from datetime import datetime

from loguru import logger

from app.data.metric_scope import is_applicable, normalize_template
from app.data.repository import get_db, normalize_code, UNIT_BY_TYPE, UNIT_UNKNOWN
from app.data.schema import FinancialCore

# 同比复现容差
YOY_TOLERANCE = 0.01
# 长表-宽表镜像容差（浮点误差）
MIRROR_TOLERANCE = 1e-6
# 完整性告警线
COVERAGE_LOW_LINE = 0.5

# v1 无源留空指标（docs/宽表字段清单_原生派生无源.md 第三节）：
# 三表无数据源或口径未实现，设计上不写入 → 不参与覆盖率分母
_EXPECTED_EMPTY = frozenset({
    "revenue_cagr_3y",          # 需跨多年期次计算
    "interest_coverage",        # 利息费用三表无单独科目
    "bps", "ocf_per_share",     # 缺股本数据
    "dividend_per_share",       # 缺分红数据
    "employee_count",           # 缺员工数
    "roe_weighted", "roe_avg", "roic",   # 加权/平均口径复杂
    "quick_ratio_conservative", # 保守口径未实现
    "operate_cost_ratio",       # 新浪专用，已弃用
    # 金融模板专用（东财三表不提供，待监管指标接口）
    "interest_income", "non_performing_loan_ratio",
    "provision_coverage", "capital_adequacy_ratio", "net_interest_margin",
})

# 宽表中需要与长表逐列核对的指标列
_CORE_COLUMNS: list[str] = [
    c.name for c in FinancialCore.__table__.columns
    if c.name not in ("period_id", "company_id", "period")
]


def _pass_rate(d: dict) -> float:
    ok, bad = d.get("pass", 0), d.get("fail", 0)
    return ok / (ok + bad) * 100 if (ok + bad) else 0.0


def run_l0(db=None, companies: list[str] | None = None,
           write_log: bool = True,
           clear_log: bool = True, max_entries: int = 5000) -> dict:
    """执行 L0 校验并返回结构化汇总

    Args:
        companies: 只校验指定公司（None=全库）
        write_log: 是否写 data_quality_log
        clear_log: 写日志前是否清空历史日志（避免无限累积）
        max_entries: 明细日志上限（超出只计数，不逐条写）
    """
    db = db or get_db()
    meta = db.list_metric_dict()

    codes = [normalize_code(c) for c in companies] if companies else db.list_company_codes()
    entries: list[dict] = []
    stats = {
        "yoy": {"pass": 0, "fail": 0, "skip": 0},
        "unit": {"pass": 0, "fail": 0, "unknown": 0},
        "core_mirror": {"pass": 0, "fail": 0},
        "coverage": {"applicable": 0, "filled": 0, "zero_metrics": [], "low_metrics": []},
    }
    unit_seen: dict[str, dict[str, int]] = {}
    mirror_fail: dict[str, int] = {}
    mirror_sample: dict[str, str] = {}
    coverage: dict[tuple[str, str], list[int]] = {}     # (template, metric) → [filled, applicable]

    n_reports = n_metrics = 0
    for code in codes:
        company = db.get_company(code=code)
        if not company:
            continue
        template = normalize_template(company.get("industry_template"))
        core_rows = db.list_core_rows(code)
        metric_rows = db.iter_metric_rows(code)
        by_report: dict[int, dict[str, dict]] = {}
        for m in metric_rows:
            by_report.setdefault(m["period_id"], {})[m["metric_code"]] = m
            unit = m["unit"] or ""
            unit_seen.setdefault(m["metric_code"], {})
            unit_seen[m["metric_code"]][unit] = unit_seen[m["metric_code"]].get(unit, 0) + 1
        n_reports += len(core_rows)
        n_metrics += len(metric_rows)

        for row in core_rows:
            rid = int(row["period_id"])
            values = {mc: m["value"] for mc, m in by_report.get(rid, {}).items()}

            # ---- 1. 同比可复现 ----
            year, ptype = str(row.get("period"))[:4], str(row.get("period_type") or "FY")
            prev = next((r for r in core_rows
                         if str(r.get("period")) == f"{int(year) - 1}{ptype}"), None)
            # net_profit_yoy 为归母同比 → 基准用 net_profit_parent（归母），
            # 否则少数股东损益占比大的公司会误报（2026-09-26 实证：万科）
            for m, base in (("revenue_yoy", "revenue"), ("net_profit_yoy", "net_profit_parent")):
                yoy = row.get(m)
                cur_base = row.get(base)
                prev_base = prev.get(base) if prev else None
                if yoy is None or cur_base is None or not prev_base:
                    stats["yoy"]["skip"] += 1
                    continue
                if prev_base <= 0:
                    # 上年同期为 0 或为负（亏损）：同比增长率本身不成立，记 not_applicable
                    stats["yoy"]["skip"] += 1
                    continue
                expected = (cur_base - prev_base) / abs(prev_base)
                if abs(expected - yoy) <= YOY_TOLERANCE:
                    stats["yoy"]["pass"] += 1
                else:
                    stats["yoy"]["fail"] += 1
                    if len(entries) < max_entries:
                        entries.append({
                            "period_id": rid, "metric_code": m,
                            "check_name": "yoy_not_reproducible", "level": "warn",
                            "detail": (f"{row.get('period')} 库内 {yoy:.6g} vs "
                                       f"按上年同期复算 {expected:.6g}"),
                        })

            # ---- 2. 长表 ↔ 宽表镜像 ----
            for col in _CORE_COLUMNS:
                core_v, metric_v = row.get(col), values.get(col)
                ok = (core_v is None and metric_v is None) or (
                    core_v is not None and metric_v is not None
                    and abs(float(core_v) - float(metric_v)) <= MIRROR_TOLERANCE
                )
                if ok:
                    stats["core_mirror"]["pass"] += 1
                else:
                    stats["core_mirror"]["fail"] += 1
                    mirror_fail[col] = mirror_fail.get(col, 0) + 1
                    mirror_sample.setdefault(
                        col, f"period_id={rid} 宽表={core_v} 长表={metric_v}")

            # ---- 3. 完整性（按模板，排除 N/A 与无源留空指标）----
            for mc, m in meta.items():
                if not is_applicable(mc, template):
                    continue
                if mc in _EXPECTED_EMPTY:
                    continue
                slot = coverage.setdefault((template, mc), [0, 0])
                slot[1] += 1
                if values.get(mc) is not None:
                    slot[0] += 1

    # ---- 单位一致性（全库按 metric_code 聚合）----
    for mc, units in unit_seen.items():
        unit_type = (meta.get(mc, {}) or {}).get("unit_type", "")
        expected_unit = UNIT_BY_TYPE.get(unit_type, UNIT_UNKNOWN)
        if mc not in meta:
            stats["unit"]["unknown"] += 1
            entries.append({
                "period_id": None, "metric_code": mc,
                "check_name": "metric_dict_gap", "level": "warn",
                "detail": f"指标 {mc} 不在 metric_dict 中，无法校验单位语义（unit={list(units)}）",
            })
        elif len(units) > 1:
            stats["unit"]["fail"] += 1
            entries.append({
                "period_id": None, "metric_code": mc,
                "check_name": "unit_inconsistent", "level": "error",
                "detail": f"同一指标存在多种单位: {units}",
            })
        elif expected_unit == UNIT_UNKNOWN:
            stats["unit"]["unknown"] += 1
            entries.append({
                "period_id": None, "metric_code": mc,
                "check_name": "metric_dict_gap", "level": "warn",
                "detail": f"unit_type={unit_type!r} 无法映射到单位（unit={list(units)}）",
            })
        elif list(units)[0] != expected_unit:
            stats["unit"]["fail"] += 1
            entries.append({
                "period_id": None, "metric_code": mc,
                "check_name": "unit_inconsistent", "level": "error",
                "detail": f"库内单位 {list(units)[0]} 与 metric_dict 推导 {expected_unit} 不一致",
            })
        else:
            stats["unit"]["pass"] += 1

    # 镜像失败按列汇总（列名进 metric_code 字段）
    for col, n in sorted(mirror_fail.items(), key=lambda x: -x[1]):
        entries.append({
            "period_id": None, "metric_code": col,
            "check_name": "core_mirror_mismatch", "level": "error",
            "detail": f"{n} 个报告期宽表与长表不一致；示例 {mirror_sample.get(col, '')}",
        })

    # 完整性告警
    total_applicable = sum(v[1] for v in coverage.values())
    total_filled = sum(v[0] for v in coverage.values())
    stats["coverage"]["applicable"] = total_applicable
    stats["coverage"]["filled"] = total_filled
    for (template, mc), (filled, applicable) in sorted(coverage.items()):
        rate = filled / applicable if applicable else 0.0
        if rate == 0:
            stats["coverage"]["zero_metrics"].append(f"{template}:{mc}")
            entries.append({
                "period_id": None, "metric_code": mc,
                "check_name": "coverage_zero", "level": "warn",
                "detail": f"模板 {template} 下核心指标 {mc} 非空率 0/{applicable}（无数据源或未采集）",
            })
        elif rate < COVERAGE_LOW_LINE:
            stats["coverage"]["low_metrics"].append(f"{template}:{mc}")
            entries.append({
                "period_id": None, "metric_code": mc,
                "check_name": "coverage_low", "level": "warn",
                "detail": (f"模板 {template} 下核心指标 {mc} 非空率 "
                           f"{rate * 100:.1f}%（{filled}/{applicable}）"),
            })

    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "companies": len(codes), "reports": n_reports, "metrics": n_metrics,
        "yoy_pass_rate": round(_pass_rate(stats["yoy"]), 2),
        "unit_pass_rate": round(_pass_rate(stats["unit"]), 2),
        "core_mirror_pass_rate": round(_pass_rate(stats["core_mirror"]), 2),
        "coverage_rate": (round(total_filled / total_applicable * 100, 2)
                          if total_applicable else 0.0),
        "stats": stats,
        "issues": len(entries),
        "coverage_by_template_metric": {
            f"{t}:{m}": {"filled": v[0], "applicable": v[1]}
            for (t, m), v in sorted(coverage.items())
        },
    }

    if write_log:
        if clear_log:
            db.clear_quality_log()
        written = db.log_quality_entries(entries)
        db.log_quality_entries([{
            "period_id": None, "metric_code": "",
            "check_name": "l0_run", "level": "info",
            "detail": json.dumps(summary, ensure_ascii=False),
        }])
        logger.info(f"L0 结论已写入 data_quality_log（{written} 条问题 + 1 条汇总）")

    logger.info(
        "L0 校验完成："
        f"同比 {summary['yoy_pass_rate']}%（fail={stats['yoy']['fail']}）/ "
        f"单位 {summary['unit_pass_rate']}% / "
        f"长表-宽表镜像 {summary['core_mirror_pass_rate']}% / "
        f"核心指标覆盖 {summary['coverage_rate']}%"
    )
    if stats["coverage"]["zero_metrics"]:
        logger.warning(
            f"零覆盖核心指标 {len(stats['coverage']['zero_metrics'])} 项："
            f"{sorted(stats['coverage']['zero_metrics'])[:12]}"
        )
    return summary


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    picks: list[str] = []
    if "--company" in argv:
        i = argv.index("--company")
        picks = [a for a in argv[i + 1:] if not a.startswith("--")]
    result = run_l0(companies=picks or None, clear_log="--keep-log" not in argv)
    print(json.dumps({k: result[k] for k in (
        "companies", "reports", "metrics", "yoy_pass_rate", "unit_pass_rate",
        "core_mirror_pass_rate", "coverage_rate", "issues")}, ensure_ascii=False, indent=2))
