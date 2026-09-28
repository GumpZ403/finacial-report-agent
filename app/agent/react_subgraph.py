"""ReAct 子图：标准 function calling 循环

消息协议（OpenAI 兼容，完整轨迹）：
    user → assistant(tool_calls=[{id, function:{name, arguments}}])
         → tool(tool_call_id=id, content=结果)
         → assistant(tool_calls / content) → ...

- llm 节点：基于完整 messages 调 chat_with_tools；assistant 的 tool_calls 消息（含 id）原样保留
- tool 节点：循环执行本轮全部 tool_calls（支持并行），按 tool_call_id 回填 role="tool"；
  工具异常也回填为 tool 消息（LLM 自行决定重试或放弃）
- 条件边：有待执行工具且未超限 → tool；否则结束
"""
from __future__ import annotations

import json

from loguru import logger
from langgraph.graph import StateGraph, END

from app.agent.state import AgentState
from app.agent import tools_registry
from app.core.llm_client import get_llm

# 默认最大工具调用轮数（防 LLM 绕圈）
DEFAULT_MAX_ROUNDS = 4

# 工具结果回填给 LLM 的最大字符数
MAX_TOOL_CONTENT = 1500

REACT_SYSTEM_PROMPT = """你是财报解读 Agent。你可以调用以下工具获取数据：
{schemas_desc}

工作方式（ReAct）：
1. 需要数据/事实时，先调用工具，再根据工具结果推理；
2. 可以一次调用多个工具（并行）；结果不够再调（最多 {max_rounds} 轮）；
3. 信息足够后，直接用中文给出分析结论，不要说「我调用了工具」；
4. 工具不可用时，明确告知用户哪些能力暂不可用，不要编造数据。"""


def _schemas_desc() -> str:
    return "\n".join(
        f"- {s['function']['name']}：{s['function'].get('description', '')}"
        for s in tools_registry.TOOLS_SCHEMAS
    ) or "（暂无可用工具）"


def _parse_args(raw) -> dict:
    """arguments（dict 或 JSON 字符串）→ dict"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _serialize_result(result: dict, max_len: int = MAX_TOOL_CONTENT) -> str:
    """工具返回值 → tool 消息 content（优先 reply 字段，兜底整体序列化）"""
    reply = result.get("reply")
    if isinstance(reply, str) and reply.strip():
        return reply[:max_len]
    try:
        return json.dumps(result, ensure_ascii=False, default=str)[:max_len]
    except (TypeError, ValueError):
        return str(result)[:max_len]


def _init_messages(state: dict) -> list[dict]:
    """首轮初始化消息轨迹：history + system + user（重复进入不重复添加）"""
    messages = list(state.get("messages") or [])
    if any(m.get("role") == "user" for m in messages):
        return messages
    messages = list(state.get("history") or [])
    messages.append({
        "role": "system",
        "content": REACT_SYSTEM_PROMPT.format(
            schemas_desc=_schemas_desc(),
            max_rounds=state.get("max_rounds", DEFAULT_MAX_ROUNDS),
        ),
    })
    messages.append({"role": "user", "content": state.get("query") or ""})
    return messages


def _build_llm_node():
    def llm_node(state: dict) -> dict:
        """LLM 决策节点：基于完整消息轨迹决定调工具或直接回答"""
        llm = get_llm()
        messages = _init_messages(state)

        llm_out = llm.chat_with_tools(messages, tools=tools_registry.TOOLS_SCHEMAS or None)
        tool_calls = llm_out.get("tool_calls") or []
        if not tool_calls:
            # 直接回答 → 结束
            answer = (llm_out.get("content") or "").strip() or "抱歉，我没能完成分析。"
            messages.append({"role": "assistant", "content": answer})
            return {"messages": messages, "answer": answer}

        # 保留 assistant 的 tool_calls 消息（含 id，标准协议必需）
        messages.append({
            "role": "assistant",
            "content": llm_out.get("content") or "",
            "tool_calls": tool_calls,
        })
        return {
            "messages": messages,
            "tool_calls": tool_calls,          # 待执行队列
            "tool_rounds": (state.get("tool_rounds") or 0) + 1,
        }

    return llm_node


def _build_tool_node():
    def tool_node(state: dict) -> dict:
        """工具执行节点：循环执行本轮全部 tool_calls，按 id 回填 role=tool"""
        tcs = list(state.get("tool_calls") or [])
        messages = list(state.get("messages") or [])
        round_no = state.get("tool_rounds", 0)

        for tc in tcs:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args = _parse_args(fn.get("arguments"))
            try:
                result = tools_registry.execute_tool(name, args)
                content = _serialize_result(result)
                logger.info(f"[ReAct] 第{round_no}轮 执行工具: {name} args={args}")
            except Exception as e:
                # 工具异常 → 也回填为 tool 消息，让 LLM 自行决定重试/放弃
                content = f"工具执行失败: {e}"
                logger.warning(f"[ReAct] 第{round_no}轮 工具 {name} 执行失败: {e}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or "",
                "content": content,
            })

        return {"messages": messages, "tool_calls": []}

    return tool_node


def _build_should_continue():
    def should_continue(state: dict) -> str:
        """条件边：有待执行工具且未超限 → tool；否则结束"""
        if state.get("tool_calls") and (state.get("tool_rounds") or 0) <= (state.get("max_rounds") or DEFAULT_MAX_ROUNDS):
            return "tool"
        return "end"

    return should_continue


def build_react_subgraph():
    """构建并编译 ReAct 子图（作为主图的一个节点挂载）"""
    g = StateGraph(AgentState)
    g.add_node("llm", _build_llm_node())
    g.add_node("tool", _build_tool_node())
    g.set_entry_point("llm")
    g.add_conditional_edges("llm", _build_should_continue(),
                            {"tool": "tool", "end": END})
    g.add_edge("tool", "llm")          # 工具结果回 LLM 继续推理
    return g.compile()
