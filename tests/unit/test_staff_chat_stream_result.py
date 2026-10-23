"""A chat reply is the provider's text once, not the streamed text plus the final result (#1341).

``claude -p --output-format stream-json`` (and cursor-agent) stream the reply in
``assistant`` events and then repeat all of it in the terminal ``result`` event.
Joining every event's text doubled every reply ("pongpong"); the Staff Console
e2e suite found it on its first turn.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from staff.adapters import get_adapter
from staff.chat_streaming import LiveProcessReader, stream_turn_output


class _Proc:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = iter(lines)
        self.stderr = iter(())
        self.returncode = 0

    def poll(self) -> int:
        return 0

    def wait(self) -> int:
        return 0


class _Bus:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def publish_token(self, thread_id: str, message_id: str, delta: str) -> None:
        self.tokens.append(delta)


def _jsonl(*events: dict[str, Any]) -> list[str]:
    return [json.dumps(e) + "\n" for e in events]


def _run(provider: str, lines: list[str], *, hide_memory_summary: bool = False) -> tuple[Any, _Bus]:
    bus = _Bus()
    out = asyncio.run(
        stream_turn_output(
            LiveProcessReader(_Proc(lines)),
            get_adapter(provider),
            bus,
            "th_1",
            "msg_1",
            hide_memory_summary=hide_memory_summary,
        )
    )
    return out, bus


def _assistant(text: str) -> dict[str, Any]:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}, "session_id": "s1"}


@pytest.mark.unit
def test_streamed_reply_is_not_repeated_by_the_result_event() -> None:
    lines = _jsonl(
        {"type": "system", "subtype": "init", "session_id": "s1"},
        _assistant("po"),
        _assistant("ng"),
        {"type": "result", "subtype": "success", "result": "pong", "session_id": "s1"},
    )
    out, bus = _run("claude", lines)

    assert out.reply_text == "pong"
    assert bus.tokens == ["po", "ng"]
    assert out.session_id == "s1"


@pytest.mark.unit
def test_a_result_only_stream_still_streams_and_replies_once() -> None:
    out, bus = _run("claude", _jsonl({"type": "result", "subtype": "success", "result": "pong", "session_id": "s1"}))

    assert out.reply_text == "pong"
    assert bus.tokens == ["pong"]


@pytest.mark.unit
def test_plain_text_providers_keep_every_line() -> None:
    out, bus = _run("codex", ["first line\n", "second line\n"])

    assert out.reply_text == "first line\nsecond line\n"
    assert bus.tokens == ["first line", "second line"]


@pytest.mark.unit
def test_opt_in_summary_capsule_is_hidden_from_sse_across_delta_boundaries() -> None:
    capsule = '```staff-memory\n{"summary":"private digest","decisions":[],"open_items":[]}\n```'
    answer = "The task is complete.\n" + capsule
    pieces = [answer[:23], answer[23:29], answer[29:41], answer[41:]]
    out, bus = _run("claude", _jsonl(*(_assistant(piece) for piece in pieces)), hide_memory_summary=True)

    assert out.reply_text == answer
    assert "The task is complete.\n" == "".join(bus.tokens)
    assert all("staff-memory" not in token and "private digest" not in token for token in bus.tokens)


@pytest.mark.unit
def test_opt_in_summary_capsule_filter_does_not_hide_fenced_examples() -> None:
    answer = 'Example:\r\n```\r\n```staff-memory\r\n{"summary":"example"}\r\n```\r\n'
    out, bus = _run("claude", _jsonl(_assistant(answer)), hide_memory_summary=True)

    assert out.reply_text == answer
    assert "".join(bus.tokens) == answer


@pytest.mark.unit
def test_summary_capsule_filter_is_opt_in_and_keeps_unrecognized_trailing_text() -> None:
    answer = "Answer.\n```staff-memoryX\nvisible text\n"
    default_out, default_bus = _run("claude", _jsonl(_assistant(answer)))
    filtered_out, filtered_bus = _run("claude", _jsonl(_assistant(answer)), hide_memory_summary=True)

    assert default_out.reply_text == answer
    assert "".join(default_bus.tokens) == answer
    assert filtered_out.reply_text == answer
    assert "".join(filtered_bus.tokens) == answer


@pytest.mark.unit
def test_plain_codex_summary_filter_preserves_lines_for_marker_detection() -> None:
    answer = 'Answer.\n```staff-memory\n{"summary":"private digest"}\n```\n'
    out, bus = _run("codex", answer.splitlines(keepends=True), hide_memory_summary=True)
    assert out.reply_text == answer
    assert "".join(bus.tokens) == "Answer.\n"
