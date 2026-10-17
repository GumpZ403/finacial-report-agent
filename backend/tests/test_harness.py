"""harness 层行为测试：工具执行层（参数校验 / 结构化错误 / 重试 / 熔断 / NO_DATA）

这些用例覆盖的是本次修复的核心：工具失败不再被吞成一句“执行失败”，
而是带状态回到 LLM，让它能区分“没数据”和“系统坏了”。
"""
import pytest

from app.harness import registry
from app.harness.invoke import (
    DEFAULT_RETRIES,
    FAILURE_THRESHOLD,
    ToolStatus,
    reset_circuit,
    run_tool,
)


ECHO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "t_echo",
        "description": "测试用回显工具",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "mode": {"type": "string", "enum": ["a", "b"]},
            },
            "required": ["text"],
        },
    },
}


def _drop_tool(name: str) -> None:
    registry.TOOLS_MAP.pop(name, None)
    registry.TOOLS_SCHEMAS[:] = [
        s for s in registry.TOOLS_SCHEMAS if s.get("function", {}).get("name") != name
    ]


@pytest.fixture(autouse=True)
def _clean_state():
    reset_circuit()
    yield
    reset_circuit()


@pytest.fixture
def echo_calls():
    """注册一个记录调用次数的回显工具，结束后清理"""
    calls = []

    def handler(args, on_stage=None):
        calls.append(args)
        return {"reply": args["text"], "meta": {"echo": True}}

    registry.register_tool("t_echo", ECHO_SCHEMA, handler)
    yield calls
    _drop_tool("t_echo")


@pytest.fixture
def broken_tool():
    """注册一个必然抛异常的工具"""
    calls = []

    def handler(args, on_stage=None):
        calls.append(args)
        raise RuntimeError("数据库连接断了")

    registry.register_tool("t_broken", None, handler)
    yield calls
    _drop_tool("t_broken")


class TestValidation:
    """参数校验前置"""

    def test_ok(self, echo_calls):
        r = run_tool("t_echo", {"text": "你好"})
        assert r.status is ToolStatus.OK
        assert r.reply == "你好"
        assert r.ok and r.attempts == 1
        assert r.meta == {"echo": True}

    def test_missing_required(self, echo_calls):
        r = run_tool("t_echo", {})
        assert r.status is ToolStatus.INVALID_ARGS
        assert "text" in r.reply
        assert echo_calls == []          # 校验不通过时不该真的调用工具

    def test_bad_enum(self, echo_calls):
        r = run_tool("t_echo", {"text": "x", "mode": "z"})
        assert r.status is ToolStatus.INVALID_ARGS
        assert "mode" in r.reply
        assert echo_calls == []

    def test_bad_type(self, echo_calls):
        r = run_tool("t_echo", {"text": 123})
        assert r.status is ToolStatus.INVALID_ARGS

    def test_integer_accepts_whole_float(self):
        from app.harness.invoke import validate_args
        schema = {"function": {"parameters": {
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
        }}}
        assert validate_args(schema, {"n": 8}) == []
        assert validate_args(schema, {"n": 8.0}) == []
        assert validate_args(schema, {"n": True}) != []
        assert validate_args(schema, {"n": "8"}) != []


class TestFailure:
    """未注册 / 系统故障 / 重试 / 熔断"""

    def test_unregistered(self):
        r = run_tool("t_not_exist", {})
        assert r.status is ToolStatus.UNREGISTERED
        assert "未注册" in r.reply

    def test_failed_with_retry(self, broken_tool):
        r = run_tool("t_broken", {})
        assert r.status is ToolStatus.FAILED
        assert r.attempts == DEFAULT_RETRIES + 1
        assert len(broken_tool) == DEFAULT_RETRIES + 1
        assert "数据库连接断了" in (r.error or "")

    def test_circuit_opens_after_threshold(self, broken_tool):
        for _ in range(FAILURE_THRESHOLD):
            assert run_tool("t_broken", {}).status is ToolStatus.FAILED
        calls_before = len(broken_tool)
        r = run_tool("t_broken", {})
        assert r.status is ToolStatus.CIRCUIT_OPEN
        assert len(broken_tool) == calls_before   # 熔断后不再真正调用

    def test_non_dict_return(self):
        registry.register_tool("t_bad_return", None, lambda args, on_stage=None: "不是 dict")
        try:
            assert run_tool("t_bad_return", {}).status is ToolStatus.FAILED
        finally:
            _drop_tool("t_bad_return")

    def test_failure_content_has_status_header(self, broken_tool):
        r = run_tool("t_broken", {})
        content = r.to_llm_content()
        assert "failed" in content and "t_broken" in content


class TestNoData:
    """NO_DATA 与故障区分开"""

    def test_explicit_no_data(self):
        def handler(args, on_stage=None):
            return {"reply": "没有这家公司", "meta": {}, "status": "no_data"}

        registry.register_tool("t_nodata", None, handler)
        try:
            r = run_tool("t_nodata", {})
            assert r.status is ToolStatus.NO_DATA
            content = r.to_llm_content()
            assert "没有这家公司" in content
            assert "不要用相同参数重复调用" in content
        finally:
            _drop_tool("t_nodata")

    def test_empty_meta_falls_back_to_no_data(self):
        def handler(args, on_stage=None):
            return {"reply": "没有", "meta": {}}

        registry.register_tool("t_empty", None, handler)
        try:
            assert run_tool("t_empty", {}).status is ToolStatus.NO_DATA
        finally:
            _drop_tool("t_empty")

    def test_real_tool_reports_no_data(self):
        """真实工具（query_company）查不存在的公司应返回 NO_DATA 而不是故障"""
        import app.domains.financial.tools  # noqa: F401  触发注册

        r = run_tool("query_company", {"company": "这家公司不存在_12345"})
        assert r.status is ToolStatus.NO_DATA
        assert "未找到公司" in r.reply
