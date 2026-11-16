"""Background JSONL session tailers at app boot (#4278 J-09)."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping

from activity_claude_tailer import ClaudeSessionTailer, is_claude_tailer_enabled
from activity_codex_tailer import CodexSessionTailer, is_codex_tailer_enabled

log = logging.getLogger("dashboard.activity_session_tailer_runtime")

_INTERVAL_S = float(os.environ.get("RUNNER_DASHBOARD_SESSION_TAILER_INTERVAL_S", "30"))
_loop_task: asyncio.Task[None] | None = None


def _reset_session_tailer_loop_task_for_tests() -> None:
    """Clear the singleton loop task (unit tests only)."""
    global _loop_task  # noqa: PLW0603
    _loop_task = None


def _any_tailer_enabled(env: Mapping[str, str]) -> bool:
    return is_claude_tailer_enabled(env) or is_codex_tailer_enabled(env)


def run_session_tailer_pass(*, node: str, env: dict[str, str] | None = None) -> dict[str, object]:
    """Run one synchronous tail pass for each enabled provider tailer."""
    resolved = os.environ if env is None else env
    out: dict[str, object] = {}
    if is_claude_tailer_enabled(resolved):
        try:
            out["claude"] = ClaudeSessionTailer(node=node, env=resolved).tail_once()
        except Exception as exc:  # noqa: BLE001
            log.warning("claude session tailer pass failed: %s", exc)
    if is_codex_tailer_enabled(resolved):
        try:
            out["codex"] = CodexSessionTailer(node=node, env=resolved).tail_once()
        except Exception as exc:  # noqa: BLE001
            log.warning("codex session tailer pass failed: %s", exc)
    return out


def start_session_tailer_loop(*, node: str) -> asyncio.Task[None] | None:
    """Start the periodic session tailer loop when any provider tailer is opt-in enabled."""
    global _loop_task  # noqa: PLW0603
    if not _any_tailer_enabled(os.environ):
        log.debug("session tailers disabled; not starting background loop")
        return None

    if _loop_task is not None and not _loop_task.done():
        log.debug("session tailer loop already running; returning existing task")
        return _loop_task

    async def _loop() -> None:
        while True:
            try:
                await asyncio.to_thread(run_session_tailer_pass, node=node)
            except Exception as exc:  # noqa: BLE001
                log.warning("session tailer pass failed: %s", exc)
            await asyncio.sleep(_INTERVAL_S)

    log.info(
        "session tailer loop started (interval=%.1fs node=%s)",
        _INTERVAL_S,
        node,
    )
    _loop_task = asyncio.create_task(_loop())
    return _loop_task
