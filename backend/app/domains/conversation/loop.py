"""对话主循环：assemble → decide → act → answer

取代原先的 classify + 四条分叉路径（simple / react / plan / chat）。原设计的三个问题：
1. `classify` 已经给出 tool_name，进 ReAct 后 LLM 又要自己选工具 —— 同一个问题问两遍；
2. simple 路径是"硬编码单工具一次调用"，两步任务（先查公司再查指标）走不通；
3. 四条路径各写一套上下文组装与错误处理，行为不一致。

现在的循环只有一条：
    assemble  组装上下文（首次初始化 / 低预算提醒）
      ↓
    decide    LLM 三选一：tool_calls / 最终回答 / 调用 ask_user 澄清
      ↓
    act       串行执行一个工具（harness.invoke：校验/重试/熔断/结构化错误）
      ↓ 有剩余调用则回 act，否则回 assemble
    answer    用已校验观察收口；预算耗尽/重复调用也在这一步兜底（禁止空手）

预算与停止原因见 harness/loop.py；本文件只管"策略"（提示词、工具集、上下文）。
"""
from __future__ import annotations

import json

from loguru import logger

from app.harness import loop as loop_budget
from app.harness.flow import Flow
from app.harness.invoke import run_tool
from app.harness.llm import get_llm
from app.harness import registry as tools_registry

MAX_TOOL_CONTENT = 1500      # 单条工具结果回填给 LLM 的最大字符数
ASK_USER = "ask_user"        # 内置澄清工具（不注册进 registry，只在本循环可见）

# 澄清工具：让"反问"成为一次标准工具调用，从而拿到结构化信号
# （question + options），供前端渲染按钮、供评测统计"低置信是否正确澄清"。
ASK_USER_SCHEMA = {
    "type": "function",
    "function": {
        "name": ASK_USER,
        "description": (
            "关键参数（公司 / 指标 / 期间 / 对比对象）缺失且无法从上下文推断时，"
            "先向用户反问澄清。**绝不猜**。只在必须知道才能继续时使用，不要用它打招呼。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "要问用户的一句话"},
                "options": {
                    "type": "array", "items": {"type": "string"},
                    "description": "2~4 个可选项（前端渲染成按钮）；没有明确选项就不要传",
                },
            },
            "required": ["question"],
        },
    },
}

SYSTEM_PROMPT = """你是财报解读助手，可以调用工具从本地财报库取数。

可用工具：
{schemas_desc}

规则：
1. 需要数据时调工具；已有的工具结果够回答就直接回答，不要重复调用同一个查询。
2. 回答里的数字必须来自工具返回结果，**不要自己估算、补全或换算**。
3. 工具返回"没有数据"时，如实说明库里没有该数据，不要编造。
4. 关键参数缺失且无法推断时，调用 {ask_user} 反问澄清，不要猜。
5. 中文回答，先给结论再给依据，简洁。
"""


# ---------------------------------------------------------------- 工具

def _tool_schemas() -> list[dict]:
    """本循环可见的工具：注册表里的业务工具 + 内置澄清工具

    必须**调用时**读 `tools_registry.TOOLS_SCHEMAS`：register_tool 里是
    `TOOLS_SCHEMAS = [...]`（重新绑定），import 时把名字绑死会永远拿到那个空列表，
    模型就会被告知"没有可用工具"。单元测试用假 LLM 不经过工具表，察觉不到这类错误，
    所以这里额外有一条 test_late_registered_tools_are_visible。
    """
    return list(tools_registry.TOOLS_SCHEMAS or []) + [ASK_USER_SCHEMA]


def _schemas_desc() -> str:
    items = []
    for s in _tool_schemas():
        fn = s.get("function") or {}
        items.append(f"- {fn.get('name')}：{fn.get('description', '')}")
    return "\n".join(items) or "（暂无可用工具）"


