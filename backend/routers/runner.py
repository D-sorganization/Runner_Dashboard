"""Runner control and management routes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from cache_utils import cache_get, cache_set
from dashboard_config import (
    FLEET_NODES,
    HOSTNAME,
    MACHINE_ROLE,
    MAX_RUNNERS,
    NUM_RUNNERS,
    ORG,
    RUNNER_ALIASES,
    RUNNER_BASE_DIR,
    RUNNER_SCHEDULE_CONFIG,
    RUNNER_SCHEDULER_BIN,
    RUNNER_SCHEDULER_SERVICE,
    RUNNER_SCHEDULER_STATE,
    RUNNER_SCHEDULER_APPLY_CMD,
    SYSTEMCTL_BIN,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from gh_utils import gh_api_admin
from identity import Principal, require_scope
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub
from system_utils import run_cmd
import config_schema
import shlex

UTC = timezone.utc  # noqa: UP017
log = logging.getLogger("dashboard.runner")
router = APIRouter(tags=["runners"])

# Default runner schedule configuration
DEFAULT_RUNNER_SCHEDULE = {
    "enabled": True,
    "timezone": os.environ.get("RUNNER_SCHEDULE_TIMEZONE", "America/Los_Angeles"),
    "default_count": min(NUM_RUNNERS, int(os.environ.get("RUNNER_SCHEDULE_DEFAULT", "4"))),
    "schedules": [
        {
            "name": "day",
            "days": ["mon", "tue", "wed", "thu", "fri"],
            "start": "07:00",
            "end": "22:00",
            "runners": min(NUM_RUNNERS, 4),
        },
        {
            "name": "overnight",
            "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "start": "22:00",
            "end": "07:00",
            "runners": NUM_RUNNERS,
        },
        {
            "name": "weekend",
            "days": ["sat", "sun"],
            "start": "07:00",
            "end": "22:00",
            "runners": min(NUM_RUNNERS, 6),
        },
    ],
}


def _runner_limit() -> int:
    """Return the hard runner capacity this dashboard is allowed to manage."""
    return max(NUM_RUNNERS, MAX_RUNNERS)


def runner_svc_path(runner_num: int) -> Path:
    """Return the path to a runner's svc.sh script."""
    return RUNNER_BASE_DIR / f"runner-{runner_num}" / "svc.sh"


