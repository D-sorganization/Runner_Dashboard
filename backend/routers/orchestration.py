# ruff: noqa: B008
"""Fleet orchestration and control routes.

Extracted from server.py (issue #359).
Routes:
  GET  /api/audit
  GET  /api/fleet/audit
  GET  /api/fleet/orchestration
  POST /api/fleet/orchestration/dispatch
  POST /api/fleet/orchestration/deploy
  POST /api/fleet/control/{action}
  GET  /api/fleet/schedule
  GET  /api/fleet/capacity
  POST /api/fleet/schedule
  GET  /api/fleet/nodes
  GET  /api/fleet/hardware
  GET  /api/fleet/nodes/{node_name}/system
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt_mod
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import dispatch_contract
import httpx
import orchestration_audit as _audit
from dashboard_config import FLEET_NODES, HOSTNAME, MACHINE_ROLE, ORG, PORT, REPO_ROOT
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_principal, require_scope  # noqa: B008
from machine_registry import load_machine_registry
from security import sanitize_log_value

if TYPE_CHECKING:
    from collections.abc import Callable

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard.orchestration")
router = APIRouter(tags=["orchestration"])

# ---------------------------------------------------------------------------
# Injected dependencies (set by server.py after import)
# ---------------------------------------------------------------------------

_fleet_control_local: Callable | None = None
_remote_fleet_control: Callable | None = None
_get_fleet_nodes_impl: Callable | None = None
_proxy_to_hub: Callable | None = None
_should_proxy_fleet_to_hub: Callable | None = None
_get_runner_capacity_snapshot: Callable | None = None
_validate_runner_schedule: Callable | None = None
_write_runner_schedule_config: Callable | None = None
_runner_scheduler_apply_command: Callable | None = None
_run_cmd: Callable | None = None
_get_system_metrics_snapshot: Callable | None = None

# These are injected scalars
_runner_scheduler_bin: str = ""
_runner_schedule_config: Path | None = None
_runner_scheduler_state: Path | None = None
_runner_base_dir: Path | None = None

_DEPLOY_ACTIONS = {"sync_workflows", "restart_runner", "update_config"}


def set_dependencies(  # noqa: PLR0913
    fleet_control_local: Callable,
    remote_fleet_control: Callable,
    get_fleet_nodes_impl: Callable,
    proxy_to_hub: Callable,
    should_proxy_fleet_to_hub: Callable,
    get_runner_capacity_snapshot: Callable,
    validate_runner_schedule: Callable,
    write_runner_schedule_config: Callable,
    runner_scheduler_apply_command: Callable,
    run_cmd: Callable,
    get_system_metrics_snapshot: Callable,
    runner_scheduler_bin: str,
    runner_schedule_config: Path,
    runner_scheduler_state: Path,
    runner_base_dir: Path,
) -> None:
    """Wire server.py helpers into this router (called at startup)."""
    global _fleet_control_local, _remote_fleet_control, _get_fleet_nodes_impl  # noqa: PLW0603
    global _proxy_to_hub, _should_proxy_fleet_to_hub  # noqa: PLW0603
    global _get_runner_capacity_snapshot, _validate_runner_schedule  # noqa: PLW0603
    global _write_runner_schedule_config, _runner_scheduler_apply_command  # noqa: PLW0603
    global _run_cmd, _get_system_metrics_snapshot  # noqa: PLW0603
    global _runner_scheduler_bin, _runner_schedule_config  # noqa: PLW0603
    global _runner_scheduler_state, _runner_base_dir  # noqa: PLW0603

    _fleet_control_local = fleet_control_local
    _remote_fleet_control = remote_fleet_control
    _get_fleet_nodes_impl = get_fleet_nodes_impl
    _proxy_to_hub = proxy_to_hub
    _should_proxy_fleet_to_hub = should_proxy_fleet_to_hub
    _get_runner_capacity_snapshot = get_runner_capacity_snapshot
    _validate_runner_schedule = validate_runner_schedule
    _write_runner_schedule_config = write_runner_schedule_config
    _runner_scheduler_apply_command = runner_scheduler_apply_command
    _run_cmd = run_cmd
    _get_system_metrics_snapshot = get_system_metrics_snapshot
    _runner_scheduler_bin = runner_scheduler_bin
    _runner_schedule_config = runner_schedule_config
    _runner_scheduler_state = runner_scheduler_state
    _runner_base_dir = runner_base_dir


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/audit", tags=["fleet"])
async def get_node_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),
) -> list[dict]:
    """Return this node's orchestration audit log."""
    return _audit.load_orchestration_audit(limit=limit, principal=principal)


