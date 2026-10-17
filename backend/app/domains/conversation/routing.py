"""意图识别 + 路由合一节点（classify）

一次 LLM 调用产出四样东西（概念两层，实现合一）：
- intent     语义层：用户想做什么（查指标/分析/报告/闲聊…）
- task_type  执行层：走哪条执行路径（simple/react/plan/chat）→ 喂给条件边
- tool_name  simple 路径预选工具（省去再调一轮工具选择）
- entities   实体抽取（公司/指标/时间），预填工具参数

路由函数 route() 只是"把 task_type 搬给条件边"的一行胶水，与分类逻辑同文件。
"""
from __future__ import annotations

import json

from loguru import logger

from app.harness.llm import get_llm

# ---- 枚举（保持单一事实来源，供 prompt / 校验 / 前端展示共用）----
INTENTS = ("company_query", "metric_query", "analysis", "report", "data_overview", "chat")
TASK_TYPES = ("simple", "react", "plan", "chat")

# task_type 阈值：分类置信度低于该值时，降级到更稳的路径
_LOW_CONFIDENCE_FALLBACK: dict[str, str] = {
    "react": "simple",   # 不确定是否多步 → 先单步试，回答不足再升级
    "plan": "react",     # 不确定是否长任务 → 走推理路径
}

CLASSIFY_SYSTEM_PROMPT = """你是财报解读 Agent 的请求分类器。根据用户问题，输出一个 JSON 对象，包含：

1. intent（用户想做什么）：
   - company_query  查公司基本信息（所属行业、上市板块、公司全称等）
   - metric_query   查具体财务指标数值（营收、毛利率、ROE、资产负债率等）
   - analysis       需要多步推理的分析（如"毛利率为什么下降""盈利质量如何"）
   - report         生成完整财报解读报告（明确要求写报告/简报/解读文档）
   - data_overview  查询数据概览（库里有哪家公司、多少期数据、字段范围等）
   - chat           闲聊、问候、咨询机制/流程解释

2. task_type（执行路径，与 intent 对应但独立判断）：
   - simple  单次工具调用即可回答（查一个数、查一家公司）
   - react   需要多轮工具调用推理（分析原因、跨期对比、多步归因）
   - plan    长任务，需要计划后执行（生成完整报告）
   - chat    直接文字回答，不调任何工具

3. tool_name（仅 task_type=simple 时给出预选工具，其他情况为 null）：
   - query_financials   查财务指标数值
   - query_company      查公司基本信息
   - query_data_stats   查数据概览
   - rag_search        检索财报知识库返回片段（由你组织回答）

4. entities（抽取的关键实体，没有的字段给 null 或空字符串）：
   - company   公司名（如"贵州茅台""600519"）
   - metric    指标（如"毛利率""ROE""资产负债率"）
   - period    报告期（如"2025Q1""2024年报"）

5. confidence（0~1，分类置信度）
6. reason（一句话说明分类依据）

规则：
- 只有明确要求产出报告才 task_type=plan（"写一份茅台财报解读报告"）
- 问"为什么/原因/怎么看/对比"类问题倾向 react
- 闲聊、问机制（"RAG是什么""你能做什么"）走 chat，不调工具
- 输出必须是合法 JSON，不要输出其他内容。"""


def classify_node(state: dict) -> dict:
    """分类节点：一次 LLM 调用，产出 intent + task_type + entities + confidence。

    Returns:
        要写回 AgentState 的字段子集（LangGraph 节点约定：只返回增量）
    """
    query = (state.get("query") or "").strip()
    if not query:
        return {"intent": "chat", "task_type": "chat", "tool_name": None,
                "entities": {}, "confidence": 1.0, "reason": "空查询"}

    llm = get_llm()
    messages = [
        {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
        {"role": "user", "content": f"用户问题：{query}"},
    ]

    raw = llm.chat_json(messages, temperature=0.1)

    intent = str(raw.get("intent") or "").strip()
    task_type = str(raw.get("task_type") or "").strip()
    tool_name = raw.get("tool_name") or None
    entities = raw.get("entities") if isinstance(raw.get("entities"), dict) else {}
    confidence = float(raw.get("confidence") or 0.0)
    reason = str(raw.get("reason") or "")[:200]

    # ---- 兜底与校正（LLM 输出不可尽信）----
    if intent not in INTENTS:
        logger.warning(f"[classify] 非法 intent={intent!r}，降级为 chat")
        intent, task_type = "chat", "chat"
    if task_type not in TASK_TYPES:
        logger.warning(f"[classify] 非法 task_type={task_type!r}，降级为 simple")
        task_type = "simple"
    # 低置信度 → 走更稳的路径
    if confidence < 0.55 and task_type in _LOW_CONFIDENCE_FALLBACK:
        logger.info(f"[classify] 置信度 {confidence:.2f} 过低，{task_type} → {_LOW_CONFIDENCE_FALLBACK[task_type]}")
        task_type = _LOW_CONFIDENCE_FALLBACK[task_type]
    # simple 必须带工具，否则视为闲聊
    if task_type == "simple" and not tool_name:
        logger.info("[classify] simple 无预选工具，降级为 chat")
        task_type = "chat"

    logger.info(f"[classify] intent={intent} task_type={task_type} tool={tool_name} conf={confidence:.2f} | {query[:40]}")
    return {
        "intent": intent,
        "task_type": task_type,
        "tool_name": tool_name,
        "entities": entities,
        "confidence": confidence,
        "reason": reason,
    }


def route(state: dict) -> str:
    """条件边函数：读 task_type，返回下一个节点/子图名（路由胶水，就这一行逻辑）"""
    return state.get("task_type") or "chat"
