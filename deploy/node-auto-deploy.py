#!/usr/bin/env python3
# ruff: noqa: T201
"""Node-local auto-deploy entrypoint (pull-based, CI-gated)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_MIN_PYTHON = (3, 11)
if sys.version_info < _MIN_PYTHON:
    sys.stderr.write(
        "node-auto-deploy requires Python "
        f"{_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}+ (use the dashboard .venv); "
        f"got {sys.version_info.major}.{sys.version_info.minor} from {sys.executable}\n"
    )
    raise SystemExit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(DEPLOY_LIB))

from node_auto_deploy.activity import (  # noqa: E402
    ActivityUnavailableError,
    fetch_staff_activity,
    local_activity_count,
    staff_database_path,
    verify_staff_activity_probe,
)
from node_auto_deploy.github_api import github_get, load_token_from_env_file  # noqa: E402
from node_auto_deploy.health import wait_for_health  # noqa: E402
from node_auto_deploy.hub_gate import fetch_fleet_status  # noqa: E402
from node_auto_deploy.kill_switch import auto_deploy_disabled, kill_switch_reason  # noqa: E402
from node_auto_deploy.releases import release_dir  # noqa: E402
from node_auto_deploy.runner import CycleConfig, CycleDeps, evaluate_cycle  # noqa: E402
from node_auto_deploy.service_scope import ServiceScopeDecision, detect_service_scope  # noqa: E402
from node_auto_deploy.staleness import DEFAULT_MAX_GREEN_BEHIND, DEFAULT_MAX_UNRELEASED_HOURS  # noqa: E402
from node_auto_deploy.state import default_state_path, load_state, record_event, save_state  # noqa: E402
from node_auto_deploy.transaction import (  # noqa: E402
    deployment_lock,
    install_and_run_transaction,
)
from node_auto_deploy.version import deployed_git_sha  # noqa: E402

DEFAULT_REPO = "D-sorganization/Runner_Dashboard"
DEFAULT_ENV_FILE = Path.home() / ".config" / "runner-dashboard" / "env"
DEFAULT_DEPLOY_DIR = Path.home() / "actions-runners" / "dashboard"
DEFAULT_PORT = 8321


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def _probe_scope() -> tuple[str, int]:
    label = "system"
    code = _run(["systemctl", "is-active", "runner-dashboard"]).returncode
    return label, code


def _probe_user_scope() -> tuple[str, int]:
    return "user", _run(["systemctl", "--user", "is-active", "runner-dashboard"]).returncode


def _resolve_role(deploy_backend: Path) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(deploy_backend)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from machine_registry import load_machine_registry, resolve_local_identity; "
            "r=load_machine_registry(); print(resolve_local_identity(r).role)",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    role = (proc.stdout or "").strip()
    return role if role in {"hub", "node"} else "node"


def _download_asset(api_url: str, token: str, dest: Path) -> None:
    import urllib.request

    req = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/octet-stream",
            "Authorization": f"Bearer {token}",
            "User-Agent": "runner-dashboard-node-auto-deploy",
        },
    )
    with urllib.request.urlopen(req, timeout=600) as resp, dest.open("wb") as out:  # noqa: S310
        out.write(resp.read())


def _install_artifact(tarball: Path, checksum: str, deploy_dir: Path) -> None:
    script = REPO_ROOT / "deploy" / "install-dashboard-artifact.sh"
    if not script.is_file():
        script = DEPLOY_LIB / "install-dashboard-artifact.sh"
    proc = subprocess.run(
        ["bash", str(script), "--artifact", str(tarball), "--checksum", checksum, "--deploy-dir", str(deploy_dir)],
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"install-dashboard-artifact.sh failed ({proc.returncode})")


def _restart_dashboard(scope_decision: ServiceScopeDecision) -> None:
    proc = _run(scope_decision.restart_argv())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "service restart failed")


def _wait_healthy(port: int) -> bool:
    return wait_for_health(port)


def _env_number(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Runner Dashboard node auto-deploy")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--deploy-dir", type=Path, default=DEFAULT_DEPLOY_DIR)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--drain-timeout", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if auto_deploy_disabled():
        print(kill_switch_reason() or "disabled")
        return 0

    token = load_token_from_env_file(str(args.env_file)) or os.environ.get("GH_TOKEN", "")
    if not token:
        print("GH_TOKEN missing", file=sys.stderr)
        return 2

    state_path = default_state_path()
    state = load_state(state_path)
    live_dir = args.deploy_dir.expanduser()
    backend = live_dir / "backend"
    role = _resolve_role(backend) if backend.is_dir() else os.environ.get("MACHINE_ROLE", "node")

    hub_url = os.environ.get("HUB_URL", "").strip() or None
    fleet_token = os.environ.get("HUB_FLEET_TOKEN", "").strip() or token

    def gh_get(path: str) -> Any:
        return github_get(path, token)

    def list_releases() -> list:
        payload = github_get(f"/repos/{args.repo}/releases?per_page=15", token)
        return payload if isinstance(payload, list) else []

    def fleet_status() -> dict:
        if not hub_url:
            return {}
        return fetch_fleet_status(hub_url, fleet_token)

    def staff_in_flight() -> int:
        return fetch_staff_activity(f"http://127.0.0.1:{args.port}", fleet_token, staff_database_path())

    cfg = CycleConfig(
        repo_full_name=args.repo,
        live_deploy_dir=str(live_dir),
        machine_role=role,
        hub_url=hub_url,
        fleet_token=fleet_token,
        max_unreleased_hours=_env_number("AUTO_DEPLOY_MAX_UNRELEASED_HOURS", DEFAULT_MAX_UNRELEASED_HOURS),
        max_green_behind=int(_env_number("AUTO_DEPLOY_MAX_GREEN_BEHIND", DEFAULT_MAX_GREEN_BEHIND)),
    )
    deps = CycleDeps(
        github_get=gh_get,
        list_releases=list_releases,
        fetch_fleet_status=fleet_status,
        staff_in_flight=staff_in_flight,
        state=state,
    )

    try:
        result, message, target_sha = evaluate_cycle(cfg, deps)
    except ActivityUnavailableError as exc:
        message = f"activity state unavailable; deployment skipped: {exc}"
        record_event(state, commit="unknown", result="skipped", message=message)
        save_state(state_path, state)
        print(message)
        return 0
    if result != "ready":
        deployed_sha = deployed_git_sha(live_dir / "deployment.json")
        main_head = str(gh_get(f"/repos/{args.repo}/commits/main").get("sha") or "").lower()
        record_commit = target_sha or main_head or deployed_sha or "unknown"
        stale = deps.release_staleness
        record_event(
            state,
            commit=record_commit,
            result="skipped",
            message=message,
            attention=stale is not None and stale.attention,
        )
        save_state(state_path, state)
        print(message)
        return 0

    main_sha = target_sha or str(gh_get(f"/repos/{args.repo}/commits/main").get("sha") or "").lower()
    from node_auto_deploy.artifact import pick_release_assets

    assets = pick_release_assets(list_releases(), main_sha)
    if not assets:
        print("release assets missing after ready evaluation")
        return 1

    try:
        active_count = staff_in_flight()
    except ActivityUnavailableError as exc:
        message = f"activity state unavailable; deployment skipped: {exc}"
        record_event(state, commit=main_sha, result="skipped", message=message)
        save_state(state_path, state)
        print(message)
        return 0
    if active_count:
        message = f"{active_count} staff/chat activity item(s) remain; deployment skipped without waiting"
        record_event(state, commit=main_sha, result="skipped", message=message)
        save_state(state_path, state)
        print(message)
        return 0

    if args.dry_run:
        print(f"dry-run: would deploy {assets['version']} at {main_sha[:7]}")
        return 0

    scope = detect_service_scope([_probe_scope(), _probe_user_scope()])
    if scope is None:
        print("runner-dashboard service not found (system or user)", file=sys.stderr)
        return 2

    previous_sha = deployed_git_sha(live_dir / "deployment.json")
    state.previous_commit = previous_sha
    target_release = release_dir(live_dir, main_sha)
    try:
        with deployment_lock(live_dir):

            def install_candidate(staged_release: Path) -> None:
                with tempfile.TemporaryDirectory() as download_dir:
                    tarball = Path(download_dir) / f"dashboard-{assets['version']}.tar.gz"
                    checksum_file = Path(download_dir) / f"dashboard-{assets['version']}.tar.gz.sha256"
                    _download_asset(assets["tarball_url"], token, tarball)
                    _download_asset(assets["checksum_url"], token, checksum_file)
                    checksum = checksum_file.read_text(encoding="utf-8").split()[0]
                    _install_artifact(tarball, checksum, staged_release)

            def stop_service() -> None:
                proc = _run(scope.stop_argv())
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr or proc.stdout or "service stop failed")

            def healthy() -> bool:
                if not _wait_healthy(args.port):
                    return False
                # A newly active item is valid after service recovery; this call
                # only verifies the authenticated local API and full DB are readable.
                verify_staff_activity_probe(
                    f"http://127.0.0.1:{args.port}",
                    fleet_token,
                    staff_database_path(),
                )
                return True

            outcome = install_and_run_transaction(
                live_dir,
                target_release,
                install_candidate,
                check_idle=lambda: staff_in_flight() == 0,
                check_quiesced=lambda: local_activity_count(staff_database_path()) == 0,
                stop_service=stop_service,
                restart_service=lambda: _restart_dashboard(scope),
                healthy=healthy,
            )
        message = f"deployed {assets['version']} ({main_sha[:7]})" if outcome.result == "success" else outcome.message
        record_event(
            state,
            commit=main_sha,
            result=outcome.result,
            message=message,
            rolled_back_from=previous_sha if outcome.result == "rollback" else None,
        )
        save_state(state_path, state)
        out = sys.stdout if outcome.result in {"success", "skipped"} else sys.stderr
        print(message if outcome.result in {"success", "skipped"} else f"{outcome.result}: {message}", file=out)
        return 0 if outcome.result in {"success", "skipped"} else 1
    except Exception as exc:  # noqa: BLE001
        record_event(state, commit=main_sha, result="failed", message=str(exc))
        save_state(state_path, state)
        print(f"failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
