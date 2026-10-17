"""Agent 状态定义（流程共享黑板）

一次运行里所有节点读写同一个 state dict（TypedDict 只做键提示，不做运行时校验）。

2026-10 变更：取消 classify 预分类，主流程改为单一有界循环
（assemble → decide → act → answer），因此删掉 intent / task_type / tool_name /
entities / confidence / reason / tool_rounds / max_rounds 七个字段，
新增循环字段（steps / observations / tool_calls_made / stop_reason / clarify）。
"""
from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """流程状态：所有节点共享的 dict（TypedDict 约束键）"""

    # ---- 输入 ----
    query: str                 # 用户问题原文
    history: list[dict]        # 对话历史（来自 Memory：摘要 + 最近窗口）

    # ---- 循环轨迹 ----
    messages: list[dict]       # 完整消息轨迹（system / user / assistant(tool_calls) / tool 回填）
    tool_calls: list[dict]     # 本轮**待执行**的工具调用队列（标准 FC：[{id, function:{name, arguments}}]）
    observations: list[dict]   # 每次工具调用的结构化结果（ToolResult.to_dict()）
    tool_calls_made: list[dict]  # 已执行的调用摘要：[{name, status}]
    fingerprints: list[str]    # 已执行调用的指纹（重复调用检测）

    # ---- 预算（LoopBudget 与 state 互转，见 harness/loop.py）----
    steps: int                 # 已消耗的 decide 轮数
    tool_calls_used: int       # 已消耗的工具调用次数
    started_at: float          # monotonic 起始时间
    max_steps: int
    max_calls: int
    max_seconds: float

    # ---- 终止 ----
    stop_reason: str           # harness.loop.STOP_*：answered / clarify / budget_* / repeated_call / error
    clarify: bool              # 是否是反问澄清
    options: list[str]         # 澄清时可选项（前端渲染成按钮）

    # ---- Plan & Solve（报告子图专有，主循环不使用）----
    plan: list[dict]
    plan_index: int
    collected: list[dict]

    # ---- 输出 ----
    answer: str                # 最终回答文本
    meta: dict[str, Any]       # 本轮结构化元信息（tools_called / steps / stop_reason / clarify）
