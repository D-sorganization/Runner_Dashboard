from __future__ import annotations

import asyncio
import logging

from agent_bus.reaper_stats import record_reap
from agent_bus.store import get_store

log = logging.getLogger("dashboard.agent_bus.reaper")
REAP_INTERVAL_SECONDS = 60
RELEASE_REASON = "agent_bus lease expired"


def reap_once() -> dict[str, int]:
    store = get_store()
    stale = store.list_stale_principals()
    released = 0
    errors = 0
    for row in stale:
        try:
            released += store.release_tracked_claims(row.session_id, row.role, reason=RELEASE_REASON)
        except Exception as exc:  # noqa: BLE001 — reaper must not stall the loop
            errors += 1
            log.warning("agent_bus reaper: claim release failed for %s: %s", row.session_id, exc)
            continue
        store.drop_principal(row.session_id)
    if stale:
        log.info("agent_bus reaper: cleared %d stale sessions, released %d claims", len(stale), released)
    result = {"released_claims": released, "stale_sessions": len(stale)}
    record_reap(result, errors=errors)
    return result


async def reaper_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(reap_once)
        except Exception as exc:  # noqa: BLE001
            log.exception("agent_bus reaper: unexpected error: %s", exc)
            record_reap({"released_claims": 0, "stale_sessions": 0}, errors=1)
        await asyncio.sleep(REAP_INTERVAL_SECONDS)
