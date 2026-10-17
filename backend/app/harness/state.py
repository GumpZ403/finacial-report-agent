"""Agent 状态定义（LangGraph 共享黑板）

整张图的所有节点读写同一个 AgentState：
- classify 节点写入 intent / task_type / entities
- 执行节点写入 tool_results / messages
- 生成节点写入 answer / meta
"""
from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """LangGraph 状态：所有节点共享的 dict（TypedDict 约束键）"""

    # ---- 输入 ----
    query: str                 # 用户问题原文
    history: list[dict]        # 对话历史（来自 Memory，注入 LLM 上下文）

    # ---- classify 产出（意图 + 路由合一）----
    intent: str                # 语义层：company_query / metric_query / analysis / report / data_overview / chat
    task_type: str             # 执行层：simple / react / plan / chat（路由依据）
    tool_name: str             # simple 路径预选工具（classify 一次调用直接给出）
    entities: dict             # 实体抽取：{"company": "贵州茅台", "metric": "gross_margin", "period": "2025Q1"}
    confidence: float          # 分类置信度（< 阈值时降级到更稳的路径）
    reason: str                # 分类依据（可观测/调试）

    # ---- 执行过程 ----
    tool_rounds: int           # 已执行工具轮数（ReAct 限深用）
    max_rounds: int            # 工具调用上限（防死循环）
    tool_results: list[dict]   # 工具执行结果列表（[{tool, args, reply}]）
    tool_calls: list[dict]     # 本轮待执行工具队列（标准 FC：[{id, function:{name, arguments}}]）
    messages: list[dict]       # agent 内部消息（完整轨迹：assistant(tool_calls) + tool 回填）

    # ---- 输出 ----
    answer: str                # 最终回答文本
    meta: dict[str, Any]       # 透传前端渲染所需元信息（sources/stats/word_count...）
