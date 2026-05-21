"""Fleet node aggregation and classification helpers.

Extracted from server.py (issue #2942).

Responsibilities
----------------
- _classify_node_offline()     — typed offline-reason classification
- _resource_offline_reason()   — resource-pressure offline classification
- _node_visibility_snapshot()  — telemetry visibility label
- _machine_name_from_runner_name() — runner → machine name mapping
- _placement_from_jobs()       — runner placement fields from job list
- _repo_name_from_run()        — extract repo name from run dict
- _enrich_run_with_job_placement() — attach placement to a run (async)
- collect_live_fleet_nodes()   — gather all fleet nodes concurrently
- get_fleet_nodes_impl()       — full aggregated fleet response
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

from dashboard_config.timeouts import HttpTimeout, ResourceThreshold
from machine_registry import load_machine_registry, merge_registry_with_live_nodes

log = logging.getLogger("dashboard")


# ---------------------------------------------------------------------------
# Module-level configuration (injected by server.py)
# ---------------------------------------------------------------------------

_ORG: str = "D-sorganization"
_HOSTNAME: str = ""
_PORT: int = 8321
_MACHINE_ROLE: str = "node"
_FLEET_NODES: dict[str, str] = {}
_BACKEND_DIR: Path = Path(__file__).resolve().parent
_RUN_CMD: Any = None  # injected: async (cmd, timeout, cwd) -> (code, out, err)
_GET_SYSTEM_METRICS_SNAPSHOT: Any = None  # injected coroutine
_HEALTH_IMPL: Any = None  # injected coroutine
_HUB_URL: str | None = None


def configure(
    *,
    org: str,
    hostname: str,
    port: int,
    machine_role: str,
    fleet_nodes: dict[str, str],
    backend_dir: Path,
    run_cmd: Any,
    get_system_metrics_snapshot: Any,
    health_impl: Any,
    hub_url: str | None,
) -> None:
    """Inject runtime dependencies from server.py (called once at startup)."""
    global _ORG, _HOSTNAME, _PORT, _MACHINE_ROLE, _FLEET_NODES
    global _BACKEND_DIR, _RUN_CMD, _GET_SYSTEM_METRICS_SNAPSHOT, _HEALTH_IMPL, _HUB_URL
    _ORG = org
    _HOSTNAME = hostname
    _PORT = port
    _MACHINE_ROLE = machine_role
    _FLEET_NODES = fleet_nodes
    _BACKEND_DIR = backend_dir
    _RUN_CMD = run_cmd
    _GET_SYSTEM_METRICS_SNAPSHOT = get_system_metrics_snapshot
    _HEALTH_IMPL = health_impl
    _HUB_URL = hub_url


# ---------------------------------------------------------------------------
# Offline classification
# ---------------------------------------------------------------------------


def _classify_node_offline(
    exc: Exception | None = None,
    *,
    status_code: int | None = None,
) -> dict:
    """Classify why a fleet node is not fully reachable.

    Uses typed exception checks (httpx exception hierarchy and OSError.errno)
    rather than fragile substring matching on str(exc).

    Postcondition: result has 'offline_reason' and 'offline_detail' keys.
    """
    if status_code is not None:
        return {
            "offline_reason": "dashboard_unhealthy",
            "offline_detail": f"Dashboard returned HTTP {status_code}",
        }
    if isinstance(exc, httpx.TimeoutException):
        return {
            "offline_reason": "computer_offline",
            "offline_detail": "Dashboard host timed out over the fleet network.",
        }
    if isinstance(exc, httpx.ConnectError):
        cause = exc.__cause__ or exc
        os_error = cause if isinstance(cause, OSError) else None
        if os_error and os_error.errno == errno.ECONNREFUSED:
            return {
                "offline_reason": "wsl_connection_lost",
                "offline_detail": (
                    "Host is reachable, but the dashboard port refused the connection. "
                    "WSL, systemd, or the dashboard service is likely stopped."
                ),
            }
        if os_error and os_error.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ECONNRESET}:
            return {
                "offline_reason": "computer_offline",
                "offline_detail": "Fleet network could not reach the computer.",
            }
        return {
            "offline_reason": "wsl_connection_lost",
            "offline_detail": "Dashboard port refused the connection.",
        }
    return {
        "offline_reason": "unknown",
        "offline_detail": str(exc) if exc else "Dashboard node is unreachable.",
    }


def _resource_offline_reason(system: dict) -> dict | None:
    """Return a resource-monitor reason dict when local metrics indicate throttling.

    Returns None when no resource pressure is detected.
    """
    cpu = system.get("cpu") or {}
    memory = system.get("memory") or {}
    disk = system.get("disk") or {}
    pressure: list[str] = []
    if (cpu.get("percent_1m_avg") or cpu.get("percent") or 0) >= ResourceThreshold.CPU_HARD_STOP_PERCENT:
        pressure.append(f"CPU >= {ResourceThreshold.CPU_HARD_STOP_PERCENT:g}%")
    if (memory.get("percent") or 0) >= ResourceThreshold.MEMORY_CRITICAL_PERCENT:
        pressure.append(f"memory >= {ResourceThreshold.MEMORY_CRITICAL_PERCENT:g}%")
    if (disk.get("pressure") or {}).get("status") == "critical":
        pressure.append("disk pressure critical")
    elif (disk.get("percent") or 0) >= ResourceThreshold.DISK_HARD_STOP_PERCENT:
        pressure.append(f"disk >= {ResourceThreshold.DISK_HARD_STOP_PERCENT:g}%")
    if not pressure:
        return None
    return {
        "offline_reason": "resource_monitoring",
        "offline_detail": "Resource pressure detected: " + ", ".join(pressure),
    }


# ---------------------------------------------------------------------------
# Visibility / telemetry labels
# ---------------------------------------------------------------------------


def _node_visibility_snapshot(node: dict) -> dict:
    """Summarize how much useful telemetry a node currently exposes.

    Postcondition: result has 'visibility_state', 'visibility_label',
        'visibility_tone', 'visibility_detail' keys.
    """
    online = bool(node.get("online"))
    dashboard_reachable = node.get("dashboard_reachable") is not False
    has_system_metrics = bool(node.get("system"))
    resource_pressure = node.get("offline_reason") == "resource_monitoring"

    if resource_pressure:
        return {
            "visibility_state": "degraded",
            "visibility_label": "Degraded",
            "visibility_tone": "yellow",
            "visibility_detail": (
                node.get("offline_detail") or "Resource pressure is high enough to warrant attention."
            ),
        }

    if online and dashboard_reachable and has_system_metrics:
        return {
            "visibility_state": "full_telemetry",
            "visibility_label": "Full telemetry",
            "visibility_tone": "green",
            "visibility_detail": "Runner status and system metrics are both available.",
        }

    if online:
        return {
            "visibility_state": "runners_only",
            "visibility_label": "Runners only",
            "visibility_tone": "orange",
            "visibility_detail": "Runner registrations are healthy, but dashboard telemetry is unavailable.",
        }

    if dashboard_reachable:
        return {
            "visibility_state": "dashboard_only",
            "visibility_label": "Dashboard only",
            "visibility_tone": "blue",
            "visibility_detail": "Dashboard is reachable, but runner registrations are offline.",
        }

    return {
        "visibility_state": "offline",
        "visibility_label": "Offline",
        "visibility_tone": "red",
        "visibility_detail": node.get("offline_detail") or node.get("error") or "No live telemetry from this machine.",
    }


# ---------------------------------------------------------------------------
# Runner / run helpers
# ---------------------------------------------------------------------------


def _machine_name_from_runner_name(runner_name: str | None) -> str | None:
    """Normalize fleet runner names to dashboard machine names.

    Strips the 'd-sorg-local-' prefix and numeric suffix.
    """
    if not runner_name:
        return None
    name = str(runner_name).strip()
    prefix = "d-sorg-local-"
    if not name.startswith(prefix):
        return name
    stem = name.removeprefix(prefix)
    machine, separator, suffix = stem.rpartition("-")
    if separator and suffix.isdigit() and machine:
        return machine
    return stem


def _placement_from_jobs(jobs: list[dict]) -> dict:
    """Extract machine placement fields from a run's jobs.

    Returns the placement dict from the first job that has a runner_name,
    or an empty dict when no placed jobs are found.
    """
    for job in jobs:
        runner_name = job.get("runner_name")
        if not runner_name:
            continue
        return {
            "runner_id": job.get("runner_id"),
            "runner_name": runner_name,
            "runner_group_name": job.get("runner_group_name"),
            "runner_labels": job.get("labels") or [],
            "machine_name": _machine_name_from_runner_name(str(runner_name)),
        }
    return {}


def _repo_name_from_run(run: dict) -> str | None:
    """Return the repository name from either normalized or raw run payloads."""
    repo = run.get("repository")
    if isinstance(repo, dict) and repo.get("name"):
        return str(repo["name"])
    if run.get("_repo"):
        return str(run["_repo"])
    return None


# ---------------------------------------------------------------------------
# Async fetch helpers
# ---------------------------------------------------------------------------


async def _fetch_run_jobs(repo_name: str, run_id: int | str) -> list[dict]:
    """Fetch job-level data for one workflow run."""
    if _RUN_CMD is None:
        return []
    rc, out, _ = await _RUN_CMD(
        ["gh", "api", f"/repos/{_ORG}/{repo_name}/actions/runs/{run_id}/jobs?per_page=100"],
        timeout=10,
    )
    if rc != 0:
        return []
    try:
        return json.loads(out).get("jobs", [])
    except (json.JSONDecodeError, ValueError):
        return []


async def _enrich_run_with_job_placement(run: dict) -> dict:
    """Attach job-level runner placement fields to a workflow run."""
    item = dict(run)
    repo_name = _repo_name_from_run(item)
    run_id = item.get("id")
    if repo_name and run_id:
        placement = _placement_from_jobs(await _fetch_run_jobs(repo_name, run_id))
        if placement:
            item.update(placement)
            return item
    machine_name = _machine_name_from_runner_name(item.get("runner_name"))
    item.setdefault("machine_name", machine_name or "GitHub")
    return item


# ---------------------------------------------------------------------------
# Fleet node collection
# ---------------------------------------------------------------------------


async def _collect_live_fleet_nodes() -> list[dict]:
    """Collect the live fleet node payload before registry metadata is merged."""
    import datetime as _dt_mod  # noqa: PLC0415

    _UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)

    async def fetch_node(name: str, url: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=HttpTimeout.PROXY_NODE_SYSTEM_S) as client:
                sys_r, health_r = await asyncio.gather(
                    client.get(f"{url}/api/system"),
                    client.get(f"{url}/api/health"),
                )
            if sys_r.status_code != 200 or health_r.status_code != 200:
                status_code = sys_r.status_code if sys_r.status_code != 200 else health_r.status_code
                reason = _classify_node_offline(status_code=status_code)
                return {
                    "name": name,
                    "url": url,
                    "online": False,
                    "dashboard_reachable": True,
                    "is_local": False,
                    "role": "node",
                    "system": sys_r.json() if sys_r.status_code == 200 else {},
                    "health": health_r.json() if health_r.status_code == 200 else {},
                    "last_seen": None,
                    "error": reason["offline_detail"],
                    **reason,
                }
            system = sys_r.json()
            resource_reason = _resource_offline_reason(system)
            return {
                "name": name,
                "url": url,
                "online": True,
                "dashboard_reachable": True,
                "is_local": False,
                "role": "node",
                "system": system,
                "hardware_specs": system.get("hardware_specs", {}),
                "workload_capacity": system.get("workload_capacity", {}),
                "health": health_r.json(),
                "last_seen": _dt_mod.datetime.now(_UTC).isoformat(),
                "error": None,
                "offline_reason": (resource_reason["offline_reason"] if resource_reason else None),
                "offline_detail": (resource_reason["offline_detail"] if resource_reason else None),
            }
        except Exception as exc:  # noqa: BLE001
            reason = _classify_node_offline(exc)
            return {
                "name": name,
                "url": url,
                "online": False,
                "dashboard_reachable": False,
                "is_local": False,
                "role": "node",
                "system": {},
                "health": {},
                "last_seen": None,
                "error": reason["offline_detail"],
                **reason,
            }

    assert _GET_SYSTEM_METRICS_SNAPSHOT is not None, "configure() must be called before use"
    assert _HEALTH_IMPL is not None, "configure() must be called before use"

    local_sys = await _GET_SYSTEM_METRICS_SNAPSHOT()
    local_health = await _HEALTH_IMPL()
    local_resource_reason = _resource_offline_reason(local_sys)
    nodes: list[dict] = [
        {
            "name": _HOSTNAME,
            "url": f"http://localhost:{_PORT}",
            "online": True,
            "dashboard_reachable": True,
            "is_local": True,
            "role": _MACHINE_ROLE,
            "system": local_sys,
            "hardware_specs": local_sys.get("hardware_specs", {}),
            "workload_capacity": local_sys.get("workload_capacity", {}),
            "health": local_health,
            "last_seen": None,
            "error": None,
            "offline_reason": (local_resource_reason["offline_reason"] if local_resource_reason else None),
            "offline_detail": (local_resource_reason["offline_detail"] if local_resource_reason else None),
        }
    ]

    if _FLEET_NODES:
        remote = await asyncio.gather(*[fetch_node(name, url) for name, url in _FLEET_NODES.items()])
        nodes.extend(remote)

    return nodes


async def get_fleet_nodes_impl() -> dict:
    """Aggregate system metrics + health from all fleet nodes.

    Always includes this machine (no HTTP round-trip).  Remote nodes are
    queried concurrently over Tailscale using FLEET_NODES config.
    Offline nodes are included with online=False so the UI can show them.

    Postcondition: result has 'nodes', 'count', 'online_count', 'total_runners'.
    """
    nodes = await _collect_live_fleet_nodes()
    try:
        registry = load_machine_registry()
    except Exception as exc:  # noqa: BLE001
        log.warning("Machine registry load failed: %s", exc)
        registry = {"version": 1, "machines": []}
    nodes = merge_registry_with_live_nodes(nodes, registry)
    nodes = [{**node, **_node_visibility_snapshot(node)} for node in nodes]
    online = sum(1 for n in nodes if n["online"])
    total_runners = sum(n["health"].get("runners_registered", 0) for n in nodes)
    result = {
        "nodes": nodes,
        "count": len(nodes),
        "online_count": online,
        "total_runners": total_runners,
        "registry": {
            "path": str(_BACKEND_DIR / "machine_registry.yml"),
            "version": registry.get("version", 1),
            "machines": len(registry.get("machines", [])),
        },
    }
    assert "nodes" in result and "count" in result
    return result


# ---------------------------------------------------------------------------
# Deployment helpers (also used by deployment router)
# ---------------------------------------------------------------------------


def _normalize_repository_input(
    value: str,
    org: str,
) -> tuple[str, str]:
    """Return (repo_name, full_name) for dashboard remediation inputs.

    Validates against a strict regex before any owner comparison or subprocess
    interpolation to prevent SSRF via malformed owner/repo slugs.
    """
    from fastapi import HTTPException  # noqa: PLC0415
    from security import validate_owner_repo_format, validate_repo_slug  # noqa: PLC0415

    text = str(value).strip()
    if "/" in text:
        validate_owner_repo_format(text)
        owner, _, repo_name = text.partition("/")
        if owner.lower() != org.lower():
            raise HTTPException(status_code=422, detail=f"repository owner must be {org}")
        repo_name = validate_repo_slug(repo_name)
        return repo_name, f"{org}/{repo_name}"
    repo_name = validate_repo_slug(text)
    return repo_name, f"{org}/{repo_name}"


# ---------------------------------------------------------------------------
# GitHub API helpers (injected run_cmd)
# ---------------------------------------------------------------------------


async def _get_recent_org_repos(limit: int = 30) -> list[dict]:
    """Fetch recently updated organization repositories."""
    if _RUN_CMD is None:
        return []
    code, stdout, _ = await _RUN_CMD(
        ["gh", "api", f"/orgs/{_ORG}/repos?per_page={limit}&sort=updated&direction=desc"],
        timeout=20,
    )
    if code != 0:
        return []
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return []


async def _fetch_repo_runs(
    repo_name: str,
    *,
    per_page: int = 10,
    status: str | None = None,
) -> list[dict]:
    """Fetch workflow runs for one repository and annotate repository name."""
    if _RUN_CMD is None:
        return []
    status_part = f"&status={status}" if status else ""
    rc, out, _ = await _RUN_CMD(
        ["gh", "api", f"/repos/{_ORG}/{repo_name}/actions/runs?per_page={per_page}{status_part}"],
        timeout=15,
    )
    if rc != 0:
        return []
    try:
        runs = json.loads(out).get("workflow_runs", [])
    except (json.JSONDecodeError, ValueError):
        return []
    for run in runs:
        if "repository" not in run or not run["repository"]:
            run["repository"] = {"name": repo_name}
    return runs


async def _github_search_total(query: str) -> int:
    """Return the GitHub Search API total_count for a query."""
    if _RUN_CMD is None:
        return 0
    code, stdout, _ = await _RUN_CMD(
        ["gh", "api", f"search/issues?q={query}&per_page=1"],
        timeout=15,
    )
    if code != 0:
        return 0
    try:
        return int(json.loads(stdout).get("total_count", 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0


async def _fetch_failed_log_excerpt(repo_name: str, run_id: int | str) -> str:
    """Best-effort failed-log excerpt for a workflow run."""
    if _RUN_CMD is None:
        return ""
    code, stdout, _ = await _RUN_CMD(
        ["gh", "run", "view", str(run_id), "--repo", f"{_ORG}/{repo_name}", "--log-failed"],
        timeout=20,
    )
    if code != 0:
        return ""
    text = stdout.strip()
    return text[:12000] if text else ""


# ---------------------------------------------------------------------------
# Fleet runner control
# ---------------------------------------------------------------------------

_RUNNER_BASE_DIR: Path = Path.home() / "actions-runners"
_NUM_RUNNERS_FLEET: int = 12


def configure_runner_control(
    *,
    runner_base_dir: Path,
    num_runners: int,
) -> None:
    """Inject runner control configuration from server.py."""
    global _RUNNER_BASE_DIR, _NUM_RUNNERS_FLEET
    _RUNNER_BASE_DIR = runner_base_dir
    _NUM_RUNNERS_FLEET = num_runners


def _runner_limit_fleet() -> int:
    """Return the hard runner capacity this fleet node manages."""
    return _NUM_RUNNERS_FLEET


async def _run_runner_svc(runner_num: int, action: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a generated GitHub runner svc.sh from its own runner directory."""
    svc_path = _RUNNER_BASE_DIR / f"runner-{runner_num}" / "svc.sh"
    if _RUN_CMD is None:
        return -1, "", "run_cmd not configured"
    return await _RUN_CMD(["sudo", str(svc_path), action], timeout=timeout)


