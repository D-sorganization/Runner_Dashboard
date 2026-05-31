"""Conductor orchestrator admission-gate + visibility API (issue #1282).

The Conductor orchestrator lives in ``Repository_Management`` and talks to this
dashboard **only over HTTP** — nothing here imports from a sibling repo at
runtime. This module is the admission gate the orchestrator obeys for
backpressure, plus the visibility surface backing the dashboard's Conductor tab.

Routes (all under ``/api/orchestrator``):
  POST /lease    Conductor requests a CI dispatch slot. Granted when idle
                 runners minus a caller-supplied reserve still cover the
                 requested slots AND the queue is in ``running`` mode.
                 Denied (HTTP 200, ``granted=false``) under saturation or a
                 manual pause/drain — this is the backpressure signal.
  POST /release  Release a previously granted lease, freeing its slots.
  GET  /queue    Visibility: current mode, active leases, capacity snapshot.
  POST /queue    Manual override: pause / resume / drain orchestrator work.

Engineering principles enforced here:
  * Feature flag (``DASHBOARD_ENABLE_CONDUCTOR``, default off) — the whole
    surface is inert and returns 404 until explicitly enabled (reversible).
  * DbC — pydantic request/response models carry pre/postconditions as field
    constraints and validators on every POST.
  * DRY — capacity comes from the injected provider that reuses
    ``runner_helpers.count_runner_capacity`` (the same arithmetic as
    ``/api/runners/fleet/capacity``); the count is never re-derived here.
  * LoD — handlers receive and return flat, typed payloads.
  * Orthogonality — lease/queue state is in-process and isolated; a failure
    here cannot cascade into other tabs, and the flag keeps it out of the
    routing table entirely when disabled.
"""

from __future__ import annotations

import inspect
import logging
import os
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger("dashboard.orchestrator")

router = APIRouter(tags=["orchestrator"])

# ─── Feature flag (reversibility) ───────────────────────────────────────────

_FLAG_ENV = "DASHBOARD_ENABLE_CONDUCTOR"
_TRUTHY = {"1", "true", "yes", "on"}

# Lease TTL: orchestrator must release or re-lease before this elapses. Expired
# leases are reaped lazily on the next capacity computation so a crashed
# orchestrator cannot wedge the fleet (self-healing backpressure).
LEASE_TTL_SECONDS = int(os.environ.get("CONDUCTOR_LEASE_TTL_SECONDS", "900"))
DEFAULT_RESERVE = int(os.environ.get("CONDUCTOR_DEFAULT_RESERVE", "1"))


def conductor_enabled() -> bool:
    """Return True when the Conductor surface is enabled via env flag."""
    return os.environ.get(_FLAG_ENV, "0").strip().lower() in _TRUTHY


def _require_enabled() -> None:
    """Precondition for every route: feature flag must be on, else 404."""
    if not conductor_enabled():
        raise HTTPException(status_code=404, detail="Conductor integration is disabled")


# ─── Injected capacity provider (DRY) ───────────────────────────────────────

# Set by server.py via set_capacity_provider(); reuses the same fleet capacity
# counts as /api/runners/fleet/capacity. Returns a flat dict with at least
# "idle_runners". May be sync or async. Defaults to a zero-capacity provider so
# the gate fails safe (denies) when not wired — never grants on missing data.
CapacityProvider = Callable[[], dict[str, int] | Awaitable[dict[str, int]]]

_capacity_provider: CapacityProvider = lambda: {  # noqa: E731
    "idle_runners": 0,
    "online_runners": 0,
    "busy_runners": 0,
    "total_runners": 0,
}


def set_capacity_provider(provider: CapacityProvider) -> None:
    """Wire the fleet capacity source (called once from server.py).

    Precondition: ``provider`` is callable. Postcondition: subsequent lease
    decisions use it as the single source of capacity truth.
    """
    assert callable(provider), "capacity provider must be callable"  # noqa: S101
    global _capacity_provider  # noqa: PLW0603
    _capacity_provider = provider


async def _fetch_capacity() -> dict[str, int]:
    """Resolve the injected capacity provider, awaiting it if it is async."""
    result = _capacity_provider()
    if inspect.isawaitable(result):
        result = await result
    return result


# ─── In-process state (orthogonal, reset-able for tests) ────────────────────

QueueMode = Literal["running", "paused", "draining"]


