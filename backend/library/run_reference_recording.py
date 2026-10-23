"""Run ownership checks and retrieval-ledger writes for library APIs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from identity import Principal


def requested_run_id(
    request: Request,
    principal: Principal,
    *,
    body_run_id: str | None = None,
) -> str | None:
    """Resolve and authorize an explicit or authenticated active-run identity."""
    header_run_id = request.headers.get("X-Staff-Run-ID") or None
    if header_run_id is not None and body_run_id is not None and header_run_id != body_run_id:
        raise HTTPException(status_code=422, detail="run identity sources conflict")
    run_id: str | None
    if body_run_id is not None:
        run_id = body_run_id
    else:
        run_id = header_run_id or os.environ.get("STAFF_RUN_ID")
    from staff.tokens import active_run_id_for_principal

    active_run_id = active_run_id_for_principal(principal.id)
    run_binding_roles = {role for role in principal.roles if role.startswith("staff-run:")}
    if run_binding_roles and (active_run_id is None or run_binding_roles != {f"staff-run:{active_run_id}"}):
        raise HTTPException(status_code=403, detail="run token binding is no longer active")
    if active_run_id is not None:
        if run_id is not None and run_id != active_run_id:
            raise HTTPException(status_code=403, detail="run token is bound to another run")
        run_id = active_run_id
    elif run_id is None:
        return None
    from library.retrieval_ledger import authorize_run_owner
    from staff.store import get_run_store

    try:
        authorize_run_owner(get_run_store(), run_id=run_id, principal_id=principal.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid run id") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="run belongs to another principal") from exc
    return run_id


def record_run_references(run_id: str | None, passages: list[dict[str, Any]], db_path: Path) -> None:
    """Append successful passage identities to the run ledger."""
    if run_id is None or not passages:
        return
    from library.retrieval_ledger import record_retrievals

    references = [
        {
            "node": passage.get("node"),
            "source_id": passage.get("source_id"),
            "work_id": passage.get("work_id"),
            "passage_id": passage.get("passage_id"),
            "file_sha256": passage.get("file_sha256"),
            "page_start": passage.get("page_start"),
            "page_end": passage.get("page_end"),
        }
        for passage in passages
    ]
    try:
        record_retrievals(db_path, run_id=run_id, references=references)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="retrieval identity could not be recorded") from exc
