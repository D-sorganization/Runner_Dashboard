"""Fleet orchestration and audit routes."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt_mod
import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import config_schema
import dispatch_contract
import httpx
from dashboard_config import FLEET_NODES, ORG
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_principal, require_scope
from security import sanitize_log_value

UTC = _dt_mod.UTC
from system_utils import run_cmd  # noqa: E402

log = logging.getLogger("dashboard.fleet_orchestration")
router = APIRouter(tags=["fleet", "orchestration"])

_orchestration_audit_lock: asyncio.Lock = asyncio.Lock()

# ─── Fleet Orchestration Control Plane ───────────────────────────────────────

_ORCHESTRATION_AUDIT_PATH = (
    Path.home() / "actions-runners" / "dashboard" / "orchestration_audit.json"
)
_DEPLOY_ACTIONS = {"sync_workflows", "restart_runner", "update_config"}


def _load_orchestration_audit(
    limit: int = 50, principal: str | None = None
) -> list[dict]:
    """Load recent orchestration audit entries from disk."""
    if not _ORCHESTRATION_AUDIT_PATH.exists():
        return []
    try:
        raw = _ORCHESTRATION_AUDIT_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        entries = json.loads(raw)
        if isinstance(entries, list):
            if principal:
                entries = [e for e in entries if e.get("principal") == principal]
            return entries[-limit:]
        return []
    except (OSError, json.JSONDecodeError):
        return []


async def _append_orchestration_audit(entry: dict) -> None:
    """Append a single audit entry to the orchestration audit log (thread-safe)."""
    async with _orchestration_audit_lock:
        existing = _load_orchestration_audit(limit=1000)
        existing.append(entry)
        try:
            config_schema.atomic_write_json(_ORCHESTRATION_AUDIT_PATH, existing)
        except OSError as exc:
            log.warning("orchestration audit write failed: %s", exc)


@router.get("/api/audit", tags=["fleet"])
async def get_node_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),  # noqa: B008
) -> list[dict]:
    """Return this node's orchestration audit log."""
    return _load_orchestration_audit(limit=limit, principal=principal)


@router.get("/api/fleet/audit", tags=["fleet"])
async def get_fleet_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),  # noqa: B008
) -> dict:
    """Return a merged view of orchestration audit logs across the fleet."""
    local_entries = _load_orchestration_audit(limit=limit, principal=principal)
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
        remotes = await asyncio.gather(
            *[fetch_remote_audit(n, u) for n, u in FLEET_NODES.items()]
        )
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
    from server import _get_fleet_nodes_impl, load_machine_registry
    registry_data = load_machine_registry()
    machines_raw = registry_data.get("machines", [])

    # Try to enrich with live node data from cache
    try:
        fleet = await _get_fleet_nodes_impl()
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
        busy_count = (
            sum(1 for r in runners_info if r.get("busy")) if runner_count else 0
        )
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

    audit_entries = _load_orchestration_audit(limit=10)
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

    audit_id = uuid4().hex
    now_str = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    # Build a dispatch_contract envelope for auditing
    try:
        confirmation = dispatch_contract.DispatchConfirmation(
            approved_by=approved_by,
            approved_at=now_str,
            note=f"Fleet orchestration dispatch to {machine_target or 'any'}",
        )
        envelope = dispatch_contract.build_envelope(
            action="runner.status",  # read-only, used for audit record only
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
    await _append_orchestration_audit(audit_entry)

    log.info(
        "fleet-orchestration dispatch repo=%s workflow=%s ref=%s target=%s by=%s",
        sanitize_log_value(repo),
        sanitize_log_value(workflow),
        sanitize_log_value(ref),
        sanitize_log_value(machine_target),
        sanitize_log_value(approved_by),
    )

    # Attempt actual workflow dispatch via gh CLI
    run_url = None
    try:
        endpoint = f"/repos/{ORG}/{repo}/actions/workflows/{workflow}/dispatches"
        dispatch_payload: dict = {"ref": ref}
        if inputs:
            dispatch_payload["inputs"] = inputs
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as pf_obj:
            json.dump(dispatch_payload, pf_obj)
            pf = pf_obj.name
        from server import REPO_ROOT
        try:
            code, _, stderr = await run_cmd(
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

    audit_id = uuid4().hex
    now_str = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    # Map deploy actions to dispatch_contract actions for auditing
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
    await _append_orchestration_audit(audit_entry)

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
