"""Work-request API: one way to request work (SC-G5-1, #1497).

``POST /api/v1/staff/requests`` validates the body per kind, records a Work Item and an
ActionProposal on the caller's *Requests* thread and executes through the action
registry. Responses: 200 dry-run plan, 201 executed, 202 approval required; a failed
backend returns its classified status as the v1 error envelope (SC-F3) whose
``error.request`` keeps the recorded ids, so the caller can follow the kept request.
"""

from __future__ import annotations

from functools import partial
from typing import Any

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from identity import Principal, require_principal
from routers.staff_threads import _get_store_or_503
from staff.v1_envelope import format_error_envelope
from staff.work_items import get_work_item_store
from staff.work_requests import RequestOutcome, RequestRejectedError, WorkRequest, submit_request

router = APIRouter(tags=["staff-requests"])


def _failure_envelope(outcome: RequestOutcome, request: Request) -> JSONResponse:
    """The v1 error envelope for a recorded request whose backend failed.

    Post: ``error.code`` is the action's failure class and ``error.request`` carries the
    thread, message, work item and proposal ids (the middleware keeps a built envelope).
    """
    body = outcome.body
    detail = {"code": body.get("failure_class") or "error", "error": body.get("error") or "request failed"}
    envelope = format_error_envelope(outcome.http_status, detail, getattr(request.state, "request_id", None))
    envelope["error"]["request"] = {k: v for k, v in body.items() if k not in ("error", "failure_class")}
    return JSONResponse(status_code=outcome.http_status, content=envelope)


@router.post("/requests", response_model_exclude_none=True, response_model=None)
async def create_request(
    body: WorkRequest,
    request: Request,
    response: Response,
    caller: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any] | JSONResponse:
    """Request work of any registered kind; see ``staff.work_requests``."""
    store = _get_store_or_503()
    try:
        outcome = await anyio.to_thread.run_sync(
            partial(submit_request, body, caller, store=store, work_items=get_work_item_store())
        )
    except RequestRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if outcome.state == "failed":
        return _failure_envelope(outcome, request)
    response.status_code = outcome.http_status
    return outcome.body
