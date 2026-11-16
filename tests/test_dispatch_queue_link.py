"""Tests for staff run ↔ dispatch overlap session binding (#4272)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from dispatch_queue_link import (  # noqa: E402
    bind_run_session,
    release_run_session,
    reset_dispatch_queue_link_state,
)
from dispatch_routing import dispatch_queue_manager  # noqa: E402


def test_bind_and_release_drains_overlap_queue() -> None:
    reset_dispatch_queue_link_state()
    first = dispatch_queue_manager.try_dispatch(repository="org/repo", number=1, prompt="touch `backend/a.py`")
    assert first.session is not None
    blocked = dispatch_queue_manager.try_dispatch(repository="org/repo", number=2, prompt="also `backend/a.py`")
    assert blocked.dispatched is False

    bind_run_session("run-1", first.session.session_id)
    drained = release_run_session("run-1", outcome="succeeded")
    assert len(drained) == 1
    assert drained[0].number == 2


def test_reset_clears_run_bindings() -> None:
    reset_dispatch_queue_link_state()
    bind_run_session("run-x", "sess-y")
    reset_dispatch_queue_link_state()
    assert release_run_session("run-x") == []
