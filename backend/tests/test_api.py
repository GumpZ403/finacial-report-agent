"""HTTP 接口测试：健康检查、SSE 流式写记忆、会话隔离、CORS

用 FakeFlow 替换真实编排流程（不花 LLM token），只验证接口层契约。
"""
import pytest
from fastapi.testclient import TestClient

from app.harness.flow import FlowEvent
from app.harness.memory import get_session_store
from app.harness.store import ConversationStore, get_conversation_store
from app.interfaces.http.routes import agent as routes_agent
from app.main import app


class FakeFlow:
    """记录入参、可按需发事件、返回固定答案的假流程

    真流程是 assemble → decide → act → answer 的有界循环；这里只模拟出两个
    阶段事件（decide / answer），用来验证接口层的 SSE 透传与记忆写入。
    """

    def __init__(self):
        self.calls: list[dict] = []

    def run(self, payload, on_event=None, checkpoint=None):
        self.calls.append(payload)
        if on_event is not None:
            on_event(FlowEvent(kind="node_start", flow="conversation", node="decide"))
            on_event(FlowEvent(kind="node_end", flow="conversation",
                               node="decide", next="answer"))
            on_event(FlowEvent(kind="node_start", flow="conversation", node="answer"))
        return {
            "answer": "假答案",
            "clarify": False,
            "options": [],
            "meta": {"tools_called": [], "steps": 1,
                     "stop_reason": "answered", "clarify": False},
        }


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_graph(monkeypatch):
    g = FakeFlow()
    monkeypatch.setattr(routes_agent, "_graph", g)
    return g


@pytest.fixture(autouse=True)
def _clean_sessions():
    store = get_session_store()
    for sid in ("t-stream", "t-chat", "t-a", "t-b"):
        store.drop(sid)
    yield


@pytest.fixture(autouse=True)
def _isolated_conversation_db(tmp_path, monkeypatch):
    """落盘测试用临时库，绝不碰真实 storage/conversations.db"""
    from app.harness import store as store_mod

    s = ConversationStore(tmp_path / "conversations.db")
    s.ensure_schema()
    monkeypatch.setattr(store_mod, "_store", s)
    return s


class TestBasics:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_api_index(self, client):
        r = client.get("/api")
        assert r.status_code == 200
        assert "endpoints" in r.json()

    def test_root_serves_frontend_or_hint(self, client):
        """/ 要么返回前端 index.html，要么 503 + 构建提示（两者都算健康）"""
        r = client.get("/")
        assert r.status_code in (200, 503)
        if r.status_code == 503:
            assert r.json()["hint"].startswith("cd frontend")


class TestSessionMemory:
    """本次修复重点：流式入口也要写记忆，且按 session_id 隔离"""

    def test_stream_writes_memory(self, client, fake_graph):
        r = client.post("/api/agent/chat/stream", json={"query": "你好", "session_id": "t-stream"})
        assert r.status_code == 200
        assert "假答案" in r.text

        mem = get_session_store().get("t-stream")
        assert [m["role"] for m in mem.short_term] == ["user", "assistant"]
        assert mem.short_term[-1]["content"] == "假答案"

    def test_chat_writes_memory(self, client, fake_graph):
        r = client.post("/api/agent/chat", json={"query": "在吗", "session_id": "t-chat"})
        assert r.status_code == 200
        assert r.json()["reply"] == "假答案"
        assert len(get_session_store().get("t-chat").short_term) == 2

    def test_second_turn_carries_history(self, client, fake_graph):
        client.post("/api/agent/chat", json={"query": "第一问", "session_id": "t-chat"})
        client.post("/api/agent/chat", json={"query": "第二问", "session_id": "t-chat"})
        history = fake_graph.calls[-1]["history"]
        assert [m["content"] for m in history] == ["第一问", "假答案"]

    def test_sessions_are_isolated(self, client, fake_graph):
        client.post("/api/agent/chat", json={"query": "会话A的问题", "session_id": "t-a"})
        client.post("/api/agent/chat", json={"query": "会话B的问题", "session_id": "t-b"})
        # B 的历史里不能出现 A 的内容
        assert fake_graph.calls[-1]["history"] == []
        assert get_session_store().get("t-a").short_term[0]["content"] == "会话A的问题"
        assert get_session_store().get("t-b").short_term[0]["content"] == "会话B的问题"

    def test_clear_drops_session(self, client, fake_graph):
        client.post("/api/agent/chat", json={"query": "清空前", "session_id": "t-a"})
        r = client.post("/api/agent/clear?session_id=t-a")
        assert r.status_code == 200
        assert "t-a" not in get_session_store().active_sessions()

    def test_memory_endpoint(self, client, fake_graph):
        client.post("/api/agent/chat", json={"query": "看下记忆", "session_id": "t-a"})
        d = client.get("/api/agent/memory?session_id=t-a").json()
        assert d["session_id"] == "t-a"
        assert d["short_term_len"] == 2


