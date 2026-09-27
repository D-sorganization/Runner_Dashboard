"""Unit tests for staff chat scratch directory management and periodic sweep (#1688)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from staff import chat_scratch, cli_projects
from staff.chat_scratch import (
    SWEEP_INTERVAL_SECONDS,
    maybe_sweep_idle_chat_scratch,
    thread_scratch_dir,
)


@pytest.fixture(autouse=True)
def reset_last_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the throttle and stub the real sweep, so no test touches the node's temp dir."""
    monkeypatch.setattr(chat_scratch, "_last_sweep", 0.0)
    monkeypatch.setattr(cli_projects, "sweep_idle_chat_scratch", lambda *_a, **_k: [])


@pytest.mark.unit
def test_maybe_sweep_calls_sweep_once_and_throttles_within_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """maybe_sweep_idle_chat_scratch calls sweep once and returns [] within SWEEP_INTERVAL_SECONDS."""
    call_count = 0

    def mock_sweep(env: Any, **kwargs: Any) -> list[str]:
        nonlocal call_count
        call_count += 1
        return ["staff_chat_swept"]

    monkeypatch.setattr(cli_projects, "sweep_idle_chat_scratch", mock_sweep)

    base_time = 1_000_000.0
    # First call: triggers sweep
    res1 = maybe_sweep_idle_chat_scratch({}, now=base_time)
    assert res1 == ["staff_chat_swept"]
    assert call_count == 1

    # Second call within interval (e.g. 5 minutes later): throttled, returns []
    res2 = maybe_sweep_idle_chat_scratch({}, now=base_time + 300.0)
    assert res2 == []
    assert call_count == 1

    # Third call after interval (SWEEP_INTERVAL_SECONDS + 1): triggers sweep again
    res3 = maybe_sweep_idle_chat_scratch({}, now=base_time + SWEEP_INTERVAL_SECONDS + 1.0)
    assert res3 == ["staff_chat_swept"]
    assert call_count == 2


@pytest.mark.unit
def test_maybe_sweep_swallows_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exception inside sweep_idle_chat_scratch is swallowed and returns []."""

    def mock_raise(env: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("simulated disk error during sweep")

    monkeypatch.setattr(cli_projects, "sweep_idle_chat_scratch", mock_raise)

    res = maybe_sweep_idle_chat_scratch({}, now=1_000_000.0)
    assert res == []


@pytest.mark.unit
def test_thread_scratch_dir_returns_dir_when_sweep_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """thread_scratch_dir succeeds even if maybe_sweep_idle_chat_scratch raises an exception."""

    def mock_broken_sweep(*args: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("unexpected catastrophic sweep crash")

    monkeypatch.setattr(chat_scratch, "maybe_sweep_idle_chat_scratch", mock_broken_sweep)

    scratch = thread_scratch_dir("test-thread-err-fallback")
    assert scratch
    p = Path(scratch)
    assert p.is_dir()
    assert "staff_chat_" in p.name


@pytest.mark.unit
def test_thread_scratch_dir_creates_and_returns_stable_dir() -> None:
    """thread_scratch_dir creates directory and returns stable path across calls."""
    dir1 = thread_scratch_dir("thread_xyz_123")
    dir2 = thread_scratch_dir("thread_xyz_123")
    assert dir1 == dir2
    assert Path(dir1).is_dir()


@pytest.mark.unit
def test_thread_scratch_dir_requires_non_empty_id() -> None:
    """thread_scratch_dir asserts thread_id is non-empty."""
    with pytest.raises(AssertionError):
        thread_scratch_dir("")
    with pytest.raises(AssertionError):
        thread_scratch_dir("   ")


@pytest.mark.unit
def test_thread_scratch_dir_marks_the_thread_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1688: opening a thread touches its dir so the idle sweep skips it."""
    import os
    import time

    monkeypatch.setattr(chat_scratch, "maybe_sweep_idle_chat_scratch", lambda *a, **k: [])
    path = Path(thread_scratch_dir("th-touch-1688"))
    os.utime(path, (1.0, 1.0))
    before = time.time() - 5
    assert Path(thread_scratch_dir("th-touch-1688")).stat().st_mtime >= before
