"""One auto-deploy cycle (pure decisions + injectable side effects)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from node_auto_deploy.artifact import pick_release_assets
from node_auto_deploy.ci_gate import ci_gate_for_commit, parse_check_runs_payload
from node_auto_deploy.contracts import AutoDeployState
from node_auto_deploy.hub_gate import hub_ready_for_commit
from node_auto_deploy.kill_switch import auto_deploy_disabled
from node_auto_deploy.state import record_event
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


def evaluate_cycle(config: CycleConfig, deps: CycleDeps) -> tuple[str, str]:
    """Return (result, message) where result is skipped|failed|ready."""
    if auto_deploy_disabled():
        return "skipped", "auto-deploy disabled by kill switch"

    main = deps.github_get(f"/repos/{config.repo_full_name}/commits/main")
    main_sha = str(main.get("sha") or "").lower()
    if len(main_sha) != 40:
        return "failed", "could not resolve main HEAD"

    from pathlib import Path

    deployed = deployed_git_sha(Path(config.live_deploy_dir) / "deployment.json")
    if should_skip_already_deployed(deployed, main_sha):
        return "skipped", f"already at {main_sha[:7]}"

    checks = deps.github_get(f"/repos/{config.repo_full_name}/commits/{main_sha}/check-runs?per_page=100")
    ok, detail = ci_gate_for_commit(parse_check_runs_payload(checks), main_sha)
    if not ok:
        return "skipped", f"CI gate: {detail}"

    assets = pick_release_assets(deps.list_releases(), main_sha)
    if not assets:
        return "skipped", f"no release artifact for {main_sha[:7]}"

    if config.machine_role != "hub" and config.hub_url:
        try:
            fleet = deps.fetch_fleet_status()
        except RuntimeError as exc:
            return "skipped", f"hub gate: {exc}"
        ready, hub_detail = hub_ready_for_commit(fleet, main_sha)
        if not ready:
            return "skipped", f"hub gate: {hub_detail}"

    if deps.staff_in_flight() > 0:
        return "skipped", "staff runs in flight"

    return "ready", assets["version"]
