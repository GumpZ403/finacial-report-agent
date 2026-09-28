"""Plan-Execute 子图：长任务（财报解读报告）骨架

流程：plan（定计划）→ execute（逐项取数）→ write（LLM 写作）→ END

当前为骨架版本：
- plan 节点：模板化计划（财报报告结构固定，不需要每次让 LLM 从头规划）
- execute 节点：按计划执行工具（从 tools_registry 取）
- write 节点：把取到的数据交给 LLM 生成报告正文

工具层就绪后，execute 节点自动生效；报告模板/合规校验后续迭代。
"""
from __future__ import annotations

from loguru import logger
from langgraph.graph import StateGraph, END

from app.agent.state import AgentState
from app.agent import tools_registry
from app.core.llm_client import get_llm

# 财报报告的标准计划骨架（报告结构固定，模板化省一次 LLM 规划）
DEFAULT_PLAN = [
    {"step": 1, "action": "query_company",  "desc": "公司基本信息（全称/行业/板块）"},
    {"step": 2, "action": "query_financials", "desc": "核心财务指标（营收/利润/毛利率/ROE）"},
    {"step": 3, "action": "query_financials", "desc": "近年趋势与同比变化"},
    {"step": 4, "action": "rag_search",   "desc": "检索知识库补充行业/公司背景"},
]

WRITE_SYSTEM_PROMPT = """你是财报解读报告撰写人。基于以下工具采集的数据，撰写一份结构完整的财报解读报告：
1. 公司概况（基本信息）
2. 核心财务表现（营收/利润/盈利能力）
3. 趋势与同比变化
4. 综合解读（亮点/风险）
5. 结论

要求：
- 数据必须来自工具结果，不得编造；
- 用中文，条理清晰，Markdown 格式；
- 数据不足的部分明确标注"数据待补充"。"""


def _build_plan_node():
    def plan_node(state: dict) -> dict:
        """计划节点：写回执行计划（当前用模板；后续可让 LLM 定制）"""
        topic = (state.get("entities") or {}).get("company") or state.get("query") or "目标公司"
        plan = list(DEFAULT_PLAN)
        logger.info(f"[Plan] 计划生成（{len(plan)} 步），主题: {topic}")
        return {
            "plan": plan,
            "plan_index": 0,
            "collected": [],
        }

    return plan_node


def _build_execute_node():
    def execute_node(state: dict) -> dict:
        """执行节点：按计划逐步执行工具，结果收集到 collected"""
        plan = state.get("plan") or []
        idx = state.get("plan_index") or 0
        collected = list(state.get("collected") or [])

        if idx >= len(plan):
            return {"plan_index": idx}   # 计划执行完

        step = plan[idx]
        action = step.get("action")
        logger.info(f"[Plan] 执行第{idx+1}/{len(plan)}步: {action} - {step.get('desc')}")
        # 工具参数：优先用计划里的参数，缺省用 entities
        args = step.get("args") or {}
        entities = state.get("entities") or {}
        if "company" not in args and entities.get("company"):
            args = {**args, "company": entities["company"]}

        result = tools_registry.execute_tool(action, args)
        collected.append({"step": step, "result": result.get("reply", "")})
        return {"collected": collected, "plan_index": idx + 1}

    return execute_node


def _build_write_node():
    def write_node(state: dict) -> dict:
        """写作节点：把采集的数据交给 LLM 生成报告正文"""
        collected = state.get("collected") or []
        data_block = "\n\n".join(
            f"【步骤{it['step'].get('step')}：{it['step'].get('desc')}】\n{it['result']}"
            for it in collected
        ) or "（未采集到数据）"

        llm = get_llm()
        messages = [
            {"role": "system", "content": WRITE_SYSTEM_PROMPT},
            {"role": "user", "content": f"主题：{(state.get('entities') or {}).get('company') or state.get('query')}\n\n采集数据：\n{data_block}"},
        ]
        reply = llm.chat(messages, max_tokens=4000)
        return {
            "answer": reply,
            "meta": {
                "intent": "report",
                "agent": "财报解读报告",
                "word_count": len(reply),
                "plan_steps": len(collected),
            },
        }

    return write_node


def build_plan_subgraph():
    """构建并编译 Plan-Execute 子图"""
    g = StateGraph(AgentState)
    g.add_node("plan", _build_plan_node())
    g.add_node("execute", _build_execute_node())
    g.add_node("write", _build_write_node())
    g.set_entry_point("plan")

    def _after_execute(state: dict) -> str:
        return "write" if (state.get("plan_index") or 0) >= len(state.get("plan") or []) else "execute"

    g.add_edge("plan", "execute")
    g.add_conditional_edges("execute", _after_execute, {"execute": "execute", "write": "write"})
    g.add_edge("write", END)
    return g.compile()
