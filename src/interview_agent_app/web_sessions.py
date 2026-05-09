"""Training session orchestration for the web API."""

from __future__ import annotations

import uuid
from typing import Any

import repository as repo
from app_config import (
    DEFAULT_API_KEY,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    interview_type_for,
    normalize_mode,
)
from interview_agent import DIFFICULTY_CHOICES, InterviewAgent, LLMClient


def create_session(payload: dict[str, Any]) -> dict[str, Any]:
    repo.init_db()
    api_key = _text_value(payload, "api_key") or DEFAULT_API_KEY
    if not api_key:
        raise ValueError("缺少 API key。请在页面填写，或设置 LLM_API_KEY / OPENAI_API_KEY。")

    base_url = _text_value(payload, "base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
    model = _text_value(payload, "model", DEFAULT_MODEL) or DEFAULT_MODEL
    operator = _text_value(payload, "operator", "default") or "default"
    language = _text_value(payload, "language", "Python") or "Python"
    interview_type = interview_type_for(language)
    topic = _text_value(payload, "topic", "综合") or "综合"
    mode = normalize_mode(_text_value(payload, "mode", "practice") or "practice")
    retry_source_session_id = _text_value(payload, "retry_source_session_id") or None
    review_context = _text_value(payload, "review_context")
    if mode == "review" and not review_context:
        review_context = repo.build_review_context(operator, language)
    difficulty = _text_value(payload, "difficulty", "medium") or "medium"
    if difficulty not in DIFFICULTY_CHOICES:
        raise ValueError("难度必须是 easy、medium 或 hard。")

    try:
        rounds = max(1, min(20, int(_text_value(payload, "rounds", "3"))))
    except ValueError as exc:
        raise ValueError("题数必须是数字。") from exc

    try:
        temperature = max(0.0, min(2.0, float(_text_value(payload, "temperature", "0.7"))))
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


def _text_value(payload: dict[str, Any], key: str, default: str = "") -> str:
    value = payload.get(key, default)
    return str(value).strip()
