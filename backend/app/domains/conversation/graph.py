"""财报解读 Agent 主流程：组装（自研 Flow，见 harness/flow.py）

```
用户提问 → classify（一次 LLM：intent + task_type + entities）
              │ 路由器 route 读 task_type
              ├─ simple → tool（执行预选工具）→ answer（LLM 总结）→ END
              ├─ react  → ReAct 子流程（多步推理循环）
              ├─ plan   → Plan-Execute 子流程（长任务报告）
              └─ chat   → chat 节点（LLM 直接回答）→ END
```

子流程就是普通节点：`add_node("react", build_react_flow())`。
"""
from __future__ import annotations

from loguru import logger

from app.domains.conversation.react import build_react_flow
from app.domains.conversation.routing import classify_node, route
from app.domains.report.graph import build_plan_flow
from app.harness.flow import Flow
from app.harness.llm import get_llm

CHAT_SYSTEM_PROMPT = """你是「财报解读 Agent」。
你能完成以下工作：
1. 财务指标查询：如「贵州茅台 2025 年一季度毛利率多少」
2. 财报分析：如「宁德时代近几年盈利能力怎么样」「毛利率为什么下降」
3. 财报解读报告：如「写一份贵州茅台财报解读报告」
4. 数据概览：如「数据库里有哪些公司、多少期数据」

当用户问「你是谁 / 你能干什么」时，简洁介绍以上能力并各给一个示例问法；
其他时候正常自然聊天。自称「财报解读助手」，不要说「路由器」「流程」「节点」等内部术语。"""


def _build_chat_node():
    def chat_node(state: dict) -> dict:
        """闲聊/机制解释：LLM 直接回答（不调工具）"""
        llm = get_llm()
        messages = list(state.get("history") or [])
        messages += [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": state.get("query") or ""},
        ]
        reply = llm.chat(messages)
        return {
            "answer": reply.strip(),
            "meta": {"intent": "chat", "agent": "财报解读助手"},
        }

    return chat_node


def _build_tool_node():
    def tool_node(state: dict) -> dict:
        """simple 路径：执行 classify 预选的工具"""
        from app.harness.invoke import run_tool

        name = state.get("tool_name") or ""
        # 工具参数：classify 抽取的实体（公司/指标/时间）直接作为入参
        args = state.get("entities") or {}
        logger.info(f"[simple] 执行工具: {name} args={args}")
        result = run_tool(name, args)
        return {"tool_results": [{
            "tool": name,
            "args": args,
            "reply": result.reply,
            "tool_status": result.status.value,
        }]}

    return tool_node


def _build_answer_node():
    def answer_node(state: dict) -> dict:
        """simple 路径：LLM 把工具结果整理成自然语言回答"""
        llm = get_llm()
        results = state.get("tool_results") or []
        data_block = "\n\n".join(f"【{r.get('tool')}】\n{r.get('reply')}" for r in results)
        messages = [
            {"role": "system", "content": (
                "你是财报解读助手。以下是工具查询返回的数据，请用简洁清晰的中文回答用户问题。"
                "直接给结论，不要说「根据工具返回」。数据为空时如实说明。"
            )},
            {"role": "user", "content": f"用户问题：{state.get('query')}\n\n工具数据：\n{data_block or '（无数据）'}"},
        ]
        reply = llm.chat(messages)
        return {
            "answer": reply.strip(),
            "meta": {
                "intent": state.get("intent") or "metric_query",
                "agent": "财报解读助手",
                "tool_results": results,
            },
        }

    return answer_node


def build_graph() -> Flow:
    """构建主流程（进程内单例由 get_agent_graph 持有）"""
    f = Flow("conversation")

    f.add_node("classify", classify_node)
    f.add_node("chat", _build_chat_node())
    f.add_node("tool", _build_tool_node())
    f.add_node("answer", _build_answer_node())
    # 子流程作为节点挂载
    f.add_node("react", build_react_flow())
    f.add_node("plan", build_plan_flow())

    f.set_entry("classify")
    f.add_router("classify", route, {
        "simple": "tool",
        "react": "react",
        "plan": "plan",
        "chat": "chat",
    })
    f.add_edge("tool", "answer")
    # answer / chat / react / plan 均无出边 → 流程结束
    return f


# 模块级单例（供 interfaces 层引用）
_compiled: Flow | None = None


def get_agent_graph() -> Flow:
    """获取主流程实例（懒加载单例）"""
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
        logger.info(f"[AgentFlow] 主流程构建完成：{_compiled.node_names}")
    return _compiled
