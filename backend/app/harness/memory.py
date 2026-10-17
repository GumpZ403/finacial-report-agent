"""
记忆模块（harness 层，与业务无关）

- 短期记忆：当前对话上下文（滑动窗口 + 摘要压缩）
- 会话隔离：**尚未实现**——当前是进程内单例，所有请求共享同一份记忆。
  下一步引入 session store 时，Memory 实例将按 session_id 创建（见 docs 架构诊断 §1.5）。

历史说明：长期记忆（add_long_term / recall）已删除——全仓库零调用，
且其向量检索用 embedding 服务，会在 import 期把 sentence-transformers 拉进来。
"""
from __future__ import annotations

from collections import deque
from datetime import datetime

from loguru import logger

from app.harness.llm import get_llm


SUMMARY_PROMPT = """请将以下对话历史压缩为简洁的摘要，保留关键信息（用户关注的事件、已确认的事实、用户偏好）：

{history}

输出不超过200字的摘要。
"""


class Memory:
    """会话记忆：短期滑窗 + 超限时 LLM 摘要压缩"""

    def __init__(self, max_short_term: int = 20):
        self.max_short_term = max_short_term
        self.short_term: deque[dict] = deque(maxlen=max_short_term)
        self.llm = get_llm()
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
        """获取对话上下文（用于 LLM 输入）

        策略：优先保留最近的对话；超过 10 条时对较早部分做摘要压缩。
        """
        messages = list(self.short_term)
        if len(messages) > 10:
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

    def clear(self):
        """清空短期记忆"""
        self.short_term.clear()
        self._summary = ""
        logger.info("短期记忆已清空")

    def get_summary(self) -> str:
        return self._summary
