"""
混合检索器
- 向量语义检索（ChromaDB）+ BM25 关键词检索
- 加权融合排序（alpha 控制语义 vs 关键词权重）
- 支持标签过滤、时间范围过滤
"""
from __future__ import annotations

import math
import pickle
from collections import defaultdict
from pathlib import Path

import jieba
from loguru import logger

from app.platform.settings import get_settings
from app.domains.retrieval.vector import get_vector_store
from app.domains.financial.repository import get_db

# BM25 分词结果磁盘缓存目录（构建 1 万条中文分词约 2-4 分钟，缓存后秒级恢复）
_BM25_CACHE_DIR = Path(__file__).resolve().parents[2] / "storage" / "cache"


class BM25Index:
    """
    内存 BM25 索引
    - 基于 rank_bm25 库
    - 索引所有新闻 chunk 的文本
    - 支持增量更新
    """

    def __init__(self):
        self._index = None
        self._chunk_ids: list[str] = []
        self._texts: list[str] = []
        self._metadata: list[dict] = []

    @staticmethod
    def _cache_path(n: int) -> Path:
        _BM25_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return _BM25_CACHE_DIR / f"bm25_tokenized_{n}.pkl"

    def build(self, chunks: list[dict]):
        """从 chunk 列表构建 BM25 索引（构建后写磁盘缓存）"""
        from rank_bm25 import BM25Okapi
        self._chunk_ids = [c["chunk_id"] for c in chunks]
        self._texts = [c["text"] for c in chunks]
        self._metadata = [{k: c.get(k, "") for k in ("company_code", "company_name", "period", "group")}
                          for c in chunks]
        # 中文分词
        tokenized = [list(jieba.cut(text)) for text in self._texts]
        self._index = BM25Okapi(tokenized)
        # 写缓存
        try:
            with open(self._cache_path(len(chunks)), "wb") as f:
                pickle.dump((self._chunk_ids, tokenized, self._texts, self._metadata), f)
        except Exception as e:
            logger.warning(f"BM25 缓存写入失败: {e}")
        logger.info(f"BM25 索引构建完成，共 {len(chunks)} 个文档")

    def load_cache(self, chunks: list[dict]) -> bool:
        """
        尝试从磁盘缓存恢复 BM25 索引（免重复分词）。
        仅当缓存 chunk_id 列表与当前向量库完全一致时生效。
        """
        from rank_bm25 import BM25Okapi
        cache_file = self._cache_path(len(chunks))
        if not cache_file.exists():
            return False
        try:
            with open(cache_file, "rb") as f:
                ids, tokenized, texts, metadata = pickle.load(f)
            cur_ids = [c["chunk_id"] for c in chunks]
            if ids != cur_ids:
                logger.info("BM25 缓存与向量库不一致，重新构建")
                return False
            self._chunk_ids = ids
            self._texts = texts
            self._metadata = metadata
            self._index = BM25Okapi(tokenized)
            logger.info(f"BM25 索引从缓存恢复（{len(chunks)} 个文档，免分词）")
            return True
        except Exception as e:
            logger.warning(f"BM25 缓存加载失败: {e}")
            return False

    def search(self, query: str, top_k: int = 20) -> list[dict]:
        """BM25 检索"""
        if not self._index:
            return []
        tokens = list(jieba.cut(query))
        scores = self._index.get_scores(tokens)
        # 排序取 top_k
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        results = []
        for idx, score in ranked:
            if score <= 0:
                continue
            results.append({
                "chunk_id": self._chunk_ids[idx],
                "text": self._texts[idx],
                **self._metadata[idx],
                "bm25_score": float(score),
            })
        return results

    def is_built(self) -> bool:
        return self._index is not None


