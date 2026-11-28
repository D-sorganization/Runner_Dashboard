"""Scheduled quota auto-restore for shared provider-down markers (#4564 slice 2)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from activity_producers import emit_activity
from coordination import provider_down_markers as markers

log = logging.getLogger("dashboard.coordination.provider_marker_restore")

DEFAULT_INTERVAL_SECONDS = 300.0
SESSION_ID = "provider-marker-restore"


@dataclass(frozen=True)
class RestoreTickResult:
    """Post: counts match markers removed from RD state on this tick."""

    store_ok: bool
    cleared_expired: tuple[dict[str, Any], ...]
    cleared_recovered: tuple[dict[str, Any], ...]

    @property
    def cleared_total(self) -> int:
        return len(self.cleared_expired) + len(self.cleared_recovered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "store_ok": self.store_ok,
            "cleared_expired": list(self.cleared_expired),
            "cleared_recovered": list(self.cleared_recovered),
            "cleared_total": self.cleared_total,
            "remaining": None,  # filled by caller after persist
        }


def _default_provider_healthy(provider: str) -> bool:
    from staff.availability import is_provider_healthy  # noqa: PLC0415

    return is_provider_healthy(provider)


def run_provider_marker_restore_tick(
    *,
    now: float | None = None,
    provider_healthy: Callable[[str], bool] | None = None,
    audit: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Remove expired markers and markers whose provider has recovered.

    Pre: ``provider_healthy`` when given maps lane provider ids to a recovery signal.
    Post: unreadable marker store yields ``store_ok=false`` and clears nothing (fail open).
    """
    current = time.time() if now is None else float(now)
    healthy = provider_healthy or _default_provider_healthy
    store_ok, cleared_expired, cleared_recovered, remaining = markers.restore_prune_markers(
        now=current,
        provider_recovered=healthy,
    )
    if not store_ok:
        return RestoreTickResult(store_ok=False, cleared_expired=(), cleared_recovered=()).as_dict()

    outcome = RestoreTickResult(
        store_ok=True,
        cleared_expired=tuple(cleared_expired),
        cleared_recovered=tuple(cleared_recovered),
    )
    payload = outcome.as_dict()
    payload["remaining"] = remaining
    if outcome.cleared_total and audit is not None:
        audit(payload)
    return payload


def emit_restore_audit(payload: dict[str, Any], *, node: str) -> None:
    log.info(
        "audit: provider_marker_restore expired=%d recovered=%d remaining=%s",
        len(payload.get("cleared_expired") or []),
        len(payload.get("cleared_recovered") or []),
        payload.get("remaining"),
    )
    emit_activity(
        source="coordination",
        session_id=SESSION_ID,
        node=node,
        kind="provider_marker_restore",
        payload={
            "cleared_expired": payload.get("cleared_expired") or [],
            "cleared_recovered": payload.get("cleared_recovered") or [],
            "cleared_total": payload.get("cleared_total") or 0,
            "remaining": payload.get("remaining"),
        },
    )


def restore_interval_seconds() -> float:
    raw = (os.environ.get("PROVIDER_MARKER_RESTORE_INTERVAL_S") or "").strip()
    if raw:
        try:
            parsed = float(raw)
            if parsed >= 60.0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_INTERVAL_SECONDS


async def periodic_provider_marker_restore_loop(
    *,
    interval: float | None = None,
    node: str = "hub",
) -> None:
    """Hub background task: prune expired / recovered markers on a schedule."""
    wait = restore_interval_seconds() if interval is None else interval
    while True:
        try:
            payload = await asyncio.to_thread(
                run_provider_marker_restore_tick,
                audit=lambda body: emit_restore_audit(body, node=node),
            )
            if payload.get("cleared_total"):
                log.info("provider marker restore tick cleared %s markers", payload["cleared_total"])
        except Exception:  # noqa: BLE001 — loop must survive a bad tick
            log.exception("provider marker restore tick failed")
        await asyncio.sleep(wait)
