"""统一 Agent 对话 API（LangGraph 版）

- POST /api/agent/chat: 单对话入口（classify → 条件边 → 四条执行路径）
- POST /api/agent/chat/stream: SSE 流式版（阶段事件 + 最终结果）
- GET  /api/agent/memory: 当前共享记忆概览
- POST /api/agent/clear: 清空共享记忆

已知问题（下一步修，见 docs 架构诊断 §1.5）：
- /chat 会写记忆，/chat/stream 不会 —— 而前端只用 stream，导致多轮对话实际不记上下文
- session_id 字段未被使用，所有请求共享同一个 Memory 单例
"""
from __future__ import annotations

from fastapi import APIRouter
from loguru import logger

from app.domains.conversation.graph import get_agent_graph
from app.harness.memory import Memory
from app.interfaces.http.schemas import AgentChatRequest

router = APIRouter(prefix="/api/agent", tags=["Agent 对话"])

# 全局单例：记忆中枢 + 编译后的 LangGraph
_memory = Memory()
_graph = get_agent_graph()

# 每个分支的对外展示名
_INTENT_AGENT = {
    "company_query": "公司档案查询",
    "metric_query": "财务指标查询",
    "analysis": "财报分析",
    "report": "财报解读报告",
    "data_overview": "数据概览",
    "chat": "财报解读助手",
}


def _friendly_error(e: Exception) -> str:
    """把底层异常转成用户可读的错误提示（不暴露堆栈与密钥）"""
    msg = str(e)
    if "401" in msg or "Authentication" in msg or "api key" in msg.lower():
        return "LLM 服务认证失败：请检查 .env 中 LLM_API_KEY 是否有效。"
    if "Connection" in msg or "timeout" in msg.lower() or "Timed out" in msg:
        return "LLM 服务连接超时：请检查网络或服务地址（LLM_BASE_URL）。"
    return f"{msg[:120]}"


def _to_response(state: dict) -> dict:
    """LangGraph 状态 → 前端四件套 {intent, agent, reply, meta}"""
    intent = state.get("intent") or "chat"
    reply = state.get("answer") or ""
    if not reply:
        reply = "抱歉，没有生成有效回答，换个说法试试？"
    return {
        "intent": intent,
        "agent": _INTENT_AGENT.get(intent, "财报解读助手"),
        "reply": reply,
        "meta": state.get("meta") or {},
    }


@router.post("/chat")
def agent_chat(req: AgentChatRequest):
    """统一对话入口：classify → 路由 → 执行 → 回答"""
    query = req.query.strip()
    if not query:
        return {"intent": "chat", "agent": "财报解读助手", "reply": "请说点什么~", "meta": {}}

    try:
        result = _graph.invoke({
            "query": query,
            "history": _memory.get_context(),
            "max_rounds": 4,
        })
    except Exception as e:
        logger.exception("[AgentChat] 对话处理失败")
        return {"intent": "chat", "agent": "财报解读助手",
                "reply": "处理失败：" + _friendly_error(e), "meta": {}}
    resp = _to_response(result)
    _memory.add("user", query)
    _memory.add("assistant", resp["reply"][:2000])
    return resp


@router.post("/chat/stream")
def agent_chat_stream(req: AgentChatRequest):
    """统一对话入口（SSE 流式版）：实时推送处理阶段，最后推送完整结果"""
    import json

    from fastapi.responses import StreamingResponse

    query = req.query.strip()

    def gen():
        if not query:
            yield f"data: {json.dumps({'type': 'result', 'intent': 'chat', 'agent': '财报解读助手', 'reply': '请说点什么~', 'meta': {}}, ensure_ascii=False)}\n\n"
            return
        state = {}
        try:
            for s in _graph.stream(
                {"query": query, "history": _memory.get_context(), "max_rounds": 4},
                stream_mode="values",
            ):
                state = s
                stage = state.get("task_type") or "processing"
                yield f"data: {json.dumps({'type': 'stage', 'stage': stage, 'message': f'正在处理（{stage}）…'}, ensure_ascii=False)}\n\n"
            resp = _to_response(state)
            yield f"data: {json.dumps({'type': 'result', **resp}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("[AgentChat] 流式处理失败")
            yield f"data: {json.dumps({'type': 'error', 'message': _friendly_error(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/memory")
def agent_memory():
    """共享记忆概览（长期记忆模块已删除，只暴露短期窗口）"""
    mem = _memory
    return {
        "short_term_len": len(mem.short_term),
        "summary": mem.get_summary(),
        "recent": [
            {"role": m["role"], "content": m["content"][:80]}
            for m in list(mem.short_term)[-6:]
        ],
    }


@router.post("/clear")
def agent_clear():
    """清空共享记忆"""
    _memory.clear()
    return {"status": "success", "message": "共享记忆已清空"}
