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
# A CI_FAILURE reason alone is not deterministic evidence: the production query
# has to return the merge-group check runs on ``beforeCommit``.
MANUAL_REASONS = frozenset({"manual", "manual_dequeue", "manually_removed"})
_MERGE_GROUP_EVENT = "merge_group"
_FAILED_CONCLUSIONS = frozenset({"failure", "failed", "timed_out", "cancelled", "canceled", "startup_failure", "stale"})
_TRANSIENT_CONCLUSIONS = frozenset({"cancelled", "canceled", "startup_failure", "stale"})
_TRANSIENT_MARKERS = (
    "runner offline",
    "waiting for a runner",
    "no runner",
    "lost communication",
    "rate limit",
    "connection reset",
    "infrastructure failure",
)
# Affirmative defect text. A bare FAILURE or TIMED_OUT is not enough: unknown
# queue failures stay transient so a missing runner cause cannot disarm a PR.
_DETERMINISTIC_MARKERS = (
    "assertionerror",
    "assertion failed",
    "traceback",
    "determinism",
    "hash-seed",
    "stale generated",
    "type error",
    "syntaxerror",
    "pytest",
    "ruff",
    "lint error",
    "snapshot mismatch",
)
_OWNER_LOGINS = frozenset({"dieterolson"})
_BARB_LOGIN_PARTS = frozenset({"barb", "openbotgit"})
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


