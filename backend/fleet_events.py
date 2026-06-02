"""Fleet event log + alarm feed (issue #863).

Replaces the old screen-covering pop-up model with a durable, append-only
history of fleet events that the frontend renders as a scrollable event log and
an Overview alarm panel.

This module owns three orthogonal pieces, kept deliberately decoupled so each
is independently testable (Orthogonality, TDD):

  1. ``FleetEvent`` — the typed, frozen record persisted in the feed.
  2. ``classify_fleet_events`` — a **pure** function that diffs a previous fleet
     snapshot against the current one and emits the events implied by the
     transition (runner went offline with a *reason*, came back online, disk
     pressure crossed a threshold, fleet saturation, watchdog regression).
  3. ``EventStore`` — a bounded, thread-safe ring buffer that records events and
     serves the most-recent slice to ``GET /api/events``.

Design by Contract: ``classify_fleet_events`` is pure (same input → same
output); the store enforces its capacity invariant on every append. The
classifier never mutates its inputs.

Disk classification (the load-bearing requirement): a node is treated as
*offline due to disk pressure* when it transitions online→offline AND its last
known / current disk metric shows critical pressure (free GB <= the configured
minimum, or used percent >= the critical threshold). The same disk metric also
drives standalone ``low_disk`` warnings at the warn threshold even while the
node is still online — so operators see the pressure *before* the node drops.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from dashboard_config import (
    DISK_CRITICAL_PERCENT,
    DISK_MIN_FREE_GB,
    DISK_WARN_PERCENT,
)

# Re-exported so consumers/tests can read the active thresholds without a second
# import of dashboard_config (DRY single source).
__all__ = [
    "DISK_CRITICAL_PERCENT",
    "DISK_MIN_FREE_GB",
    "DISK_WARN_PERCENT",
    "DEFAULT_CAPACITY",
    "EventKind",
    "EventStore",
    "FleetEvent",
    "FleetEventPoller",
    "NodeSnapshot",
    "Severity",
    "classify_fleet_events",
    "classify_node_offline_event",
    "get_event_store",
    "node_disk_is_critical",
    "node_disk_is_low",
    "nodes_from_fleet_status",
]

Severity = Literal["info", "warning", "critical"]
EventKind = Literal[
    "runner_offline",
    "runner_online",
    "low_disk",
    "saturation",
    "watchdog",
]

# Default ring-buffer capacity. Large enough to hold a meaningful operator
# history across a shift, small enough to stay cheap in memory and over the wire.
DEFAULT_CAPACITY = 500


@dataclass(frozen=True, slots=True)
class FleetEvent:
    """A single, immutable fleet event surfaced in the event log.

    Invariants (enforced in ``__post_init__``):
      - ``ts`` is a non-negative epoch-millisecond timestamp;
      - ``severity`` is one of info|warning|critical;
      - ``kind`` is one of the known event kinds;
      - ``title`` is non-empty.
    """

    ts: int
    severity: Severity
    kind: EventKind
    title: str
    detail: str = ""
    node: str | None = None

    def __post_init__(self) -> None:
        if self.ts < 0:
            raise ValueError(f"FleetEvent.ts must be >= 0, got {self.ts!r}")
        if self.severity not in ("info", "warning", "critical"):
            raise ValueError(f"FleetEvent.severity invalid: {self.severity!r}")
        if self.kind not in (
            "runner_offline",
            "runner_online",
            "low_disk",
            "saturation",
            "watchdog",
        ):
            raise ValueError(f"FleetEvent.kind invalid: {self.kind!r}")
        if not self.title:
            raise ValueError("FleetEvent.title must be non-empty")

    def to_dict(self) -> dict[str, object]:
        """Serialize for the JSON API. Omits ``node`` when absent."""
        out: dict[str, object] = {
            "ts": self.ts,
            "severity": self.severity,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
        }
        if self.node is not None:
            out["node"] = self.node
        return out


@dataclass(frozen=True, slots=True)
class NodeSnapshot:
    """The minimal, flat view of a fleet node the classifier reasons about.

    Law of Demeter: callers flatten the nested ``/api/fleet/status`` shape into
    these records once, so the classifier never reaches through nested dicts.
    """

    name: str
    online: bool
    disk_free_gb: float | None = None
    disk_percent: float | None = None
    offline_reason: str | None = None
    offline_detail: str | None = None


def node_disk_is_critical(
    free_gb: float | None,
    percent: float | None,
) -> bool:
    """True iff the disk metric indicates *critical* pressure.

    Pure. Mirrors ``get_disk_pressure_snapshot`` thresholds so the
    offline-due-to-disk classification stays consistent with the rest of the
    dashboard (DRY).
    """
    if free_gb is not None and free_gb <= DISK_MIN_FREE_GB:
        return True
    if percent is not None and percent >= DISK_CRITICAL_PERCENT:
        return True
    return False


def node_disk_is_low(
    free_gb: float | None,
    percent: float | None,
) -> bool:
    """True iff the disk metric crosses the *warning* (low-disk) threshold.

    Pure. A node at or above the warn threshold (but not yet critical) still
    emits a ``low_disk`` warning so the pressure is visible before a drop.
    """
    if node_disk_is_critical(free_gb, percent):
        return True
    if free_gb is not None and free_gb <= DISK_MIN_FREE_GB * 2:
        return True
    if percent is not None and percent >= DISK_WARN_PERCENT:
        return True
    return False


def classify_node_offline_event(snap: NodeSnapshot, ts: int) -> FleetEvent:
    """Build the ``runner_offline`` event for a node that just went offline.

    Disk pressure is the headline reason when the node's disk metric is
    critical OR the upstream offline classifier already flagged disk pressure.
    """
    disk_pressure = node_disk_is_critical(snap.disk_free_gb, snap.disk_percent) or (
        snap.offline_reason == "disk-pressure"
    )

    if disk_pressure:
        free_txt = f"{snap.disk_free_gb:.1f} GB free" if snap.disk_free_gb is not None else "low disk"
        return FleetEvent(
            ts=ts,
            severity="critical",
            kind="runner_offline",
            title=f"{snap.name} offline — disk pressure",
            detail=snap.offline_detail or f"Runner taken offline ({free_txt}).",
            node=snap.name,
        )

    reason = snap.offline_reason or "unknown"
    detail = snap.offline_detail or f"Runner offline (reason: {reason})."
    return FleetEvent(
        ts=ts,
        severity="critical",
        kind="runner_offline",
        title=f"{snap.name} offline — {reason}",
        detail=detail,
        node=snap.name,
    )


def _low_disk_event(snap: NodeSnapshot, ts: int) -> FleetEvent:
    """Build a ``low_disk`` event for a node under disk pressure."""
    critical = node_disk_is_critical(snap.disk_free_gb, snap.disk_percent)
    parts: list[str] = []
    if snap.disk_free_gb is not None:
        parts.append(f"{snap.disk_free_gb:.1f} GB free")
    if snap.disk_percent is not None:
        parts.append(f"{snap.disk_percent:.0f}% used")
    detail = ", ".join(parts) if parts else "disk space low"
    return FleetEvent(
        ts=ts,
        severity="critical" if critical else "warning",
        kind="low_disk",
        title=f"{snap.name} low disk",
        detail=detail,
        node=snap.name,
    )


def classify_fleet_events(
    previous: Iterable[NodeSnapshot] | None,
    current: Iterable[NodeSnapshot],
    *,
    ts: int,
    capacity: int | None = None,
    online_count: int | None = None,
    watchdog_status: str | None = None,
    previous_watchdog_status: str | None = None,
) -> list[FleetEvent]:
    """Diff two fleet snapshots and emit the implied events. Pure.

    Args:
      previous: node snapshots from the prior poll (``None`` on first poll —
        no transitions are emitted, only standing low-disk/saturation/watchdog
        conditions).
      current: node snapshots from the current poll.
      ts: epoch-ms timestamp stamped on every emitted event.
      capacity: total runner/node capacity; with ``online_count`` drives the
        ``saturation`` alarm. Optional.
      online_count: number of busy/active runners; compared against capacity.
      watchdog_status: current WSL-keepalive watchdog status.
      previous_watchdog_status: prior watchdog status (to detect regressions).

    Returns:
      Events in a stable order: offline transitions, online transitions,
      low-disk warnings, saturation, watchdog. Same input → same output.
    """
    events: list[FleetEvent] = []
    prev_by_name: dict[str, NodeSnapshot] = {n.name: n for n in previous} if previous is not None else {}
    curr_list = list(current)

    # 1. Offline transitions (online→offline). First poll emits none.
    for snap in curr_list:
        prev = prev_by_name.get(snap.name)
        was_online = prev.online if prev is not None else True
        if previous is not None and was_online and not snap.online:
            events.append(classify_node_offline_event(snap, ts))

    # 2. Online transitions (offline→online). First poll emits none.
    if previous is not None:
        for snap in curr_list:
            prev = prev_by_name.get(snap.name)
            if prev is not None and not prev.online and snap.online:
                events.append(
                    FleetEvent(
                        ts=ts,
                        severity="info",
                        kind="runner_online",
                        title=f"{snap.name} back online",
                        detail="Runner reconnected to the fleet.",
                        node=snap.name,
                    )
                )

    # 3. Low-disk warnings for any *online* node under pressure. (An offline
    #    node already surfaced its disk reason via the offline event.)
    for snap in curr_list:
        if snap.online and node_disk_is_low(snap.disk_free_gb, snap.disk_percent):
            events.append(_low_disk_event(snap, ts))

    # 4. Saturation: every available runner is busy.
    if capacity is not None and online_count is not None and capacity > 0 and online_count >= capacity:
        events.append(
            FleetEvent(
                ts=ts,
                severity="warning",
                kind="saturation",
                title="Fleet saturated",
                detail=f"All {capacity} runner(s) busy — new jobs will queue.",
            )
        )

    # 5. Watchdog regression (any non-healthy status, newly observed).
    if watchdog_status and watchdog_status != "healthy" and watchdog_status != previous_watchdog_status:
        events.append(
            FleetEvent(
                ts=ts,
                severity="critical" if watchdog_status == "legacy" else "warning",
                kind="watchdog",
                title=f"WSL keepalive: {watchdog_status}",
                detail="WSL keepalive watchdog needs attention.",
            )
        )

    return events


class EventStore:
    """Bounded, thread-safe, append-only ring buffer of fleet events.

    Invariant: ``len(self._events) <= capacity`` at all times. The oldest
    events are evicted first when capacity is exceeded.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError("EventStore capacity must be > 0")
        self._capacity = capacity
        self._events: list[FleetEvent] = []
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def record(self, event: FleetEvent) -> None:
        """Append a single event, evicting the oldest if at capacity."""
        with self._lock:
            self._events.append(event)
            overflow = len(self._events) - self._capacity
            if overflow > 0:
                del self._events[:overflow]

    def record_many(self, events: Iterable[FleetEvent]) -> None:
        """Append several events in order (single lock acquisition)."""
        with self._lock:
            self._events.extend(events)
            overflow = len(self._events) - self._capacity
            if overflow > 0:
                del self._events[:overflow]

    def recent(self, limit: int | None = None) -> list[FleetEvent]:
        """Return the most-recent events, newest first.

        Args:
          limit: cap the number returned; ``None`` returns all retained events.
        """
        with self._lock:
            snapshot = list(reversed(self._events))
        if limit is not None:
            if limit < 0:
                raise ValueError("recent limit must be >= 0")
            return snapshot[:limit]
        return snapshot

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


