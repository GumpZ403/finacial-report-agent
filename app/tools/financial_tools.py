"""财报专用工具集：query_company / query_financials / query_data_stats / rag_search

- 数据全部来自 SQLite（company_master / financial_core / financial_periods / metric_dict）
  与 ChromaDB（1589 个财报文本片段）
- 所有工具签名统一 (args: dict, on_stage=None) -> dict（tools_registry 契约）
- 模块加载时自动注册到 tools_registry，classify 预选与 ReAct 循环直接生效
"""
from __future__ import annotations

from loguru import logger

from app.core.config import get_settings
from app.data.repository import get_db, normalize_code
from app.tools.rag.retriever import get_retriever
from app.tools.rag.reranker import RerankerTool
from app.agent import tools_registry

# ---------- 数值格式化（按 unit_type）----------

def _fmt_value(value, unit_type: str = "") -> str:
    """把指标值按单位类型格式化为可读文本（金额→亿元 / 比率→%）"""
    if value is None:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if unit_type == "amount":
        return f"{v / 1e8:.2f} 亿元"
    if unit_type == "ratio":
        return f"{v * 100:.2f}%"
    if unit_type == "times":
        return f"{v:.2f} 次"
    if unit_type == "days":
        return f"{v:.1f} 天"
    if unit_type == "per_share":
        return f"{v:.2f} 元/股"
    return f"{v:.2f}"


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


class FinancialQueryTool:
    """财务指标查询：结构化参数（非 NL2SQL）

    支持三种模式：
    - single：查单期指标（company + metric + period，period 缺省取最新一期）
    - trend： 查近 N 期指标序列（company + metric）
    - yoy：   查指定期的同比（同口径上一报告期，如 2025Q1 vs 2024Q1）
    """

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
                seq.append(f"- {r.get('period')}：{_fmt_value(val, unit_type)}")
            return {
                "reply": f"### {comp['name']} {metric_cn} 近 {len(seq)} 期趋势\n" + "\n".join(seq),
                "meta": {"company": comp["name"], "metric": metric_cn, "mode": "trend", "periods": [r.get("period") for r in selected]},
            }

        if mode == "yoy":
            if not period:
                period = rows_sorted[0].get("period", "")
            target = next((r for r in rows_sorted if r.get("period") == period), None)
            if not target:
                return {"reply": f"{comp['name']} 无报告期 {period}", "meta": {}}
            # 同口径上一报告期：同 period_type 的前一年（2025Q1 → 2024Q1）
            ptype = target.get("period_type") or "FY"
            year = int(period[:4]) if period[:4].isdigit() else 0
            suffix = period[4:] if len(period) > 4 else ""
            prev_period = f"{year - 1}{suffix}"
            prev = next((r for r in rows_sorted if r.get("period") == prev_period), None)
            cur_v, prev_v = target.get(metric_code), (prev.get(metric_code) if prev else None)
            if cur_v is None:
                return {"reply": f"{comp['name']} {period} 无 {metric_cn} 数据", "meta": {}}
            if prev_v in (None, 0):
                return {
                    "reply": f"{comp['name']} {metric_cn}（{period}）：{_fmt_value(cur_v, unit_type)}（无同期 {prev_period} 数据，无法计算同比）",
                    "meta": {"company": comp["name"], "metric": metric_cn, "period": period},
                }
            change = (cur_v - prev_v) / abs(prev_v) * 100
            return {
                "reply": (f"{comp['name']} {metric_cn}：{period} {_fmt_value(cur_v, unit_type)}，"
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
            "reply": f"{comp['name']} {metric_cn}（{target.get('period')}）：{_fmt_value(val, unit_type)}",
            "meta": {"company": comp["name"], "metric": metric_cn,
                     "period": target.get("period"), "value": val, "unit_type": unit_type},
        }


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


class RagSearchTool:
    """RAG 检索工具：只检索 + 重排，返回相关片段（不做生成）

    生成由外层 LLM（ReAct 循环）负责——拿到片段后组织回答；
    觉得片段不足时可再次调用本工具（多跳检索）。
    """

    def __init__(self):
        self.settings = get_settings()
        self.retriever = get_retriever()
        self.reranker = RerankerTool()

    def search(self, args: dict | None = None, on_stage=None) -> dict:
        query = (args or {}).get("query", "").strip()
        if not query:
            return {"reply": "请提供检索问题", "meta": {"chunks": []}}

        # 1. 混合检索（向量 + BM25）
        docs = self.retriever.retrieve(query, top_k=self.settings.RETRIEVAL_TOP_K)
        # 2. Cross-Encoder 重排（失败降级为原始顺序）
        try:
            reranked = self.reranker.rerank(query, docs, top_n=self.settings.RERANK_TOP_N)
        except Exception as e:
            logger.warning(f"重排失败，降级使用混合检索顺序: {e}")
            reranked = docs[: self.settings.RERANK_TOP_N]

        # 3. 结构化片段列表（供外层 LLM 组织回答）
        chunks = [{
            "chunk_id": d.get("chunk_id", ""),
            "company": d.get("company_name", ""),
            "period": d.get("period", ""),
            "group": d.get("group", ""),
            "text": str(d.get("text", ""))[:800],
            "score": round(float(d.get("final_score") or 0), 4),
        } for d in reranked]

        # 4. 文本版（ReAct 消息回填用）
        lines = []
        for i, c in enumerate(chunks, 1):
            lines.append(f"[来源{i}] {c['company']} | {c['period']} | {c['group']} (score={c['score']})\n{c['text']}")
        reply = "\n\n".join(lines) or "（未检索到相关片段）"
        return {
            "reply": reply,
            "meta": {"chunks": chunks, "retrieved_count": len(docs), "used_count": len(chunks)},
        }


# ---------- 工具注册（OpenAI function calling schema）----------

_FINANCIALS_SCHEMA = {
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

_COMPANY_SCHEMA = {
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

_STATS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_data_stats",
        "description": "查询数据库数据概览（公司数、报告期数、指标条数、行业分布）。当用户问库里有什么数据、多少家公司、多少期财报时调用。",
        "parameters": {"type": "object", "properties": {}},
    },
}

_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "rag_search",
        "description": (
            "检索财报知识库，返回相关文本片段（含公司/报告期/片段类型）。"
            "用于需要综合多期数据、跨公司比较、理解趋势与异常等开放性问题"
            "（如「宁德时代近几年盈利能力怎么样」）。拿到片段后由你组织回答；"
            "片段不足时可再次调用本工具。问具体数字时优先用 query_financials。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问题原文"}
            },
            "required": ["query"],
        },
    },
}


def _register():
    company = CompanyTool()
    fin = FinancialQueryTool()
    stats = DataStatsTool()
    rag = RagSearchTool()
    tools_registry.register_tool("query_company", _COMPANY_SCHEMA, company.query_company)
    tools_registry.register_tool("query_financials", _FINANCIALS_SCHEMA, fin.query_financials)
    tools_registry.register_tool("query_data_stats", _STATS_SCHEMA, stats.query_data_stats)
    tools_registry.register_tool("rag_search", _SEARCH_SCHEMA, rag.search)
    logger.info(f"[FinancialTools] 注册完成：{tools_registry.list_tool_names()}")


_register()
