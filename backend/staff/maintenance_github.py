"""GitHub-backed workflow-run maintenance operations (#1448).

Staff action executors are synchronous and run in an anyio worker thread (the routes
call them through ``anyio.to_thread.run_sync``). ``gh_client`` owns one event-loop-bound
``httpx.AsyncClient``, so every GitHub call is sent back to the event loop with
``anyio.from_thread.run`` (``call_github``). Called from anywhere else the bridge is
unavailable and the operation fails visibly as ``bridge_unavailable``.

GitHub faults are mapped onto the maintenance failure classes: a rejected token is a
``PermissionError`` (``auth_expired``), a timeout a ``TimeoutError`` (``peer_timeout``)
and any other refusal a ``MaintenanceUpstreamError`` (``upstream_error``).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from typing import Any

import gh_client
from fastapi import HTTPException
from security import validate_repo_slug
from staff.loop_bridge import BridgeUnavailableError, run_on_loop
from staff.maintenance_policy import MaintenanceError, MaintenancePreconditionError
from staff.workspace import ORG

POLL_INTERVAL_SECONDS = 2.0
CANCEL_SETTLE_SECONDS = 30.0
RERUN_VISIBLE_SECONDS = 10.0


class MaintenanceUpstreamError(MaintenanceError):
    """GitHub refused the request (404, 409, 5xx, rate limit)."""


class MaintenanceBridgeError(MaintenanceError):
    """The operation ran outside an anyio worker thread, so GitHub is unreachable."""


def call_github(fn: Callable[..., Coroutine[Any, Any, Any]], *args: Any) -> Any:
    """Run the async ``gh_client`` call ``fn(*args)`` on the event loop from a worker thread."""
    try:
        return run_on_loop(fn, *args)
    except BridgeUnavailableError as exc:
        raise MaintenanceBridgeError(f"GitHub call {exc}") from exc
    except gh_client.GhAuthError as exc:
        raise PermissionError(f"GitHub rejected the token: {exc}") from exc
    except gh_client.GhClientError as exc:
        if exc.kind == "timeout":
            raise TimeoutError(f"GitHub timed out: {exc}") from exc
        raise MaintenanceUpstreamError(f"GitHub refused the request: {exc}") from exc


def repo_full(repo: str) -> str:
    """Return ``ORG/<name>`` for a bare or org-qualified repo; refuse anything else."""
    raw = str(repo or "").strip()
    if not raw:
        raise MaintenancePreconditionError("A 'repo' is required for workflow-run maintenance")
    owner, _, name = raw.rpartition("/")
    if owner and owner != ORG:
        raise MaintenancePreconditionError(f"repo '{raw}' is outside the {ORG} organization")
    try:
        return f"{ORG}/{validate_repo_slug(name)}"
    except HTTPException as exc:
        raise MaintenancePreconditionError(f"Invalid repo '{raw}': {exc.detail}") from exc


def get_run(repo: str, run_id: int) -> dict[str, Any]:
    run = call_github(gh_client.get, f"/repos/{repo_full(repo)}/actions/runs/{run_id}")
    return run if isinstance(run, dict) else {}


def cancel_run(repo: str, run_id: int) -> dict[str, Any]:
    """Ask GitHub to cancel the run; success means GitHub accepted the request."""
    call_github(gh_client.cancel_run, repo_full(repo), run_id)
    return {"cancel_requested": True, "run_id": run_id}


def rerun_run(repo: str, run_id: int, failed_only: bool) -> dict[str, Any]:
    """Rerun the run (or only its failed jobs), recording the attempt it replaces."""
    full = repo_full(repo)
    previous = int(get_run(repo, run_id).get("run_attempt") or 0)
    call_github(gh_client.rerun_failed if failed_only else gh_client.rerun, full, run_id)
    return {"rerun_requested": True, "run_id": run_id, "failed_only": failed_only, "previous_attempt": previous}


def wait_until_completed(repo: str, run_id: int, timeout: float = CANCEL_SETTLE_SECONDS) -> dict[str, Any]:
    """Poll until the run is completed; raise ``TimeoutError`` if it does not settle."""
    deadline = time.monotonic() + timeout
    while True:
        run = get_run(repo, run_id)
        if run.get("status") == "completed":
            return run
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Run {run_id} still '{run.get('status')}' after {timeout:.0f}s")
        time.sleep(POLL_INTERVAL_SECONDS)


def cancel_and_rerun(repo: str, run_id: int, failed_only: bool) -> dict[str, Any]:
    """Cancel, wait for the cancel to settle (GitHub cannot rerun a live run), then rerun."""
    cancel = cancel_run(repo, run_id)
    settled = wait_until_completed(repo, run_id)
    rerun = rerun_run(repo, run_id, failed_only)
    return {"cancel": cancel, "settled_conclusion": settled.get("conclusion"), "rerun": rerun}


def verify_cancelled(repo: str, run_id: int) -> tuple[bool, str]:
    """A cancel is verified only when the run concluded ``cancelled``."""
    try:
        run = wait_until_completed(repo, run_id)
    except (TimeoutError, MaintenanceError, PermissionError) as exc:
        return False, f"Run {run_id} cancel not verified: {exc}"
    conclusion = run.get("conclusion")
    if conclusion != "cancelled":
        return False, f"Run {run_id} concluded '{conclusion}', not cancelled"
    return True, f"Run {run_id} verified cancelled"


def verify_rerun(repo: str, run_id: int, previous_attempt: int) -> tuple[bool, str]:
    """A rerun is verified only when ``run_attempt`` advanced past ``previous_attempt``."""
    deadline = time.monotonic() + RERUN_VISIBLE_SECONDS
    while True:
        try:
            attempt = int(get_run(repo, run_id).get("run_attempt") or 0)
        except (TimeoutError, MaintenanceError, PermissionError) as exc:
            return False, f"Run {run_id} rerun not verified: {exc}"
        if attempt > previous_attempt:
            return True, f"Run {run_id} verified rerunning (attempt {attempt})"
        if time.monotonic() >= deadline:
            return False, f"Run {run_id} still on attempt {attempt}; rerun not visible"
        time.sleep(POLL_INTERVAL_SECONDS)


def parse_runner_num(target: str) -> int:
    """Resolve a positive local runner index from a target name or numeric string."""
    raw = str(target or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    _, sep, suffix = raw.rpartition("-")
    if sep and suffix.isdigit() and int(suffix) > 0:
        return int(suffix)
    raise MaintenancePreconditionError(f"Cannot resolve runner number from '{target}'")


def get_runner_state(runner_name_or_id: str) -> dict[str, Any]:
    """Retrieve current state of a runner from the GitHub organization runner inventory."""
    data = call_github(gh_client.get, f"/orgs/{ORG}/actions/runners")
    runners = (data or {}).get("runners") if isinstance(data, dict) else []
    needle = str(runner_name_or_id or "").strip()
    for runner in runners or []:
        if not isinstance(runner, dict):
            continue
        if str(runner.get("name") or "") == needle or str(runner.get("id") or "") == needle:
            return {
                "id": runner.get("id"),
                "name": runner.get("name") or needle,
                "status": str(runner.get("status") or "offline"),
                "busy": bool(runner.get("busy", False)),
            }
    return {"id": None, "name": needle, "status": "stopped", "busy": False}


def get_group_runners(group_label: str) -> list[dict[str, Any]]:
    """Retrieve runners belonging to a group label."""
    data = call_github(gh_client.get, f"/orgs/{ORG}/actions/runners")
    runners = (data or {}).get("runners") if isinstance(data, dict) else []
    wanted = str(group_label or "").strip().lower()
    matched: list[dict[str, Any]] = []
    for runner in runners or []:
        if not isinstance(runner, dict):
            continue
        labels = {
            str(lbl.get("name") if isinstance(lbl, dict) else lbl).strip().lower()
            for lbl in (runner.get("labels") or [])
        }
        if wanted in labels or wanted in str(runner.get("name") or "").lower():
            matched.append(runner)
    return matched


def remove_runner(target: str, resolved_id: int) -> dict[str, Any]:
    """Remove an offline or ghost runner registration from GitHub."""
    if resolved_id <= 0:
        raise MaintenancePreconditionError(f"Runner '{target}' not found in organization")
    call_github(gh_client._request, "DELETE", f"/orgs/{ORG}/actions/runners/{resolved_id}")
    return {"removed": True, "runner_id": resolved_id, "runner_name": target}


def purge_stale_queue(repo: str | None, min_age_minutes: int, max_count: int) -> dict[str, Any]:
    """Purge stale queued runs via queue_cleanup.purge_stale_runs (safe_only=True)."""
    import functools

    import queue_cleanup

    clean_repo = repo_full(repo).split("/", 1)[-1] if repo else None
    return dict(
        call_github(
            functools.partial(
                queue_cleanup.purge_stale_runs,
                ORG,
                min_age_minutes=min_age_minutes,
                repo=clean_repo,
                safe_only=True,
                max_count=max_count,
            )
        )
    )


def run_service_command(target: str, cmd: str) -> tuple[int, str, str]:
    """Execute service lifecycle command on target runner."""
    from runners import service_control

    runner_num = parse_runner_num(target)
    res = call_github(service_control.run_runner_svc, runner_num, cmd)
    if isinstance(res, tuple) and len(res) == 3:
        return int(res[0]), str(res[1]), str(res[2])
    return 1, "", "Invalid service response"


def fleet_control(action: str, host: str = "local") -> dict[str, Any]:
    """Scale or control runners on the target host."""
    from routers import fleet as fleet_router

    act = str(action or "up").strip().lower()
    action_map = {
        "start": "all-up",
        "up": "up",
        "all-up": "all-up",
        "stop": "all-down",
        "down": "down",
        "all-down": "all-down",
        "restart": "all-up",
    }
    mapped = action_map.get(act, act)
    out = call_github(fleet_router._fleet_control_local, mapped)
    return {"fleet_action": mapped, "host": host, **(out if isinstance(out, dict) else {})}
