"""harness：Agent 运行时（与业务领域无关的通用能力）

- llm.py       LLM 统一客户端（OpenAI 兼容，重试/超时/JSON/function calling）
- state.py     AgentState 共享黑板（LangGraph state schema）
- registry.py  工具注册表（schema 给 LLM 决策，handler 给系统执行）
- memory.py    会话记忆（短期滑窗 + 摘要压缩）
- invoke.py    （下一步）工具统一执行层：参数校验 / 结构化错误 / 重试 / 熔断
- trace.py     （下一步）一次运行的 trace（stage 事件、工具耗时）

纪律：harness 不得 import app.domains.*；领域通过注册表被 harness 调用。
"""
