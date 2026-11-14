"""GitHub check-run conclusion semantics (single source of truth for node auto-deploy)."""

from __future__ import annotations

import re
from typing import Any

PASSING_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})
FAILING_CONCLUSIONS = frozenset({"failure", "cancelled", "timed_out", "action_required", "startup_failure"})
# Superseded runs (concurrency cancel) neither pass nor fail when a green rerun exists.
_IGNORED_WHEN_SUPERSEDED = frozenset({"cancelled", "stale"})
PENDING_STATUSES = frozenset({"queued", "in_progress", "requested", "waiting", "pending"})
"""Conclusions that satisfy a check *name* (a required context accepts skipped only via the allowlist)."""
SATISFYING_NAME_CONCLUSIONS = frozenset({"success"})

# Required contexts designed to skip on main commits. Must stay in sync with tests.
# - "guard" (Anti-Phantom Merge Guard): judges a pull request and runs on it; its job
#   condition is false under ``merge_group``, so on a main commit its only run is skipped.
#   Branch protection already required it to pass on the PR before the merge.
SKIPPED_CONCLUSION_ALLOWLIST = frozenset({"guard"})

_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def normalize_commit_sha(value: str) -> str | None:
    """Pre: ``value`` is the expected git object id string. Post: lowercase 40-hex or None."""
    sha = value.lower()
    if not _GIT_SHA_RE.fullmatch(sha):
        return None
    return sha


def validate_check_run_record(run: Any) -> str | None:
    """Pre: one GitHub check-run dict. Post: None when shape is usable; else error detail."""
    if not isinstance(run, dict):
        return "check run is not an object"
    name = run.get("name")
    status = run.get("status")
    if not isinstance(name, str) or not name:
        return "check run missing name"
    if not isinstance(status, str) or not status:
        return "check run missing status"
    return None


def run_is_pending(run: dict[str, Any]) -> bool:
    status = run["status"]
    if status in PENDING_STATUSES:
        return True
    return status != "completed"


def completed_run_conclusion_fails(run: dict[str, Any]) -> bool:
    """Pre: run is completed (not pending)."""
    conclusion = str(run.get("conclusion") or "")
    if conclusion in FAILING_CONCLUSIONS:
        return True
    if conclusion in PASSING_CONCLUSIONS:
        return False
    return True


# Conclusions that carry no verdict on the code: the job did not run.
_NO_VERDICT_CONCLUSIONS = frozenset({"skipped", "neutral"})


def _creation_order(run: dict[str, Any]) -> tuple[int, str]:
    """Order attempts of one check: GitHub ids grow with creation, then start time."""
    run_id = run.get("id")
    ordinal = run_id if isinstance(run_id, int) and not isinstance(run_id, bool) and run_id > 0 else 0
    started = run.get("started_at")
    return ordinal, started if isinstance(started, str) else ""


def _nested_id(run: dict[str, Any], field: str) -> int:
    holder = run.get(field)
    value = holder.get("id") if isinstance(holder, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _attempt_key(run: dict[str, Any]) -> tuple[int, int]:
    """(app, check suite) of a run.

    Board ruling C1 keys an attempt by (name, SHA, app, event). A check-run record
    has no event field; the workflow run of one event owns one check suite, so the
    suite stands in for the event. The merge queue runs CI on a commit under
    ``merge_group`` and GitHub runs it again under ``push``: two suites, two keys.
    Re-running a job adds an attempt to the same suite.
    """
    return _nested_id(run, "app"), _nested_id(run, "check_suite")


def latest_attempts(completed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the deciding completed run of each (app, suite) for one check name (#4419).

    Within a suite the newest attempt that passed or failed decides, and older
    attempts are superseded. A newer attempt that skipped or was cancelled does
    not replace an earlier verdict, so a skip cannot repair a failure. A suite
    with no verdict at all yields its newest attempt. More than one run per suite
    is returned only when the newest ones cannot be told apart (no id, no start
    time); the caller must then require every one of them to pass.
    """
    by_key: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for run in completed:
        by_key.setdefault(_attempt_key(run), []).append(run)
    no_verdict = _NO_VERDICT_CONCLUSIONS | _IGNORED_WHEN_SUPERSEDED
    latest: list[dict[str, Any]] = []
    for attempts in by_key.values():
        deciding = [r for r in attempts if str(r.get("conclusion") or "") not in no_verdict] or attempts
        newest = max(_creation_order(r) for r in deciding)
        latest.extend(r for r in deciding if _creation_order(r) == newest)
    return latest


def check_name_passes(runs: list[dict[str, Any]], *, required: bool = True) -> bool:
    """Decide one check name from all of its runs on a commit.

    Pre: ``runs`` share a name and a head sha.
    Post: only the latest attempt of each (app, suite) counts; superseded attempts
    do not block. True only when
    - no latest attempt failed (a failure in any suite blocks, whatever the other
      suites say), and
    - at least one latest attempt succeeded (skipped, neutral, cancelled and stale
      attempts of other suites carry no verdict), or every latest attempt was
      skipped and the name is either not a required context or is on
      ``SKIPPED_CONCLUSION_ALLOWLIST``.
    Cancelled-only, stale-only and neutral-only names fail: skipped or neutral is
    never a pass for a required context outside the allowlist, and neutral alone
    is never a pass.
    """
    completed = [r for r in runs if not run_is_pending(r)]
    if not completed:
        return False
    conclusions = {str(r.get("conclusion") or "") for r in latest_attempts(completed)}
    if conclusions - SATISFYING_NAME_CONCLUSIONS - _NO_VERDICT_CONCLUSIONS - _IGNORED_WHEN_SUPERSEDED:
        return False  # a latest attempt failed (or has an unknown conclusion)
    if conclusions & SATISFYING_NAME_CONCLUSIONS:
        return True
    if conclusions != {"skipped"}:
        return False  # cancelled, stale or neutral with no success: nothing vouches for the name
    if not required:
        return True
    name = str(completed[0].get("name") or "")
    return name in SKIPPED_CONCLUSION_ALLOWLIST
