"""Postman relay slice 5 (#3237, part of #3226): bind relay to #3073 leader lease in #3117 topology."""

from __future__ import annotations

import datetime as _dt_mod
import importlib
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from coordination.postman import BoardPort, BoardSource, BusPort, CycleReport, Delivery, Postman, PostmanState
from coordination.relay_guard import RelayGuard
from staff.barb_continuity import DEFAULT_LEASE_TTL_SECONDS, BarbContinuityStore
from staff.barb_lease_fence import BarbClaim, StaleBarbLease
from staff.barb_node_leader import NodeLeaderConfig, ensure_relay_lease

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017


def _rd_barb_postman() -> Any:
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("scripts.rd_barb_postman")


def select_provider_adapter(provider: str, *, env: Mapping[str, str] | None = None, **kwargs: Any) -> Any:
    """Select the provider delivery adapter from slice 2's capability probe (#3234 / #3073)."""
    return _rd_barb_postman().detect_provider_caps(provider, env=env, **kwargs)


def classify_relay_wake(msg: Mapping[str, Any], *, target_agent: str) -> str:
    """Delegate @mention wake classification to #3073 (:func:`scripts.rd_barb_postman.classify_wake`)."""
    return str(_rd_barb_postman().classify_wake(msg, target_agent=target_agent))


class FencedBusPort:
    """``BusPort`` wrapper enforcing the #3073/#3117 epoch fence on every relay delivery and ack."""

    def __init__(
        self,
        inner: BusPort,
        continuity: BarbContinuityStore,
        claim: BarbClaim,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        after_send_hook: Callable[[int], None] | None = None,
    ) -> None:
        self._inner, self._continuity, self._claim = inner, continuity, claim
        self._clock, self._after_send_hook = clock, after_send_hook
        self.sent_count = 0
        self.woken_recipients: list[str] = []

    def send(self, delivery: Delivery) -> str:
        with self._continuity.fence(self._claim, now=self._clock()):
            msg_id = self._inner.send(delivery)
        self.sent_count += 1
        msg = {"to": delivery.to, "summary": delivery.body, "body": delivery.body}
        if classify_relay_wake(msg, target_agent=delivery.to) == "wake" and delivery.to not in self.woken_recipients:
            self.woken_recipients.append(delivery.to)
        if self._after_send_hook is not None:
            self._after_send_hook(self.sent_count)
        return msg_id

    def ack(self, to: str, message_id: str) -> None:
        with self._continuity.fence(self._claim, now=self._clock()):
            self._inner.ack(to, message_id)


@dataclass(frozen=True)
class TopologyStepReport:
    node: str
    role: str
    active: bool
    promoted: bool
    fenced_mid_batch: bool
    model_calls: int
    woken_recipients: tuple[str, ...]
    cycle_report: CycleReport | None = None


class _AbortOnFencePostman(Postman):
    """Postman subclass that re-raises :class:`StaleBarbLease` immediately to halt mid-batch on failover."""

    @staticmethod
    def _apply(actions: list[Any], action: Any, run: Callable[[], None]) -> None:
        try:
            run()
        except StaleBarbLease:
            raise
        except Exception as exc:  # noqa: BLE001
            err = exc

            def _fail() -> None:
                raise err

            Postman._apply(actions, action, _fail)


class TopologyRelayNode:
    """Bind one topology node (leader, standby, or forwarder) to the #3073 relay lease."""

    def __init__(
        self,
        *,
        node: str,
        config: NodeLeaderConfig,
        continuity: BarbContinuityStore,
        board: BoardPort,
        bus: BusPort,
        source: BoardSource,
        roster: Sequence[str],
        state: PostmanState,
        guard: RelayGuard | None = None,
        role: str = "rd-barb",
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        model_call_probe: Callable[[], None] | None = None,
        after_send_hook: Callable[[int], None] | None = None,
    ) -> None:
        self.node, self.config, self.continuity = node, config, continuity
        self.board, self.bus, self.source = board, bus, source
        self.roster, self.state, self.guard = tuple(roster), state, guard
        self.role, self.ttl_seconds, self.clock = role, ttl_seconds, clock
        self._model_call_probe, self._after_send_hook = model_call_probe, after_send_hook

    def step(self, *, dry_run: bool = False) -> TopologyStepReport:
        """Execute one topology-aware relay step; standby and forwarders make zero model calls."""
        if self.config.role_of(self.node) == "forwarder":
            return TopologyStepReport(self.node, "forwarder", False, False, False, 0, ())
        status = ensure_relay_lease(
            self.continuity, self.config, node=self.node, role=self.role, ttl_seconds=self.ttl_seconds, now=self.clock()
        )
        role_str, promoted = str(status["role"]), bool(status["promoted"])
        if not status["active"] or status["claim"] is None:
            return TopologyStepReport(self.node, role_str, False, False, False, 0, ())
        fenced_bus = FencedBusPort(
            self.bus, self.continuity, status["claim"], clock=self.clock, after_send_hook=self._after_send_hook
        )
        pm = _AbortOnFencePostman(
            self.board, fenced_bus, self.source, self.roster, self.state, clock=self.clock, guard=self.guard
        )
        try:
            report = pm.cycle(dry_run=dry_run)
        except StaleBarbLease:
            return TopologyStepReport(self.node, role_str, True, promoted, True, 0, tuple(fenced_bus.woken_recipients))
        return TopologyStepReport(
            self.node, role_str, True, promoted, False, 0, tuple(fenced_bus.woken_recipients), report
        )
