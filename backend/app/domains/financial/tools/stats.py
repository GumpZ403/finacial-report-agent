"""query_data_stats：数据库数据概览工具

公司数 / 报告期数 / 指标条数 / 行业分布 / 数据来源分布。
"""
from __future__ import annotations

from app.domains.financial.repository import get_db
from app.harness import registry as tools_registry


class DataStatsTool:
    """数据概览：公司数 / 报告期数 / 指标条数 / 行业分布"""

    def __init__(self):
        self.db = get_db()

    def query_data_stats(self, args: dict | None = None, on_stage=None) -> dict:
        stats = self.db.get_stats()
        ind = stats.get("industry_distribution") or {}
        ind_lines = "、".join(f"{k} {v} 家" for k, v in ind.items()) or "（无）"
        src = stats.get("metric_source_distribution") or {}
        src_lines = "、".join(f"{k} {v:,} 条" for k, v in src.items()) or "（无）"
        reply = (
            f"### 财报数据概览\n\n"
            f"- 公司数：**{stats.get('company_count', 0)}** 家\n"
            f"- 报告期：**{stats.get('report_count', 0):,}** 期\n"
            f"- 指标条数：**{stats.get('metric_count', 0):,}** 条\n"
            f"- 指标字典：**{stats.get('metric_dict_count', 0)}** 个\n"
            f"- 行业分布：{ind_lines}\n"
            f"- 数据来源：{src_lines}"
        )
        return {"reply": reply, "meta": {"stats": stats}}


# ---------- 工具注册（OpenAI function calling schema）----------

STATS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_data_stats",
        "description": "查询数据库数据概览（公司数、报告期数、指标条数、行业分布）。当用户问库里有什么数据、多少家公司、多少期财报时调用。",
        "parameters": {"type": "object", "properties": {}},
    },
}

tools_registry.register_tool("query_data_stats", STATS_SCHEMA, DataStatsTool().query_data_stats)
