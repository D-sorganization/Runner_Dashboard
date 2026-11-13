"""Fixture-backed classification of provider assistant-answer output."""

from __future__ import annotations

from typing import Any

LESSON_EVIDENCE_ASSISTANT_ANSWER = "assistant_answer"


def has_supported_assistant_answer(provider_id: str, raw: dict[str, Any], text: str) -> bool:
    """Recognize fixture-backed answer frames and require exact source-field text."""
    if not text.strip() or ("is_error" in raw and raw["is_error"] is not False):
        return False
    if raw.get("error") not in (None, False, ""):
        return False
    status = raw.get("status")
    if isinstance(status, str) and status.lower() in {"error", "failed", "failure"}:
        return False
    if provider_id == "claude":
        message = raw.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        role = message.get("role") if isinstance(message, dict) else None
        if (
            raw.get("type") != "assistant"
            or (role is not None and role != "assistant")
            or not isinstance(content, list)
            or not content
            or not all(
                isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
                for block in content
            )
        ):
            return False
        return text == "\n".join(block["text"] for block in content)
    if provider_id == "codex":
        item = raw.get("item")
        return (
            raw.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
            and text == item["text"]
        )
    if provider_id == "antigravity":
        result = raw.get("result")
        return (
            raw.get("event") == "result"
            and isinstance(result, dict)
            and result.get("status") == "SUCCESS"
            and isinstance(result.get("response"), str)
            and text == result["response"]
        )
    if provider_id == "cursor-agent":
        result = raw.get("result")
        return (
            raw.get("type") == "result"
            and raw.get("subtype") == "success"
            and isinstance(result, str)
            and text == result
        )
    return False
