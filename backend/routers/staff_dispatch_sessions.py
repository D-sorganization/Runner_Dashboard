"""Admin visibility and manual release for in-memory dispatch overlap sessions."""

# ruff: noqa: B008

from __future__ import annotations

import logging

from dispatch_queue_link import run_id_for_session
from dispatch_routing import dispatch_queue_manager
from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, format_caller, require_scope
from staff.audit import record_audit

log = logging.getLogger("dashboard.staff.dispatch_sessions")

router = APIRouter(tags=["staff-dispatch-sessions"])
_admin = require_scope("staff.admin")


@router.get("/dispatch-sessions")
def list_dispatch_sessions(_caller: Principal = Depends(_admin)) -> dict[str, object]:
    rows = dispatch_queue_manager.list_session_rows()
    for row in rows:
        sid = str(row["session_id"])
        if not row.get("run_id"):
            row["run_id"] = run_id_for_session(sid)
    return {"count": len(rows), "sessions": rows}


@router.delete("/dispatch-sessions/{session_id}")
def release_dispatch_session(
    session_id: str,
    caller: Principal = Depends(_admin),
) -> dict[str, object]:
    sid = session_id.strip()
    if sid not in dispatch_queue_manager._active_sessions:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "session not active"})
    row = next((r for r in dispatch_queue_manager.list_session_rows() if r["session_id"] == sid), {})
    dispatch_queue_manager.release_session(sid, outcome="admin_release")
    record_audit(
        action="dispatch_session.release",
        target=f"session:{sid}",
        principal=format_caller(caller),
        outcome="released",
        detail={"session": row, "reason": "admin_release"},
        fail_closed=False,
    )
    log.info("admin released dispatch session %s by %s", sid, format_caller(caller))
    return {"released": True, "session_id": sid}
