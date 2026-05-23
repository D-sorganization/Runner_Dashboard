"""Tests for backend/assistant_contract.py — issue #386."""

from __future__ import annotations

import assistant_contract as ac
import pytest
from pydantic import ValidationError


def test_assistant_context_required_fields() -> None:
    ctx = ac.AssistantContext(current_tab="overview")
    assert ctx.current_tab == "overview"
    assert ctx.selected_run_id is None
    assert ctx.selected_items is None


def test_assistant_chat_request_valid() -> None:
    ctx = ac.AssistantContext(current_tab="fleet")
    req = ac.AssistantChatRequest(prompt="What is wrong?", context=ctx)
    assert req.prompt == "What is wrong?"
    assert req.tools_enabled is False


def test_assistant_chat_request_empty_prompt_raises() -> None:
    ctx = ac.AssistantContext(current_tab="fleet")
    with pytest.raises(ValidationError):
        ac.AssistantChatRequest(prompt="", context=ctx)


def test_assistant_chat_response_fields() -> None:
    resp = ac.AssistantChatResponse(
        response="All good.",
        provider="anthropic",
        context_used={},
        timestamp="2026-05-01T00:00:00Z",
    )
    assert resp.provider == "anthropic"
    assert resp.timestamp == "2026-05-01T00:00:00Z"


def test_tool_call_card_requires_confirmation_flag() -> None:
    card = ac.ToolCallCard(
        id="tool-1",
        name="cancel_run",
        input={"run_id": 42},
        requires_confirmation=True,
    )
    assert card.requires_confirmation is True
