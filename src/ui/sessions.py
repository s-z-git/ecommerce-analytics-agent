"""电商页面的会话存储：SQLite 保存会话与查询结果，用于多轮回放。

只做展示层持久化：不改变 pipeline 的单轮语义，也不把历史结果当作新查询的上下文。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "ui_sessions.db"


class SessionStore:
    """很薄的 SQLite 会话存储；每次操作开连接，避免跨线程共享连接。"""

    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    meta_json TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_updated
                ON sessions(updated_at DESC)
            """)
            conn.execute("""
                DELETE FROM sessions
                WHERE id NOT IN (SELECT DISTINCT session_id FROM messages)
            """)

    @staticmethod
    def format_title(question: str, created_at: str | None = None) -> str:
        stamp = ""
        if created_at:
            try:
                stamp = datetime.fromisoformat(created_at).strftime("%m-%d %H:%M")
            except ValueError:
                stamp = created_at[5:16].replace("T", " ")
        if not stamp:
            stamp = datetime.now().strftime("%m-%d %H:%M")
        text = re.sub(r"\s+", " ", (question or "")).strip()
        if not text:
            return stamp + " · 新会话"
        return stamp + " · " + text[:12] + ("…" if len(text) > 12 else "")

    def ensure_session(self, session_id: str, question: str = "") -> None:
        if not session_id:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, self.format_title(question), now, now),
            )

    def session_exists(self, session_id: str) -> bool:
        if not session_id:
            return False
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone() is not None

    def list_sessions(self, limit: int = 12) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT s.id, s.title, s.created_at, s.updated_at,
                       COALESCE(SUM(CASE WHEN m.role = 'user' THEN 1 ELSE 0 END), 0) AS turns
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY s.updated_at DESC, s.created_at DESC
                LIMIT ?
            """, (max(1, limit),)).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> None:
        if not session_id:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def add_turn(self, session_id: str, role: str, content: str,
                 meta: dict | None = None) -> None:
        text = (content or "").strip()
        if not session_id or not text:
            return
        now = datetime.now().isoformat(timespec="seconds")
        payload = json.dumps(meta, ensure_ascii=False, default=str) if meta else None
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, created_at, meta_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, text[:4000], now, payload),
            )
            conn.execute(
                "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, self.format_title(text if role == "user" else ""), now, now),
            )
            if role == "user":
                turns = conn.execute(
                    "SELECT COUNT(*) AS n FROM messages "
                    "WHERE session_id = ? AND role = 'user'",
                    (session_id,),
                ).fetchone()["n"]
                if turns == 1:
                    conn.execute(
                        "UPDATE sessions SET title = ? WHERE id = ?",
                        (self.format_title(text, now), session_id),
                    )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )

    def get_messages(self, session_id: str) -> list[dict]:
        if not session_id:
            return []
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, role, content, created_at, meta_json
                FROM messages WHERE session_id = ? ORDER BY id
            """, (session_id,)).fetchall()
        out = []
        for row in rows:
            meta = {}
            if row["meta_json"]:
                try:
                    meta = json.loads(row["meta_json"])
                except (TypeError, ValueError):
                    meta = {}
            out.append({
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
                "meta": meta,
            })
        return out
