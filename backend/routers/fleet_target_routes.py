# ruff: noqa: B008
"""POST /api/fleet/target — expiring per-host runner target override (#4308)."""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import orchestration_audit as _audit
import proxy_utils
import runner_target_contract as target_contract
from dashboard_config import FLEET_NODES, HOSTNAME, MACHINE_ROLE
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_fleet_peer, require_scope
from machine_registry import build_machine_registry_index, load_machine_registry, resolve_local_identity
from pydantic import BaseModel, ConfigDict, Field
from routers import orchestration_schedule_routes as _schedule
from runner_target_contract import normalize_host_token
from security import safe_subprocess_env, sanitize_log_value

log = logging.getLogger("dashboard.fleet_target")
router = APIRouter(tags=["orchestration"])

_APPLY_TIMEOUT_S = 45


class FleetTargetSetRequest(BaseModel):
    """POST /api/fleet/target body (DbC: typed payload, no unknown fields)."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(..., min_length=1, max_length=256, description="Fleet host or alias.")
    runners: int = Field(..., ge=0, description="Requested runner count (clamped to host cap).")
    until: str | None = Field(default=None, description="ISO-8601 timestamp or HH:MM expiry.")
    reason: str = Field(default="", max_length=500)
    apply: bool = Field(default=True, description="Run runner-scheduler --apply after saving override.")

    def validated_payload(self) -> dict[str, Any]:
        """Pre/post: normalized host, default reason, nonnegative runners."""
        host = self.host.strip()
        assert host, "host is required"  # noqa: S101
        assert self.runners >= 0  # noqa: S101
        reason = (self.reason or "").strip() or "api: /api/fleet/target"
        payload = {
            "host": host,
            "runners": self.runners,
            "until": self.until,
            "reason": reason,
            "apply": self.apply,
        }
        assert payload["host"] and isinstance(payload["runners"], int)  # noqa: S101
        return payload


def _local_host_tokens() -> set[str]:
    tokens = {normalize_host_token(HOSTNAME or "local"), "local"}
    identity = resolve_local_identity(HOSTNAME, None, load_machine_registry())
    tokens.add(normalize_host_token(str(identity.get("name") or "")))
    for alias in identity.get("aliases") or []:
        tokens.add(normalize_host_token(str(alias)))
    for alias in os.environ.get("RUNNER_ALIASES", "").split(","):
        if alias.strip():
            tokens.add(normalize_host_token(alias))
    return {token for token in tokens if token}


def _host_matches_local(host: str) -> bool:
    return normalize_host_token(host) in _local_host_tokens()


def _node_url_for_host(host: str) -> str | None:
    token = normalize_host_token(host)
    index = build_machine_registry_index(load_machine_registry())
    entry = index.get(token)
    candidates: list[str] = [host]
    if isinstance(entry, dict):
        if entry.get("name"):
            candidates.append(str(entry["name"]))
        if entry.get("parent_machine"):
            candidates.append(str(entry["parent_machine"]))
    for candidate in candidates:
        url = FLEET_NODES.get(candidate)
        if url:
            return url
    for name, url in FLEET_NODES.items():
        if normalize_host_token(name) == token and url:
            return url
    return None


def _audit_entry(
    audit_id: str, phase: str, principal: Principal, payload: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    return {
        "audit_id": audit_id,
        "orchestration_type": "fleet_target",
        "action": "fleet.target.set",
        "phase": phase,
        "requested_by": principal.id,
        "recorded_at": _dt.datetime.now(_dt.UTC).isoformat().replace("+00:00", "Z"),
        "host": payload["host"],
        "runners_before": extra.get("before"),
        "runners_after": extra.get("after"),
        "until": payload.get("until"),
        "reason": payload.get("reason"),
        **extra,
    }


def _scheduled_desired_from_scheduler() -> int:
    """Runner count the schedule would apply with no target override file."""
    scheduler_bin, config, state, runner_root = _schedule.scheduler_paths()
    snap_getter = _schedule._get_runner_capacity_snapshot  # type: ignore[attr-defined]
    if snap_getter is not None and not target_contract.load_target_override():
        snapshot = snap_getter()
        desired = ((snapshot or {}).get("state") or {}).get("desired")
        if isinstance(desired, int):
            return desired
    if not Path(scheduler_bin).exists():
        return 0
    env = safe_subprocess_env()
    env["RUNNER_ROOT"] = str(runner_root)
    env["RUNNER_SCHEDULE_CONFIG"] = str(config)
    env["RUNNER_SCHEDULER_STATE"] = str(state)
    env["RUNNER_TARGET_OVERRIDE"] = str(Path(os.environ.get("TMPDIR", "/tmp")) / "missing-target-override.json")
    try:
        result = subprocess.run(
            [scheduler_bin, "--dry-run", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=env,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if result.returncode != 0:
        return 0
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return 0
    desired = payload.get("desired")
    return int(desired) if isinstance(desired, int) else 0


async def _apply_scheduler() -> dict[str, Any]:
    scheduler_bin, config, state, runner_root = _schedule.scheduler_paths()
    if not Path(scheduler_bin).exists():
        raise HTTPException(status_code=503, detail="runner-scheduler is not installed on this machine")
    env = safe_subprocess_env()
    env["RUNNER_ROOT"] = str(runner_root)
    env["RUNNER_SCHEDULE_CONFIG"] = str(config)
    env["RUNNER_SCHEDULER_STATE"] = str(state)
    env["RUNNER_TARGET_OVERRIDE"] = str(target_contract.TARGET_OVERRIDE_PATH)
    cmd = [scheduler_bin, "--apply", "--json"]
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=_APPLY_TIMEOUT_S,
            check=False,
            env=env,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail=f"scheduler apply failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:500]
        raise HTTPException(status_code=500, detail=detail or f"scheduler exit {result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"stdout": result.stdout.strip()[:1000]}


async def _apply_target_local(payload: dict[str, Any], principal: Principal) -> dict[str, Any]:
    cap = target_contract.host_runner_cap(payload["host"])
    before_record = target_contract.load_target_override()
    if before_record:
        before = int(before_record["runners"])
    else:
        before = await asyncio.to_thread(_scheduled_desired_from_scheduler)
    clamped = target_contract.clamp_runners(payload["runners"], payload["host"], cap)
    until_store = target_contract.format_until_for_storage(payload.get("until"))
    record = target_contract.write_target_override(
        runners=clamped,
        reason=payload["reason"],
        actor=principal.id,
        before=before,
        until=until_store,
    )
    apply_state: dict[str, Any] | None = None
    if payload["apply"]:
        apply_state = await _apply_scheduler()
    return {
        "host": HOSTNAME,
        "saved": True,
        "clamped_to": cap,
        "runners": clamped,
        "target_override": record,
        "apply_state": apply_state,
        "before": record["before"],
    }


async def _proxy_target_to_node(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = os.environ.get("HUB_FLEET_TOKEN", "")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_APPLY_TIMEOUT_S + 5, connect=3.0)) as client:
            response = await client.post(
                f"{url.rstrip('/')}/api/fleet/target",
                json=payload,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"target proxy failed: {exc}") from exc
    if response.status_code >= 400:
        detail = response.text[:500]
        try:
            body = response.json()
            detail = str(body.get("detail") or body)
        except ValueError:
            pass
        raise HTTPException(status_code=response.status_code, detail=detail)
    data = response.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="invalid target response")
    return data


@router.post("/api/fleet/target", dependencies=[Depends(require_fleet_peer)])
async def fleet_target(
    request: Request,
    body: FleetTargetSetRequest,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),
) -> dict[str, Any]:
    """Set an expiring runner-count override for a fleet host."""
    payload = body.validated_payload()

    if proxy_utils.should_proxy_fleet_to_hub(request):
        return await proxy_utils.proxy_to_hub(request)

    audit_id = uuid4().hex
    log.info(
        "fleet target host=%s runners=%s by=%s",
        sanitize_log_value(payload["host"]),
        payload["runners"],
        sanitize_log_value(principal.id),
    )
    await _audit.append_orchestration_audit(_audit_entry(audit_id, "requested", principal, payload))

    if _host_matches_local(payload["host"]):
        result = await _apply_target_local(payload, principal)
    else:
        if MACHINE_ROLE != "hub":
            raise HTTPException(status_code=400, detail="target host is not this machine; call from the hub")
        url = _node_url_for_host(payload["host"])
        if not url:
            raise HTTPException(status_code=404, detail=f"unknown fleet host: {payload['host']}")
        result = await _proxy_target_to_node(url, payload)

    await _audit.append_orchestration_audit(
        _audit_entry(
            audit_id,
            "completed",
            principal,
            payload,
            before=result.get("before"),
            after=result.get("runners"),
            outcome="ok",
        )
    )
    result["audit_id"] = audit_id
    return result
