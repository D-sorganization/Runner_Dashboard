"""Boot-time session tailer loop (#4278 J-09)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import activity_session_tailer_runtime as runtime
import pytest


@pytest.fixture(autouse=True)
def _reset_session_tailer_loop_task() -> None:
    runtime._reset_session_tailer_loop_task_for_tests()
    yield
    runtime._reset_session_tailer_loop_task_for_tests()


@pytest.mark.asyncio
async def test_start_session_tailer_loop_is_noop_when_both_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUNNER_DASHBOARD_CLAUDE_TAILER_ENABLED", raising=False)
    monkeypatch.delenv("RUNNER_DASHBOARD_CODEX_TAILER_ENABLED", raising=False)
    assert runtime.start_session_tailer_loop(node="node-a") is None


@pytest.mark.asyncio
async def test_start_session_tailer_loop_invokes_pass_once_before_first_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_CLAUDE_TAILER_ENABLED", "1")
    calls: list[str] = []
    slept = asyncio.Event()

    def fake_pass(*, node: str) -> dict[str, object]:
        calls.append(node)
        return {}

    async def fake_sleep(_interval: float) -> None:
        slept.set()
        await asyncio.Future()

    monkeypatch.setattr(runtime, "run_session_tailer_pass", fake_pass)
    monkeypatch.setattr(runtime.asyncio, "sleep", fake_sleep)

    task = runtime.start_session_tailer_loop(node="node-a")
    assert task is not None
    await asyncio.wait_for(slept.wait(), timeout=2.0)
    assert calls == ["node-a"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_start_session_tailer_loop_is_idempotent_while_task_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_CLAUDE_TAILER_ENABLED", "1")
    slept = asyncio.Event()

    async def fake_sleep(_interval: float) -> None:
        slept.set()
        await asyncio.Future()

    monkeypatch.setattr(runtime, "run_session_tailer_pass", lambda *, node: {})
    monkeypatch.setattr(runtime.asyncio, "sleep", fake_sleep)

    first = runtime.start_session_tailer_loop(node="node-a")
    second = runtime.start_session_tailer_loop(node="node-a")
    assert first is not None
    assert second is first
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first


def test_run_session_tailer_pass_continues_when_claude_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_CLAUDE_TAILER_ENABLED", "1")
    monkeypatch.setenv("RUNNER_DASHBOARD_CODEX_TAILER_ENABLED", "1")

    claude_tailer = MagicMock()
    claude_tailer.tail_once.side_effect = RuntimeError("claude exploded")
    codex_tailer = MagicMock()
    codex_tailer.tail_once.return_value = {"codex": True}

    monkeypatch.setattr(runtime, "ClaudeSessionTailer", lambda **_: claude_tailer)
    monkeypatch.setattr(runtime, "CodexSessionTailer", lambda **_: codex_tailer)

    env = {
        "RUNNER_DASHBOARD_CLAUDE_TAILER_ENABLED": "1",
        "RUNNER_DASHBOARD_CODEX_TAILER_ENABLED": "1",
    }
    out = runtime.run_session_tailer_pass(node="node-a", env=env)

    assert "claude" not in out
    assert out["codex"] == {"codex": True}
    codex_tailer.tail_once.assert_called_once()
