"""query_financials：财务指标查询工具（结构化参数，非 NL2SQL）

支持三种模式：
- single：查单期指标（company + metric + period，period 缺省取最新一期）
- trend： 查近 N 期指标序列（company + metric）
- yoy：   查指定期的同比（同口径上一报告期，如 2025Q1 vs 2024Q1）

数据来自 financial_core（宽表镜像）+ metric_dict（单位/中文名）。
"""
from __future__ import annotations

from app.domains.financial.formatting import fmt_value
from app.domains.financial.repository import get_db
from app.harness import registry as tools_registry


class FinancialQueryTool:
    """财务指标查询：结构化参数（非 NL2SQL）"""

    def __init__(self):
        self.db = get_db()
        self._name_to_code: dict[str, str] | None = None

    def _metric_map(self) -> dict[str, str]:
        """指标中文名 → metric_code 映射（来自 metric_dict，懒加载）"""
        if self._name_to_code is None:
            meta = self.db.list_metric_dict()
            self._name_to_code = {
                m["name_cn"]: code for code, m in meta.items() if m.get("name_cn")
            }
        return self._name_to_code

    def _resolve_metric(self, metric: str) -> str | None:
        """指标名 → code：优先中文名匹配，其次按 code 直查"""
        metric = (metric or "").strip()
        if not metric:
            return None
        name_map = self._metric_map()
        if metric in name_map:
            return name_map[metric]
        if metric in self.db.list_metric_dict():
            return metric
        # 模糊：包含匹配（如"毛利率"→gross_margin）
        for cn, code in name_map.items():
            if metric in cn or cn in metric:
                return code
        return None

    @staticmethod
    def _resolve_company(db, company: str) -> dict | None:
        """公司名/代码 → company_master 行"""
        company = (company or "").strip()
        if not company:
            return None
        return db.get_company(name=company) or db.get_company(code=company)

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
        comp = self._resolve_company(self.db, company)
        if not comp:
            return {"reply": f"未找到公司：{company or '（未指定）'}", "meta": {}}
        code = comp["code"]

        # 2. 定位指标
        metric_code = self._resolve_metric(metric)
        if not metric_code:
            return {"reply": f"未找到指标：{metric or '（未指定）'}", "meta": {}}
        meta = self.db.list_metric_dict().get(metric_code, {})
        metric_cn = meta.get("name_cn", metric_code)
        unit_type = meta.get("unit_type", "")

        # 3. 取该公司全部报告期宽表行
        rows = self.db.list_core_rows(code)
        if not rows:
            return {"reply": f"{comp['name']} 暂无财报数据", "meta": {}}
        rows_sorted = sorted(rows, key=lambda r: self._period_key(r.get("period", "")), reverse=True)

        # 4. 按模式查询
        if mode == "trend":
            selected = rows_sorted[:top_n]
            if not selected:
                return {"reply": f"{comp['name']} 无趋势数据", "meta": {}}
            seq = []
            for r in reversed(selected):
                val = r.get(metric_code)
                seq.append(f"- {r.get('period')}：{fmt_value(val, unit_type)}")
            return {
                "reply": f"### {comp['name']} {metric_cn} 近 {len(seq)} 期趋势\n" + "\n".join(seq),
                "meta": {"company": comp["name"], "metric": metric_cn, "mode": "trend",
                         "periods": [r.get("period") for r in selected]},
            }

        if mode == "yoy":
            if not period:
                period = rows_sorted[0].get("period", "")
            target = next((r for r in rows_sorted if r.get("period") == period), None)
            if not target:
                return {"reply": f"{comp['name']} 无报告期 {period}", "meta": {}}
            # 同口径上一报告期：同 period_type 的前一年（2025Q1 → 2024Q1）
            year = int(period[:4]) if period[:4].isdigit() else 0
            suffix = period[4:] if len(period) > 4 else ""
            prev_period = f"{year - 1}{suffix}"
            prev = next((r for r in rows_sorted if r.get("period") == prev_period), None)
            cur_v, prev_v = target.get(metric_code), (prev.get(metric_code) if prev else None)
            if cur_v is None:
                return {"reply": f"{comp['name']} {period} 无 {metric_cn} 数据", "meta": {}}
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
                "meta": {"company": comp["name"], "metric": metric_cn, "period": period,
                         "prev_period": prev_period, "yoy_pct": round(change, 2)},
            }

        # single（默认）：指定期或最新一期
        if period:
            target = next((r for r in rows_sorted if r.get("period") == period), None)
            if not target:
                return {"reply": f"{comp['name']} 无报告期 {period}", "meta": {}}
        else:
            target = rows_sorted[0]
        val = target.get(metric_code)
        return {
            "reply": f"{comp['name']} {metric_cn}（{target.get('period')}）：{fmt_value(val, unit_type)}",
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