class TestPersistence:
    """会话记忆已落盘：两个入口都写库，清空也清库

    注：读路径当前仍走进程内 Memory（见 harness/store.py 顶部"阶段说明"），
    所以这里只断言"写进去、能查出来"，不断言"重启后自动回到上下文"。
    """

    def test_stream_persists_turn(self, client, fake_graph):
        client.post("/api/agent/chat/stream",
                    json={"query": "落盘测试", "session_id": "t-stream"})
        store = get_conversation_store()
        rec = store.get_session("t-stream")
        assert rec["turn_count"] == 2
        assert rec["title"] == "落盘测试"
        assert [m["content"] for m in store.load_context("t-stream")] == ["落盘测试", "假答案"]

    def test_chat_persists_meta(self, client, fake_graph):
        import json
        import sqlite3

        client.post("/api/agent/chat", json={"query": "看下 meta", "session_id": "t-chat"})
        con = sqlite3.connect(get_conversation_store().db_path)
        try:
            raw = con.execute(
                "SELECT meta_json FROM conversations"
                " WHERE session_id='t-chat' AND turn_index=1"   # meta 只写 assistant 行
            ).fetchone()[0]
        finally:
            con.close()
        assert json.loads(raw)["stop_reason"] == "answered"

    def test_clear_removes_disk_rows(self, client, fake_graph):
        client.post("/api/agent/chat", json={"query": "清盘前", "session_id": "t-a"})
        r = client.post("/api/agent/clear?session_id=t-a")
        assert r.json()["deleted_messages"] == 2
        assert get_conversation_store().get_session("t-a") is None

    def test_memory_endpoint_reports_disk(self, client, fake_graph):
        client.post("/api/agent/chat", json={"query": "看下记忆", "session_id": "t-a"})
        disk = client.get("/api/agent/memory?session_id=t-a").json()["disk"]
        assert disk["turn_count"] == 2
        assert disk["title"] == "看下记忆"

    def test_disk_failure_does_not_break_answer(self, client, fake_graph, monkeypatch):
        """落盘失败必须被隔离：回答照常返回，进程内记忆照常写入"""
        def boom(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(ConversationStore, "append_turn", boom)
        r = client.post("/api/agent/chat", json={"query": "磁盘挂了", "session_id": "t-b"})
        assert r.status_code == 200
        assert r.json()["reply"] == "假答案"
        assert len(get_session_store().get("t-b").short_term) == 2


class TestStreaming:
    """SSE 逐节点阶段事件（此前每步重复同一个 task_type，等于没有信息量）"""

    def test_stage_events_are_per_node(self, client, fake_graph):
        r = client.post("/api/agent/chat/stream", json={"query": "你好", "session_id": "t-stream"})
        assert r.status_code == 200
        assert '"type": "stage"' in r.text
        assert "思考中" in r.text
        assert "整理回答" in r.text
        # 阶段事件必须按执行顺序推送（否则前端进度条会跳回）
        assert r.text.index("思考中") < r.text.index("整理回答")

    def test_flow_structure_endpoint(self, client):
        d = client.get("/api/agent/flow").json()
        assert d["entry"] == "assemble"
        assert set(d["nodes"]) == {"assemble", "decide", "act", "answer"}


class TestCORS:
    def test_wildcard_without_credentials(self, client):
        """allow_origins=['*'] 与 allow_credentials=True 互斥，浏览器会拒绝；
        本服务无 Cookie 鉴权，应为通配 + 关闭凭据"""
        r = client.get("/health", headers={"Origin": "http://127.0.0.1:23617"})
        assert r.headers.get("access-control-allow-origin") == "*"
        assert "access-control-allow-credentials" not in r.headers
