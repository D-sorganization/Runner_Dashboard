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
# Exact GitHub logins. A fragment such as "barb" inside another login is not authority.
_AUTHORIZED_RETRY_LOGINS = frozenset({"dieterolson", "openbot-barb", "openbotgit"})
_RETRY_AUTHORIZED = re.compile(r"\b(?:retry|requeue) authorized\b", re.IGNORECASE)
# An owner or Barb instruction to leave the PR unarmed. A new head does not lift it.
# A status sentence that merely mentions "unarmed" is not that instruction.
_OWNER_UNARMED = re.compile(
    r"\b(?:leave|stay|keep|remain) unarmed\b|\bdo not (?:requeue|arm)\b",
    re.IGNORECASE,
)


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


def _page_truncated(page: object) -> bool:
    """True when a GraphQL connection reports another page."""
    return isinstance(page, Mapping) and bool(page.get("hasNextPage") or page.get("hasPreviousPage"))


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


def _explicit_merge_group(suite: Mapping[str, Any]) -> bool:
    """True only when ``workflowRun.event`` is exactly ``merge_group``.

    Pre: ``suite`` is one ``checkSuites.nodes`` element.
    Post: a missing workflow or a ``pull_request`` event is not a merge-group suite.
    Other apps often have no workflow run; those must not count as the suite we needed.
    """
    workflow = suite.get("workflowRun") or {}
    if not isinstance(workflow, Mapping):
        return False
    return _text(workflow.get("event")).casefold() == _MERGE_GROUP_EVENT


def _check_runs_truncated(suite: Mapping[str, Any]) -> bool:
    """True when this suite's check runs report another page."""
    runs = suite.get("checkRuns")
    return isinstance(runs, Mapping) and _page_truncated(runs.get("pageInfo"))