def nodes_from_fleet_status(
    status: Mapping[str, Mapping[str, object]],
) -> list[NodeSnapshot]:
    """Flatten the nested ``/api/fleet/status`` payload into ``NodeSnapshot``s.

    Law of Demeter boundary: the nested-dict reaching happens here, once, so
    the classifier and store only ever see flat typed records.
    """
    out: list[NodeSnapshot] = []
    for name, data in status.items():
        if not isinstance(data, Mapping):
            continue
        status_str = data.get("status")
        offline_reason = data.get("offline_reason")
        # A node is offline if it self-reports offline OR the hub flagged it.
        online = status_str != "offline" and offline_reason != "disk-pressure"
        # Disk metric: prefer the windows-host disk, fall back to the WSL disk.
        disk = data.get("disk")
        free_gb: float | None = None
        percent: float | None = None
        if isinstance(disk, Mapping):
            host = disk.get("windows_host")
            metric = host if isinstance(host, Mapping) else disk
            raw_free = metric.get("free_gb")
            raw_percent = metric.get("percent")
            if isinstance(raw_free, (int, float)):
                free_gb = float(raw_free)
            if isinstance(raw_percent, (int, float)):
                percent = float(raw_percent)
        out.append(
            NodeSnapshot(
                name=name,
                online=bool(online),
                disk_free_gb=free_gb,
                disk_percent=percent,
                offline_reason=(str(offline_reason) if offline_reason is not None else None),
                offline_detail=(str(data.get("offline_detail")) if data.get("offline_detail") is not None else None),
            )
        )
    return out


