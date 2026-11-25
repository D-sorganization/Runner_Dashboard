"""Suppress automatic requeue after deterministic merge-queue failures (#4658).

A merge_group failure leaves the PR's own checks green, so an armed PR is
pushed back into the queue. Repeated unchanged deterministic failures, and a
manual dequeue with no later retry authorization, stop that loop. Transient
infrastructure failures do not.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

# GitHub's dequeue reason for a failing queue check. ``failed_checks`` stays a
# generic bounce (``count_queue_bounces``) so older fixtures keep that gate.
DETERMINISTIC_REASONS = frozenset({"ci_failure"})
MANUAL_REASONS = frozenset({"manual", "manual_dequeue", "manually_removed"})
_RETRY_AUTHORIZED = re.compile(r"\b(?:retry|requeue) authorized\b", re.IGNORECASE)


def _text(value: object) -> str:
    return str(value or "").strip()


def _after_head(stamp: str, head_at: str) -> bool:
    """True when ``stamp`` belongs to the current head.

    An empty head time counts every event, matching :func:`count_queue_bounces`.
    """
    if not stamp:
        return False
    if not head_at:
        return True
    return stamp > head_at


def evidence_from_node(node: Mapping[str, Any]) -> dict[str, Any]:
    """Read requeue evidence already present on a shepherd pull-request node."""
    commits = (node.get("commits") or {}).get("nodes") or []
    last = commits[-1] if commits and isinstance(commits[-1], Mapping) else {}
    head_at = _text((last.get("commit") or {}).get("committedDate"))
    removals: list[tuple[str, str]] = []
    for event in (node.get("timelineItems") or {}).get("nodes") or []:
        if not isinstance(event, Mapping):
            continue
        created_at = _text(event.get("createdAt"))
        reason = _text(event.get("reason")).casefold()
        if created_at and reason:
            removals.append((created_at, reason))
    failures: list[tuple[str, str, str]] = []
    for run in node.get("mergeGroupRuns") or []:
        if not isinstance(run, Mapping):
            continue
        if _text(run.get("conclusion")).casefold() not in {"failure", "failed"}:
            continue
        failures.append(
            (
                _text(run.get("headOid") or run.get("head_sha")),
                _text(run.get("classification")).casefold(),
                _text(run.get("signature")),
            )
        )
    comments: list[tuple[str, str]] = []
    for comment in (node.get("comments") or {}).get("nodes") or []:
        if not isinstance(comment, Mapping):
            continue
        created_at = _text(comment.get("createdAt"))
        body = _text(comment.get("body"))
        if created_at and body:
            comments.append((created_at, body))
    return {
        "head_committed_at": head_at,
        "queue_removals": tuple(removals),
        "merge_group_failures": tuple(failures),
        "comment_bodies": tuple(comments),
    }


def _deterministic_signatures(
    *,
    head_sha: str,
    head_committed_at: str,
    removals: Sequence[tuple[str, str]],
    failures: Sequence[tuple[str, str, str]],
) -> bool:
    head = head_sha.casefold()
    on_head = [row for row in failures if row[0].casefold() == head]
    if on_head:
        counts: dict[str, int] = {}
        for _, classification, signature in on_head:
            if classification != "deterministic" or not signature:
                continue
            counts[signature] = counts.get(signature, 0) + 1
        return any(count >= 2 for count in counts.values())
    deterministic_removals = [
        reason
        for created_at, reason in removals
        if _after_head(created_at, head_committed_at) and reason in DETERMINISTIC_REASONS
    ]
    return len(deterministic_removals) >= 2


def _manual_dequeue_without_authorization(
    *,
    head_committed_at: str,
    removals: Sequence[tuple[str, str]],
    comments: Sequence[tuple[str, str]],
) -> bool:
    manual_at = [
        created_at
        for created_at, reason in removals
        if _after_head(created_at, head_committed_at) and reason in MANUAL_REASONS
    ]
    if not manual_at:
        return False
    latest = max(manual_at)
    return not any(created_at > latest and _RETRY_AUTHORIZED.search(body) for created_at, body in comments)


def suppression_detail(
    *,
    head_sha: str,
    head_committed_at: str,
    removals: Sequence[tuple[str, str]],
    failures: Sequence[tuple[str, str, str]],
    comments: Sequence[tuple[str, str]],
) -> str:
    """Return why automatic requeue must stop, or ``""`` when it may proceed.

    Deterministic evidence wins over a manual dequeue so a later "retry
    authorized" note cannot revive an unchanged failing head.
    """
    if _deterministic_signatures(
        head_sha=head_sha,
        head_committed_at=head_committed_at,
        removals=removals,
        failures=failures,
    ):
        return "deterministic_merge_group"
    if _manual_dequeue_without_authorization(
        head_committed_at=head_committed_at,
        removals=removals,
        comments=comments,
    ):
        return "manual_dequeue"
    return ""
