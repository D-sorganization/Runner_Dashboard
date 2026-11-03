"""Codex-backed chats use current read-only CLI and structured stream contracts (#3595)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from staff.adapters import get_adapter
from staff.chat_streaming import LiveProcessReader, stream_turn_output


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["codex", "ollama"])
@pytest.mark.parametrize("session", [None, "native-thread"], ids=["startup", "resume"])
def test_codex_chat_command_contract(provider: str, session: str | None) -> None:
    adapter = get_adapter(provider)
    argv = adapter.chat_argv("diagnostic prompt", "/scratch", model="test-model", session_id=session)
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--json" in argv and adapter.chat_json is True
    assert "--skip-git-repo-check" in argv
    assert argv[argv.index("--model") + 1] == "test-model"
    assert argv[-1] == "diagnostic prompt"
    assert "--session" not in argv
    if session:
        assert argv.index("--sandbox") < argv.index("resume") < argv.index(session)
    else:
        assert "resume" not in argv
    if provider == "ollama":
        assert argv[argv.index("--local-provider") + 1] == "ollama"
        assert "--oss" in argv


class FixtureProcess:
    stderr = iter(())
    returncode = 0

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.stdout = iter(json.dumps(event) + "\n" for event in events)

    def poll(self) -> int:
        return 0

    def wait(self) -> int:
        return 0


class TokenBus:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def publish_token(self, thread_id: str, message_id: str, delta: str) -> None:
        self.tokens.append(delta)


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["codex", "ollama"])
def test_structured_chat_keeps_native_thread_and_only_assistant_output(provider: str) -> None:
    answer = "Answer with line breaks.\nSession: prose-is-not-the-thread"
    events = [
        {"type": "thread.started", "thread_id": "native-thread"},
        {"type": "item.completed", "item": {"type": "command_execution", "aggregated_output": "tool noise"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": answer}},
        {"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 3}},
    ]
    bus = TokenBus()
    result = asyncio.run(
        stream_turn_output(
            LiveProcessReader(FixtureProcess(events)), get_adapter(provider), bus, "thread-record", "reply-record"
        )
    )
    assert result.json_lines is True
    assert result.returncode == 0
    assert result.reply_text == answer
    assert result.session_id == "native-thread"
    assert bus.tokens == [answer]
