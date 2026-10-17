"""query_company：公司档案查询工具

自然语言公司名 / 6 位代码 → company_master 基本信息；不传公司则列出库内全部公司。
"""
from __future__ import annotations

from app.domains.financial.repository import get_db
from app.harness import registry as tools_registry


class CompanyTool:
    """公司档案查询：自然语言公司名 / 6 位代码 → 基本信息"""

    def __init__(self):
        self.db = get_db()

    def query_company(self, args: dict | None = None, on_stage=None) -> dict:
        args = args or {}
        company = (args.get("company") or "").strip()
        if not company:
            # 未指定公司 → 列出全部公司
            codes = self.db.list_company_codes()
            lines = [f"- {c}" for c in codes]
            return {
                "reply": f"库中共 {len(codes)} 家公司：\n" + "\n".join(lines),
                "meta": {"company_count": len(codes)},
            }
        info = self.db.get_company(name=company) or self.db.get_company(code=company)
        if not info:
            return {"reply": f"未找到公司：{company}", "meta": {}}
        lines = [
            f"- 公司全称：{info.get('name', '')}",
            f"- 代码：{info.get('code', '')}",
            f"- 行业（一级）：{info.get('industry_l1') or '未标注'}",
            f"- 行业（二级）：{info.get('industry_l2') or '未标注'}",
            f"- 板块：{info.get('board') or '未标注'}",
            f"- 模板：{info.get('industry_template') or 'general'}",
        ]
        return {
            "reply": f"### {info.get('name')}（{info.get('code')}）\n" + "\n".join(lines),
            "meta": {"company": info},
        }


# ---------- 工具注册（OpenAI function calling schema）----------

COMPANY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_company",
        "description": "查询公司基本信息（全称、代码、行业、板块）。当用户问公司是什么、属于什么行业、库里有哪些公司时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "公司名或代码（可选）；不传则列出全部公司"}
            },
        },
    },
}

tools_registry.register_tool("query_company", COMPANY_SCHEMA, CompanyTool().query_company)