@router.get("/api/fleet/audit", tags=["fleet"])
async def get_fleet_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),
) -> dict:
    """Return a merged view of orchestration audit logs across the fleet."""
    local_entries = _audit.load_orchestration_audit(limit=limit, principal=principal)
    all_entries = list(local_entries)

    async def fetch_remote_audit(name: str, url: str) -> list[dict]:
        try:
            params: dict[str, Any] = {"limit": limit}
            if principal:
                params["principal"] = principal
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {}
                if auth_header := request.headers.get("Authorization"):
                    headers["Authorization"] = auth_header
                r = await client.get(f"{url}/api/audit", params=params, headers=headers)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to fetch audit from %s (%s): %s", name, url, exc)
        return []

    if FLEET_NODES:
        remotes = await asyncio.gather(*[fetch_remote_audit(n, u) for n, u in FLEET_NODES.items()])
        for r_entries in remotes:
            all_entries.extend(r_entries)

    def _parse_ts(entry: dict) -> _dt_mod.datetime:
        ts_str = entry.get("timestamp") or entry.get("ts") or ""
        try:
            return _dt_mod.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            return _dt_mod.datetime.min.replace(tzinfo=UTC)

    all_entries.sort(key=_parse_ts, reverse=True)

    return {
        "entries": all_entries[:limit],
        "count": len(all_entries[:limit]),
    }


@router.get("/api/fleet/orchestration")
async def get_fleet_orchestration(request: Request) -> dict:
    """Return per-machine job assignment, queue, and capacity for fleet orchestration view."""
    registry_data = load_machine_registry()
    machines_raw = registry_data.get("machines", [])

    try:
        fleet = await _get_fleet_nodes_impl()  # type: ignore[misc]
        live_nodes = {n.get("name", ""): n for n in fleet.get("nodes", [])}
    except Exception:  # noqa: BLE001
        live_nodes = {}

    machines = []
    for m in machines_raw:
        name = m.get("name", "")
        live = live_nodes.get(name, {})
        online = bool(live.get("online", False)) if live else False
        system_info = live.get("system", {}) if live else {}
        runners_info = live.get("runners", []) if live else []
        runner_count = len(runners_info) if isinstance(runners_info, list) else 0
        busy_count = sum(1 for r in runners_info if r.get("busy")) if runner_count else 0
        machines.append(
            {
                "name": name,
                "display_name": m.get("display_name") or name,
                "role": m.get("role", "node"),
                "online": online,
                "runner_count": runner_count,
                "busy_runners": busy_count,
                "queue_depth": max(0, busy_count),
                "last_ping": live.get("last_ping") or live.get("checked_at"),
                "dashboard_url": m.get("dashboard_url"),
                "runner_labels": m.get("runner_labels", []),
                "offline_reason": live.get("offline_reason"),
                "cpu_percent": system_info.get("cpu_percent"),
                "memory_percent": system_info.get("memory_percent"),
            }
        )

    audit_entries = _audit.load_orchestration_audit(limit=10)
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "machines": machines,
        "online_count": sum(1 for m in machines if m["online"]),
        "total_count": len(machines),
        "audit_log": list(reversed(audit_entries)),
    }


