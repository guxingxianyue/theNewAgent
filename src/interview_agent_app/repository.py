"""SQLite repository for sessions, events, and long-term memory."""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading
from typing import Any

from app_config import DEFAULT_DB_PATH, interview_type_for


_db_path = DEFAULT_DB_PATH
_lock = threading.Lock()


def configure_db(path: str) -> None:
    global _db_path
    _db_path = path


def db_path() -> str:
    return _db_path


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def db_connect() -> sqlite3.Connection:
    parent = os.path.dirname(os.path.abspath(db_path()))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def sql_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql")


def read_sql_file(filename: str) -> str:
    path = os.path.join(sql_dir(), filename)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def split_sql_statements(script: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(buffer).strip().rstrip(";"))
            buffer = []
    if buffer:
        statements.append("\n".join(buffer).strip().rstrip(";"))
    return statements


def _execute_compat_statement(conn: sqlite3.Connection, statement: str) -> None:
    try:
        conn.execute(statement)
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


def init_db() -> None:
    with _lock, db_connect() as conn:
        conn.executescript(read_sql_file("001_init.sql"))
        for statement in split_sql_statements(read_sql_file("002_compat_columns.sql")):
            _execute_compat_statement(conn, statement)


def record_session(session: dict[str, Any]) -> None:
    with _lock, db_connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions
                (id, operator, interview_type, target, difficulty, model, topic,
                 mode, retry_source_session_id, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["id"],
                session["operator"],
                session["interview_type"],
                session["language"],
                session["difficulty"],
                session["model"],
                session["topic"],
                session["mode"],
                session.get("retry_source_session_id"),
                now_utc(),
            ),
        )


