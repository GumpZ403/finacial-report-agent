"""会话记忆落盘测试：sessions / conversations 两张表的读写契约

不碰真实 storage/conversations.db —— 每个用例用 tmp_path 下的独立库文件。
"""
import json
import sqlite3

import pytest

from app.harness.store import SCHEMA_VERSION, ConversationStore


@pytest.fixture
def store(tmp_path):
    s = ConversationStore(tmp_path / "conversations.db")
    s.ensure_schema()
    return s


def _rows(store, sql, params=()):
    con = sqlite3.connect(store.db_path)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


class TestSchema:
    def test_two_tables_created(self, store):
        names = {
            r[0]
            for r in _rows(store, "SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"sessions", "conversations"} <= names

    def test_ensure_schema_is_idempotent(self, store):
        store.ensure_schema()  # 第二次调用不应报错，也不应丢数据
        store.append_turn("s1", "q", "a")
        store.ensure_schema()
        assert store.get_session("s1")["turn_count"] == 2
        assert _rows(store, "PRAGMA user_version")[0][0] == SCHEMA_VERSION

    def test_unique_index_on_session_turn(self, store):
        sql = (
            "INSERT INTO conversations (session_id, turn_index, role, content, created_at)"
            " VALUES ('s1', 0, 'user', 'x', 'now')"
        )
        con = sqlite3.connect(store.db_path)
        try:
            con.execute(sql)
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(sql)
        finally:
            con.close()

    def test_role_check_constraint(self, store):
        con = sqlite3.connect(store.db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO conversations (session_id, turn_index, role, content,"
                    " created_at) VALUES ('s1', 0, 'system', 'x', 'now')"
                )
        finally:
            con.close()


class TestAppendTurn:
    def test_first_turn_creates_session(self, store):
        n = store.append_turn("s1", "贵州茅台的毛利率？", "约 91%")
        assert n == 2
        rec = store.get_session("s1")
        assert rec["turn_count"] == 2
        assert rec["title"] == "贵州茅台的毛利率？"
        assert rec["summary"] == ""
        assert rec["summary_upto"] == 0

    def test_turn_index_is_sequential(self, store):
        store.append_turn("s1", "q1", "a1")
        store.append_turn("s1", "q2", "a2")
        rows = _rows(
            store,
            "SELECT turn_index, role FROM conversations WHERE session_id='s1'"
            " ORDER BY turn_index",
        )
        assert [r[0] for r in rows] == [0, 1, 2, 3]
        assert [r[1] for r in rows] == ["user", "assistant"] * 2
        assert store.get_session("s1")["turn_count"] == 4

    def test_title_kept_from_first_turn(self, store):
        store.append_turn("s1", "第一问", "a")
        store.append_turn("s1", "第二问", "a")
        assert store.get_session("s1")["title"] == "第一问"

    def test_long_title_truncated(self, store):
        store.append_turn("s1", "很" * 40, "a")
        title = store.get_session("s1")["title"]
        assert len(title) == 25 and title.endswith("…")

    def test_replay_same_turn_is_idempotent(self, store):
        store.append_turn("s1", "q", "a")
        # 直接撞唯一键（模拟同一条消息被重发）
        con = sqlite3.connect(store.db_path)
        try:
            con.execute(
                "INSERT OR IGNORE INTO conversations (session_id, turn_index, role,"
                " content, created_at) VALUES ('s1', 0, 'user', 'dup', 'now')"
            )
            con.commit()
        finally:
            con.close()
        assert [r["content"] for r in store.load_context("s1")] == ["q", "a"]

    def test_meta_json_roundtrip(self, store):
        meta = {"intent": "metric_query", "task_type": "simple", "tool_status": ["no_data"]}
        store.append_turn("s1", "q", "a", meta=meta)
        raw = _rows(store, "SELECT meta_json FROM conversations WHERE turn_index=0")[0][0]
        assert json.loads(raw) == meta

    def test_sessions_are_isolated(self, store):
        store.append_turn("s1", "A 的问题", "a")
        store.append_turn("s2", "B 的问题", "a")
        assert [r["content"] for r in store.load_context("s1")] == ["A 的问题", "a"]
        assert [r["content"] for r in store.load_context("s2")] == ["B 的问题", "a"]
        assert store.get_session("s1")["title"] == "A 的问题"

    def test_default_session_when_blank(self, store):
        store.append_turn("   ", "q", "a")
        assert store.get_session("default")["turn_count"] == 2


class TestRead:
    def test_load_context_returns_chronological_order(self, store):
        for i in range(3):
            store.append_turn("s1", f"q{i}", f"a{i}")
        assert [r["content"] for r in store.load_context("s1")] == [
            "q0", "a0", "q1", "a1", "q2", "a2",
        ]

    def test_load_context_limit_keeps_latest(self, store):
        for i in range(3):
            store.append_turn("s1", f"q{i}", f"a{i}")
        assert [r["content"] for r in store.load_context("s1", limit=2)] == ["q2", "a2"]

    def test_load_context_unknown_session(self, store):
        assert store.load_context("nobody") == []
        assert store.get_session("nobody") is None

    def test_set_summary_and_cursor(self, store):
        store.append_turn("s1", "q", "a")
        store.set_summary("s1", "用户问过 q", 2)
        rec = store.get_session("s1")
        assert rec["summary"] == "用户问过 q"
        assert rec["summary_upto"] == 2

    def test_delete_session_removes_everything(self, store):
        store.append_turn("s1", "q", "a")
        store.append_turn("s1", "q2", "a2")
        assert store.delete_session("s1") == 4
        assert store.get_session("s1") is None
        assert store.load_context("s1") == []

    def test_delete_unknown_session_is_noop(self, store):
        assert store.delete_session("nobody") == 0

    def test_list_sessions(self, store):
        store.append_turn("s1", "q1", "a1")
        store.append_turn("s2", "q2", "a2")
        listed = {s["session_id"]: s for s in store.list_sessions()}
        assert set(listed) == {"s1", "s2"}
        assert listed["s1"]["title"] == "q1"
        assert listed["s1"]["turn_count"] == 2

    def test_stats(self, store):
        store.append_turn("s1", "q1", "a1")
        store.append_turn("s2", "q2", "a2")
        assert store.stats()["sessions"] == 2
        assert store.stats()["messages"] == 4
