"""
Embedding 与 Reranker 封装
- 使用 sentence-transformers 本地加载中文模型
- 懒加载：首次调用时才下载/加载模型，避免启动慢
- 提供批量编码、相似度计算、重排序接口
"""
from __future__ import annotations

import os
# 强制离线模式：避免启动时尝试联网下载模型
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import numpy as np
from loguru import logger

from app.core.config import get_settings


class EmbeddingService:
    """文本向量化服务（懒加载单例）"""

    def __init__(self):
        self.settings = get_settings()
        self._model = None
        self._reranker = None

    def _ensure_model(self):
        """首次调用时加载 Embedding 模型"""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model: {self.settings.EMBEDDING_MODEL}")
            self._model = SentenceTransformer(self.settings.EMBEDDING_MODEL)
        return self._model

    def _ensure_reranker(self):
        """首次调用时加载 Reranker 模型"""
        if self._reranker is None:
            from sentence_transformers import CrossEncoder
            logger.info(f"Loading reranker model: {self.settings.RERANKER_MODEL}")
            self._reranker = CrossEncoder(self.settings.RERANKER_MODEL)
        return self._reranker

    def encode(self, texts: str | list[str], normalize: bool = True) -> np.ndarray:
        """
        文本 → 向量

        Args:
            texts: 单条或批量文本
            normalize: 是否 L2 归一化（余弦相似度场景建议 True）

        Returns:
            shape=(n, dim) 的 numpy 数组
        """
        model = self._ensure_model()
        if isinstance(texts, str):
            texts = [texts]
        embeddings = model.encode(texts, normalize_embeddings=normalize, show_progress_bar=False)
        return np.array(embeddings)

    def cosine_similarity(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """计算两个向量的余弦相似度（输入已归一化时等价于点积）"""
        return float(np.dot(vec_a, vec_b) / (np.linalg.norm(vec_a) * np.linalg.norm(vec_b) + 1e-10))

    def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[tuple[int, float]]:
        """
        Cross-Encoder 重排序

        Args:
            query: 查询文本
            documents: 候选文档列表
            top_n: 返回前 N 个，None 返回全部

        Returns:
            [(原始索引, 相关分数), ...] 按分数降序
        """
        if not documents:
            return []
        reranker = self._ensure_reranker()
        pairs = [[query, doc] for doc in documents]
        scores = reranker.predict(pairs)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        if top_n:
            ranked = ranked[:top_n]
        return [(int(idx), float(score)) for idx, score in ranked]


# 单例
_embedding: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    """获取 Embedding 服务单例"""
    global _embedding
    if _embedding is None:
        _embedding = EmbeddingService()
    return _embedding
