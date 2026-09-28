"""
记忆模块
- 短期记忆：当前对话上下文（滑动窗口 + 摘要压缩）
- 长期记忆：本次进程内的事实/偏好/优质问答（持久化表已随舆情遗留清理，
  如需跨会话持久化，后续在 agent 层按需重新设计落库方案）
- 记忆检索：基于相似度召回相关历史对话
"""
from __future__ import annotations

from collections import deque
from datetime import datetime

from loguru import logger

from app.core.llm_client import get_llm
from app.core.embedding import get_embedding_service


SUMMARY_PROMPT = """请将以下对话历史压缩为简洁的摘要，保留关键信息（用户关注的事件、已确认的事实、用户偏好）：

{history}

输出不超过200字的摘要。
"""


class Memory:
    """Agent 记忆模块"""

    def __init__(self, max_short_term: int = 20):
        self.max_short_term = max_short_term
        self.short_term: deque[dict] = deque(maxlen=max_short_term)
        self.long_term: list[dict] = []  # 长期记忆条目（含向量，仅本次进程有效）
        self.llm = get_llm()
        self.embedding = get_embedding_service()
        self._summary: str = ""  # 对话摘要

    def add(self, role: str, content: str):
        """添加一条对话到短期记忆"""
        self.short_term.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        logger.debug(f"记忆添加: {role} - {content[:50]}...")

    def get_context(self, max_tokens: int = 3000) -> list[dict]:
        """
        获取对话上下文（用于 LLM 输入）

        策略：优先保留最近的对话，如果超出 token 限制，
        对较早的对话做摘要压缩
        """
        messages = list(self.short_term)
        # 简单截断：保留最近 N 条
        if len(messages) > 10:
            # 对较早部分生成摘要
            early = messages[:-8]
            history_text = "\n".join(
                f"{'用户' if m['role'] == 'user' else '助手'}: {m['content']}"
                for m in early
            )
            try:
                self._summary = self.llm.chat([
                    {"role": "system", "content": "你是一个对话摘要助手。"},
                    {"role": "user", "content": SUMMARY_PROMPT.format(history=history_text)},
                ])
            except Exception as e:
                logger.warning(f"对话摘要失败: {e}")
                self._summary = history_text[:500]
            messages = [{"role": "system", "content": f"[对话历史摘要] {self._summary}"}] + messages[-8:]

        return messages

    def add_long_term(self, content: str, category: str = "fact"):
        """
        添加长期记忆：向量化后写入内存（本次进程内有效；
        持久化方案待 agent 层设计时重新定夺）
        """
        import numpy as np
        try:
            embedding = self.embedding.encode(content).flatten()
        except Exception as e:
            logger.warning(f"长期记忆向量化失败，跳过: {e}")
            return
        self.long_term.append({
            "content": content,
            "category": category,
            "embedding": embedding,
            "timestamp": datetime.now().isoformat(),
        })
        logger.info(f"长期记忆添加: [{category}] {content[:50]}...")

    def recall(self, query: str, top_k: int = 3) -> list[dict]:
        """
        基于相似度召回相关长期记忆

        Returns:
            [{"content", "category", "score", "timestamp"}]
        """
        valid = [m for m in self.long_term if m.get("embedding") is not None]
        if not valid:
            return []
        import numpy as np
        query_vec = self.embedding.encode(query).flatten()
        scored = []
        for mem in valid:
            emb = mem["embedding"]
            if emb is None:
                continue
            sim = float(np.dot(query_vec, emb) /
                        (np.linalg.norm(query_vec) * np.linalg.norm(emb) + 1e-10))
            scored.append((mem, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            {"content": m["content"], "category": m["category"],
             "score": s, "timestamp": m["timestamp"]}
            for m, s in scored[:top_k] if s > 0.5
        ]

    def clear(self):
        """清空短期记忆"""
        self.short_term.clear()
        self._summary = ""
        logger.info("短期记忆已清空")

    def get_summary(self) -> str:
        return self._summary
