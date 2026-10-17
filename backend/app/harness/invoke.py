"""工具统一执行层：所有工具调用都从这里走

解决四类问题（也是原 tools_registry.execute_tool 的四个坑）：

1. **参数校验前置**：按注册的 JSON Schema 校验 required / type / enum，
   不让“少传一个参数”变成工具内部的 KeyError / AttributeError 再被吞成一句
   “工具执行失败”。
2. **结构化错误**：用 ToolStatus 区分「未注册 / 参数非法 / 没有数据 / 系统故障 /
   熔断」，并把状态写进回填给 LLM 的文本。模型才能判断该换参数重试、换个工具，
   还是如实告诉用户“库里没有这家公司”。
3. **有界重试**：只对系统故障重试（默认 1 次），参数错误与无数据不重试
   ——避免把 ReAct 的 4 轮预算烧在同一个必然失败的上。
4. **熔断**：同一工具连续失败 N 次后进入冷却期，期内直接 CIRCUIT_OPEN。

约定（工具侧）：
    handler(args: dict, on_stage=None) -> dict   # 至少含 "reply"
    可选键 "status": "no_data" 表示“正常执行但没查到数据”，用于把 NO_DATA
    与真实故障区分开；缺省视为 ok。

NO_DATA 不是错误：它会被原样回填给 LLM，并附一句“不要用同样参数重复调用”。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger

from app.harness import registry

# 回填给 LLM 的最大字符数（与 ReAct 的 MAX_TOOL_CONTENT 保持一致）
MAX_CONTENT = 1500

# 熔断参数
FAILURE_THRESHOLD = 3          # 连续失败多少次后熔断
COOLDOWN_SEC = 60.0            # 冷却时长
DEFAULT_RETRIES = 1            # 系统故障重试次数（不含首次）


class ToolStatus(str, Enum):
    """工具执行结果状态"""

    OK = "ok"                       # 正常返回
    NO_DATA = "no_data"             # 正常执行，但没查到数据（不是故障）
    INVALID_ARGS = "invalid_args"   # 参数校验不通过（模型可以自己改参数重试）
    UNREGISTERED = "unregistered"   # 工具不存在（不要再试）
    FAILED = "failed"               # 系统故障（可重试；连续失败会熔断）
    CIRCUIT_OPEN = "circuit_open"   # 熔断冷却中（不要再试）


# 各状态回填给 LLM 的提示后缀（引导模型下一步动作）
_HINT = {
    ToolStatus.OK: "",
    ToolStatus.NO_DATA: "（查询正常完成但没有数据。不要用相同参数重复调用；换参数，或如实告知用户库里没有该数据）",
    ToolStatus.INVALID_ARGS: "（参数不合法，请修正参数后重试；若无法构造合法参数，就直接告诉用户缺少什么信息）",
    ToolStatus.UNREGISTERED: "（该工具不存在，不要再调用它）",
    ToolStatus.FAILED: "（系统故障，不是数据问题。最多再试一次；仍失败就告知用户该能力暂不可用，不要编造数据）",
    ToolStatus.CIRCUIT_OPEN: "（该工具近期连续故障，已临时停用。不要重试；告知用户稍后再试，或改用其他工具）",
}


@dataclass
class ToolResult:
    """工具执行的结构化结果"""

    name: str
    status: ToolStatus
    reply: str = ""
    data: dict = field(default_factory=dict)
    error: str | None = None
    elapsed_ms: int = 0
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.status is ToolStatus.OK

    @property
    def meta(self) -> dict:
        return self.data.get("meta") or {}

    def to_llm_content(self, max_chars: int = MAX_CONTENT) -> str:
        """回填给 LLM 的 tool 消息内容：故障类带状态头，正常结果只给正文"""
        body = (self.reply or "").strip()
        if not body:
            body = str(self.data)[:max_chars] if self.data else "（工具无返回内容）"
        if self.status in (ToolStatus.OK, ToolStatus.NO_DATA):
            return (body + _HINT[self.status])[:max_chars]
        return f"[工具 {self.name} 调用未成功：{self.status.value}] {body}{_HINT[self.status]}"[:max_chars]

    def to_dict(self) -> dict:
        """兼容旧返回契约 {reply, meta}，并把状态附加进去（供 state/前端排查）"""
        out = dict(self.data)
        out.setdefault("reply", self.reply)
        out["tool_status"] = self.status.value
        if self.error:
            out["tool_error"] = self.error
        return out


# ---------------- 参数校验（不引第三方依赖，够用即可）----------------

_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate_args(schema: dict | None, args: dict) -> list[str]:
    """按 JSON Schema 校验参数，返回错误列表（空列表=合法）

    只覆盖 function calling 里实际会用到的子集：required / type / enum / 默认值。
    未知的多余参数放行（模型常多给一两个字段，不该因此失败）。
    """
    params = ((schema or {}).get("function") or {}).get("parameters") or {}
    props: dict = params.get("properties") or {}
    required: list[str] = params.get("required") or []
    errors: list[str] = []

    if not isinstance(args, dict):
        return [f"参数必须是对象，实际是 {type(args).__name__}"]

    for key in required:
        if args.get(key) in (None, ""):
            errors.append(f"缺少必填参数 {key}")

    for key, value in args.items():
        spec = props.get(key)
        if spec is None or value is None:
            continue
        expected = spec.get("type")
        py_type = _TYPE_MAP.get(expected or "")
        if py_type is not None:
            # bool 是 int 的子类，integer 不接受 True/False
            if isinstance(value, bool) and expected in ("integer", "number"):
                errors.append(f"参数 {key} 应为 {expected}，实际传了布尔值")
            elif not isinstance(value, py_type):
                # 整数值的浮点（如 8.0）可以接受为 integer
                if expected == "integer" and isinstance(value, float) and value.is_integer():
                    pass
                else:
                    errors.append(f"参数 {key} 应为 {expected}，实际是 {type(value).__name__}")
        enum = spec.get("enum")
        if enum and value not in enum:
            errors.append(f"参数 {key} 只能是 {enum} 之一，实际是 {value!r}")
    return errors


# ---------------- 熔断状态（进程内）----------------

_failures: dict[str, int] = {}
_open_until: dict[str, float] = {}


def _is_open(name: str) -> bool:
    until = _open_until.get(name)
    if until is None:
        return False
    if time.monotonic() >= until:
        _open_until.pop(name, None)
        _failures.pop(name, None)
        logger.info(f"[ToolInvoke] 工具 {name} 熔断冷却结束，恢复调用")
        return False
    return True


def _record_failure(name: str) -> None:
    n = _failures.get(name, 0) + 1
    _failures[name] = n
    if n >= FAILURE_THRESHOLD and name not in _open_until:
        _open_until[name] = time.monotonic() + COOLDOWN_SEC
        logger.warning(f"[ToolInvoke] 工具 {name} 连续失败 {n} 次，熔断 {COOLDOWN_SEC:.0f}s")


def _record_success(name: str) -> None:
    _failures.pop(name, None)
    _open_until.pop(name, None)


def reset_circuit(name: str | None = None) -> None:
    """重置熔断状态（测试与运维用）"""
    if name is None:
        _failures.clear()
        _open_until.clear()
    else:
        _failures.pop(name, None)
        _open_until.pop(name, None)


# ---------------- 执行入口 ----------------

def run_tool(
    name: str,
    args: dict | None = None,
    on_stage=None,
    retries: int = DEFAULT_RETRIES,
) -> ToolResult:
    """执行一个已注册工具，返回结构化 ToolResult（永不抛异常）"""
    args = args or {}
    started = time.monotonic()

    def _result(status: ToolStatus, reply: str, data: dict | None = None,
                error: str | None = None, attempts: int = 1) -> ToolResult:
        return ToolResult(
            name=name, status=status, reply=reply, data=data or {},
            error=error, attempts=attempts,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    handler = registry.get_tool(name)
    if handler is None:
        logger.warning(f"[ToolInvoke] 调用未注册工具: {name}")
        return _result(ToolStatus.UNREGISTERED,
                       f"工具 {name} 未注册（已注册：{registry.list_tool_names()}）")

    errors = validate_args(registry.get_schema(name), args)
    if errors:
        logger.info(f"[ToolInvoke] 参数校验失败 {name}: {errors}")
        return _result(ToolStatus.INVALID_ARGS, "；".join(errors))

    if _is_open(name):
        return _result(ToolStatus.CIRCUIT_OPEN, f"工具 {name} 暂时不可用（连续故障熔断中）")

    last_error = ""
    attempts = 0
    for attempt in range(1, retries + 2):
        attempts = attempt
        try:
            resp = handler(args, on_stage)
        except Exception as e:  # noqa: BLE001 —— 工具内任何异常都转成结构化故障
            last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"[ToolInvoke] 工具 {name} 第 {attempt} 次执行失败: {last_error}")
            if attempt <= retries:
                continue
            _record_failure(name)
            return _result(ToolStatus.FAILED, f"工具 {name} 执行失败：{e}",
                           error=last_error, attempts=attempts)

        if not isinstance(resp, dict):
            _record_failure(name)
            return _result(ToolStatus.FAILED,
                           f"工具 {name} 返回类型非法（{type(resp).__name__}，应为 dict）",
                           error="non-dict return", attempts=attempts)

        status = ToolStatus.NO_DATA if resp.get("status") == ToolStatus.NO_DATA.value else ToolStatus.OK
        # 兜底：老工具没有显式声明 status 时，用“meta 为空”判为无数据
        # （现有 4 个工具的成功路径都会写 meta；无数据路径 meta 为 {}）
        if status is ToolStatus.OK and not resp.get("meta"):
            status = ToolStatus.NO_DATA
        if status is ToolStatus.OK:
            _record_success(name)
        return _result(status, str(resp.get("reply") or ""), data=resp, attempts=attempts)

    # 理论不可达
    _record_failure(name)
    return _result(ToolStatus.FAILED, f"工具 {name} 执行失败：{last_error}",
                   error=last_error, attempts=attempts)


def no_data(reply: str, **meta) -> dict:
    """工具侧构造“无数据”返回的辅助函数（NO_DATA ≠ 故障）"""
    return {"reply": reply, "meta": meta, "status": ToolStatus.NO_DATA.value}
