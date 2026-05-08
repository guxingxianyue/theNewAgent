"""Evaluation parsing helpers."""

from __future__ import annotations

import json
import re
from typing import Any


def extract_evaluation_metadata(content: str) -> dict[str, Any]:
    match = re.search(r"```evaluation_json\s*(\{.*?\})\s*```", content, re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def metadata_score(metadata: dict[str, Any]) -> float | None:
    value = metadata.get("score")
    try:
        score = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if score is None:
        return None
    return max(0.0, min(100.0, score))


def metadata_tags(metadata: dict[str, Any]) -> list[str]:
    tags = metadata.get("tags")
    if not isinstance(tags, list):
        return []
    return [str(item)[:80] for item in tags[:12]]
