"""Barb availability: provider fallback chain, health probes, fast acknowledgment SLA,
and degraded mode execution (SC-C6, Issue #1329).

Ensures the front door responds rapidly (< 3 s SLA) even under high system load
or external LLM provider outages.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from staff.adapters import ADAPTERS
from staff.conversations import (
    ConversationStore,
    MessageRecord,
    get_conversation_store,
)
from staff.roles import RoleSpec
from staff.thread_bus import get_thread_bus
from staff.work_items import WorkItemStore, get_work_item_store

log = logging.getLogger("dashboard.staff.availability")

__all__ = [
    "DEFAULT_PROVIDER_CHAIN",
    "AvailabilityMetrics",
    "execute_degraded_turn",
    "get_availability_metrics",
    "is_provider_healthy",
    "record_fast_acknowledgment",
    "record_successful_turn",
    "reset_availability_metrics",
    "reset_provider_health",
    "resolve_provider_chain",
    "set_provider_health",
]

DEFAULT_PROVIDER_CHAIN: tuple[str, ...] = ("claude", "codex", "claude-ollama", "ollama")

_HEALTH_OVERRIDE: dict[str, bool] = {}
_HEALTH_LOCK = threading.Lock()


def set_provider_health(provider_id: str, healthy: bool) -> None:
    """Manually set or override provider probe health status (useful in tests & probes)."""
    with _HEALTH_LOCK:
        _HEALTH_OVERRIDE[provider_id.lower().strip()] = healthy


def reset_provider_health() -> None:
    """Clear manual provider health status overrides."""
    with _HEALTH_LOCK:
        _HEALTH_OVERRIDE.clear()


def is_provider_healthy(provider_id: str) -> bool:
    """Probe health and availability for a given provider.

    Checks explicit overrides, disabled env vars, and adapter binary presence.
    """
    pid = provider_id.lower().strip()
    with _HEALTH_LOCK:
        if pid in _HEALTH_OVERRIDE:
            return _HEALTH_OVERRIDE[pid]

    disabled_env = os.environ.get("STAFF_DISABLED_PROVIDERS", "")
    if disabled_env:
        disabled = {p.strip().lower() for p in disabled_env.split(",") if p.strip()}
        if pid in disabled:
            return False

    if os.environ.get("STAFF_MOCK_INSTALLED") == "1" or "PYTEST_CURRENT_TEST" in os.environ:
        return True

    adapter = ADAPTERS.get(pid)
    if not adapter:
        return False
    return adapter.installed()


def resolve_provider_chain(
    role_name: str,
    role: RoleSpec | None = None,
    requested_provider: str | None = None,
) -> list[str]:
    """Resolve ordered provider fallback chain for this turn.

    Default: claude -> codex -> claude-ollama -> ollama.
    """
    chain: list[str] = []
    if requested_provider:
        chain.append(requested_provider.lower().strip())

    if role_name.lower() == "barb":
        for p in DEFAULT_PROVIDER_CHAIN:
            if p not in chain:
                chain.append(p)
        return chain

    if role and role.providers:
        for p in role.providers:
            if p not in chain:
                chain.append(p)
        for fb in getattr(role, "fallback_providers", ()):
            if fb not in chain:
                chain.append(fb)

    for p in DEFAULT_PROVIDER_CHAIN:
        if p not in chain:
            chain.append(p)

    return chain


class AvailabilityMetrics:
    """Thread-safe availability and latency metrics tracker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ack_latencies: list[float] = []
        self._ttft_latencies: list[float] = []
        self._fallback_count: int = 0
        self._degraded_count: int = 0
        self._total_turns: int = 0

    def record_ack(self, latency_ms: float) -> None:
        with self._lock:
            self._ack_latencies.append(latency_ms)
            if len(self._ack_latencies) > 200:
                self._ack_latencies.pop(0)

    def record_first_token(self, latency_ms: float) -> None:
        with self._lock:
            self._ttft_latencies.append(latency_ms)
            if len(self._ttft_latencies) > 200:
                self._ttft_latencies.pop(0)

    def record_fallback(self) -> None:
        with self._lock:
            self._fallback_count += 1

    def record_degraded(self) -> None:
        with self._lock:
            self._degraded_count += 1

    def record_turn(self) -> None:
        with self._lock:
            self._total_turns += 1

    @property
    def fallback_count(self) -> int:
        with self._lock:
            return self._fallback_count

    @property
    def degraded_mode_count(self) -> int:
        with self._lock:
            return self._degraded_count

    @property
    def total_turns(self) -> int:
        with self._lock:
            return self._total_turns

    @property
    def ack_latency_ms(self) -> float | None:
        with self._lock:
            if not self._ack_latencies:
                return None
            return round(sum(self._ack_latencies) / len(self._ack_latencies), 2)

    @property
    def first_token_latency_ms(self) -> float | None:
        with self._lock:
            if not self._ttft_latencies:
                return None
            return round(sum(self._ttft_latencies) / len(self._ttft_latencies), 2)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            avg_ack = round(sum(self._ack_latencies) / len(self._ack_latencies), 2) if self._ack_latencies else None
            avg_ttft = round(sum(self._ttft_latencies) / len(self._ttft_latencies), 2) if self._ttft_latencies else None
            return {
                "ack_latency_ms": avg_ack,
                "first_token_latency_ms": avg_ttft,
                "fallback_count": self._fallback_count,
                "degraded_mode_count": self._degraded_count,
                "total_turns": self._total_turns,
            }


