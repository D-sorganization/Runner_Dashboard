"""Provider-derived fleet identity for agent-bus hooks (#2982)."""

from __future__ import annotations

import json

import pytest

from fleet_provider_identity import infer_fleet_agent, infer_fleet_session, infer_provider_label


@pytest.mark.unit
def test_infer_agent_from_claude_code_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_AGENT", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert infer_fleet_agent() == "claude"


@pytest.mark.unit
def test_infer_agent_from_codex_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_AGENT", raising=False)
    monkeypatch.setenv("CODEX_HOME", "/home/user/.codex")
    assert infer_fleet_agent() == "codex"


@pytest.mark.unit
def test_infer_session_from_conversation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_SESSION", raising=False)
    payload = json.dumps({"conversationId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"})
    session = infer_fleet_session(agent="agy", hook_stdin=payload)
    assert session.startswith("agy-")
    assert "-conv-" in session


@pytest.mark.unit
def test_explicit_fleet_agent_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_AGENT", "codex")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert infer_fleet_agent() == "codex"
    assert infer_provider_label() == "codex"
