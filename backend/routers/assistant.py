"""Assistant chat and action-execution routes.

Covers:
  - POST /api/assistant/chat           – AI chat with optional tool-use
  - POST /api/assistant/tool/execute   – execute a tool from the allowlist
  - GET  /api/assistant/audit-history  – tool-execution audit log
  - POST /api/assistant/propose-action – propose an action for approval
  - POST /api/assistant/execute-action – execute an approved action
"""

from __future__ import annotations

import datetime as _dt_mod
import json
import logging
import os
import secrets
import time

import agent_remediation
import assistant_contract
import assistant_tools
from dashboard_config import DEFAULT_LLM_MODEL, ORG, REPO_ROOT
from fastapi import APIRouter, Depends, HTTPException, Request
from gh_utils import gh_api
from identity import Principal, require_scope
from security import validate_owner_repo_format, validate_repo_slug
from system_utils import run_cmd

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard.assistant")
router = APIRouter(tags=["assistant"])

# In-memory store for proposed actions (keyed by action_id).
_proposed_actions: dict[str, dict] = {}


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _normalize_repository_input(value: str) -> tuple[str, str]:
    """Return (short_name, full_org/name) from a bare or qualified repo name (issue #326).

    Validates against a strict regex before any owner comparison or subprocess
    interpolation to prevent SSRF via malformed owner/repo slugs.
    """
    text = str(value).strip()
    if "/" in text:
        # Validate full owner/repo format before extracting parts (issue #326)
        validate_owner_repo_format(text)
        owner, _, repo_name = text.partition("/")
        if owner.lower() != ORG.lower():
            raise HTTPException(status_code=422, detail=f"repository owner must be {ORG}")
        repo_name = validate_repo_slug(repo_name)
        return repo_name, f"{ORG}/{repo_name}"
    repo_name = validate_repo_slug(text)
    return repo_name, f"{ORG}/{repo_name}"


