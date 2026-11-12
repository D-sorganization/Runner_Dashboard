"""Boot-time session tailer loop (#4278 J-09)."""

from __future__ import annotations

import asyncio

import activity_session_tailer_runtime as runtime
import pytest


@pytest.mark.asyncio
async def test_start_session_tailer_loop_is_noop_when_both_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(runtime, "run_session_tailer_pass", fake_pass)
    monkeypatch.setattr(runtime.asyncio, "sleep", fake_sleep)

    task = runtime.start_session_tailer_loop(node="node-a")
    assert task is not None
    await asyncio.wait_for(slept.wait(), timeout=2.0)
    assert calls == ["node-a"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
