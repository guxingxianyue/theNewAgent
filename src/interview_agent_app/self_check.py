"""Lightweight self-checks that do not call any LLM API."""

from __future__ import annotations

import os
import tempfile
import uuid

import evaluation
import repository
from app_config import interview_type_for, normalize_mode, topic_options_for


def check_config() -> None:
    assert interview_type_for("AI Agent") == "ai_agent"
    assert interview_type_for("Python") == "programming"
    assert normalize_mode("mock") == "mock"
    assert normalize_mode("unknown") == "practice"
    assert "综合" in topic_options_for("Python")


def check_evaluation_parser() -> None:
    content = """
    评估文本
    ```evaluation_json
    {"score": 82, "tags": ["dp", "edge_cases"], "is_wrong_question": true}
    ```
    """
    metadata = evaluation.extract_evaluation_metadata(content)
    assert evaluation.metadata_score(metadata) == 82.0
    assert evaluation.metadata_tags(metadata) == ["dp", "edge_cases"]
    assert evaluation.extract_evaluation_metadata("no json") == {}


def check_repository() -> None:
    assert "CREATE TABLE IF NOT EXISTS sessions" in repository.read_sql_file("001_init.sql")
    assert "ALTER TABLE events ADD COLUMN score" in repository.read_sql_file("002_compat_columns.sql")

    with tempfile.TemporaryDirectory() as tmp:
        db_file = os.path.join(tmp, "check.sqlite3")
        repository.configure_db(db_file)
        repository.init_db()

        session = {
            "id": uuid.uuid4().hex,
            "operator": "tester",
            "interview_type": "programming",
            "language": "Python",
            "difficulty": "medium",
            "model": "mock-model",
            "topic": "动态规划",
            "mode": "practice",
            "retry_source_session_id": None,
        }
        repository.record_session(session)
        repository.record_event(
            session["id"],
            "assistant",
            "evaluation",
            "ok",
            round_index=1,
            score=75,
            tags=["dp"],
            latency_ms=123,
            model="mock-model",
            token_usage={"total_tokens": 10},
            metadata={"score": 75},
        )
        repository.add_memory(session, "候选人动态规划需要加强。")
        repository.finish_session(session["id"], "summary")

        sessions = repository.list_sessions("tester")
        assert len(sessions) == 1
        assert sessions[0]["avg_score"] == 75.0
        detail = repository.session_detail(session["id"])
        assert detail["session"]["topic"] == "动态规划"
        assert len(detail["events"]) == 1
        memory = repository.load_memory("tester", "programming", "Python")
        assert "动态规划" in memory
        progress = repository.progress_stats("tester")
        assert progress["overview"]["evaluation_count"] == 1
        assert progress["overview"]["avg_score"] == 75.0
        wrong = repository.wrong_questions("tester", "Python")
        assert wrong == []

        repository.record_event(
            session["id"],
            "assistant",
            "evaluation",
            "weak",
            round_index=2,
            score=45,
            tags=["edge_cases"],
            metadata={"score": 45},
        )
        wrong = repository.wrong_questions("tester", "Python")
        assert len(wrong) == 1
        assert wrong[0]["score"] == 45.0


def main() -> int:
    check_config()
    check_evaluation_parser()
    check_repository()
    print("self_check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
