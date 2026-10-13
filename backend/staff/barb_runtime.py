"""Process-level wiring for the Barb single authority (#1800, BR-06 PR 2).

Resolves this node's role once, builds the replica's outbox producer and delivery transport, runs
the drain loop and reports status. With no ``BARB_AUTHORITY_*`` configured the role is ``UNSET`` and
every function here is inert, so the legacy single-node behaviour is unchanged.

Contract: a REPLICA never commits a conversation write; its only path to the authority is
:func:`enqueue_run_card` -> outbox -> :func:`drain_pass` -> ``AuthorityClient``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

from staff.barb_authority import AuthorityClient, AuthorityConfig, AuthorityRole
from staff.barb_fence import FencedAuthorityError, check_fence
from staff.barb_outbox import OutboxFullError
from staff.barb_outbox_drain import DrainStats, drain_once
from staff.conversations import get_conversation_store
from staff.store import get_store

log = logging.getLogger("dashboard.staff.barb_runtime")

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
DEFAULT_DRAIN_INTERVAL_SECONDS = 5.0
_DELIVERY_TIMEOUT_SECONDS = 10.0

_lock = threading.Lock()
_config: AuthorityConfig | None = None
_enqueue_failures = 0


def local_node_name() -> str:
    from dashboard_config import HOSTNAME  # noqa: PLC0415

    return str(HOSTNAME)


def get_authority_config() -> AuthorityConfig:
    """This node's authority role, resolved once from ``BARB_AUTHORITY_*`` (raises ``ValueError`` if malformed)."""
    global _config  # noqa: PLW0603
    with _lock:
        if _config is None:
            _config = AuthorityConfig.from_env(os.environ, local_node=local_node_name())
        return _config


def reset_authority_config() -> None:
    """Forget the cached role and counters (tests; or after the operator edits the environment)."""
    global _config, _enqueue_failures  # noqa: PLW0603
    with _lock:
        _config = None
        _enqueue_failures = 0


def relay_payload(run: Any, status: str, question: str | None, summary: str | None) -> dict[str, Any]:
    """The run-card event body shared by the legacy relay and the outbox (DRY)."""
    return {
        "run_id": run.id,
        "role": run.role,
        "status": status,
        "node": getattr(run, "machine", "") or "",
        "provider": run.provider,
        "repo": run.repo,
        "target_ref": run.target_ref,
        "branch": run.branch,
        "question": question,
        "summary": summary,
        "error": getattr(run, "error", "") or None,
        "failure_class": getattr(run, "failure_class", "") or None,
    }


def enqueue_run_card(run: Any, status: str, question: str | None = None, summary: str | None = None) -> str | None:
    """Stage a replica's run-card update in the durable outbox with the run's next card version.

    Pre: this node is a REPLICA and the run has a thread. Post: the event is durably queued (returns its
    id) or, when the outbox is full, counted in ``enqueue_failures`` and logged (returns ``None``) - the
    producing run is never failed by back-pressure.
    """
    global _enqueue_failures  # noqa: PLW0603
    thread_id = getattr(run, "thread_id", "") or ""
    if not thread_id:
        raise ValueError("a run card needs a thread_id")
    try:
        version = get_store().next_card_version(run.id)
        return (
            get_conversation_store()
            .barb_outbox()
            .enqueue(
                "run_card",
                thread_id,
                run.id,
                version,
                relay_payload(run, status, question, summary),
                terminal=status in TERMINAL_STATUSES,
            )
        )
    except OutboxFullError as exc:
        with _lock:
            _enqueue_failures += 1
        log.error("barb outbox full: run card %s %s not staged: %s", run.id, status, exc)
        return None


def http_transport(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """Synchronous POST to the authority; network failures surface as ``ConnectionError`` (retryable)."""
    import httpx  # noqa: PLC0415

    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=_DELIVERY_TIMEOUT_SECONDS)
    except httpx.TransportError as exc:
        raise ConnectionError(str(exc)) from exc
    try:
        data = resp.json()
    except ValueError:
        data = {"detail": resp.text[:200]}
    return resp.status_code, data


def get_authority_client() -> AuthorityClient | None:
    """A client for the configured authority, or ``None`` unless this node is a REPLICA."""
    config = get_authority_config()
    if not config.is_replica:
        return None
    from staff import fleet  # noqa: PLC0415

    return AuthorityClient(config, http_transport, fleet.fleet_headers())


def drain_pass(limit: int = 50) -> DrainStats | None:
    """One delivery pass; ``None`` when this node has nothing to deliver (not a replica)."""
    client = get_authority_client()
    if client is None:
        return None
    return drain_once(get_conversation_store().barb_outbox(), client, limit=limit)


async def drain_loop(interval: float = DEFAULT_DRAIN_INTERVAL_SECONDS) -> None:
    """Replica delivery loop (started from the app lifespan). Recovers in-flight entries first."""
    if not get_authority_config().is_replica:
        return
    recovered = get_conversation_store().barb_outbox().recover_in_flight()
    log.info(
        "barb drain loop started (epoch %d, %d in-flight entries recovered)", get_authority_config().epoch, recovered
    )
    while True:
        try:
            await asyncio.to_thread(drain_pass)
        except Exception:  # noqa: BLE001 - the loop must survive any single pass
            log.exception("barb drain pass failed")
        await asyncio.sleep(interval)


def barb_status() -> dict[str, Any]:
    """Role, epoch, outbox depth and degradation for operators and the Console banner."""
    config = get_authority_config()
    out: dict[str, Any] = {
        "role": config.role.value,
        "authority_node": config.node,
        "epoch": config.epoch,
        "local_node": config.local_node,
        "enqueue_failures": _enqueue_failures,
        "outbox": None,
        "degraded": False,
        **_fence_status(config),
    }
    if config.is_replica:
        s = get_conversation_store().barb_outbox().stats()
        out["outbox"] = {
            "entries": s.entries,
            "bytes": s.bytes,
            "dead": s.dead,
            "oldest_created_at": s.oldest_created_at,
        }
        out["degraded"] = s.entries > 0
    out["degraded"] = out["degraded"] or out["fenced"]
    return out


def _fence_status(config: AuthorityConfig) -> dict[str, Any]:
    """The persisted epoch fence and whether this AUTHORITY is behind it (then it refuses every write)."""
    if config.role is AuthorityRole.UNSET:
        return {"fence": None, "fenced": False}
    fence = get_conversation_store().barb_epoch_fence()
    fenced = False
    if config.is_authority:
        try:
            check_fence(fence, config.epoch, config.node)
        except FencedAuthorityError:
            fenced = True
    return {"fence": fence.to_dict() if fence else None, "fenced": fenced}
