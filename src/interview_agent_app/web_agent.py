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
import json
import sys
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import evaluation as eval_parser
import repository as repo
import web_sessions
import web_ui
from app_config import (
    ALLOW_REMOTE,
    DEFAULT_DB_PATH,
    MAX_JSON_BODY_BYTES,
    interview_type_for,
)
from interview_agent import InterviewAgent, LLMError

SESSIONS: dict[str, dict[str, Any]] = {}
SESSIONS_LOCK = threading.Lock()


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


class InterviewWebHandler(BaseHTTPRequestHandler):
    server_version = "InterviewAgentWeb/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:
        if not ALLOW_REMOTE and not is_local_request(self):
            self.send_error(HTTPStatus.FORBIDDEN, "Remote access is disabled.")
            return
        path = urlparse(self.path).path

        if path.startswith("/static/"):
            try:
                data, content_type = web_ui.read_static_asset(path)
            except FileNotFoundError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
            return

        if path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        data = web_ui.build_index_html()
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
        session = web_sessions.create_session(payload)
        with SESSIONS_LOCK:
            SESSIONS[session["id"]] = session
        response = web_sessions.session_response(session, "第 1 题", session["last_problem"])
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
                **web_sessions.llm_event_kwargs(session),
            )
            json_response(self, HTTPStatus.OK, web_sessions.session_response(session, "评估与追问", content))
            return

        if action == "hint":
            if session["mode"] == "mock":
                raise ValueError("模拟面试模式下不能使用提示。")
            content = agent.ask("请给我一个渐进式提示，不要直接给完整答案。")
            repo.record_event(session["id"], "assistant", "hint", content, **web_sessions.llm_event_kwargs(session))
            json_response(self, HTTPStatus.OK, web_sessions.session_response(session, "提示", content))
            return

        if action == "answer":
            if session["mode"] == "mock":
                raise ValueError("模拟面试模式下不能查看参考答案。")
            content = agent.ask("请给出参考答案、复杂度分析和这门语言的实现注意点。")
            repo.record_event(session["id"], "assistant", "reference_answer", content, **web_sessions.llm_event_kwargs(session))
            json_response(self, HTTPStatus.OK, web_sessions.session_response(session, "参考答案", content))
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
                repo.record_event(session["id"], "assistant", "summary", content, **web_sessions.llm_event_kwargs(session))
                repo.finish_session(session["id"], content)
                try:
                    memory = agent.ask(web_sessions.build_memory_prompt(session))
                except LLMError:
                    memory = content[:500]
                repo.add_memory(session, memory)
                repo.record_event(session["id"], "assistant", "memory", memory, **web_sessions.llm_event_kwargs(session))
                with SESSIONS_LOCK:
                    SESSIONS.pop(session_id, None)
                title = "模拟面试报告" if session["mode"] == "mock" else "训练总结"
                json_response(self, HTTPStatus.OK, web_sessions.session_response(session, title, content, finished=True))
                return
            session["round"] += 1
            content = agent.new_problem(session["round"])
            session["last_problem"] = content
            repo.record_event(session["id"], "assistant", "problem", content, **web_sessions.llm_event_kwargs(session))
            json_response(self, HTTPStatus.OK, web_sessions.session_response(session, f'第 {session["round"]} 题', content))
            return

        if action == "reset":
            with SESSIONS_LOCK:
                SESSIONS.pop(session_id, None)
            content = "已结束当前训练。你可以调整语言、难度或模型参数后重新开始。"
            repo.finish_session(session["id"], content)
            repo.record_event(session["id"], "system", "reset", content, round_index=session["round"])
            json_response(self, HTTPStatus.OK, web_sessions.session_response(session, "训练结束", content, finished=True))
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
