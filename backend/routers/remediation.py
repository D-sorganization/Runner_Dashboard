"""Agent Remediation and Dispatch routes."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt_mod
import json
import logging
import os
import re
import secrets
import tempfile
from pathlib import Path

import agent_dispatch_router
import agent_remediation
import config_schema
import quick_dispatch as _quick_dispatch
import quota_enforcement
from dashboard_config import ORG
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from identity import Principal, require_scope
from security import check_dispatch_rate
from system_utils import run_cmd

_remediation_history_lock: asyncio.Lock = asyncio.Lock()

REPO_ROOT = Path(
    os.environ.get("RUNNER_DASHBOARD_REPO_ROOT", Path(__file__).parents[2])
)


def _normalize_repository_input(value: str) -> tuple[str, str]:
    value = value.strip().lstrip("/")
    if not value:
        return "", ""
    parts = value.split("/")
    repo_name = parts[-1]
    if "/" in value:
        full_repository = value
    else:
        full_repository = f"{ORG}/{value}"
    return repo_name, full_repository


# Import server lazy imports below if needed

log = logging.getLogger("dashboard.remediation")
router = APIRouter(tags=["remediation", "agents"])


@router.get("/api/agent-remediation/config")
async def get_agent_remediation_config() -> dict:
    """Return the current CI remediation policy and provider availability."""
    policy = agent_remediation.load_policy()
    availability = agent_remediation.probe_provider_availability()
    return {
        "schema_version": agent_remediation.SCHEMA_VERSION,
        "policy": policy.to_dict(),
        "providers": {
            provider_id: provider.to_dict()
            for provider_id, provider in agent_remediation.PROVIDERS.items()
        },
        "availability": {
            provider_id: status.to_dict()
            for provider_id, status in availability.items()
        },
    }


@router.put("/api/agent-remediation/config", response_model=None)
async def update_agent_remediation_config(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict | JSONResponse:  # noqa: B008
    """Persist the remediation policy so the dashboard can tune auto-routing."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")
    payload = body.get("policy", body)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="policy must be an object")
    try:
        config_schema.validate_agent_remediation_config(body)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)

    current = agent_remediation.load_policy()
    workflow_type_rules = agent_remediation._load_workflow_type_rules(  # noqa: SLF001
        payload.get("workflow_type_rules")
    )
    policy = agent_remediation.RemediationPolicy(
        auto_dispatch_on_failure=bool(
            payload.get("auto_dispatch_on_failure", current.auto_dispatch_on_failure)
        ),
        require_failure_summary=bool(
            payload.get("require_failure_summary", current.require_failure_summary)
        ),
        require_non_protected_branch=bool(
            payload.get(
                "require_non_protected_branch",
                current.require_non_protected_branch,
            )
        ),
        max_same_failure_attempts=int(
            payload.get("max_same_failure_attempts", current.max_same_failure_attempts)
        ),
        attempt_window_hours=int(
            payload.get("attempt_window_hours", current.attempt_window_hours)
        ),
        provider_order=agent_remediation._as_tuple_strings(  # noqa: SLF001
            payload.get("provider_order"), fallback=current.provider_order
        ),
        enabled_providers=agent_remediation._as_tuple_strings(  # noqa: SLF001
            payload.get("enabled_providers"), fallback=current.enabled_providers
        ),
        default_provider=str(
            payload.get("default_provider") or current.default_provider
        ),
        workflow_type_rules=workflow_type_rules,
    )
    agent_remediation.save_policy(policy)
    availability = agent_remediation.probe_provider_availability()
    return {
        "schema_version": agent_remediation.SCHEMA_VERSION,
        "policy": policy.to_dict(),
        "providers": {
            provider_id: provider.to_dict()
            for provider_id, provider in agent_remediation.PROVIDERS.items()
        },
        "availability": {
            provider_id: status.to_dict()
            for provider_id, status in availability.items()
        },
    }


@router.get("/api/agent-remediation/workflows")
async def get_agent_remediation_workflows() -> dict:
    """Inspect local Jules workflow health and legacy command usage."""
    report = agent_remediation.inspect_jules_workflows(REPO_ROOT)
    return report.to_dict()


