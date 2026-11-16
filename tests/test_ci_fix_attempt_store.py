"""Tests for backend/ci_fix_attempt_store.py (per-PR and per-head caps, #4273)."""

from __future__ import annotations

from pathlib import Path

from ci_fix_attempt_store import CIFixAttemptStore, DEFAULT_PER_HEAD_CAP, DEFAULT_PER_PR_CAP


def test_reserve_attempt_enforces_per_head_cap(tmp_path: Path) -> None:
    store = CIFixAttemptStore(tmp_path / "attempts.db")
    for _ in range(DEFAULT_PER_HEAD_CAP):
        assert store.reserve_attempt("Runner_Dashboard", 1, "deadbeef").allowed
    blocked = store.reserve_attempt("Runner_Dashboard", 1, "deadbeef")
    assert not blocked.allowed
    assert "per-head" in blocked.reason


def test_release_reserved_attempt_by_reservation_id(tmp_path: Path) -> None:
    store = CIFixAttemptStore(tmp_path / "attempts.db")
    cap = store.reserve_attempt("Runner_Dashboard", 3, "abc123")
    assert cap.allowed
    assert cap.reservation_id is not None
    assert store.counts("Runner_Dashboard", 3, "abc123") == (1, 1)
    assert store.release_reserved_attempt(cap.reservation_id)
    assert store.counts("Runner_Dashboard", 3, "abc123") == (0, 0)
    assert not store.release_reserved_attempt(cap.reservation_id)


def test_release_reserved_attempt_only_removes_own_reservation(tmp_path: Path) -> None:
    store = CIFixAttemptStore(tmp_path / "attempts.db")
    first = store.reserve_attempt("Runner_Dashboard", 3, "abc123")
    second = store.reserve_attempt("Runner_Dashboard", 3, "abc123")
    assert first.allowed and second.allowed
    assert first.reservation_id is not None and second.reservation_id is not None
    assert first.reservation_id != second.reservation_id
    assert store.counts("Runner_Dashboard", 3, "abc123") == (2, 2)
    assert store.release_reserved_attempt(first.reservation_id)
    assert store.counts("Runner_Dashboard", 3, "abc123") == (1, 1)


def test_reserve_attempt_enforces_per_pr_cap(tmp_path: Path) -> None:
    store = CIFixAttemptStore(tmp_path / "attempts.db")
    for i in range(DEFAULT_PER_PR_CAP):
        head = f"sha{i:02d}"
        cap = store.reserve_attempt("Runner_Dashboard", 2, head)
        assert cap.allowed, cap.reason
    blocked = store.reserve_attempt("Runner_Dashboard", 2, "sha-overflow")
    assert not blocked.allowed
    assert "per-PR" in blocked.reason
