"""有界 agent 循环的通用机制（与业务无关）

Flow 只负责"节点怎么连"，本模块负责"什么时候必须停"：
- 预算：步数 / 工具调用次数 / 墙钟时间
- 无进展：同一 (工具, 参数) 再次出现
- 停止原因枚举：写进 meta，做评测时直接可以按原因分组统计

放在 harness 而不是 conversation：以后归因 worker 也要用同一套预算与重复检测。
所有状态都做成**可 JSON 化的基本类型**（int/float/str/list），方便跟着 state 走与落盘。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

# ---- 停止原因 ----
STOP_ANSWERED = "answered"            # 正常给出最终回答
STOP_CLARIFY = "clarify"              # 反问澄清
STOP_BUDGET_STEPS = "budget_steps"    # 步数用尽
STOP_BUDGET_CALLS = "budget_calls"    # 工具调用次数用尽
STOP_BUDGET_SECONDS = "budget_seconds"  # 墙钟超时
STOP_REPEATED = "repeated_call"       # 同一工具+参数重复调用（无进展）
STOP_ERROR = "error"                  # LLM 空响应等异常

DEFAULT_MAX_STEPS = 6          # 最多几轮 decide（每轮一次 LLM 调用）
DEFAULT_MAX_CALLS = 8          # 最多几次工具调用
DEFAULT_MAX_SECONDS = 90.0     # 单次对话的墙钟上限


def fingerprint(name: str, args: dict | None) -> str:
    """工具调用的稳定指纹

    参数按 key 排序后序列化：{"a":1,"b":2} 与 {"b":2,"a":1} 视为同一次调用，
    否则"重复调用检测"会被 JSON 键序差异骗过去。
    """
    try:
        body = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        body = str(args)
    return f"{name}:{body}"


@dataclass
class LoopBudget:
    """循环预算：从 state 重建 → 消耗 → 写回 state"""

    max_steps: int = DEFAULT_MAX_STEPS
    max_calls: int = DEFAULT_MAX_CALLS
    max_seconds: float = DEFAULT_MAX_SECONDS
    steps: int = 0
    calls: int = 0
    started_at: float = field(default_factory=time.monotonic)

    # ---- 与 state 互转（state 里只放基本类型）----
    @classmethod
    def from_state(cls, state: dict) -> "LoopBudget":
        # 注意不能写 `state.get("max_steps") or DEFAULT`：显式传 0 会被 or 吞成默认值
        max_steps = state.get("max_steps")
        max_calls = state.get("max_calls")
        max_seconds = state.get("max_seconds")
        started = state.get("started_at")
        return cls(
            max_steps=int(DEFAULT_MAX_STEPS if max_steps is None else max_steps),
            max_calls=int(DEFAULT_MAX_CALLS if max_calls is None else max_calls),
            max_seconds=float(DEFAULT_MAX_SECONDS if max_seconds is None else max_seconds),
            steps=int(state.get("steps") or 0),
            calls=int(state.get("tool_calls_used") or 0),
            started_at=float(time.monotonic() if started is None else started),
        )

    def to_state(self) -> dict:
        return {
            "steps": self.steps,
            "tool_calls_used": self.calls,
            "started_at": self.started_at,
            "max_steps": self.max_steps,
            "max_calls": self.max_calls,
            "max_seconds": self.max_seconds,
        }

    # ---- 消耗与判断 ----
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def spend_step(self) -> None:
        self.steps += 1

    def spend_call(self) -> None:
        self.calls += 1

    def exceeded(self, check_steps: bool = True) -> str | None:
        """超预算则返回停止原因，否则 None

        act 里要传 check_steps=False：那一步的预算已经在 decide 阶段花掉了，
        模型刚提出的工具调用应该被允许执行，否则 max_steps=1 会变成"一步都不做"。
        """
        if check_steps and self.steps >= self.max_steps:
            return STOP_BUDGET_STEPS
        if self.calls >= self.max_calls:
            return STOP_BUDGET_CALLS
        if self.elapsed() >= self.max_seconds:
            return STOP_BUDGET_SECONDS
        return None

    def snapshot(self) -> dict:
        return {"steps": self.steps, "calls": self.calls,
                "elapsed_ms": int(self.elapsed() * 1000)}


STOP_TEXT = {
    STOP_BUDGET_STEPS: "推理步数用尽",
    STOP_BUDGET_CALLS: "工具调用次数用尽",
    STOP_BUDGET_SECONDS: "处理超时",
    STOP_REPEATED: "同一个查询重复执行",
    STOP_ERROR: "模型没有返回内容",
}


def describe_stop(reason: str | None) -> str:
    """停止原因 → 给用户看的说明（兜底回答里用）"""
    return STOP_TEXT.get(reason or "", "处理未完成")
