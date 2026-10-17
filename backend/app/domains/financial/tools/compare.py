"""compare_peers：多家公司同口径对比（工具层只做参数与呈现）"""
from __future__ import annotations

from app.domains.financial.services.quote import quote
from app.domains.financial.services.resolver import resolve_company, resolve_metric
from app.harness import registry as tools_registry
from app.harness.invoke import no_data

MAX_COMPANIES = 6

COMPARE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "compare_peers",
        "description": (
            "多家公司同一指标的对比（2~6 家）。回答「谁更高 / 同行对比 / 排名」类问题时使用。"
            "会显式标注无法对比的公司（无数据或期次缺失），不会用估算值补位。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "companies": {"type": "array", "items": {"type": "string"},
                              "description": "公司名或代码列表，如 [\"贵州茅台\",\"五粮液\"]"},
                "metric": {"type": "string", "description": "指标名，如「毛利率」"},
                "period": {"type": "string", "description": "报告期（可选），缺省各取最近一期"},
            },
            "required": ["companies", "metric"],
        },
    },
}


class CompareTool:
    def compare_peers(self, args: dict | None = None, on_stage=None) -> dict:
        args = args or {}
        names = [str(c).strip() for c in (args.get("companies") or []) if str(c).strip()]
        metric_text = (args.get("metric") or "").strip()
        period = (args.get("period") or "").strip() or None

        if len(names) < 2:
            return no_data("对比需要至少两家公司")
        if len(names) > MAX_COMPANIES:
            names = names[:MAX_COMPANIES]

        mres = resolve_metric(metric_text)
        if not mres.code:
            return no_data(f"未找到指标：{metric_text or '（未指定）'}")

        rows, missing = [], []
        for name in names:
            cres = resolve_company(name)
            if not cres.code:
                missing.append(f"{name}（未识别）")
                continue
            q = quote(cres.code, mres.code, period)
            if q is None:
                missing.append(f"{name}（无 {metric_text} 数据）")
                continue
            rows.append(q)

        if not rows:
            return no_data(f"这些公司都没有 {metric_text} 数据：{', '.join(names)}")

        rows.sort(key=lambda q: q.value, reverse=True)
        label = rows[0].label
        unit = rows[0].unit_type

        lines = [f"### {label} 对比（{rows[0].period if len({r.period for r in rows}) == 1 else '各自最近一期'}）"]
        for i, q in enumerate(rows, 1):
            lines.append(f"{i}. {q.name}（{q.period}）：{q.display}")
        if missing:
            lines.append(f"> 未纳入对比：{', '.join(missing)}")

        return {
            "reply": "\n".join(lines),
            "data": [{"label": q.label, "metric": q.metric, "period": q.period,
                      "value": q.value, "display": q.display, "unit": unit,
                      "estimated": q.estimated, "company": q.name} for q in rows],
            "meta": {"metric": label, "periods": [q.period for q in rows],
                     "count": len(rows), "missing": missing},
        }


tools_registry.register_tool("compare_peers", COMPARE_SCHEMA,
                             CompareTool().compare_peers)
