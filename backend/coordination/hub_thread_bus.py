"""Hub-thread ``BusPort`` (#3235): write guarded rd-threads envelopes into staff threads.

``StaffThreadHub`` resolves ``rd-threads:{role}`` threads in the conversation store and publishes
through ``staff.thread_bus``; ``HubThreadBus`` implements ``BusPort`` for ``coordination.postman``
with relay-guard checks, bounded retries, and dead-letter on hard failure. No dispatch, no
staff-run, no model calls.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from coordination.postman import Delivery
from coordination.relay_guard import RelayGuard, RelayRejected
from scripts import rd_threads_envelope as envelope
from staff.conversations import ConversationStore
from staff.thread_bus import ThreadEventBus

log = logging.getLogger("dashboard.coordination.hub_thread_bus")

POSTMAN_ROLE = "postman"
BOARD_TITLE = "rd-threads:fleet-board"
DEFAULT_RETRY_DELAYS = (0.1, 0.3, 0.9)
_KIND_MAP = {"message": "update", "alert": "alert", "rejection": "alert"}


class ThreadHub(Protocol):
    def thread_for(self, to: str) -> str: ...

    def write_message(self, thread_id: str, sealed: dict[str, Any], idempotency_key: str) -> str: ...


def _summary_from(body: str) -> str:
    line = body.strip().split("\n", 1)[0]
    return (line[:200] if line else "postman delivery").replace("\r", "")


def envelope_from_delivery(delivery: Delivery, *, now: datetime) -> dict[str, Any]:
    """Map a board ``Delivery`` to an rd-threads envelope dict (before :func:`envelope.seal`)."""
    kind = _KIND_MAP.get(delivery.kind, "update")
    return {
        "kind": kind,
        "from": POSTMAN_ROLE,
        "to": delivery.to,
        "summary": _summary_from(delivery.body),
        "body": delivery.body,
        "hops": 0,
    }


class StaffThreadHub:
    """In-process hub threads: SQLite persistence plus ``ThreadEventBus`` fan-out."""

    def __init__(self, store: ConversationStore, bus: ThreadEventBus) -> None:
        self._store = store
        self._bus = bus
        self._by_title: dict[str, str] = {}

    def thread_for(self, to: str) -> str:
        role = "barb" if to in ("barb", "rd-barb") else to
        title = BOARD_TITLE if to == "*" else f"rd-threads:{role}"
        cached = self._by_title.get(title)
        if cached is not None:
            return cached
        for rec in self._store.list_threads(limit=250):
            if rec.title == title:
                self._by_title[title] = rec.id
                return rec.id
        if to == "*":
            rec = self._store.create_thread(title=title, kind="group", role="auto", created_by=POSTMAN_ROLE)
        else:
            rec = self._store.create_thread(title=title, kind="direct", role=role, created_by=POSTMAN_ROLE)
        self._by_title[title] = rec.id
        return rec.id

    def write_message(self, thread_id: str, sealed: dict[str, Any], idempotency_key: str) -> str:
        text = f"[{sealed['kind']}] {sealed['summary']}\n\n{sealed.get('body', '')}".rstrip()
        saved = self._store.add_message(
            thread_id,
            author_kind="system",
            author=POSTMAN_ROLE,
            kind="text",
            body_md=text,
            meta={"rd_threads": sealed},
            idempotency_key=idempotency_key,
        )
        self._bus.publish_message_sync(thread_id, saved.to_dict())
        return saved.id


class HubThreadBus:
    """``BusPort`` over hub threads: guarded envelopes, at-least-once with id dedup on the hub."""

    def __init__(
        self,
        hub: ThreadHub,
        guard: RelayGuard,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
    ) -> None:
        self._hub = hub
        self.guard = guard
        self._clock = clock
        self._sleep = sleep
        self._retry_delays = retry_delays
        self._acked: set[tuple[str, str]] = set()

    def send(self, delivery: Delivery) -> str:
        sealed = envelope.seal(envelope_from_delivery(delivery, now=self._clock()), self._clock().timestamp())
        if problems := envelope.validate(sealed):
            raise ValueError(f"invalid rd-threads envelope: {problems}")
        try:
            self.guard.inspect_outbound(sealed)
        except RelayRejected:
            raise
        envelope_id = str(sealed["id"])
        thread_id = self._hub.thread_for(delivery.to)
        last_exc: Exception | None = None
        for delay in (*self._retry_delays, None):
            try:
                message_id = self._hub.write_message(thread_id, sealed, delivery.idempotency_key)
                self.guard.note_delivered(envelope_id)
                return message_id
            except Exception as exc:  # noqa: BLE001 — retry transient hub/store failures
                last_exc = exc
                if delay is None:
                    self.guard.record_transport_failure(envelope_id, str(exc))
                    raise
                self._sleep(delay)
        raise last_exc  # pragma: no cover

    def ack(self, to: str, message_id: str) -> None:
        key = (to, message_id)
        if key in self._acked:
            return
        self._acked.add(key)


def build_hub_thread_bus(
    store: ConversationStore | None = None,
    bus: ThreadEventBus | None = None,
    *,
    roster: frozenset[str],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> HubThreadBus:
    """Wire the production hub port (store + singleton thread bus + relay guard)."""
    from staff.conversations import get_conversation_store
    from staff.thread_bus import get_thread_bus

    conv = store or get_conversation_store()
    thread_bus = bus or get_thread_bus()
    guard = RelayGuard(roster, clock=clock)
    return HubThreadBus(StaffThreadHub(conv, thread_bus), guard, clock=clock)
