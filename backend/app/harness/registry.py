"""工具注册表：Agent 与工具层之间的统一契约

- TOOLS_SCHEMAS：注册给 LLM 的 function calling schema（classify / ReAct 循环用）
- TOOLS_MAP：工具名 → 可执行函数
- register_tool()：工具层（app/tools/）实现后调用注册

工具层就绪前，注册表为空——graph 中的工具节点会返回友好提示而不是崩溃。
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from loguru import logger

# 注册给 LLM 的工具 schema 列表（OpenAI function calling 格式）
TOOLS_SCHEMAS: list[dict] = []

# 工具名 → 执行函数（签名统一：(args: dict, on_stage=None) -> dict）
TOOLS_MAP: dict[str, Callable[..., dict]] = {}

# 注册锁（防止并发注册竞态）
_lock = threading.Lock()


def register_tool(name: str, schema: dict, handler: Callable[..., dict]) -> None:
    """注册一个工具：schema 给 LLM 看（决策），handler 给系统执行。

    Args:
        name: 工具名（唯一，如 query_financials）
        schema: function calling schema（OpenAI 格式，含 description/parameters）
        handler: 执行函数，签名 (args: dict, on_stage=None) -> dict
    """
    global TOOLS_SCHEMAS, TOOLS_MAP
    if not name or not handler:
        raise ValueError("工具注册需要 name 与 handler")
    with _lock:
        if name in TOOLS_MAP:
            logger.warning(f"[ToolsRegistry] 工具 {name} 已存在，将被覆盖")
        TOOLS_MAP[name] = handler
        # schema 按 name 去重（重复注册不产生重复项）
        TOOLS_SCHEMAS = [s for s in TOOLS_SCHEMAS if s.get("function", {}).get("name") != name]
        if schema:
            TOOLS_SCHEMAS.append(schema)
        logger.info(f"[ToolsRegistry] 已注册工具: {name}（共 {len(TOOLS_MAP)} 个）")


def get_tool(name: str) -> Callable[..., dict] | None:
    """按名字取工具执行函数；未注册返回 None"""
    return TOOLS_MAP.get(name)


def list_tool_names() -> list[str]:
    """当前已注册的工具名列表（调试/展示用）"""
    return sorted(TOOLS_MAP.keys())


def execute_tool(name: str, args: dict | None = None, on_stage=None) -> dict:
    """执行一个已注册工具（统一入口，带异常兜底）

    Returns:
        工具原始返回 dict；未注册/异常时返回 {"reply": 友好提示, "meta": {}}
    """
    handler = TOOLS_MAP.get(name)
    if handler is None:
        logger.warning(f"[ToolsRegistry] 调用未注册工具: {name}")
        return {"reply": f"工具 {name} 尚未注册（工具层未就绪）", "meta": {}}
    try:
        return handler(args or {}, on_stage)
    except Exception as e:
        logger.exception(f"[ToolsRegistry] 工具 {name} 执行异常")
        return {"reply": f"工具 {name} 执行失败：{e}", "meta": {"tool_error": str(e)}}