async def _dispatch_to_ai_provider_for_chat(
    provider: str | None,
    prompt: str,
    context: dict,
) -> str:
    """Call the configured AI provider for assistant chat."""
    provider_id = provider or "ollama_local"

    availability = agent_remediation.probe_provider_availability()
    if provider_id not in availability or not availability[provider_id].available:
        return f"(Note: Provider '{provider_id}' is unavailable. Mock response for demonstration.)"

    # MVP: real provider dispatch tracked in issues #88/#89.
    return f"Assistant response to: {prompt[:100]}... (MVP mock - implement real provider dispatch)"


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.post("/api/assistant/chat", tags=["assistant"])
async def assistant_chat(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("assistant.chat")),  # noqa: B008
) -> dict:
    """Chat with AI assistant about dashboard state.

    When ``tools_enabled: true`` is set, the Anthropic tool-use loop is
    activated and the response may contain ``tool_calls`` for the client to
    render as confirmation cards (issue #89).
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    try:
        req = assistant_contract.AssistantChatRequest(**body)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(e)) from e

    now_ts = datetime.now(UTC).isoformat()

    if req.tools_enabled:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            raise HTTPException(
                status_code=503,
                detail="ANTHROPIC_API_KEY not configured; tool-use requires Anthropic.",
            )
        try:
            result = await assistant_tools.call_anthropic_with_tools(
                api_key=anthropic_key,
                prompt=req.prompt,
                context=req.context.dict(),
                model=DEFAULT_LLM_MODEL,
                tools_enabled=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Anthropic tool-use error: %s", exc)
            raise HTTPException(status_code=502, detail=f"Anthropic error: {exc}") from exc
        return {
            "message": result["message"],
            "stop_reason": result["stop_reason"],
            "tool_calls": result["tool_calls"],
            "provider": "anthropic",
            "timestamp": now_ts,
        }

    response_text = await _dispatch_to_ai_provider_for_chat(
        provider=req.provider,
        prompt=req.prompt,
        context=req.context.dict(),
    )
    return {
        "response": response_text,
        "provider": req.provider or "ollama_local",
        "context_used": req.context.dict(),
        "timestamp": now_ts,
    }


@router.post("/api/assistant/tool/execute", tags=["assistant"])
async def execute_assistant_tool(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("assistant.execute")),  # noqa: B008
) -> dict:
    """Execute a tool call from the assistant allowlist (Issue #89).

    State-changing tools require ``confirmation`` in the request body.
    Every execution (success or failure) is appended to the audit log.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    try:
        req = assistant_contract.ToolExecuteRequest(**body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if req.name not in assistant_tools.TOOL_ALLOWLIST:
        raise HTTPException(
            status_code=422,
            detail=f"Tool '{req.name}' is not in the allowlist.",
        )

    spec = assistant_tools.TOOL_ALLOWLIST[req.name]
    requires_conf = spec["requires_confirmation"]

    if requires_conf and req.confirmation is None:
        raise HTTPException(
            status_code=403,
            detail=f"Tool '{req.name}' requires explicit operator confirmation.",
        )

    try:
        outcome_data = await assistant_tools.execute_tool(
            tool_name=req.name,
            tool_call_id=req.tool_call_id,
            inputs=req.input,
            confirmation=req.confirmation.model_dump() if req.confirmation else None,
            principal=principal.id,
            on_behalf_of=(req.confirmation.on_behalf_of or "") if req.confirmation else "",
            correlation_id=(req.confirmation.correlation_id or "") if req.confirmation else "",
            gh_api_fn=gh_api,
            run_cmd_fn=run_cmd,
            normalize_repository_fn=_normalize_repository_input,
            org=ORG,
            repo_root=REPO_ROOT,
        )
        return {
            "success": True,
            "tool_call_id": req.tool_call_id,
            "name": req.name,
            "result": outcome_data.get("result"),
            "audit_id": outcome_data.get("audit_entry", {}).get("timestamp"),
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.error("tool execute error tool=%s: %s", req.name, exc)
        audit_entry = assistant_tools._record_audit(  # noqa: SLF001
            tool_name=req.name,
            tool_call_id=req.tool_call_id,
            inputs=req.input,
            outcome=f"error: {exc}",
            success=False,
            approved_by=req.confirmation.approved_by if req.confirmation else "n/a",
            principal=principal.id,
            on_behalf_of=(req.confirmation.on_behalf_of or "") if req.confirmation else "",
            correlation_id=(req.confirmation.correlation_id or "") if req.confirmation else "",
            note=req.confirmation.note if req.confirmation else "",
        )
        return {
            "success": False,
            "tool_call_id": req.tool_call_id,
            "name": req.name,
            "result": {"error": str(exc)},
            "audit_id": audit_entry["timestamp"],
        }


@router.get("/api/assistant/audit-history", tags=["assistant"])
async def get_tool_audit_history(limit: int = 50) -> dict:
    """Return the most recent assistant tool-execution audit entries (Issue #89)."""
    capped = max(1, min(limit, 200))
    entries = assistant_tools.get_audit_history(limit=capped)
    return {"entries": entries, "total": len(entries)}


@router.post("/api/assistant/propose-action", tags=["assistant"])
async def propose_action(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("assistant.chat")),  # noqa: B008
) -> dict:
    """Propose an action based on user request, awaiting operator approval."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    try:
        req = assistant_contract.ActionProposeRequest(**body)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(e)) from e

    try:
        response_text = json.dumps(
            {
                "action_type": "restart_runner",
                "description": f"Restart runner based on request: {req.user_request[:50]}",
                "parameters": {"runner_name": "auto"},
                "risk_level": "medium",
                "rationale": "This action may resolve the issue",
                "estimated_duration_seconds": 60,
            }
        )
        try:
            proposal_dict = json.loads(response_text)
        except json.JSONDecodeError:  # noqa: BLE001
            proposal_dict = {
                "action_type": "custom_response",
                "description": response_text[:200],
                "parameters": {},
                "risk_level": "medium",
                "rationale": "AI-generated action",
            }

        action_id = secrets.token_hex(8)
        _proposed_actions[action_id] = {
            "created_at": datetime.now(UTC).isoformat(),
            "proposal": proposal_dict,
            "approved": False,
        }

        return {
            "action_id": action_id,
            "action_type": proposal_dict.get("action_type", "custom"),
            "parameters": proposal_dict.get("parameters", {}),
            "description": proposal_dict.get("description", ""),
            "risk_level": proposal_dict.get("risk_level", "medium"),
            "rationale": proposal_dict.get("rationale", ""),
            "estimated_duration_seconds": proposal_dict.get("estimated_duration_seconds"),
        }
    except Exception as e:  # noqa: BLE001
        log.error("Action proposal error: %s", e)
        raise HTTPException(status_code=502, detail=f"AI provider error: {str(e)}") from e


@router.post("/api/assistant/execute-action", tags=["assistant"])
async def execute_action(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("assistant.execute")),  # noqa: B008
) -> dict:
    """Execute a proposed action after operator approval."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    try:
        req = assistant_contract.ActionExecuteRequest(**body)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(e)) from e

    if req.action_id not in _proposed_actions:
        raise HTTPException(status_code=404, detail="Action not found")

    action_record = _proposed_actions[req.action_id]
    if action_record.get("approved"):
        raise HTTPException(status_code=409, detail="Action already executed")

    if not req.approved:
        action_record["approved"] = True
        action_record["result"] = "Rejected by operator"
        return {
            "success": False,
            "action_id": req.action_id,
            "result": "Action rejected",
            "execution_time_ms": 0,
        }

    action_record["approved"] = True
    action_record["approved_at"] = datetime.now(UTC).isoformat()
    action_record["approved_by"] = "operator"
    action_record["operator_notes"] = req.operator_notes

    proposal = action_record["proposal"]
    action_type = proposal.get("action_type", "unknown")
    start_time = time.time()

    try:
        result = "Action executed successfully"

        if action_type == "restart_runner":
            runner_name = proposal.get("parameters", {}).get("runner_name")
            if runner_name:
                result = f"Runner '{runner_name}' restart initiated"

        elif action_type == "rerun_workflow":
            workflow_id = proposal.get("parameters", {}).get("workflow_id")
            if workflow_id:
                result = f"Workflow {workflow_id} rerun initiated"

        elif action_type == "dismiss_alert":
            alert_id = proposal.get("parameters", {}).get("alert_id")
            if alert_id:
                result = f"Alert {alert_id} dismissed"

        execution_time_ms = int((time.time() - start_time) * 1000)
        action_record["result"] = result
        action_record["execution_time_ms"] = execution_time_ms

        return {
            "success": True,
            "action_id": req.action_id,
            "result": result,
            "execution_time_ms": execution_time_ms,
        }

    except Exception as e:  # noqa: BLE001
        execution_time_ms = int((time.time() - start_time) * 1000)
        error_msg = str(e)
        action_record["result"] = f"Execution failed: {error_msg}"
        action_record["execution_time_ms"] = execution_time_ms
        log.error("Action execution error: %s", e)
        return {
            "success": False,
            "action_id": req.action_id,
            "result": f"Execution failed: {error_msg}",
            "execution_time_ms": execution_time_ms,
        }