def _suite_nodes(before_commit: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    suites = before_commit.get("checkSuites") or {}
    raw = suites.get("nodes") if isinstance(suites, Mapping) else suites
    return [suite for suite in raw or [] if isinstance(suite, Mapping)]


def _check_nodes(suite: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    runs = suite.get("checkRuns") or {}
    raw = runs.get("nodes") if isinstance(runs, Mapping) else runs
    return [check for check in raw or [] if isinstance(check, Mapping)]


def _suite_is_merge_group(suite: Mapping[str, Any]) -> bool:
    """True for a merge_group suite. A missing workflow event is accepted.

    Pre: ``suite`` is one ``checkSuites.nodes`` element from ``OPEN_PRS_QUERY``.
    Post: ``pull_request`` and other non-merge-group events are rejected.
    """
    workflow = suite.get("workflowRun") or {}
    if not isinstance(workflow, Mapping):
        return True
    event = _text(workflow.get("event")).casefold()
    return not event or event == _MERGE_GROUP_EVENT


def _classify_check(check: Mapping[str, Any]) -> tuple[str, str] | None:
    """Classify one failed merge-group check.

    Pre: ``check`` has ``conclusion`` plus ``name`` or ``title``.
    Post: ``None`` when the check did not fail. Cancelled, startup, stale,
    timed-out, infrastructure, and unclassified conclusions are ``transient``.
    ``deterministic`` requires a ``FAILURE`` whose name, title, or summary
    contains affirmative defect text.
    """
    conclusion = _text(check.get("conclusion")).casefold()
    if conclusion not in _FAILED_CONCLUSIONS:
        return None
    name = _text(check.get("name") or check.get("title")) or "unknown-check"
    blob = " ".join((name, _text(check.get("title")), _text(check.get("summary")))).casefold()
    transient = conclusion in _TRANSIENT_CONCLUSIONS or conclusion == "timed_out"
    transient = transient or any(marker in blob for marker in _TRANSIENT_MARKERS)
    if transient:
        return ("transient", name)
    if conclusion in {"failure", "failed"} and any(marker in blob for marker in _DETERMINISTIC_MARKERS):
        return ("deterministic", name)
    return ("transient", name)


def _attempt_from_removal(event: Mapping[str, Any]) -> tuple[str, str] | None:
    """One merge-group attempt's classification and signature, or ``None``.

    Pre: ``event`` is a ``RemovedFromMergeQueueEvent`` node.
    Post: the signature is the sorted unique failed check names. Deterministic
    names win when an attempt has both kinds. Removals with no merge-group
    check runs return ``None`` (a bare ``CI_FAILURE`` is not evidence).
    """
    before = event.get("beforeCommit")
    if not isinstance(before, Mapping):
        return None
    deterministic: list[str] = []
    transient: list[str] = []
    for suite in _suite_nodes(before):
        if not _suite_is_merge_group(suite):
            continue
        for check in _check_nodes(suite):
            classified = _classify_check(check)
            if classified is None:
                continue
            kind, name = classified
            (deterministic if kind == "deterministic" else transient).append(name)
    names = deterministic or transient
    if not names:
        return None
    kind = "deterministic" if deterministic else "transient"
    return (kind, "+".join(sorted(set(names))))


def evidence_from_node(node: Mapping[str, Any]) -> dict[str, Any]:
    """Read requeue evidence from a production shepherd pull-request node.

    Pre: ``node`` is a mapping. Queue removals are ``timelineItems.nodes`` with
    ``createdAt`` and ``reason``. Merge-group checks, when GitHub has them, are
    ``beforeCommit.checkSuites`` on those events (the ``OPEN_PRS_QUERY`` shape).
    Post: ``merge_group_failures`` holds ``(head_sha, classification, signature)``
    for removals after the head commit that carry classified merge-group checks.
    The head is the pull request ``headRefOid``, not the merge-group commit.
    A removal with no check runs does not become a failure. Comment records
    keep ``(created_at, author, body)`` so a later retry note can be checked
    for an owner or Barb author.
    """
    commits = (node.get("commits") or {}).get("nodes") or []
    last = commits[-1] if commits and isinstance(commits[-1], Mapping) else {}
    head_at = _text((last.get("commit") or {}).get("committedDate"))
    head_sha = _text(node.get("headRefOid"))
    removals: list[tuple[str, str]] = []
    failures: list[tuple[str, str, str]] = []
    for event in (node.get("timelineItems") or {}).get("nodes") or []:
        if not isinstance(event, Mapping):
            continue
        created_at = _text(event.get("createdAt"))
        reason = _text(event.get("reason")).casefold()
        if created_at and reason:
            removals.append((created_at, reason))
        if not head_sha or not _after_head(created_at, head_at):
            continue
        attempt = _attempt_from_removal(event)
        if attempt is not None:
            failures.append((head_sha, attempt[0], attempt[1]))
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
    comments: list[tuple[str, str, str]] = []
    for comment in (node.get("comments") or {}).get("nodes") or []:
        if not isinstance(comment, Mapping):
            continue
        created_at = _text(comment.get("createdAt"))
        body = _text(comment.get("body"))
        if created_at and body:
            comments.append((created_at, _comment_author(comment), body))
    return {
        "head_committed_at": head_at,
        "queue_removals": tuple(removals),
        "merge_group_failures": tuple(failures),
        "comment_bodies": tuple(comments),
    }


def _deterministic_signatures(
    *,
    head_sha: str,
    failures: Sequence[tuple[str, str, str]],
) -> bool:
    """True when one head has the same deterministic signature at least twice.

    Pre: each failure is ``(head_sha, classification, signature)`` already limited
    to removals after the head commit.
    Post: transient signatures and a single deterministic signature do not match.
    """
    head = head_sha.casefold()
    counts: dict[str, int] = {}
    for run_head, classification, signature in failures:
        if run_head.casefold() != head or classification != "deterministic" or not signature:
            continue
        counts[signature] = counts.get(signature, 0) + 1
    return any(count >= 2 for count in counts.values())


def _comment_author(comment: Mapping[str, Any]) -> str:
    """GitHub login from a GraphQL ``author`` or REST ``user`` object."""
    author = comment.get("author") or comment.get("user") or {}
    if isinstance(author, Mapping):
        return _text(author.get("login"))
    return ""


def _authorized_retry_author(login: str) -> bool:
    """True when ``login`` is the owner or a Barb identity.

    Pre: ``login`` may be empty. A ``[bot]`` suffix is ignored.
    Post: ``dieterolson``, ``openbotgit``, and a login token ``barb`` match.
    Shared fleet App logins do not, so a lane comment cannot lift a dequeue.
    """
    normalized = login.casefold().removesuffix("[bot]").strip()
    if normalized in _OWNER_LOGINS:
        return True
    parts = {part for part in re.split(r"[^a-z0-9]+", normalized) if part}
    return bool(parts & _BARB_LOGIN_PARTS)


def _manual_dequeue_without_authorization(
    *,
    head_committed_at: str,
    removals: Sequence[tuple[str, str]],
    comments: Sequence[tuple[str, str, str]],
) -> bool:
    """True when a manual dequeue has no later owner or Barb retry note.

    Pre: each comment is ``(created_at, author, body)`` and each removal is
    ``(created_at, reason)``.
    Post: an unauthorized later ``retry authorized`` comment leaves this True.
    """
    manual_at = [
        created_at
        for created_at, reason in removals
        if _after_head(created_at, head_committed_at) and reason in MANUAL_REASONS
    ]
    if not manual_at:
        return False
    latest = max(manual_at)
    return not any(
        created_at > latest and _authorized_retry_author(author) and _RETRY_AUTHORIZED.search(body)
        for created_at, author, body in comments
    )


def suppression_detail(
    *,
    head_sha: str,
    head_committed_at: str,
    removals: Sequence[tuple[str, str]],
    failures: Sequence[tuple[str, str, str]],
    comments: Sequence[tuple[str, str, str]],
) -> str:
    """Return why automatic requeue must stop, or ``""`` when it may proceed.

    Deterministic evidence wins over a manual dequeue so a later "retry
    authorized" note cannot revive an unchanged failing head.
    """
    if _deterministic_signatures(head_sha=head_sha, failures=failures):
        return "deterministic_merge_group"
    if _manual_dequeue_without_authorization(
        head_committed_at=head_committed_at,
        removals=removals,
        comments=comments,
    ):
        return "manual_dequeue"
    return ""