def _system_prompt() -> str:
    return SYSTEM_PROMPT.format(schemas_desc=_schemas_desc(), ask_user=ASK_USER)


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


# ---------------------------------------------------------------- 节点

def _assemble_node():
    """组装上下文：首次注入 system + user；临近预算耗尽时追加一次提醒"""

    def assemble(state: dict) -> dict:
        messages = list(state.get("messages") or [])
        if not any(m.get("role") == "user" for m in messages):
            messages = list(state.get("history") or [])
            messages.append({"role": "system", "content": _system_prompt()})
            messages.append({"role": "user", "content": state.get("query") or ""})
            return {"messages": messages}

        budget = loop_budget.LoopBudget.from_state(state)
        warned = any(
            m.get("role") == "system" and "只剩最后一步" in (m.get("content") or "")
            for m in messages
        )
        if budget.max_steps - budget.steps <= 1 and not warned:
            messages.append({
                "role": "system",
                "content": "（只剩最后一步：请基于已有工具结果直接作答，不要再调用工具）",
            })
            return {"messages": messages}
        return {"messages": messages}

    return assemble


def _decide_node():
    """LLM 决策：调工具 / 最终回答 / 澄清"""

    def decide(state: dict) -> dict:
        budget = loop_budget.LoopBudget.from_state(state)
        reason = budget.exceeded()
        if reason:                                    # 先查预算，别白烧一次 LLM
            return {"stop_reason": reason, **budget.to_state()}
        budget.spend_step()

        messages = list(state.get("messages") or [])
        out = get_llm().chat_with_tools(messages, tools=_tool_schemas())
        calls = out.get("tool_calls") or []

        if not calls:
            answer = (out.get("content") or "").strip()
            if not answer:
                return {"stop_reason": loop_budget.STOP_ERROR, **budget.to_state()}
            messages.append({"role": "assistant", "content": answer})
            return {"messages": messages, "answer": answer,
                    "stop_reason": loop_budget.STOP_ANSWERED, **budget.to_state()}

        # 澄清：终局动作，不进 act
        fn = (calls[0] or {}).get("function") or {}
        if (fn.get("name") or "") == ASK_USER:
            args = _parse_args(fn.get("arguments"))
            messages.append({"role": "assistant", "content": "", "tool_calls": calls})
            question = (args.get("question") or "").strip() or "请补充公司、指标或期间。"
            options = [str(o).strip() for o in (args.get("options") or []) if str(o).strip()]
            logger.info(f"[loop] 澄清反问：{question}")
            return {"messages": messages, "clarify": True, "answer": question,
                    "options": options, "stop_reason": loop_budget.STOP_CLARIFY,
                    **budget.to_state()}

        messages.append({"role": "assistant", "content": out.get("content") or "",
                         "tool_calls": calls})
        return {"messages": messages, "tool_calls": calls, **budget.to_state()}

    return decide


def _act_node():
    """执行一个工具调用（串行），结果同时进 messages 与 observations"""

    def act(state: dict) -> dict:
        budget = loop_budget.LoopBudget.from_state(state)
        reason = budget.exceeded(check_steps=False)
        if reason:
            return {"stop_reason": reason, "tool_calls": [], **budget.to_state()}

        calls = list(state.get("tool_calls") or [])
        if not calls:
            return {"stop_reason": loop_budget.STOP_ERROR, "tool_calls": []}

        tc = calls[0] or {}
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        args = _parse_args(fn.get("arguments"))

        seen = list(state.get("fingerprints") or [])
        fp = loop_budget.fingerprint(name, args)
        repeated = fp in seen                       # 同工具同参数 → 无进展

        # 统一走 invoke：参数校验 / 结构化错误 / 有界重试 / 熔断
        result = run_tool(name, args)
        budget.spend_call()

        messages = list(state.get("messages") or [])
        messages.append({"role": "tool", "tool_call_id": tc.get("id") or "",
                         "content": result.to_llm_content(MAX_TOOL_CONTENT)})
        observations = list(state.get("observations") or [])
        observations.append({"name": name, "args": args,
                             "status": result.status.value,
                             "reply": (result.reply or "")[:400],
                             "elapsed_ms": result.elapsed_ms})
        made = list(state.get("tool_calls_made") or [])
        made.append({"name": name, "status": result.status.value})
        seen.append(fp)

        logger.info(f"[loop] step={budget.steps} 工具 {name} → {result.status.value}"
                    f"（{result.elapsed_ms}ms）{'（重复调用）' if repeated else ''}")
        return {
            "messages": messages, "tool_calls": calls[1:],
            "observations": observations, "tool_calls_made": made, "fingerprints": seen,
            "stop_reason": loop_budget.STOP_REPEATED if repeated else "",
            **budget.to_state(),
        }

    return act


