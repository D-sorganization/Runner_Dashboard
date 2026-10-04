"""Router for RD-1 Event-Driven CI-Fix Dispatch (Issue #1846; wired by #1881 / #1879).

Provides HTTP endpoints to:
- Receive GitHub webhooks (``workflow_run`` failures, ``pull_request`` ``dequeued``) and
  launch CI fixes, behind ``CI_FIX_DISPATCH_ENABLED`` (default off).
- Evaluate workflow run events for CI-fix eligibility.
- Dispatch fresh, small, capped CI-fix sessions through the staff dispatch path.
- Query and release active PR locks.

The webhook authenticates by ``X-Hub-Signature-256`` against ``GITHUB_WEBHOOK_SECRET``
(fail closed when unset), like the Linear webhook; it is exempt from the operator perimeter
and CSRF check for that reason. Every other route requires ``remediation.dispatch``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ci_fix_dispatch import (
    GLOBAL_CI_FIX_LOCK_MGR,
    evaluate_ci_fix_trigger,
    route_ci_fix,
)
from ci_fix_events import (
    GITHUB_WEBHOOK_SECRET_ENV,
    CIFixEvent,
    ci_fix_dispatch_enabled,
    parse_github_event,
    verify_github_signature,
)
from ci_fix_service import (
    CIFixBusyError,
    CIFixDispatchRequest,
    CIFixLaunchUnavailableError,
    dispatch_ci_fix,
    handle_ci_fix_event,
)
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from identity import Principal, require_scope
from replay_store import ReplayStore

logger = logging.getLogger("dashboard.remediation.ci_fix")

router = APIRouter(tags=["remediation", "ci-fix"])

WEBHOOK_PATH = "/api/remediation/ci-fix/webhook"
_REPLAY_DB_PATH = Path(
    os.environ.get(
        "GITHUB_WEBHOOK_REPLAY_DB",
        str(Path.home() / ".local" / "share" / "runner-dashboard" / "github_webhook_replay.db"),
    )
)
_replay_store: ReplayStore | None = None


def _get_replay_store() -> ReplayStore:
    """Delivery-id dedupe (GitHub redeliveries reuse ``X-GitHub-Delivery``); opened on first use."""
    global _replay_store
    if _replay_store is None:
        _replay_store = ReplayStore(_REPLAY_DB_PATH, ttl_s=86_400, max_entries=50_000)
    return _replay_store


async def _process_delivery(event: CIFixEvent, delivery_id: str) -> None:
    """Dispatch, then mark the delivery done only if the dispatch was accepted (#1887 review).

    A skipped or failed dispatch leaves the delivery unrecorded, so a manual GitHub
    redelivery (same ``X-GitHub-Delivery``) can retry it.
    """
    result = await handle_ci_fix_event(event)
    if delivery_id and result.get("dispatched"):
        _get_replay_store().record(delivery_id)


@router.post(WEBHOOK_PATH, status_code=202)
async def github_ci_fix_webhook(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    x_github_event: str = Header("", alias="X-GitHub-Event"),
    x_github_delivery: str = Header("", alias="X-GitHub-Delivery"),
) -> dict[str, Any]:
    """Receive a signed GitHub webhook and queue at most one CI-fix dispatch for its PR."""
    body = await request.body()
    secret = os.environ.get(GITHUB_WEBHOOK_SECRET_ENV, "").strip()
    if not secret:
        logger.error("ci-fix webhook: %s is not set; rejecting delivery", GITHUB_WEBHOOK_SECRET_ENV)
        raise HTTPException(status_code=503, detail="GitHub webhook secret not configured")
    if not verify_github_signature(body, x_hub_signature_256, secret):
        raise HTTPException(status_code=401, detail="Signature verification failed")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Expected JSON object body")

    if x_github_delivery and _get_replay_store().is_replay(x_github_delivery):
        return {"accepted": False, "reason": "replayed delivery"}
    if not ci_fix_dispatch_enabled():
        return {"accepted": False, "reason": "CI_FIX_DISPATCH_ENABLED is off"}

    event, reason = parse_github_event(x_github_event, payload)
    if event is None:
        return {"accepted": False, "reason": reason}
    background.add_task(_process_delivery, event, x_github_delivery)
    logger.info("ci-fix webhook: queued %s for %s#%d", event.kind, event.full_repo, event.pr_number)
    return {"accepted": True, "kind": event.kind, "repo": event.repo, "pr_number": event.pr_number}


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
    body: CIFixDispatchRequest,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict[str, Any]:
    """Launch a capped CI-fix session for an open pull request through the staff dispatch path.

    409 while another CI-fix session holds the PR; 501 when the routed provider is not
    installed on this node (nothing is launched and the lock is released).
    """
    try:
        return await dispatch_ci_fix(body, principal)
    except CIFixBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CIFixLaunchUnavailableError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


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
