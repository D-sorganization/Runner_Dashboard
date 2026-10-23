"""Route-level adapter for local source/work/page library fetches."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from identity import Principal
from library.citations import _cap_fetch_text
from library.fetch_locator import fetch_locator
from library.run_reference_recording import record_run_references, requested_run_id
from library.search import AmbiguousPassageError


def fetch_page_locator(
    request: Request,
    principal: Principal,
    db_path: Path,
    *,
    source_id: str | None,
    work_id: int | None,
    page: int | None,
    scope: str,
    local_node: str,
) -> dict[str, Any]:
    """Authorize, resolve, and record a local source/work/page fetch."""
    run_id = requested_run_id(request, principal)
    if source_id is None or work_id is None or page is None:
        raise HTTPException(status_code=422, detail="source_id, work_id, and page are required together")
    if scope != "local":
        raise HTTPException(status_code=501, detail="federated page locator resolution is not available")
    try:
        hit = fetch_locator(
            db_path,
            source_id=source_id,
            work_id=work_id,
            page=page,
            shared_only=principal.id == "fleet-peer",
        )
    except AmbiguousPassageError as exc:
        raise HTTPException(status_code=409, detail="page locator is ambiguous") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="page locator not found") from exc
    hit = replace(hit, node=local_node)
    payload = {
        "citation": hit.citation,
        "cite_key": hit.cite_key,
        "cite_uri": hit.cite_uri,
        "file_sha256": hit.file_sha256,
        "work_sha256": hit.work_sha256,
        "source_id": hit.source_id,
        "node": local_node,
        "work_id": hit.work_id,
        "passage_id": hit.passage_id,
        "page_start": hit.page_start,
        "page_end": hit.page_end,
        "text": _cap_fetch_text(hit.text),
        "token_estimate": hit.token_estimate,
    }
    record_run_references(run_id, [payload], db_path)
    return payload