async def run_runner_svc(runner_num: int, action: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a generated GitHub runner svc.sh from its own runner directory."""
    svc_path = runner_svc_path(runner_num)
    return await run_cmd(["sudo", str(svc_path), action], timeout=timeout, cwd=svc_path.parent)


def runner_num_from_id(runner_id: int, runners: list[dict]) -> int | None:
    """Extract local 1-based runner index from a GitHub runner dict's ID."""
    import platform

    local_names = {
        HOSTNAME.lower(),
        platform.node().lower(),
        *(alias.lower() for alias in RUNNER_ALIASES),
    }
    for r in runners:
        name = r.get("name", "")
        parts = name.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit() and r["id"] == runner_id:
            machine = parts[0].removeprefix("d-sorg-local-").lower()
            if machine not in local_names:
                return None
            return int(parts[1])
    return None


def _runner_sort_key(runner: dict) -> tuple[str, int, str]:
    """Sort runner names by machine and numeric suffix instead of alphabetically."""
    name = str(runner.get("name", ""))
    prefix, sep, suffix = name.rpartition("-")
    number = int(suffix) if sep and suffix.isdigit() else 10**9
    return (prefix.lower(), number, name.lower())


def get_runner_service_name(runner_num: int) -> str | None:
    """Get the systemd service name for a runner."""
    svc_file = RUNNER_BASE_DIR / f"runner-{runner_num}" / ".service"
    if svc_file.exists():
        return svc_file.read_text().strip()
    # Fall back to common naming pattern
    return f"actions.runner.{ORG}.d-sorg-local-{HOSTNAME}-{runner_num}.service"


def _validate_hhmm(value: object) -> str:
    """Validate HH:MM time format."""
    import re

    if not isinstance(value, str) or not re.match(r"^\d{2}:\d{2}$", value):
        raise ValueError("time values must use HH:MM format")
    hour, minute = [int(part) for part in value.split(":", 1)]
    if hour > 23 or minute > 59:
        raise ValueError("time values must be valid HH:MM clock times")
    return value


def _validate_runner_schedule(config: dict) -> dict:
    """Validate and normalize runner schedule configuration."""
    if not isinstance(config, dict):
        raise ValueError("schedule config must be an object")
    days_allowed = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    sanitized: dict[str, Any] = {
        "enabled": bool(config.get("enabled", True)),
        "timezone": str(config.get("timezone") or "America/Los_Angeles"),
        "default_count": max(0, min(_runner_limit(), int(config.get("default_count", 1)))),
        "schedules": [],
    }
    schedules = config.get("schedules", [])
    if not isinstance(schedules, list):
        raise ValueError("schedules must be a list")
    for entry in schedules:
        if not isinstance(entry, dict):
            raise ValueError("each schedule entry must be an object")
        days = entry.get("days", [])
        if not isinstance(days, list) or not days:
            raise ValueError("each schedule entry needs at least one day")
        normalized_days = [str(day).lower() for day in days]
        if any(day not in days_allowed for day in normalized_days):
            raise ValueError("schedule days must be mon/tue/wed/thu/fri/sat/sun")
        runners = max(0, min(_runner_limit(), int(entry.get("runners", 0))))
        sanitized["schedules"].append(
            {
                "name": str(entry.get("name") or "scheduled"),
                "days": normalized_days,
                "start": _validate_hhmm(entry.get("start")),
                "end": _validate_hhmm(entry.get("end")),
                "runners": runners,
            }
        )
    return sanitized


def _load_runner_schedule_config() -> dict:
    """Load runner schedule configuration from disk."""
    raw = config_schema.safe_read_json(RUNNER_SCHEDULE_CONFIG, DEFAULT_RUNNER_SCHEDULE)
    return _validate_runner_schedule(raw)


def _write_runner_schedule_config(config: dict) -> None:
    """Write validated runner schedule configuration to disk."""
    try:
        config_schema.validate_runner_schedule_config(config)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    config_schema.atomic_write_json(RUNNER_SCHEDULE_CONFIG, config)


def _runner_scheduler_apply_command() -> list[str]:
    """Get the command to apply runner scheduler state."""
    if RUNNER_SCHEDULER_APPLY_CMD.strip():
        return shlex.split(RUNNER_SCHEDULER_APPLY_CMD)
    return ["sudo", "-n", SYSTEMCTL_BIN, "start", RUNNER_SCHEDULER_SERVICE]


def _unit_active_sync(unit: str) -> bool:
    """Check if a systemd unit is currently active."""
    if os.name == "nt":
        return False
    from utils.utilities import safe_subprocess_env

    try:
        result = subprocess.run(
            [SYSTEMCTL_BIN, "is-active", "--quiet", unit],
            timeout=5,
            check=False,
            env=safe_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _sync_runner_scheduler_state(config: dict) -> dict:
    """Sync runner scheduler state from the runner-scheduler binary."""
    from utils.utilities import safe_subprocess_env

    if not Path(RUNNER_SCHEDULER_BIN).exists():
        return {
            "available": False,
            "error": f"{RUNNER_SCHEDULER_BIN} is not installed",
            "config": config,
        }
    env = safe_subprocess_env()
    env["RUNNER_ROOT"] = str(RUNNER_BASE_DIR)
    env["RUNNER_SCHEDULE_CONFIG"] = str(RUNNER_SCHEDULE_CONFIG)
    env["RUNNER_SCHEDULER_STATE"] = str(RUNNER_SCHEDULER_STATE)
    try:
        result = subprocess.run(
            [RUNNER_SCHEDULER_BIN, "--dry-run", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc), "config": config}
    if result.returncode != 0:
        return {
            "available": True,
            "error": (result.stderr or result.stdout).strip()[:500],
            "config": config,
        }
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "available": True,
            "error": "scheduler returned invalid JSON",
            "config": config,
        }
    state["available"] = True
    return state


def get_runner_capacity_snapshot() -> dict:
    """Get current runner capacity snapshot including schedule state."""
    config_error = None
    try:
        config = _load_runner_schedule_config()
        state = _sync_runner_scheduler_state(config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        config = _validate_runner_schedule(DEFAULT_RUNNER_SCHEDULE)
        config_error = str(exc)
        state = {
            "available": False,
            "error": str(exc),
            "config": config,
        }
    scheduler_active = _unit_active_sync(RUNNER_SCHEDULER_SERVICE) if Path(RUNNER_SCHEDULER_BIN).exists() else False
    return {
        "machine": HOSTNAME,
        "aliases": RUNNER_ALIASES,
        "scheduler": state,
        "scheduler_active": scheduler_active,
        "installed_runners": sum(1 for path in RUNNER_BASE_DIR.glob("runner-*") if path.is_dir()),
        "configured_runners": NUM_RUNNERS,
        "default_runners": 12,
        "capacity": state.get("state", {}),
        "config_path": str(RUNNER_SCHEDULE_CONFIG),
        "error": config_error,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _is_matlab_runner(runner: dict) -> bool:
    """Return True if the runner appears to be a MATLAB-capable runner."""
    name = str(runner.get("name", "")).lower()
    if "matlab" in name:
        return True
    for label in runner.get("labels", []) or []:
        lname = str(label.get("name", "")).lower() if isinstance(label, dict) else str(label).lower()
        if lname == "matlab" or lname.startswith("windows-matlab") or lname.startswith("d-sorg-matlab"):
            return True
    return False


def _matlab_runner_summary(runner: dict) -> dict:
    """Project a GitHub runner record into the MATLAB health shape."""
    labels = [lbl.get("name") if isinstance(lbl, dict) else str(lbl) for lbl in (runner.get("labels") or [])]
    status = str(runner.get("status", "unknown")).lower()
    busy = bool(runner.get("busy"))
    name = str(runner.get("name", ""))
    if name.endswith("-scheduled") or "scheduled-task" in name.lower():
        persistence = "scheduled_task"
    elif status == "offline":
        persistence = "unknown"
    else:
        persistence = "windows_service"
    return {
        "id": runner.get("id"),
        "name": name,
        "status": status,
        "busy": busy,
        "labels": labels,
        "os": runner.get("os"),
        "persistence": persistence,
    }


async def _recent_matlab_workflow_runs(limit: int = 5) -> list[dict]:
    """Fetch recent MATLAB Code Analyzer workflow runs across the org.

    Note: This requires _get_recent_org_repos to be injected from server.py to avoid circular imports.
    For now returns empty list as a safe default.
    """
    # TODO: Inject _get_recent_org_repos dependency from server.py
    return []

    # Original implementation (commented pending dependency injection):
    # try:
    #     repos = await _get_recent_org_repos(limit=15)
    # except Exception:  # pragma: no cover - defensive  # noqa: BLE001
    #     return []
    if not repos:
        return []

    async def _runs_for_repo(repo_name: str) -> list[dict]:
        try:
            data = await gh_api_admin(f"/repos/{ORG}/{repo_name}/actions/runs?per_page=10")
        except Exception:  # noqa: BLE001
            return []
        out = []
        for run in data.get("workflow_runs", []) or []:
            wf_name = str(run.get("name") or "").lower()
            wf_path = str(run.get("path") or "").lower()
            if "matlab" in wf_name or "matlab" in wf_path or "code analyzer" in wf_name:
                out.append(
                    {
                        "repo": repo_name,
                        "name": run.get("name"),
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                        "html_url": run.get("html_url"),
                        "created_at": run.get("created_at"),
                        "run_id": run.get("id"),
                    }
                )
        return out

    try:
        nested = await asyncio.gather(
            *[_runs_for_repo(r["name"]) for r in repos[:10]],
            return_exceptions=True,
        )
    except Exception:  # noqa: BLE001
        return []
    flat: list[dict] = []
    for item in nested:
        if isinstance(item, list):
            flat.extend(item)
    flat.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return flat[:limit]


async def _fleet_control_local(action: str) -> dict:
    """Scale runners on this machine only."""
    data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
    runners = data.get("runners", [])
    results = []

    log.info("Local runner control on %s: %s", HOSTNAME, action)

    if action == "all-up":
        for i in range(1, _runner_limit() + 1):
            svc = runner_svc_path(i)
            if svc.exists():
                code, _, _ = await run_runner_svc(i, "start")
                results.append({"runner": i, "action": "start", "success": code == 0})

    elif action == "all-down":
        for i in range(1, _runner_limit() + 1):
            svc = runner_svc_path(i)
            if svc.exists():
                code, _, _ = await run_runner_svc(i, "stop")
                results.append({"runner": i, "action": "stop", "success": code == 0})

    elif action == "up":
        online_nums = set()
        for r in runners:
            if r["status"] == "online":
                num = runner_num_from_id(r["id"], runners)
                if num:
                    online_nums.add(num)
        for i in range(1, _runner_limit() + 1):
            if i not in online_nums:
                svc = runner_svc_path(i)
                if svc.exists():
                    code, _, _ = await run_runner_svc(i, "start")
                    results.append(
                        {
                            "runner": i,
                            "action": "start",
                            "success": code == 0,
                        }
                    )
                    break

    elif action == "down":
        idle_runners = []
        for r in runners:
            if r["status"] == "online" and not r.get("busy"):
                num = runner_num_from_id(r["id"], runners)
                if num:
                    idle_runners.append(num)
        if idle_runners:
            target = max(idle_runners)
            svc = runner_svc_path(target)
            if svc.exists():
                code, _, _ = await run_runner_svc(target, "stop")
                results.append(
                    {
                        "runner": target,
                        "action": "stop",
                        "success": code == 0,
                    }
                )
        else:
            raise HTTPException(status_code=400, detail="No idle runners to stop")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    return {"machine": HOSTNAME, "action": action, "results": results}


async def _remote_fleet_control(name: str, url: str, action: str) -> dict:
    """Ask a node dashboard to apply a runner action locally."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(f"{url}/api/fleet/control/{action}?local=1")
        if resp.status_code != 200:
            return {
                "machine": name,
                "url": url,
                "success": False,
                "status_code": resp.status_code,
                "error": resp.text[:500],
            }
        data = resp.json()
        return {
            "machine": name,
            "url": url,
            "success": True,
            "result": data,
        }
    except Exception as exc:  # noqa: BLE001 - remote nodes may be offline
        return {"machine": name, "url": url, "success": False, "error": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/api/runners")
async def get_runners(request: Request):
    """Get all org runners with their status."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = cache_get("runners", 60.0)
    if cached is not None:
        cached["runners"] = sorted(cached.get("runners", []), key=_runner_sort_key)
        return cached
    data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
    data["runners"] = sorted(data.get("runners", []), key=_runner_sort_key)
    cache_set("runners", data)
    return data


@router.get("/api/runners/matlab")
async def get_matlab_runner_health(request: Request) -> dict:
    """Surface Windows MATLAB runner health for the dashboard (issue #570).

    Response shape::

        {
          "runners": [...],          # MATLAB runner summaries
          "total": int,
          "online": int,
          "busy": int,
          "offline": int,
          "capacity_available": bool, # true iff an idle online runner exists
          "warning": str | None,      # actionable message when capacity is zero
          "recent_workflow_runs": [...],
          "generated_at": "..."
        }

    Always returns 200; absence of runners is represented explicitly so the UI
    can render an actionable warning instead of a spinner.
    """
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = cache_get("matlab_runner_health", 45.0)
    if cached is not None:
        return cached

    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        all_runners = data.get("runners", []) or []
    except Exception as exc:  # pragma: no cover - defensive  # noqa: BLE001
        all_runners = []
        api_error: str | None = f"GitHub runner API unavailable: {exc}"
    else:
        api_error = None

    matlab = [r for r in all_runners if _is_matlab_runner(r)]
    summaries = [_matlab_runner_summary(r) for r in matlab]

    online = sum(1 for r in summaries if r["status"] == "online")
    busy = sum(1 for r in summaries if r["busy"])
    offline = sum(1 for r in summaries if r["status"] != "online")
    idle_online = sum(1 for r in summaries if r["status"] == "online" and not r["busy"])

    warning: str | None = None
    if not summaries:
        warning = (
            "No Windows MATLAB runners are registered. MATLAB Code Analyzer "
            "jobs will queue indefinitely. See "
            "docs/operations/matlab_windows_runner.md to register a runner."
        )
    elif online == 0:
        warning = (
            "All MATLAB runners are offline. Start the Windows runner service "
            "on the ControlTower host to restore MATLAB lint capacity."
        )
    elif idle_online == 0:
        warning = "All MATLAB runners are currently busy. New MATLAB lint jobs will queue until one frees up."

    recent = await _recent_matlab_workflow_runs(limit=5)  # TODO: Remove when dependency injection is set up

    result = {
        "runners": summaries,
        "total": len(summaries),
        "online": online,
        "busy": busy,
        "offline": offline,
        "capacity_available": idle_online > 0,
        "warning": warning,
        "api_error": api_error,
        "recent_workflow_runs": recent,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    cache_set("matlab_runner_health", result)
    return result


@router.post("/api/runners/{runner_id}/stop")
async def stop_runner(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("runners.control")),  # noqa: B008
    runner_id: int,  # noqa: B008
):  # noqa: B008
    """Stop a specific runner's systemd service."""
    data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
    runners = data.get("runners", [])
    num = runner_num_from_id(runner_id, runners)

    if num is None:
        msg = f"Runner ID {runner_id} not found locally"
        raise HTTPException(status_code=404, detail=msg)

    svc_path = runner_svc_path(num)
    if not svc_path.exists():
        raise HTTPException(status_code=404, detail=f"Runner {num} svc.sh not found")

    log.info("Stopping runner %d (GitHub ID: %d)", num, runner_id)
    code, stdout, stderr = await run_runner_svc(num, "stop")
    if code != 0:
        log.warning("Failed to stop runner %d: %s", num, stderr[:200])
        raise HTTPException(status_code=500, detail=f"Failed to stop runner {num}")

    return {"status": "stopped", "runner": num, "output": stdout.strip()}


@router.post("/api/runners/{runner_id}/start")
async def start_runner(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("runners.control")),  # noqa: B008
    runner_id: int,  # noqa: B008
):  # noqa: B008
    """Start a specific runner's systemd service."""
    data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
    runners = data.get("runners", [])
    num = runner_num_from_id(runner_id, runners)

    if num is None:
        msg = f"Runner ID {runner_id} not found locally"
        raise HTTPException(status_code=404, detail=msg)

    svc_path = runner_svc_path(num)
    if not svc_path.exists():
        raise HTTPException(status_code=404, detail=f"Runner {num} svc.sh not found")

    log.info("Starting runner %d (GitHub ID: %d)", num, runner_id)
    code, stdout, stderr = await run_runner_svc(num, "start")
    if code != 0:
        log.warning("Failed to start runner %d: %s", num, stderr[:200])
        raise HTTPException(status_code=500, detail=f"Failed to start runner {num}")

    return {"status": "started", "runner": num, "output": stdout.strip()}


@router.post("/api/fleet/control/{action}")
async def fleet_control(
    action: str,
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
):  # noqa: B008
    """Scale runners from any dashboard.

    Nodes proxy fleet-wide requests to the hub. The hub applies the action
    locally and fans it out to configured nodes. Internal fan-out calls use
    ``?local=1`` so each node controls its own runner services.
    """
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    scope = request.query_params.get("scope", "fleet")
    should_fan_out = MACHINE_ROLE == "hub" and scope != "local" and bool(FLEET_NODES)
    local_machine = HOSTNAME
    try:
        local_result = await _fleet_control_local(action)
        local_machine = local_result.get("machine", HOSTNAME)
        local_node_result = {
            "machine": local_machine,
            "url": f"http://localhost:{os.environ.get('DASHBOARD_PORT', '8321')}",
            "success": True,
            "result": local_result,
        }
    except HTTPException as exc:
        if not should_fan_out:
            raise
        local_result = {"machine": HOSTNAME, "action": action, "results": []}
        local_node_result = {
            "machine": HOSTNAME,
            "url": f"http://localhost:{os.environ.get('DASHBOARD_PORT', '8321')}",
            "success": False,
            "status_code": exc.status_code,
            "error": str(exc.detail),
        }
    node_results = [local_node_result]

    if should_fan_out:
        remotes = await asyncio.gather(*[_remote_fleet_control(name, url, action) for name, url in FLEET_NODES.items()])
        node_results.extend(remotes)

    return {
        "action": action,
        "scope": "local" if scope == "local" else "fleet",
        "machine": local_machine,
        "results": local_result["results"],
        "nodes": node_results,
    }


@router.get("/api/fleet/schedule")
async def get_runner_schedule() -> dict:
    """Return this machine's local runner capacity schedule and live state."""
    return get_runner_capacity_snapshot()


@router.get("/api/fleet/capacity")
async def get_fleet_capacity() -> dict:
    """Compatibility endpoint for dashboard capacity summaries."""
    return get_runner_capacity_snapshot()


@router.post("/api/fleet/schedule")
async def update_runner_schedule(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict:
    """Update this machine's local runner capacity schedule."""
    from utils.utilities import safe_subprocess_env

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="schedule payload must be an object")
    try:
        config = _validate_runner_schedule(body.get("schedule", body))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _write_runner_schedule_config(config)
    apply_now = bool(body.get("apply", False))
    apply_result: dict[str, object] | None = None
    if apply_now and Path(RUNNER_SCHEDULER_BIN).exists():
        env = safe_subprocess_env()
        env["RUNNER_ROOT"] = str(RUNNER_BASE_DIR)
        env["RUNNER_SCHEDULE_CONFIG"] = str(RUNNER_SCHEDULE_CONFIG)
        env["RUNNER_SCHEDULER_STATE"] = str(RUNNER_SCHEDULER_STATE)
        apply_cmd = _runner_scheduler_apply_command()
        result = subprocess.run(
            apply_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
        apply_result = {
            "returncode": result.returncode,
            "stdout": result.stdout.strip()[:1000],
            "stderr": result.stderr.strip()[:1000],
        }
        if result.returncode != 0:
            error = apply_result["stderr"] or apply_result["stdout"]
            raise HTTPException(
                status_code=500,
                detail=f"Schedule saved, but apply failed: {error}",
            )
    return {
        "saved": True,
        "applied": apply_now,
        "apply_result": apply_result,
        **get_runner_capacity_snapshot(),
    }
