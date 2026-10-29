from __future__ import annotations

import asyncio
import logging

from agent_bus.store import get_store

log = logging.getLogger("dashboard.agent_bus.reaper")
REAP_INTERVAL_SECONDS = 60
RELEASE_REASON = "agent_bus lease expired"


def reap_once() -> dict[str, int]:
    store = get_store()
    stale = store.list_stale_principals()
    released = 0
    for row in stale:
        try:
            released += store.release_tracked_claims(row.session_id, row.role, reason=RELEASE_REASON)
        except Exception as exc:  # noqa: BLE001 — reaper must not stall the loop
            log.warning("agent_bus reaper: claim release failed for %s: %s", row.session_id, exc)
            continue
        store.drop_principal(row.session_id)
    if stale:
        log.info("agent_bus reaper: cleared %d stale sessions, released %d claims", len(stale), released)
    return {"released_claims": released, "stale_sessions": len(stale)}


async def reaper_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(reap_once)
        except Exception as exc:  # noqa: BLE001
            log.exception("agent_bus reaper: unexpected error: %s", exc)
        await asyncio.sleep(REAP_INTERVAL_SECONDS)