class HybridRetriever:
    """混合检索器：向量 + BM25 加权融合"""

    def __init__(self):
        self.settings = get_settings()
        self.vector_store = get_vector_store()
        self.db = get_db()
        self.bm25 = BM25Index()
        self._bm25_built = False

    def _ensure_bm25(self):
        """确保 BM25 索引已构建

        chunk 数据直接同步自向量库（ChromaDB collection），保证 chunk_id
        与向量检索结果完全对齐 —— 否则混合融合时两个结果集不重合，融合失效。
        """
        if self._bm25_built:
            return

        col = self.vector_store._collection
        n_total = col.count()
        if n_total == 0:
            logger.warning("向量库为空，BM25 索引跳过构建")
            return

        res = col.get(limit=n_total, include=["documents", "metadatas"])
        chunks = []
        for cid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"]):
            m = meta or {}
            chunks.append({
                "chunk_id": cid,
                "text": doc or "",
                "company_code": str(m.get("company_code", "")),
                "company_name": str(m.get("company_name", "")),
                "period": str(m.get("period", "")),
                "group": str(m.get("group", "")),
            })
        if chunks:
            if not self.bm25.load_cache(chunks):
                self.bm25.build(chunks)
            self._bm25_built = True

    def rebuild_bm25(self):
        """强制重建 BM25 索引（新增数据后调用）"""
        self._bm25_built = False
        self._ensure_bm25()

    @staticmethod
    def _min_max_normalize(scores: list[float]) -> list[float]:
        """Min-Max 归一化到 [0, 1]"""
        if not scores:
            return []
        mn, mx = min(scores), max(scores)
        if mx - mn < 1e-10:
            return [1.0] * len(scores)
        return [(s - mn) / (mx - mn) for s in scores]

    def retrieve(self, query: str, top_k: int | None = None,
                 alpha: float | None = None) -> list[dict]:
        """
        混合检索（财报版）

        Args:
            query: 查询文本
            top_k: 返回候选数（融合前），默认全局配置
            alpha: 向量检索权重 (0-1)，BM25 权重为 1-alpha；默认全局配置

        Returns:
            按融合分数降序的检索结果列表
        """
        top_k = top_k or self.settings.RETRIEVAL_TOP_K
        alpha = alpha if alpha is not None else self.settings.HYBRID_ALPHA

        # 1. 向量检索
        vector_results = self.vector_store.search(query, top_k=top_k)
        vector_scores = {r["chunk_id"]: r["score"] for r in vector_results}

        # 2. BM25 检索
        self._ensure_bm25()
        bm25_results = self.bm25.search(query, top_k=top_k)
        bm25_scores = {r["chunk_id"]: r["bm25_score"] for r in bm25_results}

        # 3. 融合（RRF 简化版 + 加权归一化）
        all_ids = set(vector_scores.keys()) | set(bm25_scores.keys())

        # 归一化
        vec_norm = dict(zip(vector_scores.keys(),
                            self._min_max_normalize(list(vector_scores.values()))))
        bm25_norm = dict(zip(bm25_scores.keys(),
                             self._min_max_normalize(list(bm25_scores.values()))))

        fused = []
        for cid in all_ids:
            vs = vec_norm.get(cid, 0.0)
            bs = bm25_norm.get(cid, 0.0)
            final_score = alpha * vs + (1 - alpha) * bs

            # 取元数据（优先从向量结果取）
            meta = None
            for r in vector_results:
                if r["chunk_id"] == cid:
                    meta = r
                    break
            if not meta:
                for r in bm25_results:
                    if r["chunk_id"] == cid:
                        meta = r
                        break
            if meta:
                fused.append({
                    **meta,
                    "final_score": final_score,
                    "vector_score": vector_scores.get(cid, 0.0),
                    "bm25_score": bm25_scores.get(cid, 0.0),
                })

        fused.sort(key=lambda x: x["final_score"], reverse=True)

        logger.info(f"混合检索: query='{query[:30]}...' 召回 {len(fused)} 条 "
                     f"(alpha={alpha}, top_k={top_k})")
        return fused


# 单例
_retriever: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
    return _retriever
