#!/usr/bin/env python3
"""
Web UI for the programming interview practice agent.

Run:
    python web_agent.py

Then open:
    http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import threading
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import evaluation as eval_parser
import repository as repo
from app_config import (
    ALLOW_REMOTE,
    DEFAULT_API_KEY,
    DEFAULT_BASE_URL,
    DEFAULT_DB_PATH,
    DEFAULT_MODEL,
    INTERVIEW_TARGETS,
    MAX_JSON_BODY_BYTES,
    TOPICS_BY_TARGET,
    TRAINING_MODES,
    interview_type_for,
    normalize_mode,
    topic_options_for,
)
from interview_agent import (
    DIFFICULTY_CHOICES,
    LANGUAGE_CHOICES,
    InterviewAgent,
    LLMClient,
    LLMError,
)

SESSIONS: dict[str, dict[str, Any]] = {}
SESSIONS_LOCK = threading.Lock()
DB_LOCK = threading.Lock()


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def db_path() -> str:
    return DEFAULT_DB_PATH


def db_connect() -> sqlite3.Connection:
    parent = os.path.dirname(os.path.abspath(db_path()))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with DB_LOCK, db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                operator TEXT NOT NULL,
                interview_type TEXT NOT NULL,
                target TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                model TEXT NOT NULL,
                topic TEXT,
                mode TEXT,
                retry_source_session_id TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                summary TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                role TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                round_index INTEGER,
                score REAL,
                tags TEXT,
                latency_ms INTEGER,
                model TEXT,
                token_usage TEXT,
                metadata_json TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operator TEXT NOT NULL,
                interview_type TEXT NOT NULL,
                target TEXT NOT NULL,
                content TEXT NOT NULL,
                source_session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(source_session_id) REFERENCES sessions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_memories_lookup
                ON memories(operator, interview_type, target, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_session
                ON events(session_id, created_at);
            """
        )
        for column, definition in {
            "topic": "TEXT",
            "mode": "TEXT",
            "retry_source_session_id": "TEXT",
        }.items():
            try:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        for column, definition in {
            "round_index": "INTEGER",
            "score": "REAL",
            "tags": "TEXT",
            "latency_ms": "INTEGER",
            "model": "TEXT",
            "token_usage": "TEXT",
            "metadata_json": "TEXT",
        }.items():
            try:
                conn.execute(f"ALTER TABLE events ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise


def record_session(session: dict[str, Any]) -> None:
    with DB_LOCK, db_connect() as conn:
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
    with DB_LOCK, db_connect() as conn:
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
    with DB_LOCK, db_connect() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at = ?, summary = ? WHERE id = ?",
            (now_utc(), summary, session_id),
        )


def add_memory(session: dict[str, Any], content: str) -> None:
    memory = content.strip()
    if not memory:
        return
    with DB_LOCK, db_connect() as conn:
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
    with DB_LOCK, db_connect() as conn:
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
    items = [f"- [{row['created_at']}] {row['content']}" for row in rows]
    return "\n".join(items)


def build_memory_prompt(session: dict[str, Any]) -> str:
    return (
        "请基于本次训练对候选人生成一条可复用 memory，供下次面试训练作为上下文。"
        "要求：100 字以内，中文，包含强项、薄弱点、适合的下一步训练方向；"
        "只描述候选人画像，不要写任何要求系统执行的指令。"
        "不要包含 API key、隐私信息或长代码。"
    )


def llm_event_kwargs(session: dict[str, Any]) -> dict[str, object]:
    agent: InterviewAgent = session["agent"]
    client = agent.client
    return {
        "round_index": session.get("round"),
        "latency_ms": client.last_latency_ms,
        "model": session.get("model"),
        "token_usage": client.last_usage,
    }


