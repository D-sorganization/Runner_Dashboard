"""Queue-shepherd pure logic: dependency-order and path-conflict detection (#2929 slice 1).

Pure functions only — no GitHub I/O. The caller parses a merge-queue snapshot
elsewhere (GraphQL ``mergeQueue.entries``, PR file lists, declared
dependencies) into :class:`QueuedPR` and hands it to this module, which only
proposes what to reorder or pull out; it never acts on the queue itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class QueuedPR:
    number: int
    position: int  # 1-based; lower position merges sooner
    files: frozenset[str] = field(default_factory=frozenset)
    depends_on: frozenset[int] = field(default_factory=frozenset)


@dataclass(frozen=True)
class DependencyViolation:
    pr: int
    pr_position: int
    blocked_by: int
    blocked_by_position: int


@dataclass(frozen=True)
class PathConflict:
    ahead_pr: int
    ahead_position: int
    behind_pr: int
    behind_position: int
    overlapping_paths: frozenset[str]


def _validate_entries(entries: Sequence[QueuedPR]) -> None:
    seen_numbers: set[int] = set()
    seen_positions: set[int] = set()
    for entry in entries:
        if entry.number in seen_numbers:
            raise ValueError(f"duplicate PR number in queue snapshot: {entry.number}")
        if entry.position in seen_positions:
            raise ValueError(f"duplicate queue position in queue snapshot: {entry.position}")
        if entry.number in entry.depends_on:
            raise ValueError(f"PR #{entry.number} depends on itself")
        seen_numbers.add(entry.number)
        seen_positions.add(entry.position)


def find_dependency_order_violations(entries: Sequence[QueuedPR]) -> list[DependencyViolation]:
    """Flag PRs whose declared dependency sits further back in the queue."""
    _validate_entries(entries)
    by_number = {entry.number: entry for entry in entries}
    violations: list[DependencyViolation] = []
    for entry in entries:
        for dep in sorted(entry.depends_on):
            dep_entry = by_number.get(dep)
            if dep_entry is None:
                continue  # dependency not part of this snapshot; nothing to reorder
            if dep_entry.position > entry.position:
                violations.append(
                    DependencyViolation(
                        pr=entry.number,
                        pr_position=entry.position,
                        blocked_by=dep,
                        blocked_by_position=dep_entry.position,
                    )
                )
    return sorted(violations, key=lambda v: (v.pr_position, v.blocked_by))


def find_path_conflicts(entries: Sequence[QueuedPR]) -> list[PathConflict]:
    """Flag PRs that overlap on changed files with another PR queued ahead of them."""
    _validate_entries(entries)
    ordered = sorted(entries, key=lambda entry: entry.position)
    conflicts: list[PathConflict] = []
    for i, ahead in enumerate(ordered):
        for behind in ordered[i + 1 :]:
            overlap = ahead.files & behind.files
            if overlap:
                conflicts.append(
                    PathConflict(
                        ahead_pr=ahead.number,
                        ahead_position=ahead.position,
                        behind_pr=behind.number,
                        behind_position=behind.position,
                        overlapping_paths=frozenset(overlap),
                    )
                )
    return conflicts
