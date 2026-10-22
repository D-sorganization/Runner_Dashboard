"""CI gate helpers shared by auto-release workflow and node auto-deploy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from node_auto_deploy.check_runs import normalize_commit_sha
from node_auto_deploy.ci_gate import ci_gate_for_commit, select_deployable_main_commit

from main_auto_release.release_index import commit_has_release

PUBLISHER_IGNORED_CHECK_NAMES = frozenset(
    {
        "Publish dashboard release when CI gate is green",
        "Main CI auto-release",
    }
)


def commit_passes_main_ci_gate(check_runs: list[dict[str, Any]], expected_sha: str) -> tuple[bool, str]:
    """Thin alias so workflow code imports the same module as auto-deploy."""
    return ci_gate_for_commit(check_runs, expected_sha)


def commit_passes_publisher_ci_gate(check_runs: list[dict[str, Any]], expected_sha: str) -> tuple[bool, str]:
    """CI gate for the release publisher (ignores this workflow's in-flight check)."""
    filtered = [r for r in check_runs if str(r.get("name") or "") not in PUBLISHER_IGNORED_CHECK_NAMES]
    return ci_gate_for_commit(filtered, expected_sha)


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
        ok, detail = ci_gate_for_commit(runs, sha)
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