def record_event(
    session_id: str,
    role: str,
    kind: str,
    content: str,
    *,
    round_index: int | None = None,
    score: float | None = None,
    tags: list[str] | None = None,
    latency_ms: int | None = None,
    model: str | None = None,
    token_usage: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    with _lock, db_connect() as conn:
        conn.execute(
            """
            INSERT INTO events (
                session_id, created_at, role, kind, content, round_index,
                score, tags, latency_ms, model, token_usage, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                now_utc(),
                role,
                kind,
                content,
                round_index,
                score,
                json.dumps(tags or [], ensure_ascii=False),
                latency_ms,
                model,
                json.dumps(token_usage or {}, ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )


def finish_session(session_id: str, summary: str) -> None:
    with _lock, db_connect() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at = ?, summary = ? WHERE id = ?",
            (now_utc(), summary, session_id),
        )


def add_memory(session: dict[str, Any], content: str) -> None:
    memory = content.strip()
    if not memory:
        return
    with _lock, db_connect() as conn:
        conn.execute(
            """
            INSERT INTO memories
                (operator, interview_type, target, content, source_session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session["operator"],
                session["interview_type"],
                session["language"],
                memory,
                session["id"],
                now_utc(),
            ),
        )


def load_memory(operator: str, interview_type: str, target: str, limit: int = 6) -> str:
    with _lock, db_connect() as conn:
        rows = conn.execute(
            """
            SELECT content, created_at
            FROM memories
            WHERE operator = ?
              AND interview_type = ?
              AND (target = ? OR target = 'ALL')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (operator, interview_type, target, limit),
        ).fetchall()
    if not rows:
        return ""
    return "\n".join(f"- [{row['created_at']}] {row['content']}" for row in rows)


def build_review_context(operator: str, target: str, limit: int = 5) -> str:
    interview_type = interview_type_for(target)
    with _lock, db_connect() as conn:
        rows = conn.execute(
            """
            SELECT e.created_at, e.round_index, e.score, e.tags, e.content
            FROM events e
            JOIN sessions s ON s.id = e.session_id
            WHERE s.operator = ?
              AND s.interview_type = ?
              AND s.target = ?
              AND e.kind = 'evaluation'
              AND (e.score IS NULL OR e.score < 70)
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            (operator, interview_type, target, limit),
        ).fetchall()
    items = []
    for row in rows:
        tags = ", ".join(json.loads(row["tags"] or "[]"))
        items.append(
            f"- {row['created_at']} round={row['round_index']} score={row['score']} tags={tags}\n"
            f"  evaluation={str(row['content'])[:700]}"
        )
    return "\n".join(items)


def list_sessions(operator: str, limit: int = 20) -> list[dict[str, object]]:
    with _lock, db_connect() as conn:
        rows = conn.execute(
            """
            SELECT
                s.id, s.operator, s.target, s.interview_type, s.difficulty, s.model,
                s.topic, s.mode, s.started_at, s.ended_at, s.summary,
                ROUND(AVG(e.score), 1) AS avg_score,
                COUNT(CASE WHEN e.kind = 'evaluation' THEN 1 END) AS evaluation_count
            FROM sessions s
            LEFT JOIN events e ON e.session_id = s.id AND e.kind = 'evaluation'
            WHERE s.operator = ?
            GROUP BY s.id
            ORDER BY s.started_at DESC
            LIMIT ?
            """,
            (operator, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def session_detail(session_id: str) -> dict[str, object]:
    with _lock, db_connect() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            raise ValueError("训练记录不存在。")
        events = conn.execute(
            """
            SELECT created_at, role, kind, content, round_index, score, tags,
                   latency_ms, model, token_usage, metadata_json
            FROM events
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()
    return {"session": dict(session), "events": [dict(row) for row in events]}


def wrong_questions(operator: str, target: str | None = None, limit: int = 30) -> list[dict[str, object]]:
    filters = ["s.operator = ?", "e.kind = 'evaluation'", "(e.score IS NULL OR e.score < 70)"]
    params: list[object] = [operator]
    if target:
        filters.append("s.target = ?")
        params.append(target)
    params.append(limit)

    with _lock, db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                e.id AS event_id,
                e.session_id,
                e.created_at,
                e.round_index,
                e.score,
                e.tags,
                e.metadata_json,
                e.content,
                s.target,
                s.topic,
                s.mode,
                s.difficulty
            FROM events e
            JOIN sessions s ON s.id = e.session_id
            WHERE {" AND ".join(filters)}
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def progress_stats(operator: str) -> dict[str, object]:
    with _lock, db_connect() as conn:
        overview = conn.execute(
            """
            SELECT
                COUNT(DISTINCT s.id) AS session_count,
                COUNT(e.id) AS evaluation_count,
                ROUND(AVG(e.score), 1) AS avg_score,
                ROUND(MAX(e.score), 1) AS best_score
            FROM sessions s
            LEFT JOIN events e ON e.session_id = s.id AND e.kind = 'evaluation'
            WHERE s.operator = ?
            """,
            (operator,),
        ).fetchone()
        by_target = conn.execute(
            """
            SELECT s.target, ROUND(AVG(e.score), 1) AS avg_score, COUNT(e.id) AS count
            FROM sessions s
            JOIN events e ON e.session_id = s.id AND e.kind = 'evaluation'
            WHERE s.operator = ?
            GROUP BY s.target
            ORDER BY count DESC, avg_score ASC
            """,
            (operator,),
        ).fetchall()
        recent = conn.execute(
            """
            SELECT date(s.started_at) AS day, ROUND(AVG(e.score), 1) AS avg_score, COUNT(e.id) AS count
            FROM sessions s
            JOIN events e ON e.session_id = s.id AND e.kind = 'evaluation'
            WHERE s.operator = ?
            GROUP BY date(s.started_at)
            ORDER BY day DESC
            LIMIT 7
            """,
            (operator,),
        ).fetchall()
        weak_rows = conn.execute(
            """
            SELECT e.tags
            FROM sessions s
            JOIN events e ON e.session_id = s.id AND e.kind = 'evaluation'
            WHERE s.operator = ? AND (e.score IS NULL OR e.score < 70)
            """,
            (operator,),
        ).fetchall()

    weak_tags: dict[str, int] = {}
    for row in weak_rows:
        for tag in json.loads(row["tags"] or "[]"):
            weak_tags[str(tag)] = weak_tags.get(str(tag), 0) + 1

    return {
        "overview": dict(overview) if overview else {},
        "by_target": [dict(row) for row in by_target],
        "recent": [dict(row) for row in recent],
        "weak_tags": [
            {"tag": tag, "count": count}
            for tag, count in sorted(weak_tags.items(), key=lambda item: item[1], reverse=True)[:10]
        ],
    }
