"""Read-only attention for queued runs that have lost their in-process worker."""

from __future__ import annotations

import math
from collections.abc import Set
from datetime import UTC, datetime
from typing import Any

from staff.classifier import attention_item_for_run
from staff.store import RunRecord

STALE_QUEUED_SECONDS = 10 * 60


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def format_stale_queued_attention(
    runs: list[RunRecord],
    *,
    machine: str,
    active_worker_ids: Set[str],
    now: datetime | None = None,
    stale_after_seconds: float = STALE_QUEUED_SECONDS,
) -> list[dict[str, Any]]:
    """Flag local queued rows older than the threshold, excluding live/backoff work."""
    if not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0:
        raise ValueError("stale_after_seconds must be positive and finite")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    items: list[dict[str, Any]] = []
    for run in runs:
        if (
            run.status != "queued"
            or (run.machine and run.machine != machine)
            or run.started_at
            or run.pid is not None
            or run.id in active_worker_ids
        ):
            continue
        created = _parse_time(run.created_at)
        if created is None:
            continue
        eligible_since = created
        if run.next_attempt_at:
            due = _parse_time(run.next_attempt_at)
            if due is None:
                continue
            eligible_since = max(created, due)
        age = (current - created).total_seconds()
        if (current - eligible_since).total_seconds() <= stale_after_seconds:
            continue
        item = attention_item_for_run(run)
        item.update(
            failure_class="orphaned_queued",
            retryable=True,
            error=f"Queued for {int(age)} seconds; no live worker owns this entry.",
            remediation="Confirm no worker is running, cancel the abandoned queued entry, and submit a new run.",
            queued_seconds=int(age),
        )
        items.append(item)
    return items
