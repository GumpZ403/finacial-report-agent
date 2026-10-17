"""dupont_analysis：ROE 三因子分解（工具层只做参数解析与呈现）

计算全部在 services/dupont.py：同源重算三因子再相乘，并显式报出与报表 ROE 的口径差。
"""
from __future__ import annotations

from app.domains.financial.services.dupont import dupont
from app.domains.financial.services.resolver import resolve_company
from app.harness import registry as tools_registry
from app.harness.invoke import no_data

DUPONT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "dupont_analysis",
        "description": (
            "杜邦分解：把 ROE 拆成 净利率 × 总资产周转率 × 权益乘数，"
            "用于回答「为什么 ROE 变了 / ROE 由什么驱动」。"
            "三因子同源重算，并给出与报表 ROE 的口径差异。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "公司名或代码"},
                "period": {"type": "string", "description": "报告期（可选），缺省最近一期"},
            },
            "required": ["company"],
        },
    },
}


def _pct(x: float | None) -> str:
    return "N/A" if x is None else f"{x * 100:.2f}%"


def _item(label: str, metric: str, value: float | None, period: str) -> dict:
    return {"label": label, "metric": metric, "period": period,
            "value": value, "display": _pct(value), "unit": "ratio",
            "estimated": True}


class DupontTool:
    def dupont_analysis(self, args: dict | None = None, on_stage=None) -> dict:
        args = args or {}
        company = (args.get("company") or "").strip()
        period = (args.get("period") or "").strip() or None

        res = resolve_company(company)
        if not res.code:
            return no_data(f"未找到公司：{company or '（未指定）'}")

        d = dupont(res.code, period)
        if d is None:
            return no_data(
                f"{company} 缺少杜邦分解所需数据（净利润 / 营收 / 总资产 / 总权益）")

        lines = [
            f"### {d.name} {d.period} 杜邦分解",
            f"- 净利率：{_pct(d.net_margin)}",
            f"- 总资产周转率：{d.asset_turnover:.3f} 次",
            f"- 权益乘数：{d.equity_multiplier:.3f} 倍",
            f"- **ROE（三因子乘积）：{_pct(d.roe)}**",
        ]
        if d.roe_parent is not None:
            lines.append(f"- 归母口径 ROE（归母净利/归母权益）：{_pct(d.roe_parent)}")
        for note in d.notes:
            lines.append(f"> 注意：{note}")

        return {
            "reply": "\n".join(lines),
            "data": [
                _item("净利率", "net_margin", d.net_margin, d.period),
                _item("ROE（三因子）", "roe", d.roe, d.period),
                _item("归母口径 ROE", "roe_parent", d.roe_parent, d.period),
            ],
            "meta": {"company": d.name, "period": d.period,
                     "factor_check": d.factor_check,
                     "diff_vs_reported_pp": d.diff_vs_reported_pp,
                     "notes": d.notes},
        }


tools_registry.register_tool("dupont_analysis", DUPONT_SCHEMA,
                             DupontTool().dupont_analysis)
