"""对话主循环（domains/conversation/loop.py）测试

用假 LLM（脚本化返回）+ 假工具替换真实依赖，验证循环的**控制流**与 7 种停止原因。
真 LLM 只用于人工回归，不放进 CI。
"""
import json

import pytest

from app.domains.conversation import loop as loop_mod
from app.domains.conversation.loop import ASK_USER, build_loop
from app.harness import loop as budget_mod
from app.harness.invoke import ToolResult, ToolStatus


class FakeLLM:
    """按脚本依次返回；剧本用完后返回空回答（模拟异常）"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def chat_with_tools(self, messages, tools=None, temperature=None):
        self.calls += 1
        if not self.script:
            return {"content": "", "tool_calls": []}
        return self.script.pop(0)

    def chat(self, messages, **kwargs):
        return "兜底回答"


def tool_call(name, args=None, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)}}


def install(monkeypatch, script, *, status=ToolStatus.OK, reply="假数据"):
    """注入假 LLM 与假工具执行器，返回 (llm, 调用记录)"""
    llm = FakeLLM(script)
    called: list[tuple[str, dict]] = []

    def fake_run_tool(name, args, on_stage=None):
        called.append((name, args or {}))
        return ToolResult(name=name, status=status, reply=reply,
                          data={"reply": reply}, elapsed_ms=1, attempts=1)

    monkeypatch.setattr(loop_mod, "get_llm", lambda: llm)
    monkeypatch.setattr(loop_mod, "run_tool", fake_run_tool)
    return llm, called


# ---------------------------------------------------------------- 正常路径
class TestHappyPath:
    def test_single_tool_then_answer(self, monkeypatch):
        llm, called = install(monkeypatch, [
            {"content": "", "tool_calls": [tool_call("query_financials", {"metric": "gross_margin"})]},
            {"content": "毛利率 91.87%", "tool_calls": []},
        ])
        state = build_loop().run({"query": "茅台毛利率", "history": []})

        assert called == [("query_financials", {"metric": "gross_margin"})]
        assert state["answer"] == "毛利率 91.87%"
        assert state["stop_reason"] == budget_mod.STOP_ANSWERED
        assert state["meta"]["tools_called"] == ["query_financials"]
        assert state["meta"]["steps"] == 2
        assert state.get("clarify") is False

    def test_two_step_task(self, monkeypatch):
        """先查公司再查指标 —— 旧 simple 路径（单轮硬编码）走不通的典型场景"""
        _, called = install(monkeypatch, [
            {"content": "", "tool_calls": [tool_call("query_company", {"company": "茅台"})]},
            {"content": "", "tool_calls": [tool_call("query_financials", {"company": "600519"})]},
            {"content": "营收 1000 亿", "tool_calls": []},
        ])
        state = build_loop().run({"query": "茅台营收", "history": []})

        assert [c[0] for c in called] == ["query_company", "query_financials"]
        assert state["meta"]["tools_called"] == ["query_company", "query_financials"]
        assert state["answer"] == "营收 1000 亿"

    def test_history_is_injected(self, monkeypatch):
        install(monkeypatch, [{"content": "答", "tool_calls": []}])
        state = build_loop().run({"query": "继续", "history": [
            {"role": "user", "content": "上一问"},
            {"role": "assistant", "content": "上一答"},
        ]})
        contents = [m.get("content") for m in state["messages"]]
        assert "上一问" in contents and "上一答" in contents
        assert any(m.get("role") == "system" for m in state["messages"])


# ---------------------------------------------------------------- 澄清
class TestClarify:
    def test_ask_user_is_terminal(self, monkeypatch):
        llm, called = install(monkeypatch, [
            {"content": "", "tool_calls": [tool_call(
                ASK_USER, {"question": "看哪个期间？", "options": ["2025Q1", "近三年"]})]},
        ])
        state = build_loop().run({"query": "茅台毛利率", "history": []})

        assert called == []                       # 澄清不经过工具执行
        assert state["clarify"] is True
        assert state["answer"] == "看哪个期间？"
        assert state["options"] == ["2025Q1", "近三年"]
        assert state["stop_reason"] == budget_mod.STOP_CLARIFY
        assert llm.calls == 1                     # 只调用一次 LLM

    def test_ask_user_without_options(self, monkeypatch):
        install(monkeypatch, [
            {"content": "", "tool_calls": [tool_call(ASK_USER, {"question": "哪家公司？"})]},
        ])
        state = build_loop().run({"query": "毛利率", "history": []})
        assert state["options"] == []

    def test_empty_question_falls_back(self, monkeypatch):
        install(monkeypatch, [{"content": "", "tool_calls": [tool_call(ASK_USER, {})]}])
        state = build_loop().run({"query": "毛利率", "history": []})
        assert state["answer"]                      # 不许空手


# ---------------------------------------------------------------- 预算
class TestBudget:
    def test_steps_exhausted(self, monkeypatch):
        script = [{"content": "", "tool_calls": [tool_call("query_financials", {"p": f"202{i}Q1"})]}
                  for i in range(10)]
        _, called = install(monkeypatch, script)
        state = build_loop().run({"query": "q", "history": [], "max_steps": 2})

        assert state["stop_reason"] == budget_mod.STOP_BUDGET_STEPS
        assert state["meta"]["steps"] == 2
        assert len(called) <= 2
        assert state["answer"]                     # 兜底不许空手

    def test_calls_exhausted(self, monkeypatch):
        script = [{"content": "", "tool_calls": [tool_call("query_financials", {"p": f"202{i}Q1"})]}
                  for i in range(10)]
        install(monkeypatch, script)
        state = build_loop().run({"query": "q", "history": [], "max_calls": 1})
        assert state["stop_reason"] == budget_mod.STOP_BUDGET_CALLS
        assert state["meta"]["tool_calls"] == 1

    def test_zero_budget_stops_before_llm(self, monkeypatch):
        llm, called = install(monkeypatch, [{"content": "不该被调用", "tool_calls": []}])
        state = build_loop().run({"query": "q", "history": [], "max_steps": 0})
        assert state["stop_reason"] == budget_mod.STOP_BUDGET_STEPS
        assert llm.calls == 0 and called == []


# ---------------------------------------------------------------- 无进展
class TestRepeatedCall:
    def test_same_tool_and_args_is_detected(self, monkeypatch):
        same = {"content": "", "tool_calls": [tool_call("query_financials", {"metric": "roe"})]}
        _, called = install(monkeypatch, [same, same, {"content": "不该走到这里", "tool_calls": []}])
        state = build_loop().run({"query": "q", "history": []})

        assert state["stop_reason"] == budget_mod.STOP_REPEATED
        assert len(called) == 2                      # 第二次执行完即终止
        assert state["answer"]

    def test_key_order_does_not_matter(self, monkeypatch):
        a = {"content": "", "tool_calls": [tool_call("query_financials", {"a": 1, "b": 2})]}
        b = {"content": "", "tool_calls": [tool_call("query_financials", {"b": 2, "a": 1})]}
        install(monkeypatch, [a, b])
        state = build_loop().run({"query": "q", "history": []})
        assert state["stop_reason"] == budget_mod.STOP_REPEATED


# ---------------------------------------------------------------- 异常与兜底
class TestFallback:
    def test_empty_llm_response_is_error(self, monkeypatch):
        install(monkeypatch, [{"content": "", "tool_calls": []}])
        state = build_loop().run({"query": "q", "history": []})
        assert state["stop_reason"] == budget_mod.STOP_ERROR
        assert state["answer"]

    def test_fallback_uses_observations(self, monkeypatch):
        """有观察时，兜底回答交给 LLM 收口（不是干巴巴报错）"""
        script = [{"content": "", "tool_calls": [tool_call("query_financials", {"p": "1"})]}
                  for _ in range(5)]
        install(monkeypatch, script)
        state = build_loop().run({"query": "q", "history": [], "max_steps": 1})
        assert state["stop_reason"] == budget_mod.STOP_BUDGET_STEPS
        assert state["meta"]["fallback"] is True
        assert state["answer"] == "兜底回答"

    def test_no_observation_still_answers(self, monkeypatch):
        install(monkeypatch, [])
        state = build_loop().run({"query": "q", "history": []})
        assert state["answer"] and state["stop_reason"] == budget_mod.STOP_ERROR


# ---------------------------------------------------------------- 流程结构
class TestToolSchemas:
    def test_ask_user_always_available(self):
        names = [s["function"]["name"] for s in loop_mod._tool_schemas()]
        assert ASK_USER in names

    def test_late_registered_tools_are_visible(self, monkeypatch):
        """register_tool 是重新绑定 TOOLS_SCHEMAS，import 时把列表绑死会拿不到新工具"""
        from app.harness import registry

        fake = {"type": "function",
                "function": {"name": "late_tool", "description": "x", "parameters": {}}}
        monkeypatch.setattr(registry, "TOOLS_SCHEMAS", [fake], raising=False)
        names = [s["function"]["name"] for s in loop_mod._tool_schemas()]
        assert "late_tool" in names and ASK_USER in names


class TestFlowShape:
    def test_nodes_and_entry(self):
        f = build_loop()
        assert f.node_names == ["assemble", "decide", "act", "answer"]
        assert f.describe()["entry"] == "assemble"

    def test_budget_roundtrip_through_state(self):
        b = budget_mod.LoopBudget(max_steps=3, max_calls=5)
        b.spend_step()
        b.spend_call()
        state = b.to_state()
        assert budget_mod.LoopBudget.from_state(state).steps == 1
        assert budget_mod.LoopBudget.from_state(state).calls == 1
