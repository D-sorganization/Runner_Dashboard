"""GitHub CI gate: a commit on main must have green check runs."""

from __future__ import annotations

from typing import Any


def parse_check_runs_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        return []
    return [r for r in runs if isinstance(r, dict)]


def ci_gate_for_commit(check_runs: list[dict[str, Any]], expected_sha: str) -> tuple[bool, str]:
    """Return (passed, detail).

    Pre: ``expected_sha`` is a lowercase 40-char commit id.
    Post: when passed, every *completed* run for that sha succeeded; pending runs fail closed.
    """
    sha = expected_sha.lower()
    if len(sha) != 40:
        return False, "invalid expected commit sha"

    relevant = [r for r in check_runs if str(r.get("head_sha") or "").lower() == sha]
    if not relevant:
        return False, f"no check runs found for commit {sha[:7]}"

    pending = [r for r in relevant if r.get("status") != "completed"]
    if pending:
        names = ", ".join(sorted({str(r.get("name") or "?") for r in pending[:5]}))
        return False, f"checks still running: {names}"

    failures = [r for r in relevant if r.get("conclusion") != "success"]
    if failures:
        names = ", ".join(sorted({str(r.get("name") or "?") for r in failures[:5]}))
        return False, f"checks failed: {names}"

    return True, f"{len(relevant)} check(s) succeeded for {sha[:7]}"
