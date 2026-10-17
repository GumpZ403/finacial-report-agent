"""query_financials：财务指标查询工具（结构化参数，非 NL2SQL）

支持三种模式：
- single：查单期指标（company + metric + period，period 缺省取最新一期）
- trend： 查近 N 期指标序列（company + metric）
- yoy：   查指定期的同比（同口径上一报告期，如 2025Q1 vs 2024Q1）

数据来自 financial_core（宽表镜像）+ metric_dict（单位/中文名）。
"""
from __future__ import annotations

from app.domains.financial.display import fmt_value
from app.domains.financial.repository import get_db
from app.domains.financial.services.resolver import (
    resolve_company, resolve_metric,
)
from app.harness import registry as tools_registry
from app.harness.invoke import no_data


class FinancialQueryTool:
    """财务指标查询：结构化参数（非 NL2SQL）"""

    def __init__(self):
        self.db = get_db()
        self._name_to_code: dict[str, str] | None = None

    def _resolve_metric(self, metric: str):
        """指标说法 → Resolution（解析逻辑统一在 services/resolver.py）"""
        return resolve_metric(metric, db=self.db)

    def _resolve_company(self, company: str):
        return resolve_company(company, db=self.db)

    def _data(self, comp, metric_code: str, metric_cn: str, unit_type: str,
              rows: list[dict]) -> list[dict]:
        """结构化数字（前端直接渲染；模型不应搬运这些值）"""
        items = []
        for r in rows:
            value = r.get(metric_code)
            if value is None:
                continue
            detail = self.db.get_metrics(r["period_id"]).get(metric_code) or {}
            items.append({
                "label": metric_cn, "metric": metric_code,
                "period": r.get("period"), "value": value,
                "display": fmt_value(value, unit_type), "unit": unit_type,
                "estimated": bool(detail.get("is_estimated")),
            })
        return items

    def _period_key(self, period: str) -> tuple[int, int, int, int]:
        """报告期排序键：年份 / 季度序 / 类型序 / 报告期原文"""
        p = (period or "").strip()
        year = int(p[:4]) if p[:4].isdigit() else 0
        order = {"Q1": 1, "H1": 2, "Q3": 3, "FY": 4}
        for k, v in order.items():
            if k in p:
                return (year, v, 0, p)
        return (year, 0, 1, p)

    def query_financials(self, args: dict | None = None, on_stage=None) -> dict:
        args = args or {}
        company = (args.get("company") or "").strip()
        metric = (args.get("metric") or "").strip()
        period = (args.get("period") or "").strip()
        mode = (args.get("mode") or "single").strip().lower()
        top_n = int(args.get("top_n") or 8)

        # 1. 定位公司
        comp_res = self._resolve_company(company)
        if not comp_res.code:
            if comp_res.candidates:
                return no_data(
                    f"公司「{company}」有多个候选：{', '.join(comp_res.candidates)}，请确认",
                    candidates=comp_res.candidates)
            return no_data(f"未找到公司：{company or '（未指定）'}")
        comp = self.db.get_company(code=comp_res.code)
        code = comp["code"]

        # 2. 定位指标（低置信返回候选，不猜）
        metric_res = self._resolve_metric(metric)
        if not metric_res.code:
            if metric_res.candidates:
                return no_data(
                    f"指标「{metric}」有多个候选：{', '.join(metric_res.candidates)}，请确认",
                    candidates=metric_res.candidates)
            return no_data(f"未找到指标：{metric or '（未指定）'}",
                           available=len(self.db.list_metric_dict()))
        metric_code = metric_res.code
        meta = self.db.list_metric_dict().get(metric_code, {})
        metric_cn = meta.get("name_cn", metric_code)
        unit_type = meta.get("unit_type", "")

        # 3. 取该公司全部报告期宽表行
        rows = self.db.list_core_rows(code)
        if not rows:
            return no_data(f"{comp['name']} 暂无财报数据")
        rows_sorted = sorted(rows, key=lambda r: self._period_key(r.get("period", "")), reverse=True)

        # 4. 按模式查询
        if mode == "trend":
            selected = rows_sorted[:top_n]
            if not selected:
                return no_data(f"{comp['name']} 无趋势数据")
            seq = []
            for r in reversed(selected):
                val = r.get(metric_code)
                seq.append(f"- {r.get('period')}：{fmt_value(val, unit_type)}")
            return {
                "reply": f"### {comp['name']} {metric_cn} 近 {len(seq)} 期趋势\n" + "\n".join(seq),
                "data": self._data(comp, metric_code, metric_cn, unit_type, list(reversed(selected))),
                "meta": {"company": comp["name"], "metric": metric_cn, "mode": "trend",
                         "periods": [r.get("period") for r in selected]},
            }

        if mode == "yoy":
            if not period:
                period = rows_sorted[0].get("period", "")
            target = next((r for r in rows_sorted if r.get("period") == period), None)
            if not target:
                return no_data(f"{comp['name']} 无报告期 {period}")
            # 同口径上一报告期：同 period_type 的前一年（2025Q1 → 2024Q1）
            year = int(period[:4]) if period[:4].isdigit() else 0
            suffix = period[4:] if len(period) > 4 else ""
            prev_period = f"{year - 1}{suffix}"
            prev = next((r for r in rows_sorted if r.get("period") == prev_period), None)
            cur_v, prev_v = target.get(metric_code), (prev.get(metric_code) if prev else None)
            if cur_v is None:
                return no_data(f"{comp['name']} {period} 无 {metric_cn} 数据")
            if prev_v in (None, 0):
                return {
                    "reply": f"{comp['name']} {metric_cn}（{period}）：{fmt_value(cur_v, unit_type)}"
                             f"（无同期 {prev_period} 数据，无法计算同比）",
                    "meta": {"company": comp["name"], "metric": metric_cn, "period": period},
                }
            change = (cur_v - prev_v) / abs(prev_v) * 100
            return {
                "reply": (f"{comp['name']} {metric_cn}：{period} {fmt_value(cur_v, unit_type)}，"
                          f"同比（{prev_period}）{change:+.1f}%"),
                "data": self._data(comp, metric_code, metric_cn, unit_type,
                                   [r for r in (target, prev) if r]),
                "meta": {"company": comp["name"], "metric": metric_cn, "period": period,
                         "prev_period": prev_period, "yoy_pct": round(change, 2)},
            }

        # single（默认）：指定期或最新一期
        if period:
            target = next((r for r in rows_sorted if r.get("period") == period), None)
            if not target:
                return no_data(f"{comp['name']} 无报告期 {period}")
        else:
            target = rows_sorted[0]
        val = target.get(metric_code)
        return {
            "reply": f"{comp['name']} {metric_cn}（{target.get('period')}）：{fmt_value(val, unit_type)}",
            "data": self._data(comp, metric_code, metric_cn, unit_type, [target]),
            "meta": {"company": comp["name"], "metric": metric_cn,
                     "period": target.get("period"), "value": val, "unit_type": unit_type},
        }


# ---------- 工具注册（OpenAI function calling schema）----------

FINANCIALS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_financials",
        "description": (
            "查询财务指标数值（结构化精确查询）。支持三种模式："
            "single 查单期值（如「茅台2025年一季度毛利率」）；"
            "trend 查近N期趋势；yoy 查同比。用户问具体数字时优先用本工具，不要用 RAG。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "公司名或代码，如「贵州茅台」「600519」"},
                "metric": {"type": "string", "description": "指标名，如「毛利率」「ROE」「营业收入」「资产负债率」"},
                "period": {"type": "string", "description": "报告期（可选），如「2025Q1」「2024FY」；缺省取最新一期"},
                "mode": {"type": "string", "enum": ["single", "trend", "yoy"],
                         "description": "查询模式：single 单期 / trend 趋势 / yoy 同比"},
            },
            "required": ["company", "metric"],
        },
    },
}

tools_registry.register_tool("query_financials", FINANCIALS_SCHEMA, FinancialQueryTool().query_financials)
