"""流程执行器（harness/flow.py）单元测试

覆盖：线性路径、状态增量合并、路由器、子流程冒泡、步数上限、扇出（含单分支失败）、
checkpoint 钩子、非法图配置。这里不涉及 LLM，纯结构验证。
"""
import pytest

from app.harness.flow import END, Flow, FlowError


def _collect(events: list):
    def cb(ev):
        events.append(ev)

    return cb


# --------------------------------------------------------------------- 基础
def test_linear_flow_merges_deltas():
    def a(state):
        return {"a": 1}

    def b(state):
        assert state["a"] == 1, "前一个节点的产应对后继节点可见"
        return {"b": state["a"] + 1}

    f = Flow("t")
    f.add_node("a", a).add_node("b", b).set_entry("a").add_edge("a", "b")
    out = f.run({"seed": 0})
    assert out == {"seed": 0, "a": 1, "b": 2}


def test_node_returning_none_changes_nothing():
    f = Flow("t")
    f.add_node("a", lambda s: None).set_entry("a")
    assert f.run({"x": 1}) == {"x": 1}


def test_router_with_mapping():
    def start(state):
        return {}

    def hi(state):
        return {"answer": "hi"}

    def bye(state):
        return {"answer": "bye"}

    f = (Flow("t").add_node("start", start).add_node("hi", hi).add_node("bye", bye)
         .set_entry("start")
         .add_router("start", lambda s: s["mode"], {"a": "hi", "b": "bye"}))
    assert f.run({"mode": "a"})["answer"] == "hi"
    assert f.run({"mode": "b"})["answer"] == "bye"


def test_router_can_end_flow():
    f = (Flow("t").add_node("n", lambda s: {"v": 1}).set_entry("n")
         .add_router("n", lambda s: END))
    out = f.run({})
    assert out["v"] == 1


# --------------------------------------------------------------------- 循环与保护
def test_loop_runs_until_router_exits():
    def count(state):
        return {"n": (state.get("n") or 0) + 1}

    f = (Flow("t").add_node("count", count).set_entry("count")
         .add_router("count", lambda s: "count" if s["n"] < 3 else END))
    assert f.run({})["n"] == 3


def test_max_steps_guard():
    f = (Flow("t").add_node("spin", lambda s: {}).set_entry("spin")
         .add_router("spin", lambda s: "spin"))
    with pytest.raises(FlowError, match="最大步数"):
        f.run({}, max_steps=5)


# --------------------------------------------------------------------- 事件
def test_events_sequence():
    events: list = []
    f = (Flow("t").add_node("a", lambda s: {}).add_node("b", lambda s: {})
         .set_entry("a").add_edge("a", "b"))
    f.run({}, on_event=_collect(events))

    kinds = [e.kind for e in events]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    starts = [e.node for e in events if e.kind == "node_start"]
    assert starts == ["a", "b"]
    # node_end 带后继节点名，前端可据此显示"下一步做什么"
    b_end = [e for e in events if e.kind == "node_end" and e.node == "b"][0]
    assert b_end.next == END


def test_subflow_events_bubble_up():
    events: list = []
    inner = Flow("inner").add_node("inner_node", lambda s: {"inner": True}).set_entry("inner_node")
    outer = Flow("outer").add_node("sub", inner).set_entry("sub")

    out = outer.run({}, on_event=_collect(events))

    assert out["inner"] is True, "子流程的产出必须合并回父流程状态"
    inner_events = [e for e in events if e.flow == "inner" and e.kind == "node_start"]
    assert [e.node for e in inner_events] == ["inner_node"], "子流程事件应冒泡到父流程回调"


def test_checkpoint_called_per_node():
    seen: list[tuple[str, dict]] = []
    f = (Flow("t").add_node("a", lambda s: {"a": 1}).add_node("b", lambda s: {"b": 2})
         .set_entry("a").add_edge("a", "b"))
    f.run({}, checkpoint=lambda node, st: seen.append((node, st)))
    assert [n for n, _ in seen] == ["a", "b"]
    assert seen[-1][1] == {"a": 1, "b": 2}


def test_checkpoint_failure_does_not_break_flow():
    def boom(node, state):
        raise RuntimeError("落盘失败")

    f = Flow("t").add_node("a", lambda s: {"ok": True}).set_entry("a")
    assert f.run({}, checkpoint=boom)["ok"] is True


def test_event_callback_failure_does_not_break_flow():
    def bad_cb(ev):
        raise RuntimeError("SSE 断了")

    f = Flow("t").add_node("a", lambda s: {"ok": True}).set_entry("a")
    assert f.run({}, on_event=bad_cb)["ok"] is True


# --------------------------------------------------------------------- 扇出
def test_fanout_runs_branches_and_joins():
    def split(state):
        return [{"key": "x", "state": {"v": 1}},
                {"key": "y", "state": {"v": 2}},
                {"key": "z", "state": {"v": 3}}]

    def work(state):
        return {"out": state["v"] * 10}

    def join(state, results):
        assert all(r["status"] == "ok" for r in results)
        return {"total": sum(r["delta"]["out"] for r in results),
                "keys": [r["key"] for r in results]}

    f = Flow("t").add_fanout("fan", work, split, join).set_entry("fan")
    out = f.run({})
    assert out["total"] == 60
    assert out["keys"] == ["x", "y", "z"], "结果应按 key 排序，保证可复现"


def test_fanout_single_branch_failure_is_isolated():
    def split(state):
        return [{"key": "good"}, {"key": "bad"}]

    def work(state):
        if state["_branch"] == "bad":
            raise RuntimeError("该章节数据缺失")
        return {"text": "ok"}

    def join(state, results):
        by_key = {r["key"]: r for r in results}
        return {
            "drafts": [r["delta"] for r in results if r["status"] == "ok"],
            "failures": [r["error"] for r in results if r["status"] == "failed"],
            "bad_status": by_key["bad"]["status"],
        }

    events: list = []
    f = Flow("t").add_fanout("fan", work, split, join).set_entry("fan")
    out = f.run({}, on_event=_collect(events))

    assert out["drafts"] == [{"text": "ok"}]
    assert "数据缺失" in out["failures"][0]
    assert out["bad_status"] == "failed"
    assert any(e.kind == "branch" and e.error for e in events), "失败分支要发事件（前端可提示）"


def test_fanout_with_empty_split_is_noop():
    f = Flow("t").add_fanout(
        "fan", lambda s: {}, lambda s: [], lambda s, rs: {"n": len(rs)}
    ).set_entry("fan")
    assert f.run({})["n"] == 0


# --------------------------------------------------------------------- 非法配置
def test_unknown_entry_rejected():
    with pytest.raises(FlowError, match="入口节点未注册"):
        Flow("t").set_entry("nope")


def test_duplicate_node_rejected():
    f = Flow("t").add_node("a", lambda s: {})
    with pytest.raises(FlowError, match="节点重复注册"):
        f.add_node("a", lambda s: {})


def test_edge_to_unknown_node_rejected():
    f = Flow("t").add_node("a", lambda s: {}).set_entry("a").add_edge("a", "ghost")
    with pytest.raises(FlowError, match="未注册的节点"):
        f.run({})


def test_run_without_entry_rejected():
    with pytest.raises(FlowError, match="未设置入口节点"):
        Flow("t").run({})
