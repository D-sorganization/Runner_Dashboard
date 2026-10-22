#!/usr/bin/env python3
"""Node-local auto-deploy entrypoint (pull-based, CI-gated)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

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

from node_auto_deploy.github_api import github_get, load_token_from_env_file  # noqa: E402
from node_auto_deploy.health import probe_health, probe_staff_summary  # noqa: E402
from node_auto_deploy.hub_gate import fetch_fleet_status  # noqa: E402
from node_auto_deploy.kill_switch import auto_deploy_disabled, kill_switch_reason  # noqa: E402
from node_auto_deploy.releases import atomic_live_switch, release_dir  # noqa: E402
from node_auto_deploy.runner import CycleConfig, CycleDeps, evaluate_cycle  # noqa: E402
from node_auto_deploy.service_scope import detect_service_scope  # noqa: E402
from node_auto_deploy.staff_drain import fetch_staff_summary, wait_for_staff_idle  # noqa: E402
from node_auto_deploy.state import default_state_path, load_state, record_event, save_state  # noqa: E402
from node_auto_deploy.version import deployed_git_sha  # noqa: E402

DEFAULT_REPO = "D-sorganization/Runner_Dashboard"
DEFAULT_ENV_FILE = Path.home() / ".config" / "runner-dashboard" / "env"
DEFAULT_DEPLOY_DIR = Path.home() / "actions-runners" / "dashboard"
DEFAULT_PORT = 8321


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True, **kwargs)  # type: ignore[arg-type]


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


def _restart_dashboard(scope_decision) -> None:
    proc = _run(scope_decision.restart_argv())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "service restart failed")


def _wait_healthy(port: int) -> bool:
    script = REPO_ROOT / "deploy" / "lib.sh"
    if not script.is_file():
        ok, _ = probe_health(f"http://127.0.0.1:{port}")
        return ok
    bash = os.environ.get("BASH", "bash")
    inner = f'source "{script}"; wait_healthy probe; probe() {{ curl -sf "http://127.0.0.1:{port}/api/health" | grep -q healthy; }}'
    return _run([bash, "-c", inner]).returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Runner Dashboard node auto-deploy")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--deploy-dir", type=Path, default=DEFAULT_DEPLOY_DIR)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--drain-timeout", type=int, default=900)
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

    def gh_get(path: str):
        return github_get(path, token)

    def list_releases() -> list:
        payload = github_get(f"/repos/{args.repo}/releases?per_page=15", token)
        return payload if isinstance(payload, list) else []

    def fleet_status() -> dict:
        if not hub_url:
            return {}
        return fetch_fleet_status(hub_url, fleet_token)

    def staff_in_flight() -> int:
        try:
            summary = fetch_staff_summary(f"http://127.0.0.1:{args.port}", fleet_token)
        except OSError:
            return 0
        rows = summary.get("in_flight")
        return len(rows) if isinstance(rows, list) else 0

    cfg = CycleConfig(
        repo_full_name=args.repo,
        live_deploy_dir=str(live_dir),
        machine_role=role,
        hub_url=hub_url,
        fleet_token=fleet_token,
    )
    deps = CycleDeps(
        github_get=gh_get,
        list_releases=list_releases,
        fetch_fleet_status=fleet_status,
        staff_in_flight=staff_in_flight,
        state=state,
    )

    result, message, target_sha = evaluate_cycle(cfg, deps)
    if result != "ready":
        record_event(state, commit=deployed_git_sha(live_dir / "deployment.json") or "unknown", result="skipped", message=message)
        save_state(state_path, state)
        print(message)
        return 0

    main_sha = target_sha or str(gh_get(f"/repos/{args.repo}/commits/main").get("sha") or "").lower()
    from node_auto_deploy.artifact import pick_release_assets

    assets = pick_release_assets(list_releases(), main_sha)
    if not assets:
        print("release assets missing after ready evaluation")
        return 1

    ready, drain_detail = wait_for_staff_idle(
        in_flight_count=staff_in_flight,
        timeout_seconds=float(args.drain_timeout),
    )
    if not ready:
        record_event(state, commit=main_sha, result="skipped", message=drain_detail)
        save_state(state_path, state)
        print(drain_detail)
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
    target_release.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tarball = Path(tmp) / f"dashboard-{assets['version']}.tar.gz"
        checksum_file = Path(tmp) / f"dashboard-{assets['version']}.tar.gz.sha256"
        _download_asset(assets["tarball_url"], token, tarball)
        _download_asset(assets["checksum_url"], token, checksum_file)
        checksum = checksum_file.read_text(encoding="utf-8").split()[0]
        staging = target_release
        if staging.exists():
            shutil_rmtree = __import__("shutil").rmtree
            shutil_rmtree(staging)
        staging.mkdir(parents=True)
        _install_artifact(tarball, checksum, staging)

    if live_dir.is_symlink() or not live_dir.exists():
        atomic_live_switch(live_dir, target_release)
    elif live_dir.is_dir():
        legacy = release_dir(live_dir, "legacy-pre-auto-deploy")
        if not legacy.exists():
            os.rename(live_dir, legacy)
        atomic_live_switch(live_dir, target_release)
    else:
        atomic_live_switch(live_dir, target_release)

    try:
        _restart_dashboard(scope)
        if not _wait_healthy(args.port):
            raise RuntimeError("health check failed after restart")
        ok_staff, staff_detail = probe_staff_summary(f"http://127.0.0.1:{args.port}", fleet_token)
        if not ok_staff:
            raise RuntimeError(staff_detail)
        record_event(state, commit=main_sha, result="success", message=f"deployed {assets['version']}")
        save_state(state_path, state)
        print(f"deployed {assets['version']} ({main_sha[:7]})")
        return 0
    except Exception as exc:  # noqa: BLE001
        if previous_sha:
            prev_release = release_dir(live_dir, previous_sha)
            if prev_release.is_dir():
                atomic_live_switch(live_dir, prev_release)
                _restart_dashboard(scope)
        record_event(
            state,
            commit=main_sha,
            result="rollback",
            message=str(exc),
            rolled_back_from=previous_sha,
        )
        save_state(state_path, state)
        print(f"rollback: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