@router.post("/api/fleet/orchestration/dispatch")
async def fleet_orchestration_dispatch(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict:
    """Dispatch a workflow to a specific machine target."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Expected JSON object")

    repo = str(body.get("repo", "")).strip()
    workflow = str(body.get("workflow", "")).strip()
    ref = str(body.get("ref", "main")).strip() or "main"
    machine_target = str(body.get("machine_target", "")).strip()
    inputs = body.get("inputs") or {}
    approved_by = principal.id

    if not repo or not workflow:
        raise HTTPException(status_code=422, detail="repo and workflow are required")

    log.info(
        "audit: fleet_orchestration_dispatch repo=%s workflow=%s ref=%s target=%s by=%s",
        sanitize_log_value(repo),
        sanitize_log_value(workflow),
        sanitize_log_value(ref),
        sanitize_log_value(machine_target),
        sanitize_log_value(approved_by),
    )

    from uuid import uuid4  # noqa: PLC0415

    audit_id = uuid4().hex
    now_str = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    try:
        confirmation = dispatch_contract.DispatchConfirmation(
            approved_by=approved_by,
            approved_at=now_str,
            note=f"Fleet orchestration dispatch to {machine_target or 'any'}",
        )
        envelope = dispatch_contract.build_envelope(
            action="runner.status",
            source="fleet-orchestration",
            target=machine_target or "fleet",
            requested_by=approved_by,
            reason=f"Dispatch {workflow} on {repo}@{ref}",
            payload={"repo": repo, "workflow": workflow, "ref": ref, "inputs": inputs},
            confirmation=confirmation,
            principal=principal.id,
            on_behalf_of=getattr(request.state, "on_behalf_of", None) or "",
        )
        validation = dispatch_contract.validate_envelope(envelope)
        audit_entry_obj = dispatch_contract.build_audit_log_entry(envelope, validation)
        audit_entry = audit_entry_obj.to_dict()
    except Exception as exc:  # noqa: BLE001
        log.warning("orchestration dispatch audit build failed: %s", exc)
        audit_entry = {
            "event_id": audit_id,
            "action": "workflow.dispatch",
            "target": machine_target,
            "requested_by": approved_by,
            "decision": "accepted",
            "recorded_at": now_str,
        }

    audit_entry["orchestration_type"] = "workflow_dispatch"
    audit_entry["repo"] = repo
    audit_entry["workflow"] = workflow
    audit_entry["ref"] = ref
    audit_entry["machine_target"] = machine_target
    audit_entry["audit_id"] = audit_id
    await _audit.append_orchestration_audit(audit_entry)

    log.info(
        "fleet-orchestration dispatch repo=%s workflow=%s ref=%s target=%s by=%s",
        sanitize_log_value(repo),
        sanitize_log_value(workflow),
        sanitize_log_value(ref),
        sanitize_log_value(machine_target),
        sanitize_log_value(approved_by),
    )

    run_url = None
    try:
        endpoint = f"/repos/{ORG}/{repo}/actions/workflows/{workflow}/dispatches"
        dispatch_payload: dict = {"ref": ref}
        if inputs:
            dispatch_payload["inputs"] = inputs
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as pf_obj:
            json.dump(dispatch_payload, pf_obj)
            pf = pf_obj.name
        try:
            code, _, stderr = await _run_cmd(  # type: ignore[misc]
                ["gh", "api", endpoint, "--method", "POST", "--input", pf],
                timeout=30,
                cwd=REPO_ROOT,
            )
        finally:
            with contextlib.suppress(OSError):
                Path(pf).unlink()
        if code != 0:
            log.warning("orchestration workflow dispatch gh failed: %s", stderr[:200])
    except Exception as exc:  # noqa: BLE001
        log.warning("orchestration dispatch gh call failed: %s", exc)

    return {
        "dispatched": True,
        "run_url": run_url,
        "audit_id": audit_id,
        "machine_target": machine_target,
        "repo": repo,
        "workflow": workflow,
        "ref": ref,
    }


@router.post("/api/fleet/orchestration/deploy")
async def fleet_orchestration_deploy(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict:
    """Deploy a workflow or config change to a fleet machine."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Expected JSON object")

    machine = str(body.get("machine", "")).strip()
    action = str(body.get("action", "")).strip()
    confirmed = bool(body.get("confirmed", False))
    requested_by = principal.id

    if not machine:
        raise HTTPException(status_code=422, detail="machine is required")
    if action not in _DEPLOY_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"action must be one of: {', '.join(sorted(_DEPLOY_ACTIONS))}",
        )
    if not confirmed:
        raise HTTPException(
            status_code=403,
            detail="confirmed=true is required to deploy to a fleet machine",
        )

    log.info(
        "audit: fleet_orchestration_deploy machine=%s action=%s by=%s",
        sanitize_log_value(machine),
        sanitize_log_value(action),
        sanitize_log_value(requested_by),
    )

    from uuid import uuid4  # noqa: PLC0415

    audit_id = uuid4().hex
    now_str = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    contract_action_map = {
        "sync_workflows": "dashboard.update_and_restart",
        "restart_runner": "runner.restart",
        "update_config": "runner.restart",
    }
    contract_action = contract_action_map.get(action, "runner.restart")

    try:
        confirmation = dispatch_contract.DispatchConfirmation(
            approved_by=requested_by,
            approved_at=now_str,
            note=f"Fleet deploy action={action} to machine={machine}",
        )
        envelope = dispatch_contract.build_envelope(
            action=contract_action,
            source="fleet-orchestration",
            target=machine,
            requested_by=requested_by,
            reason=f"Deploy action {action} to {machine}",
            payload={"deploy_action": action},
            confirmation=confirmation,
            principal=principal.id,
            on_behalf_of=getattr(request.state, "on_behalf_of", None) or "",
        )
        validation = dispatch_contract.validate_envelope(envelope)
        audit_entry_obj = dispatch_contract.build_audit_log_entry(envelope, validation)
        audit_entry = audit_entry_obj.to_dict()
    except Exception as exc:  # noqa: BLE001
        log.warning("orchestration deploy audit build failed: %s", exc)
        audit_entry = {
            "event_id": audit_id,
            "action": action,
            "target": machine,
            "requested_by": requested_by,
            "decision": "accepted",
            "recorded_at": now_str,
        }

    audit_entry["orchestration_type"] = "fleet_deploy"
    audit_entry["deploy_action"] = action
    audit_entry["machine"] = machine
    audit_entry["audit_id"] = audit_id
    await _audit.append_orchestration_audit(audit_entry)

    log.info(
        "fleet-orchestration deploy machine=%s action=%s by=%s",
        sanitize_log_value(machine),
        sanitize_log_value(action),
        sanitize_log_value(requested_by),
    )

    action_labels = {
        "sync_workflows": "Sync workflows",
        "restart_runner": "Restart runner",
        "update_config": "Update config",
    }
    return {
        "deployed": True,
        "machine": machine,
        "action": action,
        "message": f"{action_labels.get(action, action)} dispatched to {machine}",
        "audit_id": audit_id,
    }


