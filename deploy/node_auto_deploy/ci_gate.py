"""GitHub CI gate: a commit on main must have green check runs."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from node_auto_deploy.check_runs import (
    check_name_passes,
    normalize_commit_sha,
    run_is_pending,
    validate_check_run_record,
)


def parse_check_runs_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        return []
    return [r for r in runs if isinstance(r, dict)]


def ci_gate_for_commit(check_runs: list[dict[str, Any]], expected_sha: str) -> tuple[bool, str]:
    """Return (passed, detail).

    Pre: ``check_runs`` is a list of GitHub check-run dicts; ``expected_sha`` is a git commit id.
    Post: when passed, every check *name* for that sha has no failing runs and at least one
    success or skipped run; pending runs fail closed.
    """
    if not isinstance(check_runs, list):
        return False, "check_runs must be a list"

    sha = normalize_commit_sha(expected_sha)
    if sha is None:
        return False, "invalid expected commit sha"

    relevant: list[dict[str, Any]] = []
    for run in check_runs:
        err = validate_check_run_record(run)
        if err is not None:
            return False, f"invalid check run: {err}"
        if str(run.get("head_sha") or "").lower() == sha:
            relevant.append(run)

    if not relevant:
        return False, f"no check runs found for commit {sha[:7]}"

    pending = [r for r in relevant if run_is_pending(r)]
    if pending:
        names = ", ".join(sorted({r["name"] for r in pending[:5]}))
        return False, f"checks still running: {names}"

    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in relevant:
        by_name[run["name"]].append(run)

    failing_names = sorted(name for name, runs in by_name.items() if not check_name_passes(runs))
    if failing_names:
        names = ", ".join(failing_names[:5])
        return False, f"checks failed: {names}"

    return True, f"{len(by_name)} check name(s) succeeded for {sha[:7]}"