async def _fleet_control_local(action: str) -> dict:
    """Scale runners on this machine only.

    Precondition: action is one of 'all-up', 'all-down', 'up', 'down'.
    """
    assert action in {"all-up", "all-down", "up", "down"}, f"unknown action: {action}"
    from fastapi import HTTPException  # noqa: PLC0415

    if _RUN_CMD is None:
        raise HTTPException(status_code=503, detail="Fleet control not configured")

    async def _gh_api_admin(endpoint: str) -> dict:
        code, stdout, stderr = await _RUN_CMD(["gh", "api", endpoint])
        if code != 0:
            raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
        return json.loads(stdout)

    def _runner_num_from_id(runner_id: int, runners: list[dict]) -> int | None:
        local_names = {_HOSTNAME.lower()}
        for r in runners:
            name = r.get("name", "")
            parts = name.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit() and r["id"] == runner_id:
                machine = parts[0].removeprefix("d-sorg-local-").lower()
                if machine not in local_names:
                    return None
                return int(parts[1])
        return None

    data = await _gh_api_admin(f"/orgs/{_ORG}/actions/runners")
    runners = data.get("runners", [])
    results = []
    log.info("Local runner control on %s: %s", _HOSTNAME, action)

    if action == "all-up":
        for i in range(1, _runner_limit_fleet() + 1):
            svc = _RUNNER_BASE_DIR / f"runner-{i}" / "svc.sh"
            if svc.exists():
                code, _, _ = await _run_runner_svc(i, "start")
                results.append({"runner": i, "action": "start", "success": code == 0})
    elif action == "all-down":
        for i in range(1, _runner_limit_fleet() + 1):
            svc = _RUNNER_BASE_DIR / f"runner-{i}" / "svc.sh"
            if svc.exists():
                code, _, _ = await _run_runner_svc(i, "stop")
                results.append({"runner": i, "action": "stop", "success": code == 0})
    elif action == "up":
        online_nums = set()
        for r in runners:
            if r["status"] == "online":
                num = _runner_num_from_id(r["id"], runners)
                if num:
                    online_nums.add(num)
        for i in range(1, _runner_limit_fleet() + 1):
            if i not in online_nums:
                svc = _RUNNER_BASE_DIR / f"runner-{i}" / "svc.sh"
                if svc.exists():
                    code, _, _ = await _run_runner_svc(i, "start")
                    results.append({"runner": i, "action": "start", "success": code == 0})
                    break
    elif action == "down":
        idle_runners = []
        for r in runners:
            if r["status"] == "online" and not r.get("busy"):
                num = _runner_num_from_id(r["id"], runners)
                if num:
                    idle_runners.append(num)
        if idle_runners:
            target = max(idle_runners)
            svc = _RUNNER_BASE_DIR / f"runner-{target}" / "svc.sh"
            if svc.exists():
                code, _, _ = await _run_runner_svc(target, "stop")
                results.append({"runner": target, "action": "stop", "success": code == 0})
        else:
            raise HTTPException(status_code=400, detail="No idle runners to stop")

    return {"machine": _HOSTNAME, "action": action, "results": results}


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
        return {"machine": name, "url": url, "success": True, "result": data}
    except Exception as exc:  # noqa: BLE001 - remote nodes may be offline
        return {"machine": name, "url": url, "success": False, "error": str(exc)}
