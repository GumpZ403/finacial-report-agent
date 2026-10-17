"""对话主流程入口（组合根）

主流程已简化为单一有界循环，实现见 domains/conversation/loop.py：
    assemble → decide → act → answer
本模块只保留"构建 / 取单例"的约定，避免调用方各自 build 出一个流程实例。
"""
from __future__ import annotations

from app.domains.conversation.loop import build_loop
from app.harness.flow import Flow

_flow: Flow | None = None


def build_graph() -> Flow:
    """构建对话主流程（每次调用返回新实例，仅测试需要）"""
    return build_loop()


def get_agent_graph() -> Flow:
    """进程内单例（Node 的 SSE 事件回调与 describe 都依赖同一个实例）"""
    global _flow
    if _flow is None:
        _flow = build_loop()
    return _flow