def _answer_node():
    """收口：正常回答 / 澄清问题 / 预算耗尽兜底（都不许空手）"""

    def answer(state: dict) -> dict:
        # clarify / options 无论是否澄清都显式写回，最终 state 的键集合才是确定的
        base = {"clarify": bool(state.get("clarify")),
                "options": list(state.get("options") or [])}
        if state.get("answer"):
            return {**base, "meta": _meta(state)}
        text = _fallback_answer(state)
        return {**base, "answer": text, "meta": _meta(state, fallback=True)}

    return answer


# ---------------------------------------------------------------- 收口与元信息

def _fallback_answer(state: dict) -> str:
    """预算/重复/异常时，用已取得的观察生成回答"""
    observations = state.get("observations") or []
    note = loop_budget.describe_stop(state.get("stop_reason") or "")
    if not observations:
        return (f"我没能取到需要的数据（{note}）。可以换个问法，"
                f"或先确认公司名、指标名和期间。")

    lines = [f"- 工具 {o.get('name')}（{o.get('status')}）：{o.get('reply') or ''}"
             for o in observations]
    messages = list(state.get("messages") or []) + [{
        "role": "user",
        "content": ("（系统提示：推理预算已结束，不要再调用工具。"
                    "请只根据下面已获取的数据回答用户问题；数据不足就明确说明缺什么。）\n"
                    "已获取数据：\n" + "\n".join(lines)),
    }]
    try:
        text = (get_llm().chat(messages) or "").strip()
    except Exception:
        logger.exception("[loop] 兜底回答生成失败")
        text = ""
    if text:
        return text
    return f"已查到 {len(observations)} 项数据，但{note}，未能整理成完整结论。"


def _meta(state: dict, fallback: bool = False) -> dict:
    budget = loop_budget.LoopBudget.from_state(state)
    made = state.get("tool_calls_made") or []
    return {
        "tools_called": [m.get("name") for m in made],
        "steps": budget.steps,
        "tool_calls": budget.calls,
        "stop_reason": state.get("stop_reason") or "",
        "clarify": bool(state.get("clarify")),
        "fallback": fallback,
        "elapsed_ms": budget.snapshot()["elapsed_ms"],
    }


# ---------------------------------------------------------------- 路由

def _decide_router():
    def route(state: dict) -> str:
        if state.get("stop_reason"):
            return "answer"
        return "act" if state.get("tool_calls") else "answer"

    return route


def _act_router():
    def route(state: dict) -> str:
        if state.get("stop_reason"):
            return "answer"
        return "act" if state.get("tool_calls") else "assemble"

    return route


def build_loop() -> Flow:
    """构建对话主循环"""
    f = Flow("conversation")
    f.add_node("assemble", _assemble_node())
    f.add_node("decide", _decide_node())
    f.add_node("act", _act_node())
    f.add_node("answer", _answer_node())
    f.set_entry("assemble")
    f.add_edge("assemble", "decide")
    f.add_router("decide", _decide_router(), {"act": "act", "answer": "answer"})
    f.add_router("act", _act_router(), {"act": "act", "assemble": "assemble"})
    return f