@router.post("/api/fleet/control/{action}")
async def fleet_control(
    action: str,
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
):
    """Scale runners from any dashboard.

    Nodes proxy fleet-wide requests to the hub. The hub applies the action
    locally and fans it out to configured nodes. Internal fan-out calls use
    ``?local=1`` so each node controls its own runner services.
    """
    if _should_proxy_fleet_to_hub(request):  # type: ignore[misc]
        return await _proxy_to_hub(request)  # type: ignore[misc]

    scope = request.query_params.get("scope", "fleet")
    should_fan_out = MACHINE_ROLE == "hub" and scope != "local" and bool(FLEET_NODES)
    local_machine = HOSTNAME
    try:
        local_result = await _fleet_control_local(action)  # type: ignore[misc]
        local_machine = local_result.get("machine", HOSTNAME)
        local_node_result = {
            "machine": local_machine,
            "url": f"http://localhost:{PORT}",
            "success": True,
            "result": local_result,
        }
    except HTTPException as exc:
        if not should_fan_out:
            raise
        local_result = {"machine": HOSTNAME, "action": action, "results": []}
        local_node_result = {
            "machine": HOSTNAME,
            "url": f"http://localhost:{PORT}",
            "success": False,
            "status_code": exc.status_code,
            "error": str(exc.detail),
        }
    node_results = [local_node_result]

    if should_fan_out:
        remotes = await asyncio.gather(
            *[_remote_fleet_control(name, url, action) for name, url in FLEET_NODES.items()]  # type: ignore[misc]
        )
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
    return _get_runner_capacity_snapshot()  # type: ignore[misc]