def _removal_checks_truncated(event: Mapping[str, Any]) -> bool:
    """True when this removal's merge-group checks are not fully visible.

    Pre: ``event`` is a ``RemovedFromMergeQueueEvent`` node.
    Post: a missing ``pageInfo`` is complete. Another ``checkSuites`` page fails
    closed only when no suite on this page has ``workflowRun.event == merge_group``.
    Other apps fill the start of the connection and leave ``hasNextPage`` true
    even after the merge-group suites are in the window. A truncated
    ``checkRuns`` page on a merge-group suite still fails closed.
    """
    before = event.get("beforeCommit")
    if not isinstance(before, Mapping):
        return False
    suites = before.get("checkSuites")
    if not isinstance(suites, Mapping):
        return False
    merge_group = [suite for suite in _suite_nodes(before) if _explicit_merge_group(suite)]
    if any(_check_runs_truncated(suite) for suite in merge_group):
        return True
    if merge_group:
        return False
    return _page_truncated(suites.get("pageInfo"))


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
    for an exact owner or Barb login. ``queue_history_truncated`` is true when
    another timeline page can hide a removal newer than the head commit.
    """
    commits = (node.get("commits") or {}).get("nodes") or []
    last = commits[-1] if commits and isinstance(commits[-1], Mapping) else {}
    head_at = _text((last.get("commit") or {}).get("committedDate"))
    head_sha = _text(node.get("headRefOid"))
    removals: list[tuple[str, str]] = []
    failures: list[tuple[str, str, str]] = []
    checks_truncated = False
    for event in (node.get("timelineItems") or {}).get("nodes") or []:
        if not isinstance(event, Mapping):
            continue
        created_at = _text(event.get("createdAt"))
        reason = _text(event.get("reason")).casefold()
        if created_at and reason:
            removals.append((created_at, reason))
        if not head_sha or not _after_head(created_at, head_at):
            continue
        checks_truncated = checks_truncated or _removal_checks_truncated(event)
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
    comment_block = node.get("comments") or {}
    comments: list[tuple[str, str, str]] = []
    for comment in (comment_block.get("nodes") or []) if isinstance(comment_block, Mapping) else []:
        if not isinstance(comment, Mapping):
            continue
        created_at = _text(comment.get("createdAt"))
        body = _text(comment.get("body"))
        if created_at and body:
            comments.append((created_at, _comment_author(comment), body))
    comments_truncated = isinstance(comment_block, Mapping) and _page_truncated(comment_block.get("pageInfo"))
    return {
        "head_committed_at": head_at,
        "queue_removals": tuple(removals),
        "merge_group_failures": tuple(failures),
        "comment_bodies": tuple(comments),
        "queue_history_truncated": _queue_history_hides_events_after_head(node, head_at),
        "comments_truncated": comments_truncated,
        "checks_truncated": checks_truncated,
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


def _queue_history_hides_events_after_head(node: Mapping[str, Any], head_at: str) -> bool:
    """True when another timeline page may hide a removal of this head.

    Pre: ``node`` may omit ``timelineItems.pageInfo``.
    Post: a complete page is false. A truncated page is false only when its
    oldest visible event is at or before the head, so every later removal was returned.
    """
    timeline = node.get("timelineItems")
    if not isinstance(timeline, Mapping):
        return False
    page = timeline.get("pageInfo")
    if not isinstance(page, Mapping) or not (page.get("hasPreviousPage") or page.get("hasNextPage")):
        return False
    stamps = [
        _text(event.get("createdAt"))
        for event in (timeline.get("nodes") or [])
        if isinstance(event, Mapping) and _text(event.get("createdAt"))
    ]
    if not stamps or not head_at:
        return True
    return min(stamps) > head_at


def _authorized_retry_author(login: str) -> bool:
    """True when ``login`` is exactly the owner or Barb.

    Pre: ``login`` may be empty.
    Post: only ``dieterolson``, ``openbot-barb``, and ``openbotgit`` match, after a trailing
    ``[bot]`` suffix. A different login that contains one of those names does not match.
    """
    normalized = _text(login).casefold().removesuffix("[bot]").strip()
    return normalized in _AUTHORIZED_RETRY_LOGINS


def _owner_unarmed_active(comments: Sequence[tuple[str, str, str]]) -> bool:
    """True when the newest owner or Barb instruction still says to stay unarmed.

    Pre: each comment is ``(created_at, author, body)``.
    Post: a later ``retry authorized`` from an exact owner or Barb login clears
    the hold. A newer head commit does not. Other logins do not set or lift it.
    """
    held = False
    for _created_at, author, body in sorted(comments, key=lambda row: row[0]):
        if not _authorized_retry_author(author):
            continue
        if _RETRY_AUTHORIZED.search(body):
            held = False
        elif _OWNER_UNARMED.search(body):
            held = True
    return held


def _truncated_comments_may_hide_hold(
    comments: Sequence[tuple[str, str, str]],
    *,
    comments_truncated: bool,
) -> bool:
    """True when an older page could still contain an unreleased owner hold.

    Pre: ``comments`` are the newest page ``OPEN_PRS_QUERY`` returned.
    Post: a visible authorized retry is newer than every off-page comment, so
    the visible page is enough. No such retry means a missing older note could
    still be in force.
    """
    if not comments_truncated or _owner_unarmed_active(comments):
        return False
    return not any(
        _authorized_retry_author(author) and _RETRY_AUTHORIZED.search(body) for _created_at, author, body in comments
    )


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
    queue_history_truncated: bool = False,
    comments_truncated: bool = False,
    checks_truncated: bool = False,
) -> str:
    """Return why automatic requeue must stop, or ``""`` when it may proceed.

    Pre: ``queue_history_truncated`` comes from :func:`_queue_history_hides_events_after_head`.
    ``comments_truncated`` and ``checks_truncated`` come from connection ``pageInfo``.
    Post: visible deterministic evidence wins. A truncated check page then fails
    closed when no merge-group suite was on the page, or when that suite's
    check runs are themselves truncated. Then a truncated timeline, then a
    manual dequeue. An owner or Barb unarmed instruction holds across a new
    head until a later authorized retry.
    A truncated comment page fails closed when that instruction could be off-page.
    """
    if _deterministic_signatures(head_sha=head_sha, failures=failures):
        return "deterministic_merge_group"
    if checks_truncated:
        return "incomplete_check_page"
    if queue_history_truncated:
        return "incomplete_queue_history"
    if _manual_dequeue_without_authorization(
        head_committed_at=head_committed_at,
        removals=removals,
        comments=comments,
    ):
        return "manual_dequeue"
    if _owner_unarmed_active(comments):
        return "owner_unarmed"
    if _truncated_comments_may_hide_hold(comments, comments_truncated=comments_truncated):
        return "incomplete_comment_history"
    return ""
