"""CI gate helpers shared by auto-release workflow and node auto-deploy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from node_auto_deploy.check_runs import (
    PASSING_CONCLUSIONS,
    PENDING_STATUSES,
    normalize_commit_sha,
    run_is_pending,
    validate_check_run_record,
)
from node_auto_deploy.ci_gate import (
    NON_BLOCKING_DEPLOY_CHECK_NAMES,
    ci_gate_for_commit,
    ci_gate_for_deploy_commit,
    select_deployable_main_commit,
)

from main_auto_release.release_index import commit_has_release

PUBLISHER_IGNORED_CHECK_NAMES = NON_BLOCKING_DEPLOY_CHECK_NAMES


def commit_passes_main_ci_gate(check_runs: list[dict[str, Any]], expected_sha: str) -> tuple[bool, str]:
    """Thin alias so workflow code imports the same module as auto-deploy."""
    return ci_gate_for_commit(check_runs, expected_sha)


def commit_passes_publisher_ci_gate(check_runs: list[dict[str, Any]], expected_sha: str) -> tuple[bool, str]:
    """CI gate for the release publisher (ignores non-blocking advisory checks)."""
    return ci_gate_for_deploy_commit(check_runs, expected_sha)


def publisher_gate_failure_is_terminal(check_runs: list[dict[str, Any]], expected_sha: str) -> bool:
    """Classify a failed strict-gate snapshot as terminal or still retryable.

    Pre: the caller has already evaluated the strict publisher gate and it failed.
    Pending or missing matching runs may be followed by a passing run. A completed
    failing conclusion is terminal even alongside pending checks; when every
    matching blocking run is completed, cancelled/neutral-only sets are terminal
    too. The CLI must not use this helper to classify a passing snapshot.
    """
    sha = normalize_commit_sha(expected_sha)
    if sha is None or not isinstance(check_runs, list):
        return True
    if any(not isinstance(run, dict) for run in check_runs):
        return True

    relevant = [run for run in check_runs if str(run.get("name") or "") not in NON_BLOCKING_DEPLOY_CHECK_NAMES]
    matching_runs: list[dict[str, Any]] = []
    for run in relevant:
        if validate_check_run_record(run) is not None:
            return True
        if run["status"] not in PENDING_STATUSES | {"completed"}:
            return True
        head_sha = run.get("head_sha")
        if not isinstance(head_sha, str) or normalize_commit_sha(head_sha) is None:
            return True
        if head_sha.lower() != sha:
            continue
        matching_runs.append(run)
        if run_is_pending(run):
            continue
        conclusion = str(run.get("conclusion") or "")
        if conclusion not in PASSING_CONCLUSIONS | {"cancelled", "stale"}:
            return True
    return bool(matching_runs) and all(run["status"] == "completed" for run in matching_runs)


def select_green_commit_with_release(
    commits_newest_first: list[str],
    fetch_check_runs: Callable[[str], list[dict[str, Any]]],
    releases: list[dict[str, Any]],
) -> tuple[str | None, str]:
    """Newest main commit that is CI-green **and** has a published release artifact."""
    if not commits_newest_first:
        return None, "no commits in main history window"

    last_detail = "no qualifying commit"
    for raw_sha in commits_newest_first:
        sha = normalize_commit_sha(str(raw_sha))
        if sha is None:
            continue
        runs = fetch_check_runs(sha)
        ok, detail = ci_gate_for_deploy_commit(runs, sha)
        if not ok:
            last_detail = detail
            continue
        if not commit_has_release(releases, sha):
            last_detail = f"CI green at {sha[:7]} but no published release"
            continue
        return sha, detail

    return None, (f"no green released commit in last {len(commits_newest_first)} main commits: {last_detail}")


def select_release_candidate_commit(
    commits_newest_first: list[str],
    fetch_check_runs: Callable[[str], list[dict[str, Any]]],
) -> tuple[str | None, str]:
    """Newest CI-green commit without requiring an existing release (publisher gate)."""
    return select_deployable_main_commit(commits_newest_first, fetch_check_runs)
