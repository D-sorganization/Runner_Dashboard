"""One auto-deploy cycle (pure decisions + injectable side effects)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from node_auto_deploy.artifact import pick_release_assets
from node_auto_deploy.ci_gate import (
    DEFAULT_MAIN_HISTORY_LIMIT,
    parse_check_runs_payload,
    select_deployable_main_commit_with_release,
)
from node_auto_deploy.contracts import AutoDeployState
from node_auto_deploy.hub_gate import hub_ready_for_commit
from node_auto_deploy.kill_switch import auto_deploy_disabled
from node_auto_deploy.version import deployed_git_sha


@dataclass(frozen=True)
class CycleConfig:
    repo_full_name: str
    live_deploy_dir: str
    machine_role: str
    hub_url: str | None
    fleet_token: str | None


@dataclass
class CycleDeps:
    github_get: Callable[[str], Any]
    list_releases: Callable[[], list[dict[str, Any]]]
    fetch_fleet_status: Callable[[], dict[str, Any]]
    staff_in_flight: Callable[[], int]
    state: AutoDeployState


def should_skip_already_deployed(deployed_sha: str | None, main_sha: str) -> bool:
    return deployed_sha is not None and deployed_sha.lower() == main_sha.lower()


def _main_commit_history(
    deps: CycleDeps,
    repo_full_name: str,
    main_head_sha: str,
) -> list[str]:
    payload = deps.github_get(f"/repos/{repo_full_name}/commits?sha=main&per_page={DEFAULT_MAIN_HISTORY_LIMIT}")
    shas: list[str] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                sha = str(item.get("sha") or "").lower()
                if len(sha) == 40:
                    shas.append(sha)
    if main_head_sha not in shas:
        shas.insert(0, main_head_sha)
    return shas[:DEFAULT_MAIN_HISTORY_LIMIT]


def _check_runs_for_commit(
    deps: CycleDeps,
    repo_full_name: str,
    commit_sha: str,
) -> list[dict[str, Any]]:
    checks = deps.github_get(f"/repos/{repo_full_name}/commits/{commit_sha}/check-runs?per_page=100")
    payload = checks if isinstance(checks, dict) else {}
    runs = parse_check_runs_payload(payload)
    out: list[dict[str, Any]] = []
    for run in runs:
        if str(run.get("head_sha") or "").lower() != commit_sha:
            out.append({**run, "head_sha": commit_sha})
        else:
            out.append(run)
    return out


def evaluate_cycle(config: CycleConfig, deps: CycleDeps) -> tuple[str, str, str | None]:
    """Return (result, message, target_commit_sha) where result is skipped|failed|ready."""
    if auto_deploy_disabled():
        return "skipped", "auto-deploy disabled by kill switch"

    main = deps.github_get(f"/repos/{config.repo_full_name}/commits/main")
    main_sha = str(main.get("sha") or "").lower()
    if len(main_sha) != 40:
        return "failed", "could not resolve main HEAD"

    from pathlib import Path

    deployed = deployed_git_sha(Path(config.live_deploy_dir) / "deployment.json")

    history = _main_commit_history(deps, config.repo_full_name, main_sha)
    releases = deps.list_releases()

    def _has_release(sha: str) -> bool:
        return pick_release_assets(releases, sha) is not None

    deployable_sha, gate_detail = select_deployable_main_commit_with_release(
        history,
        lambda sha: _check_runs_for_commit(deps, config.repo_full_name, sha),
        _has_release,
    )
    if deployable_sha is None:
        return "failed", f"CI gate: {gate_detail}", None

    if should_skip_already_deployed(deployed, deployable_sha):
        if main_sha != deployable_sha:
            return (
                "skipped",
                f"already at {deployable_sha[:7]} (main HEAD {main_sha[:7]} awaiting CI/release)",
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