# Process-wide singleton store. The poller records into it; the API reads from
# it. A module-level singleton keeps the feed alive across requests without a
# database (ring-buffered, best-effort durability per the issue).
_STORE = EventStore()


def get_event_store() -> EventStore:
    """Return the process-wide event store singleton."""
    return _STORE


@dataclass
class FleetEventPoller:
    """Stateful glue that turns successive fleet snapshots into recorded events.

    Holds the previous snapshot between polls so ``classify_fleet_events`` can
    diff against it. Orthogonal to the HTTP layer: the poller is driven by
    whatever cadence the caller chooses (a background task, or on-demand from a
    fleet-status fetch).
    """

    store: EventStore = field(default_factory=get_event_store)
    _previous: list[NodeSnapshot] | None = field(default=None, init=False)
    _previous_watchdog: str | None = field(default=None, init=False)

    def observe(
        self,
        nodes: Iterable[NodeSnapshot],
        *,
        ts: int,
        capacity: int | None = None,
        online_count: int | None = None,
        watchdog_status: str | None = None,
    ) -> list[FleetEvent]:
        """Record the events implied by this poll and return them.

        Updates the internal previous-snapshot so the next call diffs against
        this one.
        """
        node_list = list(nodes)
        events = classify_fleet_events(
            self._previous,
            node_list,
            ts=ts,
            capacity=capacity,
            online_count=online_count,
            watchdog_status=watchdog_status,
            previous_watchdog_status=self._previous_watchdog,
        )
        if events:
            self.store.record_many(events)
        self._previous = node_list
        self._previous_watchdog = watchdog_status
        return events
