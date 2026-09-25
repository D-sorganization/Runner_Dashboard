"""Staff Maintenance API endpoints for detection and remediation (SC-E5, Issue #1322)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, Field
from staff.actions import ActionContext
from staff.conversations import get_conversation_store
from staff.maintenance_detect import (
    DetectionIssue,
    run_maintenance_detectors,
)
from staff.maintenance_playbooks import remediate_issue
from staff.work_items import WorkItemStore

log = logging.getLogger("dashboard.staff.maintenance_router")

router = APIRouter(tags=["staff-maintenance"])


class MaintenanceScanRequest(BaseModel):
    runs: list[dict[str, Any]] = Field(default_factory=list, description="Runs to inspect")
    runners: list[dict[str, Any]] = Field(default_factory=list, description="Runners to inspect")
    known_inventory: list[str] = Field(default_factory=list, description="Known fleet inventory")


class MaintenanceRemediateRequest(BaseModel):
    issue_id: str
    detector: str
    severity: str = "medium"
    target: str
    host: str = "local"
    details: dict[str, Any] = Field(default_factory=dict)
    suggested_action: str
    risk_class: str = "medium"
    self_heal_eligible: bool = False


@router.post("/maintenance/scan", response_model_exclude_none=True)
async def scan_maintenance(
    body: MaintenanceScanRequest,
    caller: Principal = Depends(require_scope("staff.read")),  # noqa: B008
) -> dict[str, Any]:
    """Execute diagnostic detectors across provided or discovered runners and queue runs."""
    log.info("Maintenance scan triggered by %s", format_caller(caller))
    inventory = set(body.known_inventory) if body.known_inventory else None
    result = run_maintenance_detectors(
        runs=body.runs,
        runners=body.runners,
        known_inventory=inventory,
    )
    return result.to_dict()


@router.post("/maintenance/remediate", response_model_exclude_none=True)
async def remediate_maintenance_issue(
    body: MaintenanceRemediateRequest,
    caller: Principal = Depends(require_scope("fleet.maintain")),  # noqa: B008
) -> dict[str, Any]:
    """Execute remediation playbook or generate proposal for a detected issue."""
    log.info("Remediation requested for issue %s by %s", body.issue_id, format_caller(caller))
    issue = DetectionIssue(
        issue_id=body.issue_id,
        detector=body.detector,
        severity=body.severity,
        target=body.target,
        host=body.host,
        details=body.details,
        suggested_action=body.suggested_action,
        risk_class=body.risk_class,
        self_heal_eligible=body.self_heal_eligible,
    )
    ctx = ActionContext(caller=caller)
    try:
        outcome = remediate_issue(
            issue=issue,
            ctx=ctx,
            conversation_store=get_conversation_store(),
            work_item_store=WorkItemStore(),
        )
        return outcome
    except Exception as exc:
        log.exception("Remediation failed for %s", body.issue_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Remediation error: {exc}",
        ) from exc
