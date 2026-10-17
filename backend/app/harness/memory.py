"""
记忆模块（harness 层，与业务无关）

- 短期记忆：当前对话上下文（滑动窗口 + 摘要压缩）
- 会话隔离：SessionStore 按 session_id 管理 Memory 实例（进程内 LRU）
  **注意**：仍是进程内存储，服务重启即丢；落库（SQLite conversations 表）列入下一步。
- 长期记忆（add_long_term / recall）已删除——全仓库零调用，且其向量检索用
  embedding 服务，会在 import 期把 sentence-transformers 拉进来。
"""
from __future__ import annotations

from collections import OrderedDict, deque
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


DEFAULT_SESSION_ID = "default"


class SessionStore:
    """按 session_id 管理 Memory 实例（进程内 LRU）

    为什么要它：此前 api 层持有**模块级单个 Memory**，`session_id` 字段形同虚设，
    所有浏览器/所有会话共用一份短期记忆——多开标签页会互相污染上下文。

    容量用满后淘汰最久未使用的会话（其上下文即丢失，不做持久化）。
    """

    def __init__(self, max_sessions: int = 50, max_short_term: int = 20):
        self.max_sessions = max_sessions
        self.max_short_term = max_short_term
        self._sessions: OrderedDict[str, Memory] = OrderedDict()

    @staticmethod
    def normalize(session_id: str | None) -> str:
        sid = (session_id or "").strip()
        return sid or DEFAULT_SESSION_ID

    def get(self, session_id: str | None) -> Memory:
        """取（或创建）某会话的记忆实例；访问即刷新 LRU 顺序"""
        sid = self.normalize(session_id)
        mem = self._sessions.get(sid)
        if mem is not None:
            self._sessions.move_to_end(sid)
            return mem
        if len(self._sessions) >= self.max_sessions:
            evicted, _ = self._sessions.popitem(last=False)
            logger.info(f"[SessionStore] 会话数达上限，淘汰最久未用会话: {evicted}")
        mem = Memory(max_short_term=self.max_short_term)
        self._sessions[sid] = mem
        return mem

    def drop(self, session_id: str | None) -> None:
        """丢弃一个会话的记忆（/clear 用）"""
        sid = self.normalize(session_id)
        if self._sessions.pop(sid, None) is not None:
            logger.info(f"[SessionStore] 会话已清理: {sid}")

    def active_sessions(self) -> list[str]:
        return list(self._sessions.keys())


_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """获取全局 SessionStore 单例"""
    global _store
    if _store is None:
        _store = SessionStore()
    return _store

