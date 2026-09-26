"""Retired legacy dispatch routes returning HTTP 410 Gone (SC-G5-6 / #1503)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from identity import Principal, require_scope

router = APIRouter(tags=["remediation"])

SUNSET_DATE = "Wed, 25 Sep 2026 00:00:00 GMT"
SUCCESSOR_URL = "/api/v1/staff/requests"


@router.post("/api/agents/quick-dispatch", response_model=None, status_code=410)
async def api_quick_dispatch(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> JSONResponse:
    """Retired: Dispatch an ad-hoc agent task via Agent-Quick-Dispatch.yml.

    Returns HTTP 410 Gone with Link and Sunset headers pointing to /api/v1/staff/requests (SC-G5-6 #1503).
    """
    detail = (
        "The legacy quick-dispatch endpoint has been retired. "
        "Use the Staff Requests API at /api/v1/staff/requests instead."
    )
    headers = {
        "Link": f'<{SUCCESSOR_URL}>; rel="successor-version"',
        "Sunset": SUNSET_DATE,
    }
    return JSONResponse(
        status_code=410,
        headers=headers,
        content={
            "error": detail,
            "detail": detail,
            "redirect_url": SUCCESSOR_URL,
        },
    )


@router.post("/api/agent-remediation/dispatch-jules", status_code=410)
async def dispatch_jules_workflow(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> JSONResponse:
    """Retired: Dispatch one of this repo's agent workflows via workflow_dispatch.

    Returns HTTP 410 Gone with Link and Sunset headers pointing to /api/v1/staff/requests (SC-G5-6 #1503).
    """
    detail = (
        "The Jules remediation dispatch endpoint has been retired (RM#1483). "
        "Use the Staff Requests API at /api/v1/staff/requests or Workflows dispatch at /api/workflows/dispatch instead."
    )
    headers = {
        "Link": f'<{SUCCESSOR_URL}>; rel="successor-version"',
        "Sunset": SUNSET_DATE,
    }
    return JSONResponse(
        status_code=410,
        headers=headers,
        content={
            "error": detail,
            "detail": detail,
            "redirect_url": SUCCESSOR_URL,
        },
    )
