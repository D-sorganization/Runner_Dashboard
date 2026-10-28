"""Relay guard (issue #3233, slice 1 of #3226): the one gate every postman delivery and future
cross-node relay envelope must pass through before it is sent or forwarded.

Pure and model-free: no network call, no dispatch, no staff-run import — the import-boundary
test in ``tests/api/test_relay_guard.py`` enforces that. It reuses ``staff.redaction`` for secret
detection and ``scripts.rd_threads_envelope`` for the envelope schema and dedupe-key shape; it adds
no parallel schema of its own.

DbC: :meth:`RelayGuard.admit` either returns ``envelope`` unchanged (post: content untouched, never
logged) or raises :class:`RelayRejected` (post: the envelope was not delivered). A stale or
over-hop-limit envelope is also recorded in :meth:`dead_letters`, bounded, so a backlog is visible.
``admit`` never mutates its argument and never writes to disk or the network.

Spec only, not yet wired to a transport (future slices #3235/#3237 build the relay port and run it
under the #3117 leader lease): failover-window delivery is at-least-once, and id dedup on top of
that makes the externally visible effect exactly once. Bridge lag — the delay while a node is
reconnecting — is absorbed by a bounded buffer on the sending side; it must never block the local
side, and overflow is dropped to the dead-letter queue rather than growing without bound.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from staff.redaction import redact_sensitive_content

from scripts import rd_threads_envelope as envelope

MAX_HOPS = 3
MAX_SEEN = 5000  # same bound as postman.MAX_DELIVERED
MAX_DEAD_LETTERS = 500
DIETER = "dieter"
RELAY_RECIPIENTS = frozenset({"barb", "rd-barb", "claude-planner", "*"})


class RelayRejected(ValueError):
    """An envelope failed a relay-guard precondition; it must never be delivered or forwarded."""


@dataclass(frozen=True)
class DeadLetter:
    """A dropped envelope, recorded (never its secrets) so a backlog is visible."""

    envelope_id: str
    reason: str
    at: str


def _parse_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class _BoundedSeen:
    """An insertion-ordered id set capped at ``maxlen``; the oldest id is evicted first."""

    def __init__(self, maxlen: int) -> None:
        self._ids: dict[str, None] = {}
        self._maxlen = maxlen

    def __contains__(self, item: str) -> bool:
        return item in self._ids

    def add(self, item: str) -> None:
        self._ids[item] = None
        if len(self._ids) > self._maxlen:
            self._ids.pop(next(iter(self._ids)))


class RelayGuard:
    """Pre: ``roster`` is the live agent roster. Every ``admit`` call is independent; the guard
    carries only its seen-id window and dead-letter list between calls.
    """

    def __init__(
        self,
        roster: frozenset[str] | Any,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.roster = frozenset(roster)
        self._clock = clock
        self._seen = _BoundedSeen(MAX_SEEN)
        self._dead_letters: deque[DeadLetter] = deque(maxlen=MAX_DEAD_LETTERS)

    def admit(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Run every check; return ``msg`` unchanged, or raise :class:`RelayRejected`.

        Post: a passing envelope's id is recorded so a replay (including one arriving back around a
        multi-node cycle, which a per-edge hop count alone would miss) is refused next time.
        """
        envelope_id = self._envelope_id(msg)
        self._refuse_secrets(msg)
        self._check_recipient(msg)
        self._check_stale(msg, envelope_id)
        self._check_hops(msg, envelope_id)
        self._check_replay(msg, envelope_id)
        self._seen.add(envelope_id)
        return msg

    def dead_letters(self) -> list[DeadLetter]:
        """The bounded, most-recent-first backlog of dropped envelopes. Never holds message content."""
        return list(self._dead_letters)

    def refuse_secrets(self, text: str) -> None:
        """Raise :class:`RelayRejected` if ``text`` holds a secret. Usable standalone (for example
        by ``Postman._deliver``, which sends bare bus bodies, not full relay envelopes).
        """
        if isinstance(text, str) and redact_sensitive_content(text) != text:
            raise RelayRejected("text holds a secret and is refused, not redacted or logged")

    def inspect_outbound(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Pre-send checks for postman→thread delivery; does not record replay state (#3235)."""
        envelope_id = self._envelope_id(msg)
        self._refuse_secrets(msg)
        self._check_recipient(msg)
        self._check_stale(msg, envelope_id)
        self._check_hops(msg, envelope_id)
        return msg

    def note_delivered(self, envelope_id: str) -> None:
        """Record a successfully written envelope id so a later relay replay is refused."""
        if envelope_id.strip():
            self._seen.add(envelope_id)

    def record_transport_failure(self, envelope_id: str, reason: str) -> None:
        """After bounded retries are exhausted, record a hard failure (never message content)."""
        self._dead_letters.append(DeadLetter(envelope_id, reason, self._clock().isoformat()))

    def _envelope_id(self, msg: dict[str, Any]) -> str:
        msg_id = msg.get("id")
        if isinstance(msg_id, str) and msg_id.strip():
            return msg_id
        return envelope.dedupe_key(msg, self._clock().timestamp())

    def _refuse_secrets(self, msg: dict[str, Any]) -> None:
        for field in ("body", "summary"):
            value = msg.get(field)
            if isinstance(value, str):
                self.refuse_secrets(value)

    def _check_recipient(self, msg: dict[str, Any]) -> None:
        to = str(msg.get("to", ""))
        if to == DIETER:
            raise RelayRejected("dieter is not a relay recipient; reach Dieter only via rd-barb's webhook")
        if to not in self.roster and to not in RELAY_RECIPIENTS:
            raise RelayRejected(f"unknown relay recipient {to!r}")

    def _check_hops(self, msg: dict[str, Any], envelope_id: str) -> None:
        hops = int(msg.get("hops") or 0)
        if hops > MAX_HOPS:
            self._drop(envelope_id, f"hop limit exceeded ({hops} > {MAX_HOPS})")

    def _check_replay(self, msg: dict[str, Any], envelope_id: str) -> None:
        if envelope_id in self._seen:
            raise RelayRejected(f"envelope {envelope_id!r} already relayed (replay or cycle)")

    def _check_stale(self, msg: dict[str, Any], envelope_id: str) -> None:
        expiry = _parse_time(msg.get("expiry")) if msg.get("expiry") else None
        if expiry is not None and self._clock() > expiry:
            self._drop(envelope_id, f"expired at {expiry.isoformat()}")

    def _drop(self, envelope_id: str, reason: str) -> None:
        self._dead_letters.append(DeadLetter(envelope_id, reason, self._clock().isoformat()))
        raise RelayRejected(reason)
