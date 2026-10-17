"""rag_search：财报知识库检索工具（只检索 + 重排，不做生成）

生成由外层 LLM（ReAct 循环）负责——拿到片段后组织回答；
片段不足时可再次调用本工具（多跳检索）。

import 本模块即完成注册。
"""
from __future__ import annotations

from loguru import logger

from app.domains.retrieval.hybrid import get_retriever
from app.domains.retrieval.reranker import RerankerTool
from app.harness import registry as tools_registry
from app.harness.invoke import no_data
from app.platform.settings import get_settings


class RagSearchTool:
    """RAG 检索工具：混合检索 → 重排 → 结构化片段"""

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
        if not chunks:
            # 检索不到 ≠ 系统故障：显式声明 no_data，避免上层把它当异常重试
            return no_data("（未检索到相关片段）", chunks=[], retrieved_count=len(docs), used_count=0)

        lines = []
        for i, c in enumerate(chunks, 1):
            lines.append(f"[来源{i}] {c['company']} | {c['period']} | {c['group']} (score={c['score']})\n{c['text']}")
        reply = "\n\n".join(lines)
        return {
            "reply": reply,
            "meta": {"chunks": chunks, "retrieved_count": len(docs), "used_count": len(chunks)},
        }


# ---------- 工具注册（OpenAI function calling schema）----------

SEARCH_SCHEMA = {
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

tools_registry.register_tool("rag_search", SEARCH_SCHEMA, RagSearchTool().search)
