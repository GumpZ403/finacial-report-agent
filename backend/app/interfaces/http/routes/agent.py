"""统一 Agent 对话 API

- POST /api/agent/chat: 单对话入口（classify → 路由 → 四条执行路径）
- POST /api/agent/chat/stream: SSE 流式版（逐节点阶段事件 + 最终结果）
- GET  /api/agent/memory: 指定会话的记忆概览
- POST /api/agent/clear: 清空指定会话的记忆
- GET  /api/agent/flow: 主流程结构（调试用）

2026-10 变更：
- 编排层由 LangGraph 换成自研 Flow（harness/flow.py）。SSE 阶段事件改为
  **逐节点真流式**：流程跑在后台线程里，事件经 Queue 送到生成器，不再等整个流程结束。
- 此前只有非流式 /chat 写记忆，而前端只用流式 → 多轮对话实际不记上下文。两个入口都写。
- 此前 session_id 字段存在但未使用，所有请求共享一个 Memory 单例；现在按会话隔离。
- 会话记忆已落盘（storage/conversations.db，见 harness/store.py）。当前是**双写**：
  读仍走进程内 Memory（重启后历史不会回上下文），写同时进库 → 下一步把读路径切到库里。
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from loguru import logger

from app.domains.conversation.graph import get_agent_graph
from app.harness.flow import FlowEvent
from app.harness.memory import DEFAULT_SESSION_ID, get_session_store
from app.harness.store import get_conversation_store
from app.interfaces.http.schemas import AgentChatRequest

router = APIRouter(prefix="/api/agent", tags=["Agent 对话"])

# 主流程实例（进程内单例）
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

# 节点 → 前端展示文案。key 是 "流程名.节点名"（节点名在不同流程里会重名）
_STAGE_TEXT = {
    "conversation.classify": "识别意图…",
    "conversation.tool": "查询财务数据…",
    "conversation.answer": "整理回答…",
    "conversation.chat": "生成回答…",
    "react.llm": "推理中…",
    "react.tool": "调用工具取数…",
    "plan.plan": "规划报告结构…",
    "plan.execute": "采集报告数据…",
    "plan.write": "撰写报告正文…",
}

# 一个会话最多保留的字符数（写记忆时截断，防止摘要被超长回复拖慢）
_MAX_MEMORY_CHARS = 2000


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _friendly_error(e: Exception) -> str:
    """把底层异常转成用户可读的错误提示（不暴露堆栈与密钥）"""
    msg = str(e)
    if "401" in msg or "Authentication" in msg or "api key" in msg.lower():
        return "LLM 服务认证失败：请检查 .env 中 LLM_API_KEY 是否有效。"
    if "Connection" in msg or "timeout" in msg.lower() or "Timed out" in msg:
        return "LLM 服务连接超时：请检查网络或服务地址（LLM_BASE_URL）。"
    return f"{msg[:120]}"


def _to_response(state: dict) -> dict:
    """流程状态 → 前端四件套 {intent, agent, reply, meta}"""
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


def _meta_snapshot(state: dict) -> dict:
    """从流程最终状态里摘出要落盘的决策信息（这是唯一需要"从流程内部取"的字段）"""
    snap = {
        "intent": state.get("intent"),
        "task_type": state.get("task_type"),
        "tool_name": state.get("tool_name"),
        "confidence": state.get("confidence"),
        "tool_status": [
            r.get("tool_status") for r in (state.get("tool_results") or []) if isinstance(r, dict)
        ],
    }
    return {k: v for k, v in snap.items() if v not in (None, "", [])}


def _remember(session_id: str, query: str, reply: str, meta: dict | None = None) -> None:
    """把一问一答写入该会话记忆：进程内窗口（读路径）+ 落盘（storage/conversations.db）

    两个入口共用。落盘失败只记日志——绝不因为存库失败而丢掉回答。
    """
    mem = get_session_store().get(session_id)
    mem.add("user", query)
    mem.add("assistant", reply[:_MAX_MEMORY_CHARS])
    try:
        get_conversation_store().append_turn(session_id, query, reply, meta=meta)
    except Exception:
        logger.warning("[memory] 会话写盘失败（已忽略，不影响回答）")


def _stage_message(ev: FlowEvent) -> str | None:
    """节点开始事件 → 一句人能看的进度文案（其他事件返回 None，不下发）"""
    if ev.kind != "node_start" or not ev.node:
        return None
    key = f"{ev.flow}.{ev.node}"
    return _STAGE_TEXT.get(key) or f"处理中（{ev.node}）…"


@router.post("/chat")
def agent_chat(req: AgentChatRequest):
    """统一对话入口（同步）：classify → 路由 → 执行 → 回答"""
    query = req.query.strip()
    if not query:
        return {"intent": "chat", "agent": "财报解读助手", "reply": "请说点什么~", "meta": {}}

    mem = get_session_store().get(req.session_id)
    try:
        result = _graph.run({
            "query": query,
            "history": mem.get_context(),
            "max_rounds": 4,
        })
    except Exception as e:
        logger.exception("[AgentChat] 对话处理失败")
        return {"intent": "chat", "agent": "财报解读助手",
                "reply": "处理失败：" + _friendly_error(e), "meta": {}}
    resp = _to_response(result)
    _remember(req.session_id, query, resp["reply"], meta=_meta_snapshot(result))
    return resp


@router.post("/chat/stream")
def agent_chat_stream(req: AgentChatRequest):
    """统一对话入口（SSE 流式）：逐节点推送进度，最后推送完整结果"""
    query = req.query.strip()
    session_id = req.session_id

    def gen():
        if not query:
            yield _sse({"type": "result", "intent": "chat", "agent": "财报解读助手",
                        "reply": "请说点什么~", "meta": {}})
            return

        mem = get_session_store().get(session_id)
        # 流程跑在后台线程，事件经 Queue 送回本生成器 → 真正做到"边跑边推"
        q: queue.Queue[tuple[str, Any]] = queue.Queue()

        def worker() -> None:
            try:
                state = _graph.run(
                    {"query": query, "history": mem.get_context(), "max_rounds": 4},
                    on_event=lambda ev: q.put(("event", ev)),
                )
                q.put(("done", state))
            except Exception as e:  # 交给生成器统一转成 SSE error
                q.put(("error", e))

        threading.Thread(target=worker, name=f"chat-{session_id}", daemon=True).start()

        final: dict = {}
        while True:
            kind, payload = q.get()
            if kind == "event":
                msg = _stage_message(payload)
                if msg:
                    yield _sse({"type": "stage", "stage": payload.node, "message": msg})
            elif kind == "done":
                final = payload
                break
            else:  # error
                logger.exception("[AgentChat] 流式处理失败")
                yield _sse({"type": "error", "message": _friendly_error(payload)})
                return

        resp = _to_response(final)
        # 流式入口同样写记忆（此前只有 /chat 写，前端只用流式 → 上下文丢失）
        _remember(session_id, query, resp["reply"], meta=_meta_snapshot(final))
        yield _sse({"type": "result", **resp})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/memory")
def agent_memory(session_id: str = Query(DEFAULT_SESSION_ID, description="会话ID")):
    """指定会话的记忆概览（短期窗口 + 落盘记录）"""
    mem = get_session_store().get(session_id)
    record = get_conversation_store().get_session(session_id)
    return {
        "session_id": session_id,
        "short_term_len": len(mem.short_term),
        "summary": mem.get_summary(),
        "disk": (
            {
                "title": record.get("title"),
                "turn_count": record.get("turn_count"),
                "summary_upto": record.get("summary_upto"),
                "updated_at": record.get("updated_at"),
            }
            if record
            else None
        ),
        "recent": [
            {"role": m["role"], "content": m["content"][:80]}
            for m in list(mem.short_term)[-6:]
        ],
    }


@router.post("/clear")
def agent_clear(session_id: str = Query(DEFAULT_SESSION_ID, description="会话ID")):
    """清空指定会话的记忆（进程内窗口 + 落盘记录）"""
    get_session_store().drop(session_id)
    try:
        deleted = get_conversation_store().delete_session(session_id)
    except Exception:
        logger.warning("[memory] 会话清盘失败（已忽略）")
        deleted = 0
    return {
        "status": "success",
        "message": f"会话 {session_id} 的记忆已清空",
        "deleted_messages": deleted,
    }


@router.get("/flow")
def agent_flow():
    """主流程结构（调试用：一眼看清有哪些节点与路由）"""
    return _graph.describe()
