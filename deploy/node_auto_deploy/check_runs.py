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
    """Order attempts of one check name: GitHub ids grow with creation, then start time.

    The merge queue runs CI on a commit under ``merge_group`` and again under
    ``push``; the two runs share a name and the later one has the larger id.
    """
    run_id = run.get("id")
    ordinal = run_id if isinstance(run_id, int) and not isinstance(run_id, bool) and run_id > 0 else 0
    started = run.get("started_at")
    return ordinal, started if isinstance(started, str) else ""


def latest_verdict_runs(completed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the newest completed run(s) that carry a verdict (#4419).

    Skipped/neutral runs did not run and cancelled/stale runs were superseded, so
    neither decides a name while another run does. More than one run is returned
    only when the newest ones cannot be told apart (no id, no start time); the
    caller must then require every one of them to pass.
    """
    deciding = [
        r for r in completed if str(r.get("conclusion") or "") not in _NO_VERDICT_CONCLUSIONS | _IGNORED_WHEN_SUPERSEDED
    ]
    if not deciding:
        return []
    newest = max(_creation_order(r) for r in deciding)
    return [r for r in deciding if _creation_order(r) == newest]


def check_name_passes(runs: list[dict[str, Any]], *, required: bool = True) -> bool:
    """Decide one check name from all of its runs on a commit.

    Pre: ``runs`` share a name and a head sha.
    Post: True only when
    - the newest run that carries a verdict succeeded (an older failure that was
      re-run, or a superseded merge-queue run, does not block; a failure in the
      newest run always does), or
    - no run carries a verdict, none was cancelled, and the name is not a required
      context (a job that skipped everywhere is not a failure). A required
      context needs a success, or an allowlisted skip.
    """
    completed = [r for r in runs if not run_is_pending(r)]
    if not completed:
        return False
    latest = latest_verdict_runs(completed)
    if latest:
        return all(str(r.get("conclusion") or "") in SATISFYING_NAME_CONCLUSIONS for r in latest)

    conclusions = {str(r.get("conclusion") or "") for r in completed}
    if not conclusions <= _NO_VERDICT_CONCLUSIONS:
        return False  # only cancelled/stale (or malformed) runs: nothing vouches for the name
    if not required:
        return True
    name = str(completed[0].get("name") or "")
    return conclusions == {"skipped"} and name in SKIPPED_CONCLUSION_ALLOWLIST
