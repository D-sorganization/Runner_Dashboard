"""Guard the runner_ops -> runner_stream move (#2803): importers keep working unchanged."""

from __future__ import annotations

import pytest
from staff import runner_ops, runner_stream

MOVED = (
    "RESULT_NUDGE_PROMPT",
    "PumpResult",
    "execute_retry_nudge",
    "extract_transcript_question",
    "pump_nudge_output",
    "pump_output",
)


@pytest.mark.parametrize("name", MOVED)
def test_moved_name_resolves_from_both_modules(name: str) -> None:
    assert getattr(runner_ops, name) is getattr(runner_stream, name)


def test_retry_nudge_without_session_is_a_noop() -> None:
    result = runner_stream.execute_retry_nudge(
        rec=None,
        adapter=None,
        plan=None,
        role=None,
        workdir=None,
        env={},
        session_id=None,  # type: ignore[arg-type]
        transcript=None,
        store=None,
        wall_clock_timeout=1,
        idle_timeout=1,
        lock=None,
        procs={},
        cancel_flags=set(),  # type: ignore[arg-type]
    )
    assert result == ("", {}, 0)
