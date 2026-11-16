"""Cartographer scheduler<->persona pending-run wiring (#3788; 500-line cap split).

Extracted from ``staff.scheduler`` so the fire-time fallback-lease fencing and the
terminal-run capsule/report reconciliation for the Cartographer role live together,
next to the ``persona_fallback_lease`` and ``cartographer_persona`` calls they wrap.
All functions here are stateless helpers that take the scheduler's lock and shared
state so the caller keeps its own locking discipline.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from staff.barb_continuity import BarbContinuityStore
from staff.role_capsule import RoleCapsuleStore

from staff import cartographer_persona, persona_fallback_lease

log = logging.getLogger("dashboard.staff.scheduler")


def fire_guard(
    role_name: str,
    *,
    continuity: BarbContinuityStore,
    job: str,
    slot: str,
    now: datetime,
) -> str:
    """Skip reason when firing this Cartographer slot must be suppressed, else "".

    An active Grok fallback lease or an already-claimed slot must stop RD from also
    invoking the model for the same slot (#3788): no ``runner.submit`` at all this tick.
    """
    if persona_fallback_lease.is_scheduler_blocked_by_fallback(continuity, role_name, now=now):
        return "fallback_lease_held"
    if persona_fallback_lease.should_skip_slot(continuity, role=role_name, job=job, slot=slot, now=now):
        return "slot_already_executed"
    return ""


def reconcile_pending(
    lock: threading.RLock,
    pending: dict[str, dict[str, str]],
    *,
    store: Any,
    continuity_factory: Callable[[], BarbContinuityStore],
    capsules_factory: Callable[[], RoleCapsuleStore],
    notify_opts: Mapping[str, Any],
    machine: str,
    now: datetime,
) -> None:
    """Persist the Cartographer capsule/report once a dispatched slot's run goes terminal.

    Evidence is recorded only for a ``succeeded`` run (#3788 acceptance): a failed, cancelled
    or needs_input run is dropped from the pending set without writing a capsule, so a
    provider failure (including a provider-auth error, which surfaces as ``failed``) is never
    recorded as a successful sweep. Reuses ``run_cartographer_slot`` wholesale so the fallback
    lease recheck, slot ledger entry and capsule/notify delivery stay the one persona contract.
    """
    with lock:
        snapshot = dict(pending)
    for run_id, info in snapshot.items():
        rec = store.get_run(run_id)
        if rec is None or rec.status in ("queued", "preparing", "running", "needs_input"):
            continue
        # A "succeeded" run whose post-run verification (#1516) came back "failed" is not a
        # successful sweep either -- the model-reported exit code is not the acceptance signal.
        if rec.status != "succeeded" or rec.verification == "failed":
            with lock:
                pending.pop(run_id, None)
            log.info(
                "staff scheduler: cartographer run %s ended %s (verification=%s); no capsule recorded",
                run_id,
                rec.status,
                rec.verification,
            )
            continue
        try:
            result = cartographer_persona.run_cartographer_slot(
                continuity_factory(),
                capsules_factory(),
                job=info["job"],
                slot=info["slot"],
                sweep_date=now.date().isoformat(),
                notify_opts=notify_opts,
                summary=(rec.outcome or "Cartographer weekly map sweep"),
                holder=f"rd:{machine.lower()}",
                now=now,
            )
        except Exception:  # noqa: BLE001
            # Left pending: a hard persistence failure here stays retryable and visible on the
            # next tick's reconciliation pass, rather than being silently dropped (#3788).
            log.exception("staff scheduler: failed to record cartographer capsule for run %s", run_id)
            continue
        status = result.get("status")
        if status != "executed":
            # The persona contract wrote no capsule/notify for "failed" or "skipped" (#3788
            # follow-up). A fallback-lease-held or persistence-failed result is left pending so
            # the next tick retries once the condition clears; a duplicate-slot skip is terminal
            # (another holder already delivered this slot) and is dropped instead.
            if result.get("reason") == "slot_already_executed":
                with lock:
                    pending.pop(run_id, None)
            log.info(
                "staff scheduler: cartographer run %s did not record a capsule (%s: %s)",
                run_id,
                status,
                result.get("reason"),
            )
            continue
        with lock:
            pending.pop(run_id, None)
        log.info("staff scheduler: cartographer run %s recorded (%s)", run_id, status)
