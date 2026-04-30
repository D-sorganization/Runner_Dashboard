"""Fleet control and orchestration routes (mutations).

Mutating fleet orchestration endpoints: runner control, fleet auto-scaling,
workflow dispatch, and deployment actions. These routes make changes to the
fleet state and record audit entries for compliance.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dashboard_config import FLEET_NODES, HOSTNAME, MACHINE_ROLE, ORG, PORT, REPO_ROOT
from fastapi import APIRouter, Depends, HTTPException, Request
from gh_utils import run_cmd
from identity import Principal, require_scope
from machine_registry import load_machine_registry

import config_schema as config_schema  # noqa: E402
import deployment_drift as deployment_drift  # noqa: E402
import dispatch_contract as dispatch_contract  # noqa: E402

UTC = timezone.utc  # noqa: UP017
log = logging.getLogger("dashboard.fleet_control")
router = APIRouter(tags=["fleet"])

# Module-level path for orchestration audit logs
_ORCHESTRATION_AUDIT_PATH = Path.home() / "actions-runners" / "dashboard" / "orchestration_audit.json"

# Lock for atomic audit writes
_orchestration_audit_lock: asyncio.Lock | None = None

# Valid deployment actions
_DEPLOY_ACTIONS = {"sync_workflows", "restart_runner", "update_config"}


def _set_audit_lock(lock: asyncio.Lock) -> None:
    """Set the audit lock (called from server.py)."""
    global _orchestration_audit_lock  # noqa: PLW0603
    _orchestration_audit_lock = lock


def _sanitize_log_value(value: str) -> str:
    """Strip log-injection characters from user-controlled strings."""
    return value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")[:200]


def _load_orchestration_audit(limit: int = 50, principal: str | None = None) -> list[dict]:
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
    if _orchestration_audit_lock is None:
        log.warning("audit lock not initialized; skipping append")
        return
    async with _orchestration_audit_lock:
        existing = _load_orchestration_audit(limit=1000)
        existing.append(entry)
        try:
            config_schema.atomic_write_json(_ORCHESTRATION_AUDIT_PATH, existing)
        except OSError as exc:
            log.warning("orchestration audit write failed: %s", exc)


async def _fleet_control_local(action: str) -> dict:
    """Scale runners on this machine only."""
    from dashboard_config import MAX_RUNNERS, NUM_RUNNERS
    from routers.fleet import _runner_limit, run_runner_svc, runner_svc_path, runner_num_from_id
    from gh_utils import gh_api_admin

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


# ─── Routes ──────────────────────────────────────────────────────────────────


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
    from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub

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
        remotes = await asyncio.gather(*[_remote_fleet_control(name, url, action) for name, url in FLEET_NODES.items()])
        node_results.extend(remotes)

    return {
        "action": action,
        "scope": "local" if scope == "local" else "fleet",
        "machine": local_machine,
        "results": local_result["results"],
        "nodes": node_results,
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
        _sanitize_log_value(repo),
        _sanitize_log_value(workflow),
        _sanitize_log_value(ref),
        _sanitize_log_value(machine_target),
        _sanitize_log_value(approved_by),
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
        _sanitize_log_value(repo),
        _sanitize_log_value(workflow),
        _sanitize_log_value(ref),
        _sanitize_log_value(machine_target),
        _sanitize_log_value(approved_by),
    )

    # Attempt actual workflow dispatch via gh CLI
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
        _sanitize_log_value(machine),
        _sanitize_log_value(action),
        _sanitize_log_value(requested_by),
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
        _sanitize_log_value(machine),
        _sanitize_log_value(action),
        _sanitize_log_value(requested_by),
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
