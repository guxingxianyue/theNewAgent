"""HTML and static asset rendering for the web interview UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app_config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    TOPICS_BY_TARGET,
    topic_options_for,
)
from interview_agent import DIFFICULTY_CHOICES


PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"
STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


def build_index_html() -> bytes:
    template = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
    difficulty_options = "".join(
        f'<option value="{escape_html(item)}"{" selected" if item == "medium" else ""}>{escape_html(item)}</option>'
        for item in DIFFICULTY_CHOICES
    )
    topic_options = "".join(
        f'<option value="{escape_html(item)}">{escape_html(item)}</option>'
        for item in topic_options_for("Python")
    )
    topics_json = json.dumps(TOPICS_BY_TARGET, ensure_ascii=False)
    html = (
        template.replace("__DEFAULT_BASE_URL__", escape_html(DEFAULT_BASE_URL))
        .replace("__DEFAULT_MODEL__", escape_html(DEFAULT_MODEL))
        .replace("__DIFFICULTY_OPTIONS__", difficulty_options)
        .replace("__TOPIC_OPTIONS__", topic_options)
        .replace("__TOPICS_JSON__", topics_json)
    )
    return html.encode("utf-8")


def read_static_asset(request_path: str) -> tuple[bytes, str]:
    relative = request_path.removeprefix("/static/").strip("/")
    if not relative or "/" in relative or "\\" in relative:
        raise FileNotFoundError(request_path)

    path = STATIC_DIR / relative
    content_type = STATIC_CONTENT_TYPES.get(path.suffix)
    if content_type is None or not path.is_file():
        raise FileNotFoundError(request_path)
    return path.read_bytes(), content_type


def escape_html(value: Any) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