@router.post("/api/agent-remediation/plan")
async def plan_agent_remediation(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict:
    """Build a guarded remediation plan for one failed workflow run."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")

    context = agent_remediation.FailureContext.from_dict(body)
    if not context.repository.strip():
        raise HTTPException(status_code=400, detail="repository is required")
    if not context.workflow_name.strip():
        raise HTTPException(status_code=400, detail="workflow_name is required")
    if not context.branch.strip():
        raise HTTPException(status_code=400, detail="branch is required")

    repo_name, full_repository = _normalize_repository_input(context.repository)
    context = agent_remediation.FailureContext(
        repository=repo_name,
        workflow_name=context.workflow_name,
        branch=context.branch,
        failure_reason=context.failure_reason,
        log_excerpt=context.log_excerpt,
        run_id=context.run_id,
        conclusion=context.conclusion,
        protected_branch=context.protected_branch,
        source=context.source,
    )

    if context.run_id and not context.log_excerpt.strip():
        from server import _fetch_failed_log_excerpt

        log_excerpt = await _fetch_failed_log_excerpt(repo_name, context.run_id)
        if log_excerpt:
            context = agent_remediation.FailureContext(
                repository=context.repository,
                workflow_name=context.workflow_name,
                branch=context.branch,
                failure_reason=context.failure_reason,
                log_excerpt=log_excerpt,
                run_id=context.run_id,
                conclusion=context.conclusion,
                protected_branch=context.protected_branch,
                source=context.source,
            )

    attempts_payload = body.get("attempts", [])
    if attempts_payload is None:
        attempts_payload = []
    if not isinstance(attempts_payload, list):
        raise HTTPException(status_code=422, detail="attempts must be a list")
    attempts = [
        agent_remediation.AttemptRecord.from_dict(item)
        for item in attempts_payload
        if isinstance(item, dict)
    ]
    availability = agent_remediation.probe_provider_availability()
    decision = agent_remediation.plan_dispatch(
        context,
        policy=agent_remediation.load_policy(),
        availability=availability,
        attempts=attempts,
        provider_override=(
            str(body.get("provider_override")).strip()
            if body.get("provider_override")
            else None
        ),
        dispatch_origin="manual",
    )
    return {
        "context": {**context.to_dict(), "full_repository": full_repository},
        "decision": decision.to_dict(),
        "availability": {
            provider_id: status.to_dict()
            for provider_id, status in availability.items()
        },
    }


@router.post("/api/agent-remediation/dispatch")
async def dispatch_agent_remediation(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch the central CI remediation workflow in Repository_Management."""
    client_ip = request.client.host if request.client else "unknown"
    check_dispatch_rate(client_ip)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")

    context = agent_remediation.FailureContext.from_dict(body)

    # Wave 3: Quota and Fair Sharing
    allowed, reason = quota_enforcement.quota_enforcement.check_dispatch_quota(
        principal, estimated_cost=0.10
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Quota exceeded: {reason}")

    repo_name, full_repository = _normalize_repository_input(context.repository)
    context = agent_remediation.FailureContext(
        repository=repo_name,
        workflow_name=context.workflow_name,
        branch=context.branch,
        failure_reason=context.failure_reason,
        log_excerpt=context.log_excerpt,
        run_id=context.run_id,
        conclusion=context.conclusion,
        protected_branch=context.protected_branch,
        source=context.source,
    )
    provider_id = str(
        body.get("provider")
        or body.get("provider_override")
        or agent_remediation.load_policy().default_provider
    ).strip()
    attempts_payload = body.get("attempts", [])
    if attempts_payload is None:
        attempts_payload = []
    attempts = [
        agent_remediation.AttemptRecord.from_dict(item)
        for item in attempts_payload
        if isinstance(item, dict)
    ]

    if context.run_id and not context.log_excerpt.strip():
        from server import _fetch_failed_log_excerpt

        log_excerpt = await _fetch_failed_log_excerpt(repo_name, context.run_id)
        if log_excerpt:
            context = agent_remediation.FailureContext(
                repository=context.repository,
                workflow_name=context.workflow_name,
                branch=context.branch,
                failure_reason=context.failure_reason,
                log_excerpt=log_excerpt,
                run_id=context.run_id,
                conclusion=context.conclusion,
                protected_branch=context.protected_branch,
                source=context.source,
            )

    decision = agent_remediation.plan_dispatch(
        context,
        policy=agent_remediation.load_policy(),
        availability=agent_remediation.probe_provider_availability(),
        attempts=attempts,
        provider_override=provider_id,
        dispatch_origin="manual",
    )
    if not decision.accepted:
        raise HTTPException(status_code=409, detail=decision.reason)

    dispatch_ref = str(body.get("ref") or "main")
    failure_reason = re.sub(r"\s+", " ", context.failure_reason).strip()[:1000]
    log_excerpt = re.sub(r"\s+", " ", context.log_excerpt).strip()[:8000]
    endpoint = f"/repos/{ORG}/Repository_Management/actions/workflows/Agent-CI-Remediation.yml/dispatches"
    payload = {
        "ref": dispatch_ref,
        "inputs": {
            "target_repository": full_repository,
            "provider": decision.provider_id or provider_id,
            "run_id": str(context.run_id or ""),
            "branch": context.branch,
            "workflow_name": context.workflow_name,
            "failure_reason": failure_reason,
            "log_excerpt": log_excerpt,
            "fingerprint": decision.fingerprint,
        },
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="agent-remediation-dispatch-",
        suffix=".json",
        delete=False,
    ) as payload_file:
        json.dump(payload, payload_file)
        payload_path = payload_file.name
    command = [
        "gh",
        "api",
        endpoint,
        "--method",
        "POST",
        "--input",
        payload_path,
    ]
    try:
        code, _, stderr = await run_cmd(
            command,
            timeout=30,
            cwd=REPO_ROOT,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(payload_path).unlink()
    if code != 0:
        log.warning(
            "remediation dispatch failed: target=%s stderr=%s",
            full_repository,
            stderr.strip()[:300],
        )
        raise HTTPException(
            status_code=502,
            detail="Failed to dispatch remediation workflow",
        )

    # Wave 3: Record spend and lease
    quota_enforcement.quota_enforcement.add_spend(principal.id, 0.10)
    try:
        from runner_lease import lease_manager  # noqa: PLC0415

        lease_manager.acquire_lease(
            principal=principal,
            # We don't have an envelope_id here, use fingerprint
            runner_id=f"virtual-{decision.fingerprint}",
            duration_seconds=3600,
            task_id=decision.fingerprint,
            metadata={"source": "agent_remediation", "repo": full_repository},
        )
    except (ValueError, PermissionError) as exc:
        log.warning("Failed to acquire virtual lease for %s: %s", principal.id, exc)
    result = {
        "status": "dispatched",
        "workflow": "Agent-CI-Remediation.yml",
        "target_repository": full_repository,
        "provider": decision.provider_id,
        "fingerprint": decision.fingerprint,
        "reason": decision.reason,
        "note": "Central remediation workflow dispatch recorded in Repository_Management.",
    }
    await _append_remediation_history(
        {
            "timestamp": _dt_mod.datetime.now(_dt_mod.UTC).isoformat(),
            "repository": full_repository,
            "workflow_name": context.workflow_name,
            "branch": context.branch,
            "run_id": context.run_id,
            "provider": decision.provider_id,
            "fingerprint": decision.fingerprint,
            "status": "dispatched",
            "origin": "manual",
        }
    )
    return result


# ─── Quick Dispatch ───────────────────────────────────────────────────────────


@router.post("/api/agents/quick-dispatch")
async def api_quick_dispatch(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch an ad-hoc agent task via Agent-Quick-Dispatch.yml."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")
    try:
        req = _quick_dispatch.QuickDispatchRequest(**body)
        req.requested_by = principal.id
        req.principal = principal.id
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if len(req.prompt.strip()) < 10:
        raise HTTPException(
            status_code=400, detail="prompt must be at least 10 characters"
        )

    # Wave 3: Quota and Fair Sharing
    allowed, reason = quota_enforcement.quota_enforcement.check_dispatch_quota(
        principal, estimated_cost=0.10
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Quota exceeded: {reason}")

    resp = await _quick_dispatch.quick_dispatch(
        req,
        run_cmd_fn=run_cmd,
        org=ORG,
        repo_root=REPO_ROOT,
        normalize_repository_fn=_normalize_repository_input,
    )
    if not resp.accepted:
        reason = resp.reason or "rejected"
        if reason.startswith("rate_limited"):
            retry_after = 1
            for part in reason.split("="):
                try:
                    retry_after = int(part)
                except ValueError:
                    pass
            raise HTTPException(
                status_code=429,
                detail={"reason": "rate_limited", "retry_after_seconds": retry_after},
            )
        if reason.startswith("workflow_not_configured"):
            raise HTTPException(
                status_code=501,
                detail={
                    "reason": "workflow_not_configured",
                    "suggested_workflow": "Agent-Quick-Dispatch.yml",
                },
            )
        if reason.startswith("prompt_too_short"):
            raise HTTPException(status_code=400, detail=reason)
        raise HTTPException(
            status_code=409, detail={"accepted": False, "reason": reason}
        )
    return resp.model_dump()


# ─── Bulk PR / Issue Agent Dispatch ──────────────────────────────────────────


@router.post("/api/prs/dispatch")
async def api_dispatch_to_prs(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("github.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch agents to one or more pull requests."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")
    try:
        req = agent_dispatch_router.PRDispatchRequest(**body)
        req.principal = principal.id
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Wave 3: Quota and Fair Sharing
    allowed, reason = quota_enforcement.quota_enforcement.check_dispatch_quota(
        principal, estimated_cost=0.10
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Quota exceeded: {reason}")

    result = await agent_dispatch_router.dispatch_to_prs(
        req,
        run_cmd_fn=run_cmd,
        org=ORG,
        repo_root=REPO_ROOT,
        normalize_repository_fn=_normalize_repository_input,
    )
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(
            status_code=result.get("status_code", 400), detail=result["error"]
        )
    if isinstance(result, agent_dispatch_router.BulkDispatchResponse):
        return result.model_dump()
    return dict(result)


@router.post("/api/issues/dispatch")
async def api_dispatch_to_issues(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("github.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch agents to one or more issues."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")
    try:
        req = agent_dispatch_router.IssueDispatchRequest(**body)
        req.principal = principal.id
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Wave 3: Quota and Fair Sharing
    allowed, reason = quota_enforcement.quota_enforcement.check_dispatch_quota(
        principal, estimated_cost=0.10
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Quota exceeded: {reason}")

    result = await agent_dispatch_router.dispatch_to_issues(
        req,
        run_cmd_fn=run_cmd,
        org=ORG,
        repo_root=REPO_ROOT,
        normalize_repository_fn=_normalize_repository_input,
    )
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(
            status_code=result.get("status_code", 400), detail=result["error"]
        )
    if isinstance(result, agent_dispatch_router.BulkDispatchResponse):
        return result.model_dump()
    return dict(result)


# ─── Remediation History ──────────────────────────────────────────────────────

_REMEDIATION_HISTORY_PATH = Path(os.environ.get("REMEDIATION_HISTORY_PATH", "")) or (
    Path.home() / "actions-runners" / "dashboard" / "remediation_history.json"
)


async def _append_remediation_history(entry: dict) -> None:
    """Append a dispatch record to the local history file (thread-safe)."""
    async with _remediation_history_lock:
        try:
            history: list[dict] = []
            if _REMEDIATION_HISTORY_PATH.exists():
                try:
                    history = json.loads(
                        _REMEDIATION_HISTORY_PATH.read_text(encoding="utf-8")
                    )
                except Exception:  # noqa: BLE001
                    history = []
            history.append(entry)
            history = history[-200:]  # keep last 200 entries
            config_schema.atomic_write_json(_REMEDIATION_HISTORY_PATH, history)
        except Exception:  # noqa: BLE001
            pass  # history is best-effort


@router.post("/api/agent-remediation/dispatch-jules")
async def dispatch_jules_workflow(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch a specific Jules workflow via workflow_dispatch."""
    body = await request.json()
    workflow_file = str(body.get("workflow_file", "")).strip()
    ref = str(body.get("ref", "main")).strip()
    inputs = body.get("inputs", {}) or {}
    correlation_id = request.headers.get("X-Correlation-Id", secrets.token_hex(8))
    inputs["correlation_id"] = correlation_id
    if not workflow_file:
        raise HTTPException(status_code=422, detail="workflow_file required")
    endpoint = f"/repos/{ORG}/Repository_Management/actions/workflows/{workflow_file}/dispatches"
    payload = {"ref": ref, "inputs": inputs}
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", delete=False
    ) as f:
        json.dump(payload, f)
        pf = f.name
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
        log.warning(
            "jules dispatch failed: workflow=%s stderr=%s",
            workflow_file,
            stderr.strip()[:300],
        )
        raise HTTPException(status_code=502, detail="Jules dispatch failed")
    return {"status": "dispatched", "workflow_file": workflow_file}


@router.get("/api/agent-remediation/history")
async def get_remediation_history() -> dict:
    """Return recent remediation dispatch history."""
    try:
        if _REMEDIATION_HISTORY_PATH.exists():
            history = json.loads(_REMEDIATION_HISTORY_PATH.read_text(encoding="utf-8"))
        else:
            history = []
    except Exception:  # noqa: BLE001
        history = []
    return {"history": list(reversed(history[-100:]))}  # newest first


_PROVIDERS_WITH_MODEL_SELECTION: frozenset[str] = frozenset(
    {"claude_code_cli", "codex_cli", "gemini_cli"}
)


@router.get("/api/agents/providers")
async def get_agent_providers() -> dict:
    """Return available agent providers and their availability status."""
    availability = agent_remediation.probe_provider_availability()
    return {
        "providers": {
            pid: p.to_dict() for pid, p in agent_remediation.PROVIDERS.items()
        },
        "availability": {pid: s.to_dict() for pid, s in availability.items()},
        "providers_with_model_selection": sorted(_PROVIDERS_WITH_MODEL_SELECTION),
    }