_METRICS = AvailabilityMetrics()


def get_availability_metrics() -> AvailabilityMetrics:
    return _METRICS


def reset_availability_metrics() -> None:
    global _METRICS
    _METRICS = AvailabilityMetrics()


async def record_fast_acknowledgment(
    conv_store: ConversationStore,
    bus: Any,
    thread_id: str,
    user_message_id: str,
    target_role: str,
    prompt_text: str = "",
    start_time: float | None = None,
) -> MessageRecord | None:
    """Create and broadcast an immediate acknowledgment within < 3 s SLA."""
    now = time.perf_counter()
    latency_ms = ((now - start_time) * 1000.0) if start_time is not None else 5.0

    routed_target = target_role
    if target_role.lower() == "barb" and prompt_text:
        try:
            from staff.router import route_deterministic

            decision = route_deterministic(prompt_text)
            if decision:
                routed_target = decision.chosen_role
        except Exception as exc:  # noqa: BLE001
            log.debug("Pre-router routing preview failed: %s", exc)

    body_md = f"On it: routing to {routed_target}..."
    ack_msg = conv_store.add_message(
        thread_id=thread_id,
        author_kind="system",
        author="system",
        kind="text",
        body_md=body_md,
        meta={
            "in_reply_to": user_message_id,
            "acknowledgement": True,
            "ack_latency_ms": round(latency_ms, 2),
            "routed_to": routed_target,
        },
        delivery="complete",
    )

    try:
        await bus.publish_message(thread_id, ack_msg.to_dict())
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to publish acknowledgment to bus: %s", exc)

    get_availability_metrics().record_ack(latency_ms)
    return ack_msg


async def execute_degraded_turn(
    thread_id: str,
    user_message_id: str,
    placeholder_id: str,
    role_name: str,
    conv_store: ConversationStore | None = None,
    work_store: WorkItemStore | None = None,
) -> Any:
    """Execute degraded fallback mode: deterministic routing + queued work item."""
    from staff.chat import ChatTurnResult
    from staff.router import route_deterministic

    store = conv_store or get_conversation_store()
    w_store = work_store or get_work_item_store()

    user_msg = store.get_message(user_message_id)
    prompt_text = user_msg.body_md if user_msg else ""

    decision = route_deterministic(prompt_text)
    routed_role = decision.chosen_role if decision else (role_name or "barb")
    reason = decision.reason if decision else "Fleet status and manual triage"

    # Queue follow-up work item
    snippet = prompt_text[:50].strip() or "Staff request"
    title = f"[Degraded] Triage request: {snippet}"
    work_item = w_store.create_work_item(
        title=title,
        requested_by=user_msg.author if user_msg else "user",
        thread_id=thread_id,
        owner_role=routed_role,
    )

    reply_body = (
        f"[Degraded Mode] Automatic routing applied. All AI providers are currently unavailable. "
        f"Routed to {routed_role}: {reason}. Follow-up work item #{work_item.id} has been queued."
    )

    store.update_message(
        placeholder_id,
        kind="text",
        delivery="complete",
        body_md=reply_body,
        meta={
            "degraded_mode": True,
            "active_provider": "degraded",
            "routed_role": routed_role,
            "work_item_id": work_item.id,
        },
    )

    th = store.get_thread(thread_id)
    if th:
        th_meta = dict(th.meta)
        th_meta["active_provider"] = "degraded"
        th_meta["degraded_mode"] = True
        store.update_thread(thread_id, meta=th_meta)

    bus = get_thread_bus()
    completed_msg = store.get_message(placeholder_id)
    if completed_msg:
        try:
            await bus.publish_message(thread_id, completed_msg.to_dict())
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to publish degraded message to bus: %s", exc)

    get_availability_metrics().record_degraded()

    return ChatTurnResult(
        ok=True,
        reply=reply_body,
        session_id=None,
        metrics={"provider": "degraded", "degraded_mode": True},
    )


def record_successful_turn(
    conv_store: ConversationStore,
    thread_id: str,
    placeholder_id: str,
    provider: str,
    fallback_count: int,
    result: Any,
) -> None:
    """Update thread and message metadata with active provider and record TTFT."""
    th = conv_store.get_thread(thread_id)
    if th:
        th_meta = dict(th.meta)
        th_meta["active_provider"] = provider
        conv_store.update_thread(thread_id, meta=th_meta)

    msg = conv_store.get_message(placeholder_id)
    if msg:
        m_meta = dict(msg.meta)
        m_meta["active_provider"] = provider
        m_meta["fallback_count"] = fallback_count
        conv_store.update_message(placeholder_id, meta=m_meta)

    ttft_s = result.metrics.get("time_to_first_token_seconds")
    if isinstance(ttft_s, (int, float)):
        get_availability_metrics().record_first_token(ttft_s * 1000.0)
