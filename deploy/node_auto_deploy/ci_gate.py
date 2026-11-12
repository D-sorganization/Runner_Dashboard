"""GitHub CI gate: a commit on main must have green check runs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from node_auto_deploy.check_runs import (
    check_name_passes,
    normalize_commit_sha,
    run_is_pending,
    validate_check_run_record,
)

DEFAULT_MAIN_HISTORY_LIMIT = 20

# Advisory / self-referential checks must not block node auto-deploy or main CI auto-release.
NON_BLOCKING_DEPLOY_CHECK_NAMES = frozenset(
    {
        "Publish dashboard release when CI gate is green",
        "Publish dashboard release (manual dispatch)",
        "Wait for CI gate (cancellable; newer candidate supersedes)",
        "Main CI auto-release",
        "compare",  # Maxwell Contract Drift
        "reconcile",  # Armed PR cap reconcile
        "refresh",  # Agent Fleet Dashboard
        # Bot-maintenance jobs triggered by issues/schedule events attach their check-runs to
        # the current main HEAD but say nothing about code quality; a queued or cancelled one
        # must not starve auto-deploy (see tests/deploy/test_ci_gate_non_blocking.py).
        "verify-closure",  # Verify Issue Closure (issues: closed)
        "reap",  # Agent Lease Reaper / Queued Job Reaper (schedule)
        "sweep",  # Agent panel review sweep (schedule)
        "on-demand-label",  # Agent panel review (issues: labeled)
        "upgrade",  # Lockfile Upgrade (schedule)
        "route",  # Taxonomy rollout (issues: labeled)
        "preview",  # Taxonomy rollout dry-run
        "apply",  # Taxonomy rollout apply
        "slo",  # CI Queue SLO Check (schedule); queue health, not code quality
    }
)


def filter_non_blocking_check_runs(check_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in check_runs if str(r.get("name") or "") not in NON_BLOCKING_DEPLOY_CHECK_NAMES]


def ci_gate_for_deploy_commit(check_runs: list[dict[str, Any]], expected_sha: str) -> tuple[bool, str]:
    """Deploy/release gate: required CI checks only (excludes publisher and fleet hygiene jobs)."""
    return ci_gate_for_commit(filter_non_blocking_check_runs(check_runs), expected_sha)


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


def select_deployable_main_commit(
    commits_newest_first: list[str],
    fetch_check_runs: Callable[[str], list[dict[str, Any]]],
) -> tuple[str | None, str]:
    """Return the newest main commit in ``commits_newest_first`` that passes ``ci_gate_for_commit``.

    Pre: ``commits_newest_first`` is ordered from HEAD toward ancestors (bounded window).
    Post: when a sha is returned, ``ci_gate_for_commit`` passes for that sha; otherwise None.
    """
    if not commits_newest_first:
        return None, "no commits in main history window"

    last_detail = "no qualifying commit"
    for raw_sha in commits_newest_first:
        sha = normalize_commit_sha(str(raw_sha))
        if sha is None:
            continue
        runs = fetch_check_runs(sha)
        ok, detail = ci_gate_for_deploy_commit(runs, sha)
        if ok:
            return sha, detail
        last_detail = detail

    return None, (f"no green commit in last {len(commits_newest_first)} main commits: {last_detail}")


@dataclass(frozen=True)
class MainWindowScan:
    """Newest-first view of the main window: what is deployable and what is merely green.

    ``newest_state`` describes the newest commit with a usable verdict (not the oldest), so a
    red ancestor cannot make a green HEAD look broken.
    """

    deployable_sha: str | None
    deployable_detail: str
    newest_sha: str | None
    newest_state: str  # "green_released" | "green_unreleased" | "not_green" | "empty"
    newest_detail: str
    green_unreleased: tuple[str, ...]
    window: int

    @property
    def newest_green_unreleased(self) -> str | None:
        return self.green_unreleased[0] if self.green_unreleased else None

    @property
    def detail(self) -> str:
        if self.deployable_sha is not None:
            return self.deployable_detail
        if self.newest_sha is None:
            return "no commits in main history window"
        short = self.newest_sha[:7]
        if self.newest_state == "green_unreleased":
            return f"main {short} is green but has no published release (none of last {self.window} commits released)"
        return f"main {short} is not green: {self.newest_detail} (no green released commit in last {self.window})"


def scan_main_window(
    commits_newest_first: list[str],
    fetch_check_runs: Callable[[str], list[dict[str, Any]]],
    has_release: Callable[[str], bool],
) -> MainWindowScan:
    """Walk newest-first until the first green *released* commit, recording green-unreleased ones."""
    window = len(commits_newest_first)
    newest_sha: str | None = None
    newest_state = "empty"
    newest_detail = ""
    unreleased: list[str] = []
    for raw_sha in commits_newest_first:
        sha = normalize_commit_sha(str(raw_sha))
        if sha is None:
            continue
        ok, detail = ci_gate_for_deploy_commit(fetch_check_runs(sha), sha)
        released = ok and has_release(sha)
        if newest_sha is None:
            newest_sha = sha
            newest_detail = detail
            newest_state = "green_released" if released else ("green_unreleased" if ok else "not_green")
        if released:
            return MainWindowScan(sha, detail, newest_sha, newest_state, newest_detail, tuple(unreleased), window)
        if ok:
            unreleased.append(sha)
    return MainWindowScan(None, "", newest_sha, newest_state, newest_detail, tuple(unreleased), window)


def select_deployable_main_commit_with_release(
    commits_newest_first: list[str],
    fetch_check_runs: Callable[[str], list[dict[str, Any]]],
    has_release: Callable[[str], bool],
) -> tuple[str | None, str]:
    """Newest commit that passes ``ci_gate_for_deploy_commit`` and ``has_release(sha)``."""
    scan = scan_main_window(commits_newest_first, fetch_check_runs, has_release)
    return scan.deployable_sha, scan.detail
