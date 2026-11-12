"""GitHub check-run conclusion semantics (single source of truth for node auto-deploy)."""

from __future__ import annotations

import re
from typing import Any

PASSING_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})
FAILING_CONCLUSIONS = frozenset({"failure", "cancelled", "timed_out", "action_required", "startup_failure"})
# Superseded runs (concurrency cancel) neither pass nor fail when a green rerun exists.
_IGNORED_WHEN_SUPERSEDED = frozenset({"cancelled", "stale"})
PENDING_STATUSES = frozenset({"queued", "in_progress", "requested", "waiting", "pending"})
"""Conclusions that satisfy a check *name* (skipped is never satisfying except allowlist)."""
SATISFYING_NAME_CONCLUSIONS = frozenset({"success"})

# Checks designed to skip on some events (e.g. path filters). Must stay in sync with tests.
SKIPPED_CONCLUSION_ALLOWLIST = frozenset[str]()

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


def _duplicate_runs_pass(completed: list[dict[str, Any]]) -> bool:
    """Only a single success with superseded cancelled/stale siblings may pass."""
    successes = [r for r in completed if str(r.get("conclusion") or "") == "success"]
    if len(successes) != 1:
        return False
    for run in completed:
        conclusion = str(run.get("conclusion") or "")
        if conclusion in SATISFYING_NAME_CONCLUSIONS:
            continue
        if conclusion in _IGNORED_WHEN_SUPERSEDED:
            continue
        return False
    return True


def check_name_passes(runs: list[dict[str, Any]]) -> bool:
    """Pass only on explicit success, or allowlisted skip with a single completed run."""
    completed = [r for r in runs if not run_is_pending(r)]
    if not completed:
        return False
    if len(completed) > 1:
        return _duplicate_runs_pass(completed)

    conclusion = str(completed[0].get("conclusion") or "")
    name = str(completed[0].get("name") or runs[0].get("name") or "")
    if conclusion in SATISFYING_NAME_CONCLUSIONS:
        return True
    if conclusion == "skipped" and name in SKIPPED_CONCLUSION_ALLOWLIST:
        return True
    if completed_run_conclusion_fails(completed[0]):
        return False
    return False
