"""HTTP 请求/响应模型

只放协议契约（Pydantic），不放业务逻辑。
响应体统一约定为四件套：{intent, agent, reply, meta}
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AgentChatRequest(BaseModel):
    """对话请求（/chat 与 /chat/stream 共用）"""

    query: str = Field(..., description="用户消息")
    session_id: str = Field(
        "default",
        description="会话ID。当前尚未真正生效（进程内单例记忆，所有请求共享），下一步接入 session store",
    )