@router.get("/api/fleet/capacity")
async def get_fleet_capacity() -> dict:
    """Compatibility endpoint for dashboard capacity summaries."""
    return _get_runner_capacity_snapshot()  # type: ignore[misc]


@router.post("/api/fleet/schedule")
async def update_runner_schedule(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict:
    """Update this machine's local runner capacity schedule."""
    from security import safe_subprocess_env  # noqa: PLC0415

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="schedule payload must be an object")
    try:
        config = _validate_runner_schedule(body.get("schedule", body))  # type: ignore[misc]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _write_runner_schedule_config(config)  # type: ignore[misc]
    apply_now = bool(body.get("apply", False))
    apply_result: dict[str, object] | None = None
    if apply_now and Path(_runner_scheduler_bin).exists():
        env = safe_subprocess_env()
        env["RUNNER_ROOT"] = str(_runner_base_dir)
        env["RUNNER_SCHEDULE_CONFIG"] = str(_runner_schedule_config)
        env["RUNNER_SCHEDULER_STATE"] = str(_runner_scheduler_state)
        apply_cmd = _runner_scheduler_apply_command()  # type: ignore[misc]
        result = await asyncio.to_thread(
            subprocess.run,
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
        **_get_runner_capacity_snapshot(),  # type: ignore[misc]
    }


@router.get("/api/fleet/nodes")
async def get_fleet_nodes(request: Request) -> dict:
    """Aggregate system metrics + health from all fleet nodes."""
    if _should_proxy_fleet_to_hub(request):  # type: ignore[misc]
        return await _proxy_to_hub(request)  # type: ignore[misc]
    return await _get_fleet_nodes_impl()  # type: ignore[misc]


@router.get("/api/fleet/hardware")
async def get_fleet_hardware(request: Request) -> dict:
    """Return centralized fleet hardware specs for workload placement."""
    if _should_proxy_fleet_to_hub(request):  # type: ignore[misc]
        return await _proxy_to_hub(request)  # type: ignore[misc]
    fleet = await _get_fleet_nodes_impl()  # type: ignore[misc]
    machines = []
    for node in fleet.get("nodes", []):
        registry = node.get("registry") or {}
        specs = node.get("hardware_specs") or node.get("system", {}).get("hardware_specs", {})
        capacity = node.get("workload_capacity") or node.get("system", {}).get("workload_capacity", {})
        machines.append(
            {
                "name": node.get("name"),
                "display_name": registry.get("display_name") or node.get("name"),
                "online": bool(node.get("online")),
                "dashboard_reachable": bool(node.get("dashboard_reachable")),
                "role": registry.get("role") or node.get("role"),
                "runner_labels": registry.get("runner_labels", []),
                "hardware_specs": specs,
                "workload_capacity": capacity,
                "offline_reason": node.get("offline_reason"),
            }
        )
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "machines": machines,
        "count": len(machines),
        "online_count": sum(1 for machine in machines if machine["online"]),
        "registry": fleet.get("registry", {}),
    }


@router.get("/api/fleet/nodes/{node_name}/system")
async def proxy_node_system(node_name: str) -> dict:
    """Proxy /api/system from a named fleet node (for detailed drill-down)."""
    if node_name in (HOSTNAME, "local"):
        return await _get_system_metrics_snapshot()  # type: ignore[misc]
    url = FLEET_NODES.get(node_name)
    if not url:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_name}")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{url}/api/system")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Node returned error")
        return resp.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"{node_name} timed out") from exc
    except httpx.RequestError as exc:
        log.warning("Node %s unreachable: %s", node_name, exc)
        raise HTTPException(status_code=502, detail=f"{node_name} unreachable") from exc
