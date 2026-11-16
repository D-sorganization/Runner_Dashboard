"""Answer evidence must match a supported, successful provider frame exactly."""

from __future__ import annotations

from typing import Any

import pytest
from staff.lesson_evidence import has_supported_assistant_answer


@pytest.fixture
def answer_frames() -> dict[str, dict[str, Any]]:
    return {
        "claude": {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Answer"}]},
        },
        "codex": {"type": "item.completed", "item": {"type": "agent_message", "text": "Answer"}},
        "antigravity": {"event": "result", "result": {"status": "SUCCESS", "response": "Answer"}},
        "cursor-agent": {"type": "result", "subtype": "success", "result": "Answer"},
    }


@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity", "cursor-agent"])
def test_accepts_exact_answer_only(provider: str, answer_frames: dict[str, dict[str, Any]]) -> None:
    frame = answer_frames[provider]
    assert has_supported_assistant_answer(provider, frame, "Answer")
    for text in ("", " \n", "answer", "Answer ", "prefix Answer"):
        assert not has_supported_assistant_answer(provider, frame, text)


@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity", "cursor-agent"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("is_error", True),
        ("is_error", None),
        ("is_error", 0),
        ("error", "provider failed"),
        ("error", {"message": "provider failed"}),
        ("status", "ERROR"),
        ("status", "Failed"),
        ("status", "failure"),
    ],
)
def test_error_metadata_overrides_valid_answer(
    provider: str, field: str, value: Any, answer_frames: dict[str, dict[str, Any]]
) -> None:
    frame = {**answer_frames[provider], field: value}
    assert not has_supported_assistant_answer(provider, frame, "Answer")


@pytest.mark.parametrize("error", [None, False, ""])
@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity", "cursor-agent"])
def test_explicit_non_error_metadata_is_accepted(
    provider: str, error: Any, answer_frames: dict[str, dict[str, Any]]
) -> None:
    frame = {**answer_frames[provider], "is_error": False, "error": error}
    assert has_supported_assistant_answer(provider, frame, "Answer")


@pytest.mark.parametrize(
    "message",
    [
        None,
        [],
        {},
        {"role": "user", "content": [{"type": "text", "text": "Answer"}]},
        {"content": []},
        {"content": "Answer"},
        {"content": ["Answer"]},
        {"content": [{"type": "tool_use", "text": "Answer"}]},
        {"content": [{"type": "text", "text": 123}]},
    ],
)
def test_claude_rejects_malformed_or_non_assistant_content(message: Any) -> None:
    assert not has_supported_assistant_answer("claude", {"type": "assistant", "message": message}, "Answer")


def test_claude_joins_text_blocks_in_order_and_allows_absent_role() -> None:
    frame = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "First"},
                {"type": "text", "text": "Second"},
            ]
        },
    }
    assert has_supported_assistant_answer("claude", frame, "First\nSecond")
    assert not has_supported_assistant_answer("claude", frame, "Second\nFirst")


@pytest.mark.parametrize(
    ("provider", "frame"),
    [
        ("claude", {"type": "result", "message": {"content": [{"type": "text", "text": "Answer"}]}}),
        ("codex", {"type": "item.started", "item": {"type": "agent_message", "text": "Answer"}}),
        ("codex", {"type": "item.completed", "item": {"type": "command_execution", "text": "Answer"}}),
        ("codex", {"type": "item.completed", "item": None}),
        ("codex", {"type": "item.completed", "item": {"type": "agent_message", "text": 123}}),
        ("antigravity", {"event": "progress", "result": {"status": "SUCCESS", "response": "Answer"}}),
        ("antigravity", {"event": "result", "result": {"status": "FAILED", "response": "Answer"}}),
        ("antigravity", {"event": "result", "result": None}),
        ("antigravity", {"event": "result", "result": {"status": "SUCCESS", "response": 123}}),
        ("cursor-agent", {"type": "assistant", "subtype": "success", "result": "Answer"}),
        ("cursor-agent", {"type": "result", "subtype": "error", "result": "Answer"}),
        ("cursor-agent", {"type": "result", "subtype": "success", "result": {"text": "Answer"}}),
    ],
)
def test_rejects_wrong_event_or_payload_shape(provider: str, frame: dict[str, Any]) -> None:
    assert not has_supported_assistant_answer(provider, frame, "Answer")


def test_unknown_provider_cannot_borrow_another_provider_frame(answer_frames: dict[str, dict[str, Any]]) -> None:
    assert not has_supported_assistant_answer("unknown", answer_frames["codex"], "Answer")
