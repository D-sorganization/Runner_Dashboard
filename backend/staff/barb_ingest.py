"""Authority-side ingest of a relayed run card (#1800, BR-06 PR 2).

Maps the version/epoch gates onto the status codes ``AuthorityClient`` understands:
200 applied or duplicate, 409 ``stale_version``, 409 ``stale_authority_epoch``, 503 ``not_authority``.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse
from staff.barb_runtime import get_authority_config
from staff.barb_versions import ApplyOutcome, StaleEpochError
from staff.run_link import apply_relayed_run_card


def ingest_relayed_card(store: Any, bus: Any, thread_id: str, body: Any) -> dict[str, Any] | JSONResponse:
    """Pre: the thread exists. Post: a replica refuses (it must never commit); otherwise the card is applied
    at most once per ``(run_id, version)`` and a fenced or stale sender gets a coded 409."""
    config = get_authority_config()
    if config.is_replica:
        return JSONResponse(
            status_code=503,
            content={"code": "not_authority", "message": f"{config.local_node} is a replica of {config.node}"},
        )
    try:
        saved, result = apply_relayed_run_card(store, bus, thread_id, body)
    except StaleEpochError as exc:
        return JSONResponse(status_code=409, content={"code": "stale_authority_epoch", "message": str(exc)})
    if result is not None and result.outcome is ApplyOutcome.STALE:
        return JSONResponse(status_code=409, content={"code": "stale_version", "message": "newer version held"})
    return {
        "ok": True,
        "thread_id": thread_id,
        "run_id": body.run_id,
        "status": body.status,
        "message_id": saved.id if saved else None,
        "outcome": result.outcome.value if result is not None else None,
    }
