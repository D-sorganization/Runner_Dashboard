"""Lease-epoch fence for Barb-authored writes (#2734).

A request declares that it acts as Barb with ``X-Barb-Scope`` + ``X-Barb-Lease-Epoch`` (and,
optionally, ``X-Barb-Holder`` to name one seat among several that share a token). The hub then
checks, under the continuity store's lock, that the caller holds that scope's lease, that the
lease is unexpired and that the epoch matches, and performs the write inside the same critical
section. ``checkout_lease`` (including takeover), ``heartbeat_lease`` and release take the same
lock, so a takeover cannot land between the check and the write.

This extends the existing ``barb_scoped_leases.epoch``; it is not a second fence next to
``barb_fence.py`` (the authority-epoch fence for outbox delivery).

Rule: the lock is a ``threading.RLock`` and is held only across the *synchronous* write; never
``await`` inside :func:`barb_write_fence`.

Unclaimed Barb writes (#3279). Declaring the claim is opt-in for ordinary callers, but a write from a
Barb seat (:func:`is_barb_seat`) that carries neither header is not silently trusted.
``DASHBOARD_BARB_FENCE_MODE`` selects what :func:`claim_for_write` does with it:

- ``off``: nothing; the write runs unfenced (the pre-#3279 behaviour).
- ``warn`` (default; unknown values fall back to it): log a warning naming the principal and surface
  (never header values or credentials), count it in ``dashboard_barb_unclaimed_writes_total``, allow it.
- ``enforce``: count it and refuse with the fence's ``stale_barb_lease`` 409, reason ``claim_required``.

Non-Barb requests are unaffected in every mode.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from fastapi import HTTPException

if TYPE_CHECKING:
    from identity import Principal

log = logging.getLogger("dashboard.barb_fence")

SCOPE_HEADER = "x-barb-scope"
EPOCH_HEADER = "x-barb-lease-epoch"
HOLDER_HEADER = "x-barb-holder"
STALE_ERROR = "stale_barb_lease"
MODE_ENV = "DASHBOARD_BARB_FENCE_MODE"
FenceMode = Literal["off", "warn", "enforce"]
_MODES: tuple[FenceMode, ...] = ("off", "warn", "enforce")
DEFAULT_MODE: FenceMode = "warn"
BARB_SEAT_IDS = frozenset({"barb", "agent-grok"})


@dataclass(frozen=True)
class BarbClaim:
    """A request's declaration that it acts as Barb under ``scope`` at lease ``epoch``."""

    scope: str
    epoch: int
    caller_id: str
    holder: str = ""


class StaleBarbLease(Exception):
    """The claimed Barb lease is not (or no longer) held by the caller at that epoch."""

    def __init__(self, *, reason: str, scope: str, current_epoch: int, holder: str, claimed_epoch: int) -> None:
        super().__init__(f"stale barb lease for {scope}: {reason}")
        self.reason = reason
        self.detail: dict[str, Any] = {
            "error": STALE_ERROR,
            "reason": reason,
            "scope": scope,
            "current_epoch": current_epoch,
            "holder": holder,
            "claimed_epoch": claimed_epoch,
        }


def _holder_bound_to(caller_id: str, holder: str) -> bool:
    return holder == caller_id or holder.startswith(caller_id + ":")


def parse_barb_claim(headers: Mapping[str, str], caller_id: str) -> BarbClaim | None:
    """Return the declared claim, ``None`` for an ordinary request; 422 for a malformed declaration."""
    scope = (headers.get(SCOPE_HEADER) or "").strip()
    raw_epoch = (headers.get(EPOCH_HEADER) or "").strip()
    if not scope and not raw_epoch:
        return None
    if not scope or not raw_epoch:
        raise HTTPException(status_code=422, detail={"error": "barb_claim_incomplete", "required": "scope+epoch"})
    try:
        epoch = int(raw_epoch)
    except ValueError:
        raise HTTPException(status_code=422, detail={"error": "barb_epoch_invalid", "value": raw_epoch}) from None
    if epoch < 1:
        raise HTTPException(status_code=422, detail={"error": "barb_epoch_invalid", "value": raw_epoch})
    return BarbClaim(scope=scope, epoch=epoch, caller_id=caller_id, holder=(headers.get(HOLDER_HEADER) or "").strip())


def is_barb_seat(principal: Principal | None) -> bool:
    """True for a principal that acts as a Barb seat (Grok-kind assistant, a Barb id or the ``barb`` role/scope).

    Admins are deliberately not Barb seats here: they may read Barb-only memory, but their writes are
    operator writes, not Barb-authority writes.
    """
    if principal is None:
        return False
    marks = set(principal.roles) | set(principal.scopes)
    return principal.assistant_kind == "grokbot" or principal.id in BARB_SEAT_IDS or "barb" in marks


def fence_mode() -> FenceMode:
    """The configured unclaimed-write mode; an unset or unknown value is ``warn``."""
    raw = os.environ.get(MODE_ENV, "").strip().lower()
    for mode in _MODES:
        if raw == mode:
            return mode
    if raw:
        log.warning("barb fence: unknown %s value; using %s", MODE_ENV, DEFAULT_MODE)
    return DEFAULT_MODE


