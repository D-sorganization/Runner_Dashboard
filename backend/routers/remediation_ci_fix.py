"""Router for RD-1 Event-Driven CI-Fix Dispatch (Issue #1846).

Provides HTTP endpoints to:
- Evaluate workflow run events for CI-fix eligibility.
- Dispatch fresh, small, capped CI-fix sessions with concurrency locking.
- Query and release active PR locks.
"""

from __future__ import annotations

import logging
from typing import Any

from ci_fix_dispatch import (
    GLOBAL_CI_FIX_LOCK_MGR,
    build_ci_fix_prompt,
    classify_failure_type,
    evaluate_ci_fix_trigger,
    extract_failing_test_names,
    record_ci_fix_audit,
    route_ci_fix,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_scope

logger = logging.getLogger("dashboard.remediation.ci_fix")

router = APIRouter(tags=["remediation", "ci-fix"])


@router.post("/api/remediation/ci-fix/evaluate")
async def evaluate_ci_fix_endpoint(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict[str, Any]:
    """Evaluate whether an incoming event payload warrants an automated CI-fix session."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Expected JSON object body")

    decision = evaluate_ci_fix_trigger(body, lock_manager=GLOBAL_CI_FIX_LOCK_MGR)
    result = decision.to_dict()

    if decision.eligible:
        route = route_ci_fix(decision.failure_type, attempt_number=1)
        result["route"] = route.to_dict()

    return result


@router.post("/api/remediation/ci-fix/dispatch")
async def dispatch_ci_fix_endpoint(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict[str, Any]:
    """Dispatch a capped CI-fix session for an open pull request."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Expected JSON object body")

    repo = str(body.get("repo") or body.get("repository") or "").strip()
    pr_num_raw = body.get("pr_number") or body.get("number")
    pr_number = 0
    if pr_num_raw is not None:
        try:
            pr_number = int(pr_num_raw)
        except (TypeError, ValueError):
            pr_number = 0

    if not repo or pr_number <= 0:
        raise HTTPException(status_code=400, detail="Valid repo and pr_number are required")

    session_id = str(body.get("session_id") or f"ci-fix-{repo}-{pr_number}")
    acquired = GLOBAL_CI_FIX_LOCK_MGR.acquire(repo, pr_number, session_id=session_id)
    if not acquired:
        raise HTTPException(
            status_code=409,
            detail=f"A CI-fix session is already active for {repo}#{pr_number}",
        )

    workflow_name = str(body.get("workflow_name") or "")
    log_tail = str(body.get("log_tail") or "")
    pr_diff = str(body.get("pr_diff") or "")
    branch = str(body.get("branch") or "")
    run_id_raw = body.get("run_id")
    run_id: int | None = None
    if run_id_raw is not None:
        try:
            run_id = int(run_id_raw)
        except (TypeError, ValueError):
            run_id = None

    attempt_raw = body.get("attempt_number", 1)
    attempt_number = 1
    if attempt_raw is not None:
        try:
            attempt_number = int(attempt_raw)
        except (TypeError, ValueError):
            attempt_number = 1

    failure_type = body.get("failure_type")
    if not failure_type:
        failure_type = classify_failure_type(workflow_name, log_tail)

    failing_tests = body.get("failing_tests")
    if failing_tests is None or not isinstance(failing_tests, list):
        failing_tests = extract_failing_test_names(log_tail)

    conflicting_files = body.get("conflicting_files")
    if conflicting_files is not None and not isinstance(conflicting_files, list):
        conflicting_files = None

    route = route_ci_fix(failure_type, attempt_number=attempt_number)
    prompt = build_ci_fix_prompt(
        repo=repo,
        pr_number=pr_number,
        branch=branch,
        log_tail=log_tail,
        failing_tests=failing_tests,
        pr_diff=pr_diff,
        conflicting_files=conflicting_files,
    )

    record_ci_fix_audit(
        repo=repo,
        pr_number=pr_number,
        workflow_name=workflow_name,
        run_id=run_id,
        failure_type=failure_type,
        provider=route.provider,
        model=route.model,
        attempt_number=attempt_number,
        cost_estimate=route.cost_budget,
    )

    return {
        "status": "dispatched",
        "repo": repo,
        "pr_number": pr_number,
        "route": route.to_dict(),
        "prompt": prompt,
        "session_id": session_id,
        "failing_tests": failing_tests,
    }


@router.get("/api/remediation/ci-fix/locks")
async def list_ci_fix_locks_endpoint(
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict[str, Any]:
    """List all active CI-fix PR concurrency locks."""
    return {"locks": GLOBAL_CI_FIX_LOCK_MGR.list_locks()}


@router.delete("/api/remediation/ci-fix/locks/{repo}/{pr_number}")
async def release_ci_fix_lock_endpoint(
    repo: str,
    pr_number: int,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict[str, Any]:
    """Release a CI-fix concurrency lock for a PR."""
    released = GLOBAL_CI_FIX_LOCK_MGR.release(repo, pr_number)
    return {"released": released, "repo": repo, "pr_number": pr_number}
