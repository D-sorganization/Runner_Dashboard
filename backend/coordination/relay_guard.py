"""Model-free postman relay guard (#3233).

Pre: ``admit`` receives an rd-threads-shaped envelope dict with relay fields ``id`` (non-empty string)
and ``hops`` (int ≥ 0). ``check_delivery`` receives a well-formed :class:`coordination.postman.Delivery`.

Post: on allow, ``admit`` records the envelope id in a bounded seen-set (replay refusal breaks multi-node
cycles). On refuse, no id is recorded and no secret-bearing field is logged. Stale envelopes (past
``expiry``) are copied to a bounded dead-letter list retrievable via :func:`dead_letters`.

Failover / bridge lag (specified here, implemented in later slices): delivery is at-least-once with
envelope-id dedup so the effect is exactly-once; bridge overflow must dead-letter without blocking the
local side.

This module has no dispatch or staff-run imports and cannot start staff runs.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from scripts import rd_threads_envelope as envelope
from staff import redaction

log = logging.getLogger("dashboard.coordination.relay_guard")

EXTRA_RELAY_AGENTS = frozenset({"barb", "rd-barb", "claude-planner", "*"})
DENIED_RECIPIENTS = frozenset({"dieter"})
MAX_HOPS = 3
MAX_SEEN = 5000  # keep in sync with coordination.postman.MAX_DELIVERED
MAX_DEAD_LETTERS = 500
DEFAULT_TTL = timedelta(hours=24)

_SECRET_PATTERNS = tuple(getattr(redaction, name) for name in dir(redaction) if name.endswith("_RE"))
_PASSWORD_ASSIGN_RE = re.compile(r"(?i)\bpassword\s*[:=]\s*\S+")


class RelayRefused(Exception):
    """Relay guard refused a delivery; ``args[0]`` is a stable reason code (never secret material)."""


@dataclass(frozen=True)
class RelayVerdict:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class DeadLetter:
    envelope_id: str
    reason: str


def _parse_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _contains_secret(value: Any) -> bool:
    if isinstance(value, str):
        if not value:
            return False
        if any(p.search(value) for p in _SECRET_PATTERNS):
            return True
        return bool(_PASSWORD_ASSIGN_RE.search(value))
    if isinstance(value, dict):
        return any(_contains_secret(v) for v in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_secret(v) for v in value)
    return False


def _scan_envelope_text(msg: dict[str, Any]) -> bool:
    return _contains_secret({k: msg.get(k) for k in ("summary", "body", "recommended_action", "recommendation_reason")})


class RelayGuard:
    """Pure relay policy engine: allow or refuse; never redact in place."""

    def __init__(
        self,
        roster: Sequence[str],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_seen: int = MAX_SEEN,
        max_dead_letters: int = MAX_DEAD_LETTERS,
        default_ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._allowed_senders = frozenset(roster) | EXTRA_RELAY_AGENTS
        self._allowed_recipients = self._allowed_senders
        self._clock = clock
        self._max_seen = max_seen
        self._max_dead_letters = max_dead_letters
        self._default_ttl = default_ttl
        self._seen: deque[str] = deque()
        self._seen_set: set[str] = set()
        self._dead: deque[DeadLetter] = deque()

    def dead_letters(self) -> tuple[DeadLetter, ...]:
        return tuple(self._dead)

    def _remember_seen(self, envelope_id: str) -> None:
        if envelope_id in self._seen_set:
            return
        self._seen.append(envelope_id)
        self._seen_set.add(envelope_id)
        while len(self._seen) > self._max_seen:
            old = self._seen.popleft()
            self._seen_set.discard(old)

    def _dead_letter(self, envelope_id: str, reason: str) -> None:
        self._dead.append(DeadLetter(envelope_id, reason))
        while len(self._dead) > self._max_dead_letters:
            self._dead.popleft()

    def _is_stale(self, msg: dict[str, Any], now: datetime) -> bool:
        raw = msg.get("expiry")
        if raw is None:
            return False
        expires = _parse_time(raw)
        if expires is None:
            return False
        return now >= expires

    def _allowlist(self, msg: dict[str, Any]) -> RelayVerdict | None:
        sender = str(msg.get("from", "")).strip()
        recipient = str(msg.get("to", "")).strip()
        if recipient in DENIED_RECIPIENTS:
            return RelayVerdict(False, "dieter_recipient")
        if sender not in self._allowed_senders:
            return RelayVerdict(False, "unknown_sender")
        if recipient not in self._allowed_recipients:
            return RelayVerdict(False, "unknown_recipient")
        return None

    def admit(self, msg: dict[str, Any]) -> RelayVerdict:
        """Evaluate a relay envelope. Post: allowed envelopes are recorded by id."""
        problems = envelope.validate(msg)
        if problems:
            return RelayVerdict(False, "schema_invalid")
        envelope_id = str(msg.get("id", "")).strip()
        if not envelope_id:
            return RelayVerdict(False, "schema_invalid")
        hops = msg.get("hops", 0)
        if not isinstance(hops, int) or isinstance(hops, bool) or hops < 0:
            return RelayVerdict(False, "schema_invalid")
        if hops > MAX_HOPS:
            return RelayVerdict(False, "hop_limit")
        now = self._clock()
        if self._is_stale(msg, now):
            self._dead_letter(envelope_id, "stale")
            return RelayVerdict(False, "stale")
        if envelope_id in self._seen_set:
            return RelayVerdict(False, "replay")
        if _scan_envelope_text(msg):
            return RelayVerdict(False, "secret")
        blocked = self._allowlist(msg)
        if blocked is not None:
            return blocked
        self._remember_seen(envelope_id)
        return RelayVerdict(True, "")

    def check_delivery(self, delivery: object) -> RelayVerdict:
        """Board bus deliveries: secret refusal and explicit dieter block only."""
        to = str(getattr(delivery, "to", "")).strip()
        body = getattr(delivery, "body", "")
        if to in DENIED_RECIPIENTS:
            return RelayVerdict(False, "dieter_recipient")
        if _contains_secret(body):
            return RelayVerdict(False, "secret")
        return RelayVerdict(True, "")
