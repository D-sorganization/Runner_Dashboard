"""Fleet backup restore drill failure ingest (#4020)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fleet_backup_drill import emit_restore_drill_failure_alert
from identity import require_fleet_peer
from pydantic import BaseModel, Field

router = APIRouter(tags=["fleet-backup"])


class DrillFailureBody(BaseModel):
    machine: str = Field(..., min_length=1, max_length=128)
    detail: str = Field(..., min_length=1, max_length=4000)


@router.post("/api/fleet/backup/drill-failure")
async def post_backup_drill_failure(
    body: DrillFailureBody,
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, bool]:
    emit_restore_drill_failure_alert(body.machine, body.detail)
    return {"ok": True}
