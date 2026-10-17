"""Plan-Execute 子图：长任务（财报解读报告）骨架

流程：plan（定计划）→ execute（逐项取数）→ write（LLM 写作）→ END

当前为骨架版本：
- plan 节点：模板化计划（财报报告结构固定，不需要每次让 LLM 从头规划）
- execute 节点：按计划执行工具（统一走 harness/invoke.run_tool，带参数校验与结构化错误）
- write 节点：把取到的数据交给 LLM 生成报告正文

当前仍是骨架：报告八章结构、数字白名单溯源、合规校验（方案 §6）尚未落地。
"""
from __future__ import annotations

from loguru import logger

from app.harness.flow import Flow
from app.harness.llm import get_llm

# 财报报告的标准计划骨架（报告结构固定，模板化省一次 LLM 规划）
# 注：rag_search 已于 2026-10 撤下（检索模块冻结，理由见 docs/ 与 main.py 组合根注释）
DEFAULT_PLAN = [
    {"step": 1, "action": "query_company",  "desc": "公司基本信息（全称/行业/板块）"},
    {"step": 2, "action": "query_financials", "desc": "核心财务指标（营收/利润/毛利率/ROE）"},
    {"step": 3, "action": "query_financials", "desc": "近年趋势与同比变化"},
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

        from app.harness.invoke import run_tool

        result = run_tool(action, args)
        # 记录状态：无数据/故障要在写作阶段显式标注，不能让 LLM 拿空数据硬写
        collected.append({
            "step": step,
            "result": result.reply,
            "status": result.status.value,
        })
        return {"collected": collected, "plan_index": idx + 1}

    return execute_node


def _build_write_node():
    def write_node(state: dict) -> dict:
        """写作节点：把采集的数据交给 LLM 生成报告正文"""
        collected = state.get("collected") or []
        data_block = "\n\n".join(
            f"【步骤{it['step'].get('step')}：{it['step'].get('desc')}｜状态 {it.get('status', 'ok')}】\n{it['result']}"
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


def build_plan_flow() -> Flow:
    """构建 Plan-Execute 子流程"""
    f = Flow("plan")
    f.add_node("plan", _build_plan_node())
    f.add_node("execute", _build_execute_node())
    f.add_node("write", _build_write_node())
    f.set_entry("plan")

    def _after_execute(state: dict) -> str:
        return "write" if (state.get("plan_index") or 0) >= len(state.get("plan") or []) else "execute"

    f.add_edge("plan", "execute")
    f.add_router("execute", _after_execute, {"execute": "execute", "write": "write"})
    return f
