"""Authorization for run-card relay writes (RM#2856 / E-01)."""

from __future__ import annotations

from fastapi import HTTPException
from identity import Principal
from staff.adapters import ADAPTERS
from staff.store import RunRecord
from staff.tokens import is_active_run_principal

STAFF_RELAY_WRITE_SCOPE = "staff.relay.write"


def _lease_principal_id_for_run(rec: RunRecord) -> str:
    adapter = ADAPTERS.get(rec.provider)
    if adapter is None:
        raise HTTPException(status_code=500, detail=f"unknown provider {rec.provider!r}")
    return f"agent-{adapter.lease_agent}"


def assert_relay_run_card_authorized(*, thread_id: str, run_id: str, caller: Principal) -> RunRecord:
    """Post: ``run_id`` belongs to ``thread_id`` and ``caller`` may relay for that run."""
    from staff.runner import get_runner  # noqa: PLC0415

    rid = str(run_id or "").strip()
    tid = str(thread_id or "").strip()
    if not rid or not tid:
        raise HTTPException(status_code=403, detail="relay not authorized for this run")
    rec = get_runner().store.get_run(rid)
    if rec is None or (rec.thread_id or "").strip() != tid:
        raise HTTPException(status_code=403, detail="relay not authorized for this run")
    lease_principal = _lease_principal_id_for_run(rec)
    if caller.id == "fleet-peer" or "fleet-peer" in caller.roles:
        return rec
    if is_active_run_principal(run_id=rec.id, role=rec.role, principal_id=caller.id):
        return rec
    if caller.id == lease_principal:
        return rec
    raise HTTPException(status_code=403, detail="relay not authorized for this run")
