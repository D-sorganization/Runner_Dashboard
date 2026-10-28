"""Barbara ``barb-inbox`` store-and-forward bridge (#3236, slice 4 of #3226).

Imports one fenced rd-threads envelope per exchange-repo issue into hub threads and exports
relay traffic addressed to Claude Duet / ``claude-planner`` back to Barbara's exchange repo.
Reuses :mod:`scripts.claude_barb_channel` for render, parse and the channel kill switch; never
addresses ``dieter`` (Dieter-bound traffic is rerouted to ``rd-barb``). Bridge lag uses a bounded
outbound buffer with dead-letter overflow and GitHub rate-limit backoff without blocking local
hub delivery.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from coordination.hub_thread_bus import ThreadHub
from coordination.relay_guard import RelayGuard, RelayRejected

from scripts import claude_barb_channel as channel

log = logging.getLogger("dashboard.coordination.barb_inbox_bridge")

DUET_RECIPIENTS = frozenset({channel.SENDER, "claude"})
DEFAULT_BUFFER_SIZE = 32


class ExchangeRateLimitError(RuntimeError):
    """GitHub secondary rate limit; the bridge must buffer and backoff."""


class ExchangeClient(Protocol):
    def list_inbox_issues(self) -> Sequence[Mapping[str, Any]]: ...

    def create_issue(self, repo: str, issue: Mapping[str, Any]) -> str: ...


@dataclass(frozen=True)
class BridgeDeadLetter:
    envelope_id: str
    reason: str


@dataclass
class ImportReport:
    status: str
    imported: int = 0
    refused: int = 0
    skipped: int = 0


@dataclass
class ExportResult:
    ok: bool
    status: str
    routed_to: str | None = None
    error: str | None = None


@dataclass
class FlushReport:
    sent: int = 0
    remaining: int = 0
    dead_letters: int = 0


class OutboundBuffer:
    """Bounded FIFO for export lag; overflow is reported to the bridge for dead-letter."""

    def __init__(self, max_size: int) -> None:
        if max_size < 1:
            raise ValueError("buffer max_size must be >= 1")
        self._max = max_size
        self._items: deque[dict[str, Any]] = deque()

    def enqueue(self, sealed: dict[str, Any]) -> bool:
        if len(self._items) >= self._max:
            return False
        self._items.append(sealed)
        return True

    def pop_left(self) -> dict[str, Any] | None:
        if not self._items:
            return None
        return self._items.popleft()

    def drain(self) -> list[dict[str, Any]]:
        items = list(self._items)
        self._items.clear()
        return items

    def __len__(self) -> int:
        return len(self._items)


@dataclass
class BarbInboxBridge:
    """Store-and-forward between the exchange repo and hub threads."""

    hub: ThreadHub
    guard: RelayGuard
    client: ExchangeClient
    repo: str
    env: Mapping[str, str]
    buffer: OutboundBuffer = field(default_factory=lambda: OutboundBuffer(DEFAULT_BUFFER_SIZE))
    sleep: Callable[[float], None] = lambda _s: None
    _imported_issues: set[int] = field(default_factory=set)
    _dead_letters: list[BridgeDeadLetter] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        hub: ThreadHub,
        guard: RelayGuard,
        client: ExchangeClient,
        *,
        repo: str,
        env: Mapping[str, str] | None = None,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        sleep: Callable[[float], None] | None = None,
    ) -> BarbInboxBridge:
        return cls(
            hub,
            guard,
            client,
            repo.strip(),
            dict(env or {}),
            OutboundBuffer(buffer_size),
            sleep or (lambda _s: None),
        )

    @property
    def live(self) -> bool:
        return channel.channel_live(self.env)

    def dead_letters(self) -> list[BridgeDeadLetter]:
        return list(self._dead_letters)

    def import_inbox(self) -> ImportReport:
        if not self.live:
            return ImportReport(status="dry_run")
        report = ImportReport(status="ok")
        for raw in self.client.list_inbox_issues():
            number = int(raw["number"])
            if number in self._imported_issues:
                report.skipped += 1
                continue
            sealed, problems = channel.parse_exchange_body(str(raw.get("body") or ""))
            if sealed is None:
                report.refused += 1
                log.warning("barb-inbox import refused issue #%s: %s", number, "; ".join(problems))
                continue
            try:
                self.guard.inspect_outbound(sealed)
            except RelayRejected as exc:
                report.refused += 1
                log.warning("barb-inbox import guard refused issue #%s: %s", number, exc)
                continue
            thread_id = self.hub.thread_for(str(sealed.get("to") or "barb"))
            self.hub.write_message(thread_id, sealed, str(sealed["id"]))
            self._imported_issues.add(number)
            report.imported += 1
        return report

    def export_envelope(self, sealed: Mapping[str, Any]) -> ExportResult:
        payload = dict(sealed)
        routed_to: str | None = None
        if payload.get("to") == "dieter":
            payload["to"] = "rd-barb"
            routed_to = "rd-barb"
        to = str(payload.get("to") or "")
        if to not in DUET_RECIPIENTS and to != "rd-barb":
            return ExportResult(ok=False, status="rejected", error="invalid_recipient")
        try:
            self.guard.inspect_outbound(payload)
        except RelayRejected as exc:
            return ExportResult(ok=False, status="rejected", error=str(exc))
        issue = channel.to_exchange_issue(payload)
        if not self.live:
            return ExportResult(ok=True, status="dry_run", routed_to=routed_to)
        return self._deliver_or_buffer(payload, issue, routed_to)

    def flush_outbound(self) -> FlushReport:
        if not self.live:
            return FlushReport()
        report = FlushReport()
        while True:
            sealed = self.buffer.pop_left()
            if sealed is None:
                break
            try:
                self.client.create_issue(self.repo, channel.to_exchange_issue(sealed))
            except ExchangeRateLimitError:
                self.buffer.enqueue(sealed)
                report.remaining = len(self.buffer)
                break
            report.sent += 1
        report.remaining = len(self.buffer)
        return report

    def _deliver_or_buffer(
        self,
        sealed: Mapping[str, Any],
        issue: Mapping[str, Any],
        routed_to: str | None,
    ) -> ExportResult:
        try:
            ref = self.client.create_issue(self.repo, issue)
        except ExchangeRateLimitError:
            if self.buffer.enqueue(dict(sealed)):
                return ExportResult(ok=True, status="buffered", routed_to=routed_to)
            self._record_dead_letter(str(sealed.get("id") or ""), "outbound buffer overflow")
            return ExportResult(ok=True, status="dead_letter", routed_to=routed_to)
        log.info("barb-inbox exported %s -> %s", sealed.get("id"), ref)
        return ExportResult(ok=True, status="sent", routed_to=routed_to)

    def _record_dead_letter(self, envelope_id: str, reason: str) -> None:
        self._dead_letters.append(BridgeDeadLetter(envelope_id or "unknown", reason))
        self.guard.record_transport_failure(envelope_id or "unknown", reason)