class _Lease:
    __slots__ = ("lease_id", "requested_by", "slots", "provider", "granted_at", "expires_at")

    def __init__(self, lease_id: str, requested_by: str, slots: int, provider: str) -> None:
        now = time.monotonic()
        self.lease_id = lease_id
        self.requested_by = requested_by
        self.slots = slots
        self.provider = provider or "unknown"
        self.granted_at = now
        self.expires_at = now + LEASE_TTL_SECONDS


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mode: QueueMode = "running"
        self.leases: dict[str, _Lease] = {}

    def reap_expired(self) -> None:
        now = time.monotonic()
        expired = [lid for lid, lease in self.leases.items() if lease.expires_at <= now]
        for lid in expired:
            log.info("orchestrator: reaping expired lease %s", lid)
            del self.leases[lid]

    def reserved_slots(self) -> int:
        return sum(lease.slots for lease in self.leases.values())

    def provider_mix(self) -> dict[str, int]:
        """Active lease count grouped by dispatch provider (for the tab's mix)."""
        mix: dict[str, int] = {}
        for lease in self.leases.values():
            mix[lease.provider] = mix.get(lease.provider, 0) + 1
        return mix


_state = _State()


def reset_state() -> None:
    """Reset in-process lease/queue state. Intended for tests."""
    global _state  # noqa: PLW0603
    _state = _State()


# ─── DbC request/response models ────────────────────────────────────────────


class LeaseRequest(BaseModel):
    """Conductor's request for a CI dispatch slot."""

    requested_by: str = Field(..., min_length=1, max_length=200)
    slots: int = Field(default=1, ge=1, le=64, description="Number of dispatch slots requested")
    reserve: int = Field(
        default=DEFAULT_RESERVE,
        ge=0,
        le=64,
        description="Idle runners to keep free for non-orchestrator work",
    )
    provider: str = Field(default="unknown", max_length=100, description="Dispatch provider for the provider mix")
    correlation_id: str = Field(default="", max_length=100)


class LeaseResponse(BaseModel):
    """Admission decision. ``granted=false`` is the backpressure signal."""

    granted: bool
    lease_id: str | None = None
    ttl_seconds: int
    reason: str = ""
    idle_runners: int
    free_slots: int


class ReleaseRequest(BaseModel):
    lease_id: str = Field(..., min_length=1, max_length=64)


class ReleaseResponse(BaseModel):
    released: bool
    lease_id: str


class QueueActionRequest(BaseModel):
    """Manual override of orchestrator-tracked work."""

    action: Literal["pause", "resume", "drain"]

    @field_validator("action")
    @classmethod
    def _known_action(cls, value: str) -> str:
        assert value in {"pause", "resume", "drain"}, f"unknown action: {value}"  # noqa: S101
        return value


class WorkSummary(BaseModel):
    """Orchestrator-tracked work classification surfaced on the Conductor tab."""

    planned: int = 0
    active: int = 0
    blocked: int = 0


class BudgetSummary(BaseModel):
    """Budget burn for the tab. ``limit_usd <= 0`` means "no limit configured"."""

    spent_usd: float = 0.0
    limit_usd: float = 0.0


class QueueStatusResponse(BaseModel):
    enabled: bool
    mode: QueueMode
    active_leases: int
    reserved_slots: int
    capacity: dict[str, int]
    work: WorkSummary
    provider_mix: dict[str, int]
    budget: BudgetSummary


# ─── Decision core (pure, testable) ─────────────────────────────────────────


def _free_slots(capacity: dict[str, int], reserve: int) -> int:
    """Idle runners minus reserve minus slots already leased out.

    Postcondition: result is never negative.
    """
    idle = int(capacity.get("idle_runners", 0))
    return max(idle - reserve - _state.reserved_slots(), 0)


