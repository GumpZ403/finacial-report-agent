"""会话记忆落盘（SQLite，独立库文件 storage/conversations.db）

为什么单独一个库：见 `Settings.conversations_db_path` 的注释（权威数据 vs 运行状态）。

两张表
------
sessions        会话元信息：标题、轮次计数、摘要与摘要游标
conversations   逐轮消息：turn_index 单调递增，UNIQUE(session_id, turn_index) 做幂等护栏

字段来源（谁产生什么）
----------------------
session_id     HTTP 请求体（前端 useChat.js 的 s.id），空则 DEFAULT_SESSION_ID
title          首条用户问题截断（服务端权威，前端只读）
turn_count     服务端自增，每轮 +2（user + assistant 两行）
summary        早期轮次的 LLM 摘要；summary_upto 是"摘要已覆盖到第几轮"的游标
               ——没有游标就只能每轮重摘要全部历史，token 随轮数平方增长
content        user 行 = req.query；assistant 行 = 流程最终 answer
meta_json      intent / task_type / tool_name / confidence / tool_status（来自流程 state）
created_at     服务端 UTC 时间

阶段说明（当前进度）
--------------------
**写已落地，读仍走进程内 Memory**（`harness/memory.py` 的 SessionStore）。
即：每次问答都会写进本库，但 `get_context()` 还没切换到这里——
重启后历史不会自动回到上下文里。下一步把读路径切到
`load_context()` + `get_session().summary`，届时 SessionStore 退化为纯缓存或删除。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from app.platform.settings import get_settings

# schema 版本记在 PRAGMA user_version，后续加字段时按版本号写迁移
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    title        TEXT,
    turn_count   INTEGER NOT NULL DEFAULT 0,
    summary      TEXT NOT NULL DEFAULT '',
    summary_upto INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    meta_json  TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (session_id, turn_index)
);

CREATE INDEX IF NOT EXISTS idx_conv_session
    ON conversations (session_id, turn_index);
"""

_TITLE_MAX = 24
_DEFAULT_CONTEXT_TURNS = 16


def _now() -> str:
    """统一 UTC 存储，展示时再转本地时区"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _make_title(query: str) -> str:
    q = " ".join((query or "").split())
    return q if len(q) <= _TITLE_MAX else q[:_TITLE_MAX] + "…"


class ConversationStore:
    """会话记忆读写（每次操作一个短连接，WAL 模式，够单用户场景用）"""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or get_settings().conversations_db_path)

    # ------------------------------------------------------------------ 基础
    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None：关掉隐式事务，由调用方显式 BEGIN，避免
        # "cannot start a transaction within a transaction"
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        """建表（幂等）。启动时调用一次，失败只记日志、不阻止服务启动。"""
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            logger.info(
                f"[ConversationStore] schema 就绪 v{max(version, SCHEMA_VERSION)}"
                f" · {self.db_path}"
            )
        finally:
            conn.close()

    # ------------------------------------------------------------------ 写
    def append_turn(
        self,
        session_id: str,
        query: str,
        reply: str,
        meta: dict | None = None,
    ) -> int:
        """追加一问一答两行，返回写入后的会话总轮次。

        - turn_index 由服务端分配（绝不接受前端传入），避免并发撞唯一键；
        - INSERT OR IGNORE：重放同一轮次不会产生重复行；
        - 整个追加在一个 BEGIN IMMEDIATE 事务里，保证 index 分配与写入原子。
        """
        sid = (session_id or "").strip() or "default"
        meta_json = json.dumps(meta or {}, ensure_ascii=False) if meta else None
        now = _now()

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT turn_count, title FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()
            start = int(row["turn_count"]) if row else 0
            title = (row["title"] if row else None) or _make_title(query)

            conn.execute(
                """
                INSERT INTO sessions (session_id, title, turn_count, created_at, updated_at)
                VALUES (?, ?, 2, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    turn_count = sessions.turn_count + 2,
                    title      = COALESCE(sessions.title, excluded.title),
                    updated_at = excluded.updated_at
                """,
                (sid, title, now, now),
            )
            conn.executemany(
                """
                INSERT OR IGNORE INTO conversations
                    (session_id, turn_index, role, content, meta_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    # meta 只挂在 assistant 行：user 行的内容就是用户原话，
                    # 再存一份同样的 JSON 是纯冗余（既浪费存储也容易读错）
                    (sid, start, "user", query, None, now),
                    (sid, start + 1, "assistant", reply, meta_json, now),
                ],
            )
            conn.execute("COMMIT")
            return start + 2
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def set_summary(self, session_id: str, summary: str, upto: int) -> None:
        """写回摘要与游标（读路径懒生成摘要时调用，不要在写 hook 里做）"""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE sessions SET summary = ?, summary_upto = ?, updated_at = ? "
                "WHERE session_id = ?",
                (summary, int(upto), _now(), session_id),
            )
        finally:
            conn.close()

    def delete_session(self, session_id: str) -> int:
        """/clear 用：删除会话及其全部消息，返回删掉的消息行数"""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
            deleted = cur.rowcount or 0
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.execute("COMMIT")
            return deleted
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ 读
    def load_context(self, session_id: str, limit: int = _DEFAULT_CONTEXT_TURNS) -> list[dict]:
        """最近 N 条消息（按时间正序返回），供组装 LLM 上下文用"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT role, content FROM conversations WHERE session_id = ? "
                "ORDER BY turn_index DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        finally:
            conn.close()

    def load_turns(self, session_id: str, limit: int = 20) -> list[dict]:
        """最近 N 条消息（正序，含 meta_json 解析结果）—— /trace 与评测用"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT turn_index, role, content, meta_json, created_at "
                "FROM conversations WHERE session_id = ? "
                "ORDER BY turn_index DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        finally:
            conn.close()
        out = []
        for r in reversed(rows):
            out.append({
                "turn_index": r["turn_index"],
                "role": r["role"],
                "content": r["content"],
                "meta": json.loads(r["meta_json"]) if r["meta_json"] else None,
                "created_at": r["created_at"],
            })
        return out

    def get_session(self, session_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_sessions(self, limit: int = 50) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT session_id, title, turn_count, updated_at FROM sessions "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def stats(self) -> dict:
        """概览（启动日志 / 调试用）"""
        conn = self._connect()
        try:
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            messages = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            return {"sessions": sessions, "messages": messages, "db": str(self.db_path)}
        finally:
            conn.close()


_store: ConversationStore | None = None


def get_conversation_store() -> ConversationStore:
    """全局单例；首次获取即建表（失败只记日志，不阻止服务启动）"""
    global _store
    if _store is None:
        _store = ConversationStore()
        try:
            _store.ensure_schema()
        except Exception:
            logger.exception("[ConversationStore] 建表失败：会话落盘将不可用（不影响问答）")
    return _store
