"""One delivery pass over the Barb outbox (#1800, BR-06). PR 2 schedules this from the app lifespan."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from staff.barb_authority import (
    AuthorityEpochMismatch,
    AuthorityRejected,
    AuthorityUnavailable,
    DeliveryOutcome,
)
from staff.barb_outbox import OutboxStore

log = logging.getLogger("dashboard.staff.barb_outbox_drain")


class _Poster(Protocol):
    def post(
        self, kind: str, thread_id: str, run_id: str, version: int, payload: dict[str, Any]
    ) -> DeliveryOutcome: ...


@dataclass(frozen=True)
class DrainStats:
    attempted: int = 0
    delivered: int = 0
    retried: int = 0
    dead: int = 0


def drain_once(outbox: OutboxStore, client: _Poster, *, limit: int = 50, now: float | None = None) -> DrainStats:
    """Deliver due entries in per-key version order.

    Post: each claimed entry ends delivered (applied, duplicate or stale), pending with a later
    ``next_attempt_at`` (unavailable / epoch fenced) or dead (rejected) - never lost. After the
    first ``AuthorityUnavailable`` the rest of the pass is released untouched (no hammering).
    """
    at = outbox.clock() if now is None else now
    claimed = outbox.claim_due(limit=limit, now=at)
    delivered = retried = dead = attempted = 0
    for i, e in enumerate(claimed):
        attempted += 1
        try:
            outcome = client.post(e.kind, e.thread_id, e.run_id, e.version, e.payload)
        except AuthorityUnavailable as exc:
            outbox.mark_retry(e.outbox_id, str(exc), now=at)
            outbox.release([c.outbox_id for c in claimed[i + 1 :]])
            retried += 1
            break
        except AuthorityEpochMismatch as exc:
            outbox.mark_retry(e.outbox_id, str(exc), now=at)
            retried += 1
        except AuthorityRejected as exc:
            log.error("barb outbox: %s %s v%d dead-lettered: %s", e.kind, e.run_id, e.version, exc)
            outbox.mark_dead(e.outbox_id, str(exc))
            dead += 1
        else:
            outbox.mark_delivered(e.outbox_id, now=at)
            delivered += 1
            log.debug("barb outbox: %s %s v%d %s", e.kind, e.run_id, e.version, outcome.value)
    return DrainStats(attempted, delivered, retried, dead)