def _build_status(capacity: dict[str, int]) -> QueueStatusResponse:
    """Assemble the queue status response from in-process state (LoD: flat).

    Caller must hold ``_state.lock``. ``planned`` work counts requests currently
    held back by backpressure/pause; with the in-process model this is the
    number of leases that would not fit if re-requested, approximated as 0 when
    running and ``active_leases`` is the live signal. ``active`` mirrors the
    granted leases; ``blocked`` is non-zero only while paused/draining.
    """
    active = len(_state.leases)
    blocked = active if _state.mode in ("paused", "draining") else 0
    work = WorkSummary(planned=0, active=active, blocked=blocked)
    return QueueStatusResponse(
        enabled=True,
        mode=_state.mode,
        active_leases=active,
        reserved_slots=_state.reserved_slots(),
        capacity={
            "idle_runners": int(capacity.get("idle_runners", 0)),
            "online_runners": int(capacity.get("online_runners", 0)),
            "busy_runners": int(capacity.get("busy_runners", 0)),
            "total_runners": int(capacity.get("total_runners", 0)),
        },
        work=work,
        provider_mix=_state.provider_mix(),
        budget=BudgetSummary(spent_usd=0.0, limit_usd=_budget_limit_usd()),
    )


def _budget_limit_usd() -> float:
    """Configured orchestrator budget ceiling (0 = unconfigured)."""
    try:
        return float(os.environ.get("CONDUCTOR_BUDGET_LIMIT_USD", "0") or "0")
    except ValueError:
        return 0.0


# ─── Routes ─────────────────────────────────────────────────────────────────


@router.post("/api/orchestrator/lease", response_model=LeaseResponse)
async def request_lease(req: LeaseRequest) -> LeaseResponse:
    """Admission gate: grant a dispatch slot iff capacity and mode allow."""
    _require_enabled()
    capacity = await _fetch_capacity()

    with _state.lock:
        _state.reap_expired()
        idle = int(capacity.get("idle_runners", 0))

        if _state.mode != "running":
            return LeaseResponse(
                granted=False,
                lease_id=None,
                ttl_seconds=LEASE_TTL_SECONDS,
                reason=f"orchestrator queue is {_state.mode}",
                idle_runners=idle,
                free_slots=0,
            )

        free = _free_slots(capacity, req.reserve)
        if req.slots > free:
            log.info(
                "orchestrator: DENY lease for %s (slots=%d free=%d idle=%d reserve=%d)",
                req.requested_by,
                req.slots,
                free,
                idle,
                req.reserve,
            )
            return LeaseResponse(
                granted=False,
                lease_id=None,
                ttl_seconds=LEASE_TTL_SECONDS,
                reason=(f"fleet saturated: {req.slots} slot(s) requested, {free} free (idle={idle})"),
                idle_runners=idle,
                free_slots=free,
            )

        lease = _Lease(
            lease_id=f"lease-{uuid.uuid4().hex[:16]}",
            requested_by=req.requested_by,
            slots=req.slots,
            provider=req.provider,
        )
        _state.leases[lease.lease_id] = lease
        log.info(
            "orchestrator: GRANT lease %s to %s (slots=%d idle=%d)",
            lease.lease_id,
            req.requested_by,
            req.slots,
            idle,
        )
        return LeaseResponse(
            granted=True,
            lease_id=lease.lease_id,
            ttl_seconds=LEASE_TTL_SECONDS,
            reason="",
            idle_runners=idle,
            free_slots=max(free - req.slots, 0),
        )


@router.post("/api/orchestrator/release", response_model=ReleaseResponse)
async def release_lease(req: ReleaseRequest) -> ReleaseResponse:
    """Release a lease, freeing its slots. Idempotent for unknown ids."""
    _require_enabled()
    with _state.lock:
        existed = _state.leases.pop(req.lease_id, None) is not None
    if existed:
        log.info("orchestrator: released lease %s", req.lease_id)
    return ReleaseResponse(released=existed, lease_id=req.lease_id)


@router.get("/api/orchestrator/queue", response_model=QueueStatusResponse)
async def get_queue() -> QueueStatusResponse:
    """Visibility surface for the Conductor tab."""
    _require_enabled()
    capacity = await _fetch_capacity()
    with _state.lock:
        _state.reap_expired()
        return _build_status(capacity)


@router.post("/api/orchestrator/queue", response_model=QueueStatusResponse)
async def control_queue(req: QueueActionRequest) -> QueueStatusResponse:
    """Manual override: pause / resume / drain orchestrator work."""
    _require_enabled()
    capacity = await _fetch_capacity()
    with _state.lock:
        match req.action:
            case "pause":
                _state.mode = "paused"
            case "resume":
                _state.mode = "running"
            case "drain":
                _state.mode = "draining"
        log.info("orchestrator: queue mode set to %s via %s", _state.mode, req.action)
        return _build_status(capacity)
