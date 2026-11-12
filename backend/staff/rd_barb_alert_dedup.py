"""rd-barb cross-node alert dedup + status surfacing (#3117 slice S4, part of #3064).

S3's forwarder (:mod:`staff.barb_forwarder`) seals one rd-threads envelope per node per signal;
:func:`scripts.rd_threads_envelope.seal`'s dedupe key already makes retries from the *same* node
idempotent (#3072), but that key includes ``from`` (``forwarder:<node>``), so the same underlying
condition reported independently by two different nodes (for example every node's ``/readyz``
failing the same shared ``github_token`` probe) still seals to two distinct envelope ids. This
module adds the missing cross-node layer:

- :func:`cross_node_alert_key` derives a key from only ``kind`` and a normalized ``body``,
  deliberately excluding ``from``/``summary`` (which carry the reporting node's identity).
- :class:`AlertDeduplicator` debounces envelopes sharing that key within a time window, mirroring
  :mod:`staff.liveness`'s ``notify_dead`` debounce (DRY: same "seen within a window, skip" shape,
  not a second mechanism).
- :func:`surface_alert` records survivors as a :class:`~fleet_events.FleetEvent` through the
  existing event store / ``GET /api/events`` pipe (:mod:`fleet_events`), rather than a new alerts
  surface.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from fleet_events import EventKind, EventStore, FleetEvent, get_event_store
from staff.barb_forwarder import FORWARDER_FROM_PREFIX

DEFAULT_DEDUP_WINDOW_SECONDS = 300.0
ALERT_EVENT_KIND: Final[EventKind] = "node_forwarder_alert"

_DIGITS_RE = re.compile(r"\d+")
_WHITESPACE_RE = re.compile(r"\s+")


def cross_node_alert_key(envelope: Mapping[str, Any]) -> str:
    """Node-independent dedup key for a forwarded alert envelope.

    Pre: ``envelope`` has ``kind`` and ``body`` fields (a sealed rd-threads 'alert' envelope, see
    :func:`staff.barb_forwarder.build_alert`, satisfies this).
    Post: two envelopes with the same ``kind`` and the same body text - ignoring case, whitespace
    and digit runs, so a stuck-lane age reported in seconds doesn't defeat the match - return the
    same key regardless of which node forwarded them.
    """
    kind = str(envelope.get("kind", ""))
    body = _normalize(str(envelope.get("body", "")))
    digest = hashlib.sha256(f"{kind}|{body}".encode()).hexdigest()[:24]
    return f"d_{digest}"


def _normalize(text: str) -> str:
    collapsed = _WHITESPACE_RE.sub(" ", text.strip().lower())
    return _DIGITS_RE.sub("#", collapsed)


@dataclass
class AlertDeduplicator:
    """Debounces alert envelopes sharing a :func:`cross_node_alert_key` within a time window.

    Mirrors :func:`staff.liveness.notify_dead`'s ``{key: last_seen}`` debounce shape so this
    module doesn't invent a second one.
    """

    window_seconds: float = DEFAULT_DEDUP_WINDOW_SECONDS
    _last_seen: dict[str, float] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def should_surface(self, envelope: Mapping[str, Any], *, now: float | None = None) -> bool:
        """True the first time a key is seen, and again once ``window_seconds`` has elapsed."""
        clock = time.time() if now is None else now
        key = cross_node_alert_key(envelope)
        with self._lock:
            last = self._last_seen.get(key)
            if last is not None and clock - last < self.window_seconds:
                return False
            self._last_seen[key] = clock
            return True

    def reset(self) -> None:
        """Clear all debounce state (test/ops hook, mirrors ``reset_dead_notifications``)."""
        with self._lock:
            self._last_seen.clear()


def surface_alert(
    envelope: Mapping[str, Any],
    dedup: AlertDeduplicator,
    *,
    now: float | None = None,
    store: EventStore | None = None,
) -> FleetEvent | None:
    """Record ``envelope`` as a :class:`FleetEvent`, deduped across nodes.

    Pre: ``envelope`` is a sealed rd-threads 'alert' envelope.
    Post: returns the recorded event, or ``None`` when :class:`AlertDeduplicator` suppressed it
    as a duplicate within the window - nothing is recorded in that case.
    """
    clock = time.time() if now is None else now
    if not dedup.should_surface(envelope, now=clock):
        return None
    event = FleetEvent(
        ts=int(clock * 1000),
        severity="warning",
        kind=ALERT_EVENT_KIND,
        title=str(envelope.get("summary", "forwarder alert")),
        detail=str(envelope.get("body", "")),
        node=_node_from(str(envelope.get("from", ""))),
    )
    target_store = store if store is not None else get_event_store()
    target_store.record(event)
    return event


def _node_from(from_field: str) -> str | None:
    prefix = f"{FORWARDER_FROM_PREFIX}:"
    if not from_field.startswith(prefix):
        return None
    return from_field[len(prefix) :] or None
