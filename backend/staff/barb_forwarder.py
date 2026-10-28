"""rd-barb per-node forwarder primitive (#3117 slice S3, part of #3064).

Every node (the leader, the warm standby and every plain forwarder) runs this: a lightweight,
model-free primitive that turns a deterministic local signal - host health, an auth problem, or
a stuck lane - into a sealed rd-threads envelope (:mod:`scripts.rd_threads_envelope`) addressed
to rd-barb. "Model-free" means no LLM call anywhere in this module: every function here is pure
or touches only a config file, a clock and an injected callable.

Delivery itself is a separate concern (#3089): :func:`forward` hands the sealed envelope to an
injectable ``Sender`` instead of opening a transport of its own. :func:`build_outbox_sender`
adapts the existing bounded outbox (:mod:`staff.barb_outbox`) as one such ``Sender`` so this
module reuses that primitive's bound/backoff/crash-safety machinery rather than inventing a
second queue (DRY).

Node validity reuses the S1 node-roles config (:mod:`staff.barb_node_leader`) rather than a
second notion of "known node" (DRY, Law of Demeter): :func:`confirm_forwarding_node` is the one
place this module asks "is this a real node", and it fails closed for anything else.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from staff.barb_node_leader import NodeLeaderConfig, load_node_leader_config

from scripts import rd_threads_envelope as envelope

if TYPE_CHECKING:
    from staff.barb_outbox import OutboxStore

log = logging.getLogger("dashboard.staff.barb_forwarder")

FORWARDER_FROM_PREFIX = "forwarder"
DEFAULT_STUCK_LANE_AFTER_SECONDS = 3600.0

_SIGNAL_SUMMARIES: dict[str, str] = {
    "host_health": "host health problem",
    "auth_problem": "authentication problem",
    "stuck_lane": "stuck lane",
}

# readiness.py probe names (backend/readiness.py) that signal an auth problem rather than a
# plain host-health one; everything else not-'ok' is classified as host_health.
_AUTH_PROBE_NAMES = frozenset({"github_token", "gh_cli"})


class UnknownForwarderNodeError(RuntimeError):
    """The forwarder was asked to run on a node absent from the node-roles config; fails closed."""


@dataclass(frozen=True)
class ForwarderSignal:
    """One deterministic local finding worth telling rd-barb about.

    Pre: ``kind`` is one of ``'host_health'``, ``'auth_problem'``, ``'stuck_lane'``; ``detail``
    and ``recommended_action`` are each meant to be one non-empty line (the rd-threads 'alert'
    contract checks this at seal time, in :func:`build_alert`).
    """

    kind: str
    detail: str
    recommended_action: str


class Sender(Protocol):
    """Narrow delivery seam (#3089 is a separate issue): hands a sealed envelope onward."""

    def __call__(self, sealed_envelope: dict[str, Any]) -> None: ...


def confirm_forwarding_node(node: str, config: NodeLeaderConfig | None = None) -> str:
    """Return ``node``'s configured role. Every role forwards (leader, standby, forwarder alike).

    Pre: ``config`` defaults to :func:`load_node_leader_config`'s result when omitted.
    Post: never returns ``None`` - raises :class:`UnknownForwarderNodeError` for any node the
    config does not name, so an unrecognized node can never silently forward.
    """
    cfg = config or load_node_leader_config()
    role = cfg.role_of(node)
    if role is None:
        raise UnknownForwarderNodeError(f"node {node!r} is not in the node-roles config; refusing to forward")
    return role


def build_alert(node: str, signal: ForwarderSignal, *, to: str = "rd-barb") -> dict[str, Any]:
    """Build the rd-threads 'alert' fields for ``signal`` (defaults/sealing happen in :func:`forward`).

    Pre: ``signal.kind`` is a known signal kind.
    Post: once passed through :func:`scripts.rd_threads_envelope.seal`, the result validates clean.
    """
    if signal.kind not in _SIGNAL_SUMMARIES:
        raise ValueError(f"unknown forwarder signal kind {signal.kind!r}; expected one of {sorted(_SIGNAL_SUMMARIES)}")
    return {
        "kind": "alert",
        "from": f"{FORWARDER_FROM_PREFIX}:{node}",
        "to": to,
        "summary": f"{node}: {_SIGNAL_SUMMARIES[signal.kind]}",
        "body": signal.detail,
        "recommended_action": signal.recommended_action,
    }


def forward(
    node: str,
    signal: ForwarderSignal,
    send: Sender,
    *,
    config: NodeLeaderConfig | None = None,
    now: float,
    to: str = "rd-barb",
) -> dict[str, Any]:
    """Confirm ``node`` may forward, seal ``signal`` as an rd-threads alert, hand it to ``send``.

    Pre: ``node`` is a configured node (see :func:`confirm_forwarding_node`); ``now`` is a POSIX
    timestamp (caller-supplied so sealing/dedupe is deterministic under test).
    Post: returns the sealed envelope passed to ``send``. Fails closed - an unknown node or an
    envelope that fails :func:`~scripts.rd_threads_envelope.validate` raises before ``send`` is
    ever called, so nothing half-sent reaches the ``Sender``. Calling twice for the same node and
    signal within the same 60s dedupe bucket seals to the same ``id`` (stable retry, #3072).
    """
    confirm_forwarding_node(node, config)
    sealed = envelope.seal(build_alert(node, signal, to=to), now)
    problems = envelope.validate(sealed)
    if problems:
        raise ValueError(f"forwarder built an invalid rd-threads envelope: {problems}")
    send(sealed)
    log.info("barb_forwarder: forwarded %s signal from %s (%s)", signal.kind, node, sealed["id"])
    return sealed


def signals_from_readyz(node: str, checks: Mapping[str, Any]) -> list[ForwarderSignal]:
    """Reclassify an already-computed ``/readyz`` ``checks`` payload (``backend/readiness.py``)
    into forwarder signals for anything not ``'ok'``.

    This never re-implements a host or auth check: ``readiness.py``'s probes remain the one source
    of truth for what is broken (DRY); this function only repackages their verdict for rd-threads.

    Pre: ``checks`` matches :func:`backend.readiness.aggregate`'s ``checks`` dict shape - each
    value is either a bare status string or ``{"status": ..., "detail": ...}``.
    """
    signals: list[ForwarderSignal] = []
    for name, value in checks.items():
        status, detail = (value, None) if isinstance(value, str) else (value.get("status"), value.get("detail"))
        if status == "ok":
            continue
        kind = "auth_problem" if name in _AUTH_PROBE_NAMES else "host_health"
        signals.append(
            ForwarderSignal(
                kind=kind,
                detail=str(detail) if detail else f"{name} reported {status}",
                recommended_action=f"Investigate {name} on {node} ({status}).",
            )
        )
    return signals


def detect_stuck_lane(
    lane_id: str,
    last_heartbeat_at: float,
    *,
    now: float,
    stale_after_seconds: float = DEFAULT_STUCK_LANE_AFTER_SECONDS,
) -> ForwarderSignal | None:
    """A lane is stuck once its last heartbeat is older than ``stale_after_seconds``; else ``None``.

    Pre: ``lane_id`` is non-empty; ``stale_after_seconds > 0``. A negative age (clock skew, a
    heartbeat in the future) is treated as fresh, never as stuck.
    """
    if not lane_id.strip():
        raise ValueError("lane_id must be non-empty")
    if stale_after_seconds <= 0:
        raise ValueError("stale_after_seconds must be > 0")
    age = now - last_heartbeat_at
    if age < stale_after_seconds:
        return None
    return ForwarderSignal(
        kind="stuck_lane",
        detail=f"lane {lane_id!r} has not heartbeat in {age:.0f}s (threshold {stale_after_seconds:.0f}s)",
        recommended_action=f"Check lane {lane_id!r} for a wedged agent session or stuck claim.",
    )


def build_outbox_sender(outbox: OutboxStore, *, node: str) -> Sender:
    """Adapt the existing bounded outbox as a forwarder :class:`Sender` rather than a new transport.

    Each sealed envelope becomes its own one-shot, non-coalescing delivery keyed by the envelope's
    own content-addressed ``id`` (set 1:1 as the outbox ``run_id``), so re-forwarding the same
    signal within the same 60s dedupe bucket (same sealed ``id``) enqueues only once - the outbox's
    existing idempotent-enqueue behavior, not a second dedupe mechanism.
    """

    def _send(sealed_envelope: dict[str, Any]) -> None:
        outbox.enqueue(
            kind=f"forwarder_{sealed_envelope.get('kind', 'alert')}",
            thread_id=f"{FORWARDER_FROM_PREFIX}:{node}",
            run_id=str(sealed_envelope["id"]),
            version=1,
            payload=sealed_envelope,
            terminal=True,
        )

    return _send
