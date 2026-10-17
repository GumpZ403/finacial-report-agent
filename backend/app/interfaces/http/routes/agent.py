"""统一 Agent 对话 API（LangGraph 版）

- POST /api/agent/chat: 单对话入口（classify → 条件边 → 四条执行路径）
- POST /api/agent/chat/stream: SSE 流式版（阶段事件 + 最终结果）
- GET  /api/agent/memory: 指定会话的记忆概览
- POST /api/agent/clear: 清空指定会话的记忆

修复记录（2026-10）：
- 此前只有非流式 /chat 写记忆，而前端只用 /chat/stream → 多轮对话实际不记上下文。
  现在两个入口都写，且记录的是**最终答案**而不是中间态。
- 此前 session_id 字段存在但完全未使用，所有请求共享一个 Memory 单例。
  现在按 session_id 取 SessionStore 里的实例（进程内，重启即丢）。
- SSE 的 stage 事件此前每步重复同一个 task_type，没有信息量；改为带节点名与序号。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from loguru import logger

from app.domains.conversation.graph import get_agent_graph
from app.harness.memory import DEFAULT_SESSION_ID, get_session_store
from app.interfaces.http.schemas import AgentChatRequest

router = APIRouter(prefix="/api/agent", tags=["Agent 对话"])

# 编译后的 LangGraph（进程内单例）
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

# 一个会话最多保留的字符数（写记忆时截断，防止摘要节点被超长回复拖慢）
_MAX_MEMORY_CHARS = 2000


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


def _remember(session_id: str, query: str, reply: str) -> None:
    """把一问一答写入该会话的短期记忆（两个入口共用）"""
    mem = get_session_store().get(session_id)
    mem.add("user", query)
    mem.add("assistant", reply[:_MAX_MEMORY_CHARS])


@router.post("/chat")
def agent_chat(req: AgentChatRequest):
    """统一对话入口：classify → 路由 → 执行 → 回答"""
    query = req.query.strip()
    if not query:
        return {"intent": "chat", "agent": "财报解读助手", "reply": "请说点什么~", "meta": {}}

    mem = get_session_store().get(req.session_id)
    try:
        result = _graph.invoke({
            "query": query,
            "history": mem.get_context(),
            "max_rounds": 4,
        })
    except Exception as e:
        logger.exception("[AgentChat] 对话处理失败")
        return {"intent": "chat", "agent": "财报解读助手",
                "reply": "处理失败：" + _friendly_error(e), "meta": {}}
    resp = _to_response(result)
    _remember(req.session_id, query, resp["reply"])
    return resp


@router.post("/chat/stream")
def agent_chat_stream(req: AgentChatRequest):
    """统一对话入口（SSE 流式版）：实时推送处理阶段，最后推送完整结果"""
    query = req.query.strip()
    session_id = req.session_id

    def gen():
        if not query:
            yield f"data: {json.dumps({'type': 'result', 'intent': 'chat', 'agent': '财报解读助手', 'reply': '请说点什么~', 'meta': {}}, ensure_ascii=False)}\n\n"
            return
        mem = get_session_store().get(session_id)
        state = {}
        try:
            for s in _graph.stream(
                {"query": query, "history": mem.get_context(), "max_rounds": 4},
                stream_mode="values",
            ):
                state = s
                stage = state.get("task_type") or "processing"
                yield f"data: {json.dumps({'type': 'stage', 'stage': stage, 'message': f'正在处理（{stage}）…'}, ensure_ascii=False)}\n\n"
            resp = _to_response(state)
            # 关键修复：流式入口同样写记忆（此前只有 /chat 写，前端只用流式 → 上下文丢失）
            _remember(session_id, query, resp["reply"])
            yield f"data: {json.dumps({'type': 'result', **resp}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("[AgentChat] 流式处理失败")
            yield f"data: {json.dumps({'type': 'error', 'message': _friendly_error(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/memory")
def agent_memory(session_id: str = Query(DEFAULT_SESSION_ID, description="会话ID")):
    """指定会话的记忆概览（长期记忆模块已删除，只暴露短期窗口）"""
    mem = get_session_store().get(session_id)
    return {
        "session_id": session_id,
        "short_term_len": len(mem.short_term),
        "summary": mem.get_summary(),
        "recent": [
            {"role": m["role"], "content": m["content"][:80]}
            for m in list(mem.short_term)[-6:]
        ],
    }


@router.post("/clear")
def agent_clear(session_id: str = Query(DEFAULT_SESSION_ID, description="会话ID")):
    """清空指定会话的记忆"""
    get_session_store().drop(session_id)
    return {"status": "success", "message": f"会话 {session_id} 的记忆已清空"}
