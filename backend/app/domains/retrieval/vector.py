"""
向量数据库层（ChromaDB 封装）— 财报版
- 使用 ChromaDB PersistentClient 本地持久化，落盘目录取 settings.CHROMA_PERSIST_DIR
- 集合 financial_chunks，hnsw:space=cosine
- 每条 chunk = 一个"公司 × 报告期"的文本片段（corpus.py 生成），
  metadata 携带 company_code / company_name / period / period_type / group
- 支持幂等写入（按 chunk_id upsert）、元数据过滤（where）、按公司删除

已知平台坑（保留自舆情版，防御性）：
1. chromadb Rust 后端在 Windows 上，单纯等进程退出不保证 HNSW 完整写盘，
   1000 条以上重启可能报 “Error loading hnsw index” → 批量写入/删除后显式 flush()。
2. 含中文的绝对路径传给 compactor 时可能写不出 HNSW 文件，
   因此统一把落盘目录换算成相对当前工作目录的路径再传给 Chroma。
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from loguru import logger

# 关闭匿名遥测，避免离线/内网环境下启动时联网等待
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

import chromadb

from app.platform.settings import get_settings
from app.platform.embedding import get_embedding_service


COLLECTION_NAME = "financial_chunks"

# chunk 分组标记（corpus.py 写入）
GROUP_OVERVIEW = "overview"   # 综合描述段
GROUP_ANOMALY = "anomaly"     # 异常关注段


class VectorStore:
    """ChromaDB 向量存储封装（财报版，单例）"""

    def __init__(self):
        self.settings = get_settings()
        self.embedding_service = get_embedding_service()
        self.dim = int(self.settings.EMBEDDING_DIM)

        # 兼容相对路径：相对项目根目录解析，而不是相对进程启动目录
        persist_dir = Path(self.settings.CHROMA_PERSIST_DIR)
        if not persist_dir.is_absolute():
            persist_dir = self.settings.project_root / persist_dir
        self._dir = persist_dir
        self._dir.mkdir(parents=True, exist_ok=True)

        # Chroma 需要相对/ASCII 路径；从 cwd 换算，避免中文绝对路径触发 HNSW bug
        self._chroma_path = self._to_chroma_path(self._dir)

        self._client = chromadb.PersistentClient(path=self._chroma_path)
        self._collection = self._open_collection()
        logger.info(
            f"VectorStore 初始化完成，{self.count()} 个向量（ChromaDB: {self._dir}）"
        )

    # ---------- 内部：集合管理 ----------
    @staticmethod
    def _to_chroma_path(absolute_dir: Path) -> str:
        """把目标目录转成 Chroma 能安全落盘的路径字符串"""
        try:
            rel = os.path.relpath(str(absolute_dir), os.getcwd())
            if not rel.startswith("..") and not Path(rel).is_absolute():
                return rel.replace(os.sep, "/")
        except ValueError:
            pass
        return str(absolute_dir)

    def _open_client(self):
        self._client = chromadb.PersistentClient(path=self._chroma_path)
        self._collection = self._open_collection()

    def _open_collection(self):
        """获取或创建向量集合（cosine 距离）"""
        return self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def flush(self):
        """显式落盘并重开客户端

        ChromaDB Rust 后端的 HNSW 索引只有在 client.close() 时才会完整写到
        segment 目录；仅靠脚本正常退出不可靠。批量写入后必须调用一次。
        """
        self._client.close()
        self._open_client()

    def close(self):
        """释放 ChromaDB 客户端（进程结束前调用）"""
        if self._client is not None:
            self._client.close()

    # ---------- 对外接口 ----------
    def add_chunks(self, chunks: list[dict]):
        """
        批量写入 chunk（幂等：chunk_id 相同则覆盖，不产生重复记录）

        Args:
            chunks: [{"chunk_id": "300750_2024FY_overview",
                      "text": "...",
                      "company_code": "300750",
                      "company_name": "宁德时代",
                      "period": "2024FY",
                      "period_type": "FY",
                      "group": "overview"}]
        """
        if not chunks:
            return

        # 去重 + 跳过空文本：同一批里重复 id 会让 Chroma upsert 报错，
        # 空文本片段没有检索价值（textify 已尽量不产出，这里兜底）
        seen: set[str] = set()
        clean: list[dict] = []
        for c in chunks:
            cid = str(c.get("chunk_id", ""))
            text = str(c.get("text", "") or "").strip()
            if not cid or not text:
                logger.warning(f"跳过无效 chunk（chunk_id={cid!r} 文本长度={len(text)}）")
                continue
            if cid in seen:
                logger.warning(f"跳过重复 chunk_id: {cid}")
                continue
            seen.add(cid)
            clean.append(c)
        if not clean:
            return
        chunks = clean

        ids = [str(c["chunk_id"]) for c in chunks]
        texts = [str(c.get("text", "")) for c in chunks]

        embs = np.asarray(
            self.embedding_service.encode(texts), dtype=np.float32
        )
        if embs.ndim == 1:
            embs = embs.reshape(1, -1)
        if embs.shape[1] != self.dim:
            logger.warning(
                f"向量维度 {embs.shape[1]} 与配置维度 {self.dim} 不一致，"
                "请检查 EMBEDDING_MODEL / EMBEDDING_DIM"
            )

        metadatas = []
        for c in chunks:
            metadatas.append({
                "company_code": str(c.get("company_code", "")),
                "company_name": str(c.get("company_name", ""))[:100],
                "period": str(c.get("period", "")),
                "period_type": str(c.get("period_type", "")),
                "group": str(c.get("group", GROUP_OVERVIEW)),
            })

        self._collection.upsert(
            ids=ids,
            embeddings=embs.tolist(),
            documents=texts,
            metadatas=metadatas,
        )
        self.flush()
        logger.info(
            f"向量库写入 {len(chunks)} 个 chunk（总数 {self.count()}）"
        )

    def search(self, query: str, top_k: int | None = None,
               where: dict | None = None) -> list[dict]:
        """
        语义检索（余弦相似度）

        Args:
            query: 查询文本
            top_k: 返回数量，默认用全局配置
            where: 元数据过滤，如 {"company_code": "300750"}（等值匹配）

        Returns:
            [{"chunk_id", "text", "company_code", "company_name",
              "period", "period_type", "group", "score"}]
        """
        top_k = top_k or self.settings.RETRIEVAL_TOP_K
        n_total = self.count()
        if n_total == 0 or top_k <= 0:
            return []

        qv = np.asarray(
            self.embedding_service.encode(query), dtype=np.float32
        )
        if qv.ndim == 1:
            qv = qv.reshape(1, -1)

        result = self._collection.query(
            query_embeddings=qv.tolist(),
            n_results=min(int(top_k), n_total),
            where=where or None,
            include=["metadatas", "documents", "distances"],
        )

        ids = result.get("ids", [[]])[0] or []
        docs = result.get("documents", [[]])[0] or []
        metas = result.get("metadatas", [[]])[0] or []
        dists = result.get("distances", [[]])[0] or []

        hits = []
        for i, chunk_id in enumerate(ids):
            if chunk_id is None:
                continue
            # hnsw cosine 距离 = 1 - 余弦相似度
            score = float(1.0 - dists[i]) if i < len(dists) else 0.0
            text = docs[i] if i < len(docs) else ""
            meta = dict(metas[i]) if (i < len(metas) and metas[i]) else {}
            hits.append(self._build_hit(chunk_id, text, meta, score))
        return hits

    @staticmethod
    def _build_hit(chunk_id: str, text: str, meta: dict, score: float) -> dict:
        """chunk_id + 元数据 → 检索结果 dict"""
        return {
            "chunk_id": chunk_id,
            "text": text or meta.get("text", ""),
            "company_code": str(meta.get("company_code", "")),
            "company_name": str(meta.get("company_name", "")),
            "period": str(meta.get("period", "")),
            "period_type": str(meta.get("period_type", "")),
            "group": str(meta.get("group", GROUP_OVERVIEW)),
            "score": score,
        }

    def delete_by_company_code(self, company_code: str | int):
        """删除某家公司的所有 chunk（按 company_code 过滤）"""
        rows = self._collection.get(where={"company_code": str(company_code)})
        ids = list(rows.get("ids", []) or [])
        if ids:
            self._collection.delete(ids=ids)
            self.flush()
            logger.info(f"删除 company_code={company_code} 的 {len(ids)} 个 chunk")

    def delete_by_period(self, company_code: str | int, period: str):
        """删除某公司某个报告期的所有 chunk"""
        rows = self._collection.get(where={
            "company_code": str(company_code), "period": str(period),
        })
        ids = list(rows.get("ids", []) or [])
        if ids:
            self._collection.delete(ids=ids)
            self.flush()
            logger.info(f"删除 {company_code}/{period} 的 {len(ids)} 个 chunk")

    def count(self) -> int:
        return int(self._collection.count())

    def clear(self):
        """清空向量库（慎用）：删除集合后重建，保证后续写入正常"""
        try:
            self._client.delete_collection(name=COLLECTION_NAME)
        except Exception as e:
            logger.warning(f"清空时删除集合失败（{e}），将直接重建")
        self._collection = self._open_collection()
        logger.warning("向量库已清空")


# 单例
_vector_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    """获取向量存储单例"""
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store
