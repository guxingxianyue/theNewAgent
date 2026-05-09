"""Central configuration and product constants for the interview agent."""

from __future__ import annotations

import os


DEFAULT_BASE_URL = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
DEFAULT_MODEL = os.getenv("LLM_MODEL") or "gpt-4o-mini"
DEFAULT_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
DEFAULT_DB_PATH = os.getenv("INTERVIEW_AGENT_DB") or os.path.join("data", "interview_memory.sqlite3")

INTERVIEW_TARGETS = ["Python", "Java", "AI Agent"]
TRAINING_MODES = ["practice", "mock", "review"]

MAX_JSON_BODY_BYTES = int(os.getenv("INTERVIEW_AGENT_MAX_BODY_BYTES") or str(512 * 1024))
ALLOW_REMOTE = os.getenv("INTERVIEW_AGENT_ALLOW_REMOTE", "").lower() in {"1", "true", "yes"}

TOPICS_BY_TARGET = {
    "Python": [
        "综合",
        "数组/字符串",
        "哈希表",
        "链表",
        "树/图",
        "动态规划",
        "并发",
        "装饰器/迭代器",
        "性能优化",
    ],
    "Java": [
        "综合",
        "集合",
        "JVM",
        "多线程",
        "Spring",
        "数据库",
        "系统设计",
        "性能调优",
    ],
    "AI Agent": [
        "综合",
        "Agent 架构",
        "Tool Calling",
        "Memory",
        "RAG",
        "Evaluation",
        "Guardrails",
        "部署监控",
        "成本控制",
    ],
}


def interview_type_for(target: str) -> str:
    normalized = target.lower().replace("-", " ").replace("_", " ").strip()
    return "ai_agent" if normalized in {"ai agent", "agent", "ai"} else "programming"


def normalize_mode(value: str) -> str:
    return value if value in TRAINING_MODES else "practice"


def topic_options_for(target: str) -> list[str]:
    return TOPICS_BY_TARGET.get(target, ["综合"])
