"""GitHub CI gate: a commit on main must have green check runs."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

_PASSING_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})
_FAILING_CONCLUSIONS = frozenset(
    {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
)
_PENDING_STATUSES = frozenset({"queued", "in_progress"})


def parse_check_runs_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        return []
    return [r for r in runs if isinstance(r, dict)]


def _run_is_pending(run: dict[str, Any]) -> bool:
    status = str(run.get("status") or "")
    if status in _PENDING_STATUSES:
        return True
    return status != "completed"


def _run_conclusion_fails(run: dict[str, Any]) -> bool:
    conclusion = str(run.get("conclusion") or "")
    if conclusion in _FAILING_CONCLUSIONS:
        return True
    if conclusion in _PASSING_CONCLUSIONS:
        return False
    return True


def _check_name_passes(runs: list[dict[str, Any]]) -> bool:
    completed = [r for r in runs if not _run_is_pending(r)]
    if any(_run_conclusion_fails(r) for r in completed):
        return False
    return any(str(r.get("conclusion") or "") in ("success", "skipped") for r in completed)


def ci_gate_for_commit(check_runs: list[dict[str, Any]], expected_sha: str) -> tuple[bool, str]:
    """Return (passed, detail).

    Pre: ``expected_sha`` is a lowercase 40-char commit id.
    Post: when passed, every check *name* for that sha has no failing runs and at least one
    success or skipped run; pending runs fail closed.
    """
    sha = expected_sha.lower()
    if len(sha) != 40:
        return False, "invalid expected commit sha"

    relevant = [r for r in check_runs if str(r.get("head_sha") or "").lower() == sha]
    if not relevant:
        return False, f"no check runs found for commit {sha[:7]}"

    pending = [r for r in relevant if _run_is_pending(r)]
    if pending:
        names = ", ".join(sorted({str(r.get("name") or "?") for r in pending[:5]}))
        return False, f"checks still running: {names}"

    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in relevant:
        by_name[str(run.get("name") or "?")].append(run)

    failing_names = sorted(name for name, runs in by_name.items() if not _check_name_passes(runs))
    if failing_names:
        names = ", ".join(failing_names[:5])
        return False, f"checks failed: {names}"

    return True, f"{len(by_name)} check name(s) succeeded for {sha[:7]}"
