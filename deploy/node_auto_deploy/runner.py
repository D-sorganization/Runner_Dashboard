"""One auto-deploy cycle (pure decisions + injectable side effects)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from node_auto_deploy.artifact import pick_release_assets
from node_auto_deploy.check_inventory import CheckInventoryUnavailable, fetch_commit_checks
from node_auto_deploy.ci_gate import (
    DEFAULT_MAIN_HISTORY_LIMIT,
    scan_main_window,
)
from node_auto_deploy.contracts import AutoDeployState
from node_auto_deploy.hub_gate import hub_ready_for_commit
from node_auto_deploy.kill_switch import auto_deploy_disabled
from node_auto_deploy.staleness import (
    DEFAULT_MAX_GREEN_BEHIND,
    DEFAULT_MAX_UNRELEASED_HOURS,
    ReleaseStaleness,
    assess_release_staleness,
)
from node_auto_deploy.version import deployed_git_sha


@dataclass(frozen=True)
class CycleConfig:
    repo_full_name: str
    live_deploy_dir: str
    machine_role: str
    hub_url: str | None
    fleet_token: str | None
    max_unreleased_hours: float = DEFAULT_MAX_UNRELEASED_HOURS
    max_green_behind: int = DEFAULT_MAX_GREEN_BEHIND


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)  # noqa: UP017 - hub supports Python 3.10


@dataclass
class CycleDeps:
    github_get: Callable[[str], Any]
    list_releases: Callable[[], list[dict[str, Any]]]
    fetch_fleet_status: Callable[[], dict[str, Any]]
    staff_in_flight: Callable[[], int]
    state: AutoDeployState
    now: Callable[[], datetime] = _utcnow
    release_staleness: ReleaseStaleness | None = None


def should_skip_already_deployed(deployed_sha: str | None, main_sha: str) -> bool:
    return deployed_sha is not None and deployed_sha.lower() == main_sha.lower()


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)  # noqa: UP017


def _main_commit_log(
    deps: CycleDeps,
    repo_full_name: str,
    main_head_sha: str,
) -> tuple[list[str], dict[str, datetime]]:
    payload = deps.github_get(f"/repos/{repo_full_name}/commits?sha=main&per_page={DEFAULT_MAIN_HISTORY_LIMIT}")
    shas: list[str] = []
    times: dict[str, datetime] = {}
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                sha = str(item.get("sha") or "").lower()
                if len(sha) == 40:
                    shas.append(sha)
                    committer = (item.get("commit") or {}).get("committer") or {}
                    when = _parse_time(committer.get("date"))
                    if when is not None:
                        times[sha] = when
    if main_head_sha not in shas:
        shas.insert(0, main_head_sha)
    return shas[:DEFAULT_MAIN_HISTORY_LIMIT], times


def _check_runs_for_commit(
    deps: CycleDeps,
    repo_full_name: str,
    commit_sha: str,
) -> list[dict[str, Any]]:
    return fetch_commit_checks(deps.github_get, repo_full_name, commit_sha)


def evaluate_cycle(config: CycleConfig, deps: CycleDeps) -> tuple[str, str, str | None]:
    """Return (result, message, target_commit_sha) where result is skipped|failed|ready."""
    if auto_deploy_disabled():
        return "skipped", "auto-deploy disabled by kill switch", None

    main = deps.github_get(f"/repos/{config.repo_full_name}/commits/main")
    main_sha = str(main.get("sha") or "").lower()
    if len(main_sha) != 40:
        return "failed", "could not resolve main HEAD", None

    from pathlib import Path

    deployed = deployed_git_sha(Path(config.live_deploy_dir) / "deployment.json")

    history, commit_times = _main_commit_log(deps, config.repo_full_name, main_sha)
    releases = deps.list_releases()

    def _has_release(sha: str) -> bool:
        return pick_release_assets(releases, sha) is not None

    try:
        scan = scan_main_window(
            history,
            lambda sha: _check_runs_for_commit(deps, config.repo_full_name, sha),
            _has_release,
        )
    except CheckInventoryUnavailable as exc:
        return "failed", f"CI gate unavailable: {exc}", None
    stale = assess_release_staleness(
        scan,
        commit_times,
        deps.now(),
        max_age_hours=config.max_unreleased_hours,
        max_green_behind=config.max_green_behind,
    )
    deps.release_staleness = stale

    def _flag(message: str) -> str:
        return f"{stale.reason}; {message}" if stale.attention else message

    deployable_sha = scan.deployable_sha
    if deployable_sha is None:
        return "failed", _flag(f"CI gate: {scan.detail}"), None

    if should_skip_already_deployed(deployed, deployable_sha):
        if main_sha != deployable_sha:
            return (
                "skipped",
                _flag(f"already at {deployable_sha[:7]} (main HEAD {main_sha[:7]} awaiting CI/release)"),
                None,
            )
        return "skipped", f"already at {deployable_sha[:7]}", None

    assets = pick_release_assets(releases, deployable_sha)
    if not assets:
        return "skipped", f"no release artifact for {deployable_sha[:7]}", None

    if config.machine_role != "hub" and config.hub_url:
        try:
            fleet = deps.fetch_fleet_status()
        except RuntimeError as exc:
            return "skipped", f"hub gate: {exc}", None
        ready, hub_detail = hub_ready_for_commit(fleet, deployable_sha)
        if not ready:
            return "skipped", f"hub gate: {hub_detail}", None

    if deps.staff_in_flight() > 0:
        return "skipped", "staff runs in flight", None

    return "ready", assets["version"], deployable_sha