def claim_for_write(headers: Mapping[str, str], principal: Principal, *, surface: str) -> BarbClaim | None:
    """Parse the request's Barb claim and apply the unclaimed-write policy for Barb seats (#3279).

    Pre-condition: ``surface`` is non-empty. Post-condition: the result is exactly
    :func:`parse_barb_claim`'s; the only extra effect is a log line, a metric increment or (``enforce``) a 409.
    """
    assert surface, "surface must be non-empty"
    claim = parse_barb_claim(headers, principal.id)
    if claim is not None or not is_barb_seat(principal):
        return claim
    mode = fence_mode()
    if mode == "off":
        return None
    from prometheus_metrics import record_barb_unclaimed_write  # noqa: PLC0415

    record_barb_unclaimed_write(surface)
    log.warning(
        "barb fence: write by Barb principal %s on %s arrived without lease claim headers (mode=%s)",
        principal.id,
        surface,
        mode,
    )
    if mode == "enforce":
        exc = StaleBarbLease(reason="claim_required", scope="", current_epoch=0, holder="", claimed_epoch=0)
        from identity import format_caller  # noqa: PLC0415

        caller = format_caller(principal)
        raise reject_stale(exc, claimed_holder=principal.id, surface=surface, target=surface, principal=caller)
    return None


class BarbLeaseFenceMixin:
    """Fence methods for the continuity store; expects ``_lock`` (RLock) and ``_conn`` from the host."""

    _lock: Any
    _conn: Any

    def assert_lease_claim(self, claim: BarbClaim, *, now: datetime | None = None) -> dict[str, Any]:
        """Raise :class:`StaleBarbLease` unless ``claim`` is the live lease. Call with the lock held."""
        from staff.barb_task_inventory import _now_iso  # noqa: PLC0415

        now_s = _now_iso(now)
        row = self._conn.execute("SELECT * FROM barb_scoped_leases WHERE scope = ?", (claim.scope,)).fetchone()
        if row is None:
            raise StaleBarbLease(
                reason="no_lease", scope=claim.scope, current_epoch=0, holder="", claimed_epoch=claim.epoch
            )
        holder, epoch = str(row["holder"]), int(row["epoch"])

        def stale(reason: str) -> StaleBarbLease:
            return StaleBarbLease(
                reason=reason, scope=claim.scope, current_epoch=epoch, holder=holder, claimed_epoch=claim.epoch
            )

        if claim.holder:
            seat_ok = holder == claim.holder and _holder_bound_to(claim.caller_id, claim.holder)
        else:
            seat_ok = _holder_bound_to(claim.caller_id, holder)
        if not seat_ok:
            raise stale("holder_mismatch")
        if str(row["expires_at"]) <= now_s:
            raise stale("expired")
        if epoch != claim.epoch:
            raise stale("stale_epoch")
        return {"scope": claim.scope, "epoch": epoch, "holder": holder}

    @contextmanager
    def fence(self, claim: BarbClaim, *, now: datetime | None = None) -> Iterator[dict[str, Any]]:
        """Validate ``claim`` and keep every lease mutation out until the ``with`` body finishes."""
        with self._lock:
            yield self.assert_lease_claim(claim, now=now)


def reject_stale(
    exc: StaleBarbLease, *, claimed_holder: str, surface: str, target: str, principal: str
) -> HTTPException:
    """Audit the rejection and build the 409 the caller receives."""
    try:
        from staff.audit import record_audit  # noqa: PLC0415

        record_audit(
            action="barb_write_rejected",
            target=target,
            principal=principal,
            surface=surface,
            outcome="rejected",
            detail={**exc.detail, "claimed_holder": claimed_holder},
            fail_closed=False,
        )
    except Exception:  # noqa: BLE001 - the rejection must reach the caller even if the audit sink is down
        log.warning("barb fence: audit of rejected write failed", exc_info=True)
    return HTTPException(status_code=409, detail=exc.detail)


def _supersede_admitted_audit(audit: dict[str, Any], rejection: HTTPException) -> None:
    """Append a ``dispatch``/``rejected`` row so an earlier ``admitted`` row is not the last word (#2970)."""
    try:
        from staff.audit import record_audit  # noqa: PLC0415

        detail = rejection.detail if isinstance(rejection.detail, dict) else {"error": str(rejection.detail)}
        record_audit(action="dispatch", **audit, outcome="rejected", detail=detail, fail_closed=False)
    except Exception:  # noqa: BLE001 - the rejection must reach the caller even if the audit sink is down
        log.warning("barb fence: audit of superseded admission failed", exc_info=True)


@contextmanager
def barb_write_fence(
    claim: BarbClaim | None,
    *,
    surface: str,
    target: str,
    principal: str,
    store: Any = None,
    audit: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Run a synchronous write under the Barb lease fence; a ``None`` claim is a no-op (non-Barb request).

    ``audit`` is the ``record_audit`` context of an ``admitted`` row already written for this write;
    when the fence rejects, a ``rejected`` row for the same context is appended so the trail stays accurate.
    """
    if claim is None:
        yield
        return
    if store is None:
        from staff.barb_continuity import get_barb_continuity_store  # noqa: PLC0415

        store = get_barb_continuity_store()
    with ExitStack() as stack:
        try:
            stack.enter_context(store.fence(claim))
        except StaleBarbLease as exc:
            rejection = reject_stale(
                exc, claimed_holder=claim.holder or claim.caller_id, surface=surface, target=target, principal=principal
            )
            if audit is not None:
                _supersede_admitted_audit(audit, rejection)
            raise rejection from None
        yield
