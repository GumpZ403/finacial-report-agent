# -*- coding: utf-8 -*-
"""
重排工具（Reranker）
- 对混合检索结果做 Cross-Encoder 精排
- 模型能力来自 core/embedding（BAAI/bge-reranker-v2-m3）
"""
from __future__ import annotations

from loguru import logger

from app.platform.settings import get_settings
from app.platform.embedding import get_embedding_service


class RerankerTool:
    """重排工具：query + docs → 按相关性排序后的 docs"""

    def __init__(self):
        self.settings = get_settings()
        self.embedding = get_embedding_service()

    def rerank(self, query: str, docs: list[dict], top_n: int | None = None) -> list[dict]:
        """对检索结果做 Cross-Encoder 精排，返回 top_n 个 docs"""
        top_n = top_n or self.settings.RERANK_TOP_N
        if not docs:
            return []
        texts = [d.get("text", "") for d in docs]
        ranked = self.embedding.rerank(query, texts, top_n=top_n)
        return [docs[idx] for idx, _ in ranked]
