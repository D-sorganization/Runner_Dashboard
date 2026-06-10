"""Tests for runner_state_cleanup."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import runner_state_cleanup  # noqa: E402
from runner_state_cleanup import (  # noqa: E402
    _STALE_LOCK_AGE_S,
    cleanup_runner_state,
)


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME at a fresh tmp dir for the duration of the test."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Defensively wipe SUDO_USER + /home enumeration drift.
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(runner_state_cleanup, "_candidate_runner_homes", lambda unit: [tmp_path])
    return tmp_path


def test_removes_stale_gitconfig_lock(fake_home: Path) -> None:
    """The canonical case — a leftover ~/.gitconfig.lock is purged."""
    lock = fake_home / ".gitconfig.lock"
    lock.write_text("")
    assert lock.exists()

    summary = cleanup_runner_state("actions.runner.test.host-1.service")

    assert not lock.exists()
    assert summary["home_locks_removed"] == 1
    assert summary["errors"] == 0


def test_removes_gitconfig_lock_dot_zero(fake_home: Path) -> None:
    """Git uses .gitconfig.lock.0 / .1 suffixes when retrying lock acquisition."""
    lock0 = fake_home / ".gitconfig.lock.0"
    lock0.write_text("")

    cleanup_runner_state("actions.runner.test.host-1.service")

    assert not lock0.exists()


def test_no_op_when_no_lock_present(fake_home: Path) -> None:
    """Idempotent: running cleanup on a clean home yields zero removals."""
    summary = cleanup_runner_state("actions.runner.test.host-1.service")
    assert summary["home_locks_removed"] == 0
    assert summary["errors"] == 0


def test_preserves_real_gitconfig(fake_home: Path) -> None:
    """The actual ~/.gitconfig file must never be touched."""
    real = fake_home / ".gitconfig"
    real.write_text("[user]\n\tname = Tester\n")
    cleanup_runner_state("actions.runner.test.host-1.service")
    assert real.exists()
    assert "Tester" in real.read_text()


def test_removes_stale_worktree_locks(fake_home: Path) -> None:
    """Per-worktree .git/index.lock older than threshold is purged."""
    work_git = fake_home / "actions-runners" / "runner-1" / "_work" / "repo" / "repo" / ".git"
    work_git.mkdir(parents=True)
    stale = work_git / "index.lock"
    stale.write_text("")
    # Set mtime to comfortably past the threshold.
    old = time.time() - (_STALE_LOCK_AGE_S + 5)
    os.utime(stale, (old, old))

    summary = cleanup_runner_state("actions.runner.test.host-1.service")

    assert not stale.exists()
    assert summary["worktree_locks_removed"] == 1


def test_skips_fresh_worktree_locks(fake_home: Path) -> None:
    """A freshly-created lock (mid-job) is NOT removed — would race active git."""
    work_git = fake_home / "actions-runners" / "runner-1" / "_work" / "repo" / "repo" / ".git"
    work_git.mkdir(parents=True)
    fresh = work_git / "index.lock"
    fresh.write_text("")

    summary = cleanup_runner_state("actions.runner.test.host-1.service")

    assert fresh.exists(), "fresh lock removed — would race active git operations"
    assert summary["worktree_locks_removed"] == 0
    assert summary["fresh_locks_skipped"] == 1


def test_never_raises_on_oserror(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cleanup must be best-effort — OSError during unlink is logged, not raised."""
    lock = fake_home / ".gitconfig.lock"
    lock.write_text("")

    def _boom(self: Path) -> None:  # type: ignore[no-untyped-def]
        raise OSError("simulated permission denied")

    monkeypatch.setattr(Path, "unlink", _boom)

    # Should NOT raise.
    summary = cleanup_runner_state("actions.runner.test.host-1.service")
    assert summary["errors"] >= 1
