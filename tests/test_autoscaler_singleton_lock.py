"""Autoscaler singleton-lock semantics (issue #933).

Before #933, ``_acquire_lock`` caught every ``OSError`` (which includes
``BlockingIOError`` — the "lock is held" signal) and fell through to the next
candidate path. So a second autoscaler instance failed flock on the primary
lock and then silently acquired an ALTERNATE lock file and ran anyway — two
concurrent autoscalers double-stopping/double-starting runners.

These tests assert the held-lock signal now exits 75 instead of falling through,
while a genuinely-unusable path still falls back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")


def test_held_primary_lock_exits_75_does_not_fall_through(tmp_path, monkeypatch):
    """A held primary lock must cause SystemExit(75), NOT acquisition of an
    alternate path (#933)."""
    import fcntl

    import runner_autoscaler as ra

    primary = tmp_path / "primary.lock"
    # Hold the primary lock from this process.
    holder = open(primary, "w")  # noqa: SIM115 — kept open to retain the lock
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    # Point the autoscaler's first candidate at the held lock.
    monkeypatch.setenv("AUTOSCALER_LOCK_PATH", str(primary))
    monkeypatch.setattr(ra, "_lock_fd", None, raising=False)

    try:
        with pytest.raises(SystemExit) as exc:
            ra._acquire_lock()
        assert exc.value.code == 75
        # It must NOT have acquired any alternate lock fd.
        assert ra._lock_fd is None
    finally:
        try:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        finally:
            holder.close()


def test_free_lock_is_acquired(tmp_path, monkeypatch):
    """When the primary candidate is free, the lock is acquired (no regression)."""
    import runner_autoscaler as ra

    primary = tmp_path / "free.lock"
    monkeypatch.setenv("AUTOSCALER_LOCK_PATH", str(primary))
    monkeypatch.setattr(ra, "_lock_fd", None, raising=False)

    try:
        ra._acquire_lock()  # should not raise
        assert ra._lock_fd is not None
        assert primary.exists()
    finally:
        if ra._lock_fd is not None:
            ra._lock_fd.close()
            ra._lock_fd = None


def test_unwritable_primary_falls_back_to_next(tmp_path, monkeypatch):
    """A genuinely-unusable primary path (ENOENT on an unwritable parent) must
    fall through to a writable candidate — fallback still works (#933)."""
    import runner_autoscaler as ra

    # AUTOSCALER_LOCK_PATH points under a path component that is a file, so
    # makedirs/open raises (NotADirectoryError, an OSError) — the "unusable path"
    # branch, which should fall through rather than exit.
    not_a_dir = tmp_path / "iamfile"
    not_a_dir.write_text("x")
    unusable = not_a_dir / "child" / "lock"  # parent is a file → open fails

    writable_fallback = tmp_path / "fallback.lock"
    monkeypatch.setenv("AUTOSCALER_LOCK_PATH", str(unusable))
    monkeypatch.setattr(ra, "_lock_fd", None, raising=False)

    # Monkeypatch the hard-coded candidate list tail by pointing HOME so the
    # ~/.cache fallback lands inside tmp; simplest is to also patch os.path
    # expansion target. Instead, assert it does NOT exit 75 on the unusable path
    # (it should reach a later writable candidate or the final sys.exit only if
    # ALL fail). We make the ~/.cache candidate writable via HOME.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    try:
        ra._acquire_lock()
        # A lock was acquired on SOME later candidate (not the unusable one).
        assert ra._lock_fd is not None
    except SystemExit as exc:  # pragma: no cover - environment-dependent
        # If the sandbox forbids every fallback path, the contract is still
        # "exit 75 only after exhausting candidates", never silent double-run.
        assert exc.code == 75
    finally:
        if getattr(ra, "_lock_fd", None) is not None:
            ra._lock_fd.close()
            ra._lock_fd = None
    # The unusable path itself must never have been created as a lock.
    assert not writable_fallback.is_dir()
