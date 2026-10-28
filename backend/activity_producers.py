"""In-process producer adapter for the fleet Activity Log (issue #2859, epic #2856 slice A3).

Producers (staff runs, coordination presence/messages, agent-bus hooks) call
:func:`emit_activity` and never touch :class:`activity.ActivityStore` directly (DRY,
LoD). Every failure is caught here: an activity-log outage must never block or
corrupt the producer's own operation.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from activity import ActivityEnvelope, ActivitySource, get_activity_store

log = logging.getLogger("dashboard.activity_producers")

# Opaque hub-local identity for in-process producers; never a secret or network address.
PRODUCER_PRINCIPAL = "hub:activity-producer"

_seq_lock = threading.Lock()
_next_seq: dict[tuple[str, str], int] = {}


def _reserve_seq(source: str, session_id: str) -> int:
    """Next monotonically increasing seq for ``(source, session_id)`` in this process."""
    key = (source, session_id)
    with _seq_lock:
        seq = _next_seq.get(key, 0) + 1
        _next_seq[key] = seq
        return seq


def emit_activity(
    *,
    source: ActivitySource,
    session_id: str,
    node: str,
    kind: str,
    seq: int | None = None,
    repo: str | None = None,
    issue: int | None = None,
    branch: str | None = None,
    pr: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one envelope to the activity log; never raises.

    Pre: ``source``/``session_id``/``node``/``kind`` describe one real event.
    Post: either a durable (possibly deduplicated) envelope was appended, or the
    failure was logged and swallowed — the caller's own operation is unaffected.

    ``seq`` lets a caller reuse a sequence number it already owns (e.g. the staff
    run event log's own per-run counter) so a retried call stays idempotent;
    otherwise this process's own per-``(source, session_id)`` counter is used.
    """
    try:
        resolved_seq = seq if seq is not None else _reserve_seq(source, session_id)
        envelope = ActivityEnvelope(
            schema_version=1,
            event_id=f"{source}:{session_id}:{resolved_seq}",
            source=source,
            principal=PRODUCER_PRINCIPAL,
            node=(node or "unknown")[:128],
            session_id=session_id,
            seq=resolved_seq,
            ts=datetime.now(UTC),
            repo=repo,
            issue=issue,
            branch=branch,
            pr=pr,
            kind=kind,
            redaction_version=1,
            payload=payload or {},
        )
        get_activity_store().ingest([envelope], principal=PRODUCER_PRINCIPAL)
    except Exception:  # noqa: BLE001 — an activity-log outage must never break the producer
        log.warning(
            "activity adapter failed for source=%s session_id=%s kind=%s", source, session_id, kind, exc_info=True
        )