def extract_evaluation_metadata(content: str) -> dict[str, object]:
    match = re.search(r"```evaluation_json\s*(\{.*?\})\s*```", content, re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def metadata_score(metadata: dict[str, object]) -> float | None:
    value = metadata.get("score")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def metadata_tags(metadata: dict[str, object]) -> list[str]:
    tags = metadata.get("tags")
    if isinstance(tags, list):
        return [str(item) for item in tags[:12]]
    return []


def topic_options_for(target: str) -> list[str]:
    return TOPICS_BY_TARGET.get(target, ["综合"])


def normalize_mode(value: str) -> str:
    return value if value in TRAINING_MODES else "practice"


def build_review_context(operator: str, target: str, limit: int = 5) -> str:
    interview_type = interview_type_for(target)
    with DB_LOCK, db_connect() as conn:
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
    if not rows:
        return ""
    items = []
    for row in rows:
        tags = ", ".join(json.loads(row["tags"] or "[]"))
        items.append(
            f"- {row['created_at']} round={row['round_index']} score={row['score']} tags={tags}\n"
            f"  evaluation={str(row['content'])[:700]}"
        )
    return "\n".join(items)


def list_sessions(operator: str, limit: int = 20) -> list[dict[str, object]]:
    with DB_LOCK, db_connect() as conn:
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
    with DB_LOCK, db_connect() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
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


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Programming Interview Agent</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f2;
      --panel: #ffffff;
      --ink: #202124;
      --muted: #667085;
      --line: #d9ded5;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --warm: #b45309;
      --danger: #b42318;
      --code: #101828;
      --shadow: 0 12px 30px rgba(32, 33, 36, 0.08);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    button, input, select, textarea {
      font: inherit;
    }

    .app {
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
    }

    aside {
      border-right: 1px solid var(--line);
      background: #fbfcf8;
      padding: 24px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }

    main {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      min-width: 0;
      height: 100vh;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .mark {
      width: 38px;
      height: 38px;
      display: grid;
      place-items: center;
      border-radius: 8px;
      background: var(--code);
      color: #ffffff;
      font-weight: 800;
    }

    h1 {
      margin: 0;
      font-size: 20px;
      line-height: 1.2;
      letter-spacing: 0;
    }

    .subtitle {
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    .field {
      display: grid;
      gap: 7px;
    }

    label {
      color: #344054;
      font-size: 13px;
      font-weight: 650;
    }

    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      color: var(--ink);
      outline: none;
    }

    input, select {
      height: 40px;
      padding: 0 11px;
    }

    textarea {
      min-height: 160px;
      max-height: 34vh;
      resize: vertical;
      padding: 12px;
      line-height: 1.5;
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      font-size: 13px;
    }

    input:focus, select:focus, textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.12);
    }

    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }

    .actions {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .preset-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }

    .preset {
      min-height: 36px;
      padding: 0 8px;
      font-size: 13px;
    }

    .preset.active {
      color: #ffffff;
      background: var(--code);
      border-color: var(--code);
      font-weight: 700;
    }

    button {
      border: 1px solid transparent;
      border-radius: 8px;
      min-height: 40px;
      padding: 0 12px;
      cursor: pointer;
      color: var(--ink);
      background: #ffffff;
      border-color: var(--line);
      transition: transform 120ms ease, background 120ms ease, border-color 120ms ease;
    }

    button:hover { transform: translateY(-1px); }
    button:disabled { opacity: 0.55; cursor: not-allowed; transform: none; }

    .primary {
      color: #ffffff;
      background: var(--accent);
      border-color: var(--accent);
      font-weight: 700;
    }

    .primary:hover {
      background: var(--accent-strong);
      border-color: var(--accent-strong);
    }

    .warn {
      color: #ffffff;
      background: var(--warm);
      border-color: var(--warm);
    }

    .danger {
      color: var(--danger);
      border-color: #f3b8b2;
    }

    .topbar {
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.78);
      padding: 18px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
    }

    .status {
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: anywhere;
    }

    .log {
      overflow: auto;
      padding: 24px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .message {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .message header {
      min-height: 42px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--line);
      color: #344054;
      font-weight: 750;
      font-size: 13px;
    }

    .message pre {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      padding: 15px;
      line-height: 1.58;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
    }

    .composer {
      border-top: 1px solid var(--line);
      background: #fbfcf8;
      padding: 18px 24px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 170px;
      gap: 14px;
      align-items: end;
    }

    .composer-actions {
      display: grid;
      gap: 9px;
    }

    .history-list {
      display: grid;
      gap: 10px;
    }

    .history-item {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      padding: 12px;
      display: grid;
      gap: 8px;
    }

    .history-item strong {
      font-size: 14px;
    }

    .history-actions {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }

    .score-card, .stats-grid {
      display: grid;
      gap: 10px;
      padding: 14px;
      border-bottom: 1px solid var(--line);
      background: #f8fbf9;
    }

    .score-main {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
    }

    .score-main strong {
      font-size: 26px;
      color: var(--accent-strong);
    }

    .score-bars {
      display: grid;
      gap: 8px;
    }

    .bar-row {
      display: grid;
      grid-template-columns: 110px minmax(0, 1fr) 42px;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      color: #344054;
    }

    .bar {
      height: 8px;
      border-radius: 999px;
      background: #dde6df;
      overflow: hidden;
    }

    .bar span {
      display: block;
      height: 100%;
      background: var(--accent);
    }

    .tags {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .tag {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 8px;
      background: #ffffff;
      font-size: 12px;
      color: #344054;
    }

    .stats-grid {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }

    .stat {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      padding: 10px;
    }

    .stat strong {
      display: block;
      font-size: 22px;
      color: var(--code);
    }

    .stat span {
      color: var(--muted);
      font-size: 12px;
    }

    .empty {
      border: 1px dashed #b7c0b2;
      border-radius: 8px;
      padding: 28px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.55);
      line-height: 1.6;
    }

    .small {
      font-size: 12px;
      color: var(--muted);
      line-height: 1.45;
    }

    @media (max-width: 840px) {
      .app {
        grid-template-columns: 1fr;
      }

      aside {
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }

      main {
        height: auto;
        min-height: 70vh;
      }

      .composer {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <div class="brand">
        <div class="mark">AI</div>
        <div>
          <h1>编程面试训练</h1>
          <p class="subtitle">选择语言后开始刷题，面试官会出题、提示、评估和追问。</p>
        </div>
      </div>

      <div class="field">
        <label for="apiKey">API Key</label>
        <input id="apiKey" type="password" autocomplete="off" placeholder="可留空使用环境变量" />
      </div>

      <div class="field">
        <label for="operator">操作者</label>
        <input id="operator" value="default" />
      </div>

      <div class="field">
        <label for="baseUrl">Base URL</label>
        <input id="baseUrl" value="__DEFAULT_BASE_URL__" />
      </div>

      <div class="field">
        <label for="model">模型</label>
        <input id="model" value="__DEFAULT_MODEL__" />
      </div>

      <div class="field">
        <label>面试方向</label>
        <div class="preset-grid">
          <button type="button" class="preset active" data-target="Python">Python</button>
          <button type="button" class="preset" data-target="Java">Java</button>
          <button type="button" class="preset" data-target="AI Agent">AI Agent</button>
        </div>
      </div>

      <div class="row">
        <div class="field">
          <label for="language">方向名称</label>
          <input id="language" list="languageOptions" value="Python" />
          <datalist id="languageOptions">__LANGUAGE_OPTIONS__</datalist>
        </div>
        <div class="field">
          <label for="difficulty">难度</label>
          <select id="difficulty">__DIFFICULTY_OPTIONS__</select>
        </div>
      </div>

      <div class="field">
        <label for="topic">题型/专项</label>
        <select id="topic">__TOPIC_OPTIONS__</select>
      </div>

      <div class="field">
        <label for="mode">训练模式</label>
        <select id="mode">
          <option value="practice" selected>练习模式</option>
          <option value="mock">模拟面试</option>
          <option value="review">错题重练</option>
        </select>
      </div>

      <div class="row">
        <div class="field">
          <label for="rounds">题数</label>
          <input id="rounds" type="number" min="1" max="20" value="3" />
        </div>
        <div class="field">
          <label for="temperature">温度</label>
          <input id="temperature" type="number" min="0" max="2" step="0.1" value="0.7" />
        </div>
      </div>

      <button id="startBtn" class="primary">开始训练</button>
      <button id="memoryBtn">查看历史 Memory</button>
      <button id="progressBtn">训练进度</button>
      <button id="wrongBookBtn">错题本</button>
      <button id="historyBtn">训练历史/复盘</button>
      <button id="wrongBtn">错题重练</button>
      <button id="resetBtn" class="danger" disabled>结束当前训练</button>
      <p class="small">训练过程会写入本地 SQLite 知识库；API Key 只保存在当前进程内存中。</p>
    </aside>

    <main>
      <div class="topbar">
        <strong id="sessionTitle">等待开始</strong>
        <span id="status" class="status">配置参数后点击开始训练。</span>
      </div>

      <section id="log" class="log">
        <div class="empty">这里会显示题目、提示、评估、追问和训练总结。</div>
      </section>

      <section class="composer">
        <div class="field">
          <label for="solution">你的答案</label>
          <textarea id="solution" placeholder="写思路或代码，然后点击提交评估。" disabled></textarea>
        </div>
        <div class="composer-actions">
          <button id="submitBtn" class="primary" disabled>提交评估</button>
          <button id="hintBtn" disabled>提示</button>
          <button id="answerBtn" class="warn" disabled>参考答案</button>
          <button id="nextBtn" disabled>下一题</button>
        </div>
      </section>
    </main>
  </div>

  <script>
    const state = { sessionId: null, busy: false, finished: false };
    const topicsByTarget = __TOPICS_JSON__;

    const $ = (id) => document.getElementById(id);
    const log = $("log");
    const buttons = ["startBtn", "resetBtn", "submitBtn", "hintBtn", "answerBtn", "nextBtn"];

    function setTarget(target) {
      $("language").value = target;
      document.querySelectorAll(".preset").forEach((button) => {
        button.classList.toggle("active", button.dataset.target === target);
      });
      refreshTopics(target);
    }

    function refreshTopics(target) {
      const current = $("topic").value;
      const topics = topicsByTarget[target] || ["综合"];
      $("topic").innerHTML = "";
      topics.forEach((topic) => {
        const option = document.createElement("option");
        option.value = topic;
        option.textContent = topic;
        $("topic").appendChild(option);
      });
      $("topic").value = topics.includes(current) ? current : topics[0];
    }

    function setBusy(value, text) {
      state.busy = value;
      $("status").textContent = text || (value ? "正在请求模型..." : "就绪");
      updateControls();
    }

    function updateControls() {
      const active = Boolean(state.sessionId) && !state.finished;
      $("startBtn").disabled = state.busy;
      $("memoryBtn").disabled = state.busy;
      $("progressBtn").disabled = state.busy;
      $("wrongBookBtn").disabled = state.busy;
      $("historyBtn").disabled = state.busy;
      $("wrongBtn").disabled = state.busy;
      $("resetBtn").disabled = state.busy || !state.sessionId;
      $("solution").disabled = state.busy || !active;
      $("submitBtn").disabled = state.busy || !active;
      $("hintBtn").disabled = state.busy || !active || $("mode").value === "mock";
      $("answerBtn").disabled = state.busy || !active || $("mode").value === "mock";
      $("nextBtn").disabled = state.busy || !active;
    }

    function clearLog() {
      log.innerHTML = "";
    }

    function addMessage(title, content, meta) {
      const article = document.createElement("article");
      article.className = "message";
      const header = document.createElement("header");
      const heading = document.createElement("span");
      heading.textContent = title;
      const info = document.createElement("span");
      info.className = "small";
      info.textContent = meta || "";
      const pre = document.createElement("pre");
      const metadata = parseEvaluationJson(content);
      pre.textContent = stripEvaluationJson(content);
      header.append(heading, info);
      article.append(header);
      if (metadata) {
        article.appendChild(renderScoreCard(metadata));
      }
      article.append(pre);
      log.appendChild(article);
      log.scrollTop = log.scrollHeight;
    }

    function parseEvaluationJson(content) {
      const match = String(content || "").match(/```evaluation_json\s*(\{[\s\S]*?\})\s*```/);
      if (!match) return null;
      try {
        return JSON.parse(match[1]);
      } catch {
        return null;
      }
    }

    function stripEvaluationJson(content) {
      return String(content || "").replace(/```evaluation_json\s*\{[\s\S]*?\}\s*```/g, "").trim();
    }

    function clampScore(value) {
      const number = Number(value);
      if (!Number.isFinite(number)) return null;
      return Math.max(0, Math.min(100, number));
    }

    function renderScoreCard(metadata) {
      const card = document.createElement("div");
      card.className = "score-card";
      const score = clampScore(metadata.score);
      const maxScore = metadata.max_score || 100;
      const main = document.createElement("div");
      main.className = "score-main";
      const left = document.createElement("div");
      left.innerHTML = `<span class="small">本题评分</span><strong>${score ?? "无"}</strong><span class="small"> / ${maxScore}</span>`;
      const verdict = document.createElement("span");
      verdict.className = "small";
      verdict.textContent = metadata.is_wrong_question ? "已进入错题关注" : "表现可继续推进";
      main.append(left, verdict);
      card.appendChild(main);

      const bars = document.createElement("div");
      bars.className = "score-bars";
      [
        ["正确性", metadata.correctness],
        ["复杂度", metadata.complexity],
        ["边界条件", metadata.edge_cases],
        ["代码质量", metadata.code_quality],
        ["沟通表达", metadata.communication],
      ].forEach(([label, value]) => {
        const itemScore = clampScore(value);
        if (itemScore == null) return;
        const row = document.createElement("div");
        row.className = "bar-row";
        row.innerHTML = `<span>${label}</span><div class="bar"><span style="width:${itemScore}%"></span></div><span>${itemScore}</span>`;
        bars.appendChild(row);
      });
      card.appendChild(bars);

      const tags = Array.isArray(metadata.tags) ? metadata.tags : [];
      if (tags.length) {
        const tagBox = document.createElement("div");
        tagBox.className = "tags";
        tags.slice(0, 10).forEach((tag) => {
          const item = document.createElement("span");
          item.className = "tag";
          item.textContent = tag;
          tagBox.appendChild(item);
        });
        card.appendChild(tagBox);
      }
      return card;
    }

    async function post(path, payload) {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      const data = await response.json();
      if (!response.ok || data.error) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      return data;
    }

    function applyResponse(data) {
      if (data.session_id) state.sessionId = data.session_id;
      state.finished = Boolean(data.finished);
      $("sessionTitle").textContent = data.round && data.total
        ? `第 ${data.round} / ${data.total} 题`
        : (state.sessionId ? "训练中" : "等待开始");
      $("status").textContent = data.status || "就绪";
      if (data.title && data.content) {
        addMessage(data.title, data.content, data.meta);
      }
      if (data.memory_context) {
        addMessage("已载入历史 Memory", data.memory_context, "SQLite 知识库");
      }
      if (data.review_context) {
        addMessage("已载入错题上下文", data.review_context, "SQLite 知识库");
      }
      if (data.finished) {
        $("solution").value = "";
      }
      updateControls();
    }

    async function startSession() {
      clearLog();
      setBusy(true, "正在生成第一题...");
      try {
        const data = await post("/api/start", {
          api_key: $("apiKey").value,
          base_url: $("baseUrl").value,
          model: $("model").value,
          operator: $("operator").value,
          language: $("language").value,
          topic: $("topic").value,
          mode: $("mode").value,
          difficulty: $("difficulty").value,
          rounds: $("rounds").value,
          temperature: $("temperature").value,
        });
        $("solution").value = "";
        applyResponse(data);
      } catch (error) {
        addMessage("错误", error.message);
        state.sessionId = null;
        state.finished = false;
      } finally {
        setBusy(false, state.sessionId ? "第一题已生成。" : "启动失败。");
      }
    }

    async function showMemory() {
      setBusy(true, "正在读取 SQLite memory...");
      try {
        const data = await post("/api/memory", {
          operator: $("operator").value,
          language: $("language").value,
        });
        addMessage("历史 Memory", data.content || "当前方向还没有历史 memory。", "SQLite 知识库");
      } catch (error) {
        addMessage("错误", error.message);
      } finally {
        setBusy(false, "就绪");
      }
    }

    async function showHistory() {
      setBusy(true, "正在读取训练历史...");
      try {
        const data = await post("/api/history", {
          operator: $("operator").value,
        });
        renderHistory(data.sessions || []);
      } catch (error) {
        addMessage("错误", error.message);
      } finally {
        setBusy(false, "就绪");
      }
    }

    async function showProgress() {
      setBusy(true, "正在读取训练进度...");
      try {
        const data = await post("/api/progress", {
          operator: $("operator").value,
        });
        renderProgress(data);
      } catch (error) {
        addMessage("错误", error.message);
      } finally {
        setBusy(false, "就绪");
      }
    }

    function renderProgress(data) {
      const overview = data.overview || {};
      const article = document.createElement("article");
      article.className = "message";
      const header = document.createElement("header");
      const heading = document.createElement("span");
      heading.textContent = "训练进度";
      const info = document.createElement("span");
      info.className = "small";
      info.textContent = "SQLite 知识库";
      header.append(heading, info);
      const stats = document.createElement("div");
      stats.className = "stats-grid";
      [
        ["训练次数", overview.session_count ?? 0],
        ["评估次数", overview.evaluation_count ?? 0],
        ["平均分", overview.avg_score ?? "无"],
        ["最高分", overview.best_score ?? "无"],
      ].forEach(([label, value]) => {
        const item = document.createElement("div");
        item.className = "stat";
        item.innerHTML = `<strong>${value}</strong><span>${label}</span>`;
        stats.appendChild(item);
      });
      const body = document.createElement("pre");
      const weak = (data.weak_tags || []).map((item) => `${item.tag} x${item.count}`).join("、") || "暂无";
      const byTarget = (data.by_target || []).map((item) => `${item.target}: ${item.avg_score ?? "无"} 分 / ${item.count} 次`).join("\n") || "暂无";
      const recent = (data.recent || []).map((item) => `${item.day}: ${item.avg_score ?? "无"} 分 / ${item.count} 次`).join("\n") || "暂无";
      body.textContent = `薄弱点排行：${weak}\n\n方向表现：\n${byTarget}\n\n最近训练：\n${recent}`;
      article.append(header, stats, body);
      log.appendChild(article);
      log.scrollTop = log.scrollHeight;
    }

    async function showWrongBook() {
      setBusy(true, "正在读取错题本...");
      try {
        const data = await post("/api/wrong_questions", {
          operator: $("operator").value,
          language: $("language").value,
        });
        renderWrongBook(data.items || []);
      } catch (error) {
        addMessage("错误", error.message);
      } finally {
        setBusy(false, "就绪");
      }
    }

    function renderWrongBook(items) {
      const wrapper = document.createElement("div");
      wrapper.className = "history-list";
      if (!items.length) {
        addMessage("错题本", "当前方向还没有低分评估记录。", "SQLite 知识库");
        return;
      }
      items.forEach((item) => {
        const card = document.createElement("div");
        card.className = "history-item";
        const title = document.createElement("strong");
        title.textContent = `${item.target || ""} · ${item.topic || "综合"} · 第 ${item.round_index || "-"} 题 · ${item.score ?? "无"} 分`;
        const meta = document.createElement("span");
        meta.className = "small";
        let tags = [];
        try { tags = JSON.parse(item.tags || "[]"); } catch {}
        meta.textContent = `${item.created_at || ""} · ${tags.join("、") || "无标签"}`;
        const preview = document.createElement("pre");
        preview.textContent = stripEvaluationJson(String(item.content || "")).slice(0, 600);
        const actions = document.createElement("div");
        actions.className = "history-actions";
        const retryBtn = document.createElement("button");
        retryBtn.textContent = "重练相似题";
        retryBtn.addEventListener("click", () => startWrongReview(item));
        const detailBtn = document.createElement("button");
        detailBtn.textContent = "查看原复盘";
        detailBtn.addEventListener("click", () => showSessionDetail(item.session_id));
        actions.append(retryBtn, detailBtn);
        card.append(title, meta, preview, actions);
        wrapper.appendChild(card);
      });
      const article = document.createElement("article");
      article.className = "message";
      const header = document.createElement("header");
      const heading = document.createElement("span");
      heading.textContent = "错题本";
      const info = document.createElement("span");
      info.className = "small";
      info.textContent = "低于 70 分或未评分的评估";
      header.append(heading, info);
      article.append(header, wrapper);
      log.appendChild(article);
      log.scrollTop = log.scrollHeight;
    }

    function renderHistory(sessions) {
      const wrapper = document.createElement("div");
      wrapper.className = "history-list";
      if (!sessions.length) {
        addMessage("训练历史", "当前操作者还没有训练记录。", "SQLite 知识库");
        return;
      }
      sessions.forEach((item) => {
        const card = document.createElement("div");
        card.className = "history-item";
        const title = document.createElement("strong");
        title.textContent = `${item.target || ""} · ${item.topic || "综合"} · ${item.mode || "practice"}`;
        const meta = document.createElement("span");
        meta.className = "small";
        meta.textContent = `${item.started_at || ""} · 平均分 ${item.avg_score ?? "无"} · 评估 ${item.evaluation_count || 0} 次`;
        const actions = document.createElement("div");
        actions.className = "history-actions";
        const detailBtn = document.createElement("button");
        detailBtn.textContent = "查看复盘";
        detailBtn.addEventListener("click", () => showSessionDetail(item.id));
        const retryBtn = document.createElement("button");
        retryBtn.textContent = "错题重练";
        retryBtn.addEventListener("click", () => startWrongReview(item));
        actions.append(detailBtn, retryBtn);
        card.append(title, meta, actions);
        wrapper.appendChild(card);
      });
      const article = document.createElement("article");
      article.className = "message";
      const header = document.createElement("header");
      const heading = document.createElement("span");
      heading.textContent = "训练历史";
      const info = document.createElement("span");
      info.className = "small";
      info.textContent = "SQLite 知识库";
      header.append(heading, info);
      article.append(header, wrapper);
      log.appendChild(article);
      log.scrollTop = log.scrollHeight;
    }

    async function showSessionDetail(sessionId) {
      setBusy(true, "正在生成复盘...");
      try {
        const data = await post("/api/session_detail", { session_id: sessionId });
        addMessage("训练复盘", formatSessionDetail(data), "SQLite 知识库");
      } catch (error) {
        addMessage("错误", error.message);
      } finally {
        setBusy(false, "就绪");
      }
    }

    function formatSessionDetail(data) {
      const session = data.session || {};
      const events = data.events || [];
      const lines = [
        `方向：${session.target || ""}`,
        `题型：${session.topic || "综合"}`,
        `模式：${session.mode || "practice"}`,
        `难度：${session.difficulty || ""}`,
        `开始：${session.started_at || ""}`,
        "",
      ];
      events.forEach((event) => {
        const score = event.score == null ? "" : ` score=${event.score}`;
        lines.push(`[${event.round_index || "-"}] ${event.kind}${score}`);
        lines.push(String(event.content || "").slice(0, 1200));
        lines.push("");
      });
      return lines.join("\n");
    }

    async function startWrongReview(source) {
      setTarget(source?.target || $("language").value);
      const desiredTopic = source?.topic || "综合";
      if ([...$("topic").options].some((option) => option.value === desiredTopic)) {
        $("topic").value = desiredTopic;
      }
      $("mode").value = "review";
      clearLog();
      setBusy(true, "正在生成错题重练...");
      try {
        const data = await post("/api/start", {
          api_key: $("apiKey").value,
          base_url: $("baseUrl").value,
          model: $("model").value,
          operator: $("operator").value,
          language: $("language").value,
          topic: $("topic").value,
          mode: "review",
          difficulty: $("difficulty").value,
          rounds: $("rounds").value,
          temperature: $("temperature").value,
          retry_source_session_id: source?.id || "",
        });
        $("solution").value = "";
        applyResponse(data);
      } catch (error) {
        addMessage("错误", error.message);
      } finally {
        setBusy(false, state.sessionId ? "错题重练已开始。" : "启动失败。");
      }
    }

    async function sendAction(action) {
      if (!state.sessionId) return;
      const labels = {
        submit: "正在评估答案...",
        hint: "正在生成提示...",
        answer: "正在生成参考答案...",
        next: "正在进入下一题...",
        reset: "正在结束训练...",
      };
      setBusy(true, labels[action]);
      try {
        const solution = $("solution").value.trim();
        if (action === "submit" && !solution) {
          throw new Error("请先输入答案。");
        }
        const data = await post("/api/action", {
          session_id: state.sessionId,
          action,
          solution,
        });
        if (action === "submit" || action === "next") {
          $("solution").value = "";
        }
        if (action === "reset") {
          state.sessionId = null;
          state.finished = true;
        }
        applyResponse(data);
      } catch (error) {
        addMessage("错误", error.message);
      } finally {
        setBusy(false, state.finished ? "训练已结束。" : "就绪");
      }
    }

    $("startBtn").addEventListener("click", startSession);
    $("memoryBtn").addEventListener("click", showMemory);
    $("progressBtn").addEventListener("click", showProgress);
    $("wrongBookBtn").addEventListener("click", showWrongBook);
    $("historyBtn").addEventListener("click", showHistory);
    $("wrongBtn").addEventListener("click", () => startWrongReview(null));
    $("resetBtn").addEventListener("click", () => sendAction("reset"));
    $("submitBtn").addEventListener("click", () => sendAction("submit"));
    $("hintBtn").addEventListener("click", () => sendAction("hint"));
    $("answerBtn").addEventListener("click", () => sendAction("answer"));
    $("nextBtn").addEventListener("click", () => sendAction("next"));
    $("language").addEventListener("input", () => setTarget($("language").value));
    $("mode").addEventListener("change", updateControls);
    document.querySelectorAll(".preset").forEach((button) => {
      button.addEventListener("click", () => setTarget(button.dataset.target));
    });
    updateControls();
  </script>
</body>
</html>
"""


def build_index_html() -> bytes:
    options = list(dict.fromkeys(INTERVIEW_TARGETS + LANGUAGE_CHOICES))
    language_options = "".join(f'<option value="{escape_html(item)}"></option>' for item in options)
    difficulty_options = "".join(
        f'<option value="{escape_html(item)}"{" selected" if item == "medium" else ""}>{escape_html(item)}</option>'
        for item in DIFFICULTY_CHOICES
    )
    topic_options = "".join(f'<option value="{escape_html(item)}">{escape_html(item)}</option>' for item in topic_options_for("Python"))
    topics_json = json.dumps(TOPICS_BY_TARGET, ensure_ascii=False)
    html = (
        INDEX_HTML.replace("__DEFAULT_BASE_URL__", escape_html(DEFAULT_BASE_URL))
        .replace("__DEFAULT_MODEL__", escape_html(DEFAULT_MODEL))
        .replace("__LANGUAGE_OPTIONS__", language_options)
        .replace("__DIFFICULTY_OPTIONS__", difficulty_options)
        .replace("__TOPIC_OPTIONS__", topic_options)
        .replace("__TOPICS_JSON__", topics_json)
    )
    return html.encode("utf-8")


def escape_html(value: Any) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length > MAX_JSON_BODY_BYTES:
        raise ValueError(f"请求体过大，最大允许 {MAX_JSON_BODY_BYTES} bytes。")
    raw = handler.rfile.read(length).decode("utf-8")
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("JSON body must be an object.")
    return parsed


def is_local_request(handler: BaseHTTPRequestHandler) -> bool:
    host = handler.client_address[0]
    return host in {"127.0.0.1", "::1", "localhost"} or host.startswith("127.")


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def text_value(payload: dict[str, Any], key: str, default: str = "") -> str:
    value = payload.get(key, default)
    return str(value).strip()


def interview_type_for(target: str) -> str:
    normalized = target.lower().replace("-", " ").replace("_", " ").strip()
    return "ai_agent" if normalized in {"ai agent", "agent", "ai"} else "programming"


def create_session(payload: dict[str, Any]) -> dict[str, Any]:
    repo.init_db()
    api_key = text_value(payload, "api_key") or DEFAULT_API_KEY
    if not api_key:
        raise ValueError("缺少 API key。请在页面填写，或设置 LLM_API_KEY / OPENAI_API_KEY。")

    base_url = text_value(payload, "base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
    model = text_value(payload, "model", DEFAULT_MODEL) or DEFAULT_MODEL
    operator = text_value(payload, "operator", "default") or "default"
    language = text_value(payload, "language", "Python") or "Python"
    interview_type = interview_type_for(language)
    topic = text_value(payload, "topic", "综合") or "综合"
    mode = normalize_mode(text_value(payload, "mode", "practice") or "practice")
    retry_source_session_id = text_value(payload, "retry_source_session_id") or None
    review_context = text_value(payload, "review_context")
    if mode == "review" and not review_context:
        review_context = repo.build_review_context(operator, language)
    difficulty = text_value(payload, "difficulty", "medium") or "medium"
    if difficulty not in DIFFICULTY_CHOICES:
        raise ValueError("难度必须是 easy、medium 或 hard。")

    try:
        rounds = max(1, min(20, int(text_value(payload, "rounds", "3"))))
    except ValueError as exc:
        raise ValueError("题数必须是数字。") from exc

    try:
        temperature = max(0.0, min(2.0, float(text_value(payload, "temperature", "0.7"))))
    except ValueError as exc:
        raise ValueError("温度必须是数字。") from exc

    client = LLMClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=temperature,
    )
    memory_context = repo.load_memory(operator, interview_type, language)
    agent = InterviewAgent(
        client,
        language=language,
        difficulty=difficulty,
        interview_type=interview_type,
        memory_context=memory_context,
        topic=topic,
        mode=mode,
        review_context=review_context,
    )
    session = {
        "id": uuid.uuid4().hex,
        "agent": agent,
        "operator": operator,
        "interview_type": interview_type,
        "language": language,
        "difficulty": difficulty,
        "topic": topic,
        "mode": mode,
        "retry_source_session_id": retry_source_session_id,
        "round": 1,
        "rounds": rounds,
        "model": model,
        "memory_context": memory_context,
        "review_context": review_context,
    }
    first_problem = agent.new_problem(1)
    session["last_problem"] = first_problem
    repo.record_session(session)
    repo.record_event(session["id"], "assistant", "problem", first_problem, **llm_event_kwargs(session))
    return session


def session_response(session: dict[str, Any], title: str, content: str, finished: bool = False) -> dict[str, Any]:
    return {
        "session_id": session["id"],
        "round": session["round"],
        "total": session["rounds"],
        "title": title,
        "content": content,
        "meta": (
            f'{session["operator"]} · {session["language"]} · '
            f'{session["topic"]} · {session["mode"]} · '
            f'{session["difficulty"]} · {session["model"]}'
        ),
        "finished": finished,
        "status": "训练已结束。" if finished else "就绪",
    }


class InterviewWebHandler(BaseHTTPRequestHandler):
    server_version = "InterviewAgentWeb/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:
        if not ALLOW_REMOTE and not is_local_request(self):
            self.send_error(HTTPStatus.FORBIDDEN, "Remote access is disabled.")
            return
        path = urlparse(self.path).path
        if path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        data = build_index_html()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        if not ALLOW_REMOTE and not is_local_request(self):
            json_response(self, HTTPStatus.FORBIDDEN, {"error": "Remote access is disabled."})
            return
        path = urlparse(self.path).path
        try:
            if path == "/api/start":
                self.handle_start()
            elif path == "/api/action":
                self.handle_action()
            elif path == "/api/memory":
                self.handle_memory()
            elif path == "/api/history":
                self.handle_history()
            elif path == "/api/session_detail":
                self.handle_session_detail()
            elif path == "/api/progress":
                self.handle_progress()
            elif path == "/api/wrong_questions":
                self.handle_wrong_questions()
            else:
                json_response(self, HTTPStatus.NOT_FOUND, {"error": "接口不存在。"})
        except (ValueError, LLMError) as exc:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"服务器异常：{exc}"})

    def handle_start(self) -> None:
        payload = read_json(self)
        session = create_session(payload)
        with SESSIONS_LOCK:
            SESSIONS[session["id"]] = session
        response = session_response(session, "第 1 题", session["last_problem"])
        response["memory_context"] = session["memory_context"]
        response["review_context"] = session["review_context"]
        json_response(self, HTTPStatus.OK, response)

    def handle_memory(self) -> None:
        repo.init_db()
        payload = read_json(self)
        operator = text_value(payload, "operator", "default") or "default"
        target = text_value(payload, "language", "Python") or "Python"
        memory = repo.load_memory(operator, interview_type_for(target), target, limit=12)
        json_response(self, HTTPStatus.OK, {"content": memory})

    def handle_history(self) -> None:
        repo.init_db()
        payload = read_json(self)
        operator = text_value(payload, "operator", "default") or "default"
        json_response(self, HTTPStatus.OK, {"sessions": repo.list_sessions(operator)})

    def handle_session_detail(self) -> None:
        repo.init_db()
        payload = read_json(self)
        session_id = text_value(payload, "session_id")
        if not session_id:
            raise ValueError("缺少 session_id。")
        json_response(self, HTTPStatus.OK, repo.session_detail(session_id))

    def handle_progress(self) -> None:
        repo.init_db()
        payload = read_json(self)
        operator = text_value(payload, "operator", "default") or "default"
        json_response(self, HTTPStatus.OK, repo.progress_stats(operator))

    def handle_wrong_questions(self) -> None:
        repo.init_db()
        payload = read_json(self)
        operator = text_value(payload, "operator", "default") or "default"
        target = text_value(payload, "language")
        json_response(self, HTTPStatus.OK, {"items": repo.wrong_questions(operator, target or None)})

    def handle_action(self) -> None:
        payload = read_json(self)
        session_id = text_value(payload, "session_id")
        action = text_value(payload, "action")
        if not session_id:
            raise ValueError("缺少 session_id。")

        with SESSIONS_LOCK:
            session = SESSIONS.get(session_id)
        if not session:
            raise ValueError("会话不存在或已结束，请重新开始训练。")

        agent: InterviewAgent = session["agent"]

        if action == "submit":
            solution = text_value(payload, "solution")
            if not solution:
                raise ValueError("请先输入答案。")
            repo.record_event(session["id"], "user", "answer", solution, round_index=session["round"])
            content = agent.evaluate_solution(solution)
            metadata = eval_parser.extract_evaluation_metadata(content)
            repo.record_event(
                session["id"],
                "assistant",
                "evaluation",
                content,
                score=eval_parser.metadata_score(metadata),
                tags=eval_parser.metadata_tags(metadata),
                metadata=metadata,
                **llm_event_kwargs(session),
            )
            json_response(self, HTTPStatus.OK, session_response(session, "评估与追问", content))
            return

        if action == "hint":
            if session["mode"] == "mock":
                raise ValueError("模拟面试模式下不能使用提示。")
            content = agent.ask("请给我一个渐进式提示，不要直接给完整答案。")
            repo.record_event(session["id"], "assistant", "hint", content, **llm_event_kwargs(session))
            json_response(self, HTTPStatus.OK, session_response(session, "提示", content))
            return

        if action == "answer":
            if session["mode"] == "mock":
                raise ValueError("模拟面试模式下不能查看参考答案。")
            content = agent.ask("请给出参考答案、复杂度分析和这门语言的实现注意点。")
            repo.record_event(session["id"], "assistant", "reference_answer", content, **llm_event_kwargs(session))
            json_response(self, HTTPStatus.OK, session_response(session, "参考答案", content))
            return

        if action == "next":
            if session["round"] >= session["rounds"]:
                if session["mode"] == "mock":
                    summary_prompt = (
                        "请生成模拟面试最终报告。输出必须包含：总分、是否建议通过、"
                        "通过概率、分项表现、关键失分点、最应该补的 3 个训练项、"
                        "下一次模拟面试建议。请保持真实面试反馈风格。"
                    )
                else:
                    summary_prompt = "请总结这次训练的表现，并给出下一步刷题建议。"
                content = agent.ask(summary_prompt)
                repo.record_event(session["id"], "assistant", "summary", content, **llm_event_kwargs(session))
                repo.finish_session(session["id"], content)
                try:
                    memory = agent.ask(build_memory_prompt(session))
                except LLMError:
                    memory = content[:500]
                repo.add_memory(session, memory)
                repo.record_event(session["id"], "assistant", "memory", memory, **llm_event_kwargs(session))
                with SESSIONS_LOCK:
                    SESSIONS.pop(session_id, None)
                title = "模拟面试报告" if session["mode"] == "mock" else "训练总结"
                json_response(self, HTTPStatus.OK, session_response(session, title, content, finished=True))
                return
            session["round"] += 1
            content = agent.new_problem(session["round"])
            session["last_problem"] = content
            repo.record_event(session["id"], "assistant", "problem", content, **llm_event_kwargs(session))
            json_response(self, HTTPStatus.OK, session_response(session, f'第 {session["round"]} 题', content))
            return

        if action == "reset":
            with SESSIONS_LOCK:
                SESSIONS.pop(session_id, None)
            content = "已结束当前训练。你可以调整语言、难度或模型参数后重新开始。"
            repo.finish_session(session["id"], content)
            repo.record_event(session["id"], "system", "reset", content, round_index=session["round"])
            json_response(self, HTTPStatus.OK, session_response(session, "训练结束", content, finished=True))
            return

        raise ValueError("不支持的操作。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the web UI for the programming interview agent.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite database path for sessions and memory.")
    return parser.parse_args()


def main() -> int:
    global DEFAULT_DB_PATH
    args = parse_args()
    DEFAULT_DB_PATH = args.db
    repo.configure_db(args.db)
    repo.init_db()
    server = ThreadingHTTPServer((args.host, args.port), InterviewWebHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Programming Interview Agent Web UI is running at {url}")
    print(f"SQLite knowledge base: {DEFAULT_DB_PATH}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
