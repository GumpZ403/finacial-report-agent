"""detect_anomalies：三表勾稽异常（规则在 services/anomaly.py，脚本判定）"""
from __future__ import annotations

from app.domains.financial.services.anomaly import detect_anomalies
from app.domains.financial.services.resolver import resolve_company
from app.harness import registry as tools_registry
from app.harness.invoke import no_data

ANOMALY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "detect_anomalies",
        "description": (
            "检测财务数据中的勾稽异常：应收增速远超营收、存货增速远超成本、"
            "经营现金流支撑不足、同比大幅波动。每条异常都带原始数值证据。"
            "回答「有没有异常 / 需要注意什么」时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "公司名或代码"},
                "periods": {"type": "integer", "description": "检查最近几期（默认 4）"},
            },
            "required": ["company"],
        },
    },
}


class AnomalyTool:
    def detect_anomalies(self, args: dict | None = None, on_stage=None) -> dict:
        args = args or {}
        company = (args.get("company") or "").strip()
        periods = int(args.get("periods") or 4)

        res = resolve_company(company)
        if not res.code:
            return no_data(f"未找到公司：{company or '（未指定）'}")

        items = detect_anomalies(res.code, periods)
        if not items:
            return {
                "reply": f"最近 {periods} 期未发现勾稽异常（应收/存货/现金流/同比波动均在阈值内）。",
                "data": [],
                "meta": {"company": company, "periods": periods, "count": 0},
            }

        lines = [f"### {company} 最近 {periods} 期异常（{len(items)} 条）"]
        for a in items:
            tag = "高" if a.severity == "high" else "中"
            lines.append(f"- [{tag}] {a.period} {a.message}")
        return {
            "reply": "\n".join(lines),
            "data": [{"label": a.rule, "metric": a.rule, "period": a.period,
                      "value": a.delta, "display": f"{a.delta:+.1f}{a.unit}",
                      "unit": a.unit, "estimated": False} for a in items],
            "meta": {"company": company, "periods": periods, "count": len(items),
                     "anomalies": [a.to_dict() for a in items]},
        }


tools_registry.register_tool("detect_anomalies", ANOMALY_SCHEMA,
                             AnomalyTool().detect_anomalies)
