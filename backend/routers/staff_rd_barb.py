"""rd-barb read API for Grok Barb (RD #3065, epic #3064). Agent-facing, so not in the generated client."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, require_scope
from staff.rd_barb_memory import GuidelinesDoc, default_guidelines_path
from staff.rd_barb_policy import LockedPolicyError, load_locked_policy

router = APIRouter(prefix="/api/v1/staff/rd-barb", tags=["staff", "rd-barb"], include_in_schema=False)


def guidelines_doc() -> GuidelinesDoc:
    return GuidelinesDoc(default_guidelines_path(), load_locked_policy())


@router.get("/guidelines")
def get_guidelines(_principal: Principal = Depends(require_scope("fleet.read"))) -> dict[str, Any]:  # noqa: B008
    """Rendered guidelines doc plus ``size_bytes`` and ``compacted_at``; entries are secret-linted on write."""
    try:
        doc = guidelines_doc()
    except LockedPolicyError as exc:  # fail closed: no policy, no doc
        raise HTTPException(status_code=503, detail="rd-barb locked policy unavailable") from exc
    text = doc.render()
    return {"doc": text, "size_bytes": len(text.encode("utf-8")), "compacted_at": doc.compacted_at}
