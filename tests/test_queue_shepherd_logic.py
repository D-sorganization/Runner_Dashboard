"""Tests for queue-shepherd dependency-order and path-conflict detection (#2929 slice 1)."""

from __future__ import annotations

import pytest

from shared_scripts.queue_shepherd_logic import (
    DependencyViolation,
    PathConflict,
    QueuedPR,
    find_dependency_order_violations,
    find_path_conflicts,
)


def test_detects_dependency_queued_behind_its_prerequisite() -> None:
    # Mirrors the issue's own example: #2780 needs #2775 but sits ahead of it.
    entries = [
        QueuedPR(number=2780, position=1, depends_on=frozenset({2775})),
        QueuedPR(number=2775, position=2),
    ]

    violations = find_dependency_order_violations(entries)

    assert violations == [DependencyViolation(pr=2780, pr_position=1, blocked_by=2775, blocked_by_position=2)]


def test_no_violation_when_prerequisite_already_ahead() -> None:
    entries = [
        QueuedPR(number=2775, position=1),
        QueuedPR(number=2780, position=2, depends_on=frozenset({2775})),
    ]

    assert find_dependency_order_violations(entries) == []


def test_dependency_outside_queue_snapshot_is_not_a_violation() -> None:
    entries = [QueuedPR(number=2780, position=1, depends_on=frozenset({9999}))]

    assert find_dependency_order_violations(entries) == []


def test_multiple_violations_sorted_by_position_then_dependency() -> None:
    entries = [
        QueuedPR(number=10, position=1, depends_on=frozenset({30, 20})),
        QueuedPR(number=20, position=3),
        QueuedPR(number=30, position=4),
    ]

    violations = find_dependency_order_violations(entries)

    assert violations == [
        DependencyViolation(pr=10, pr_position=1, blocked_by=20, blocked_by_position=3),
        DependencyViolation(pr=10, pr_position=1, blocked_by=30, blocked_by_position=4),
    ]


def test_detects_path_overlap_between_queued_entries() -> None:
    entries = [
        QueuedPR(number=100, position=1, files=frozenset({"backend/staff/roles.py", "SPEC.md"})),
        QueuedPR(number=101, position=2, files=frozenset({"backend/staff/roles.py"})),
        QueuedPR(number=102, position=3, files=frozenset({"frontend/src/App.tsx"})),
    ]

    conflicts = find_path_conflicts(entries)

    assert conflicts == [
        PathConflict(
            ahead_pr=100,
            ahead_position=1,
            behind_pr=101,
            behind_position=2,
            overlapping_paths=frozenset({"backend/staff/roles.py"}),
        )
    ]


def test_no_conflict_when_file_sets_are_disjoint() -> None:
    entries = [
        QueuedPR(number=1, position=1, files=frozenset({"a.py"})),
        QueuedPR(number=2, position=2, files=frozenset({"b.py"})),
    ]

    assert find_path_conflicts(entries) == []


def test_duplicate_queue_position_is_rejected() -> None:
    entries = [
        QueuedPR(number=1, position=1),
        QueuedPR(number=2, position=1),
    ]

    with pytest.raises(ValueError, match="duplicate"):
        find_dependency_order_violations(entries)

    with pytest.raises(ValueError, match="duplicate"):
        find_path_conflicts(entries)


def test_duplicate_pr_number_is_rejected() -> None:
    entries = [
        QueuedPR(number=1, position=1),
        QueuedPR(number=1, position=2),
    ]

    with pytest.raises(ValueError, match="duplicate"):
        find_dependency_order_violations(entries)


def test_self_dependency_is_rejected() -> None:
    entries = [QueuedPR(number=1, position=1, depends_on=frozenset({1}))]

    with pytest.raises(ValueError, match="depends on itself"):
        find_dependency_order_violations(entries)
