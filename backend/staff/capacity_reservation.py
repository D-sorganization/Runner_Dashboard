"""Fleet capacity reservation and budget gating (BR-17, Issue #1810).

Policy-owned limits and atomic reservations keyed by admitted operation,
with bounded pending queues, priority/fairness, lease expiry, node loss
reconciliation, and settlement of actual usage against estimates.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from staff.store import default_db_path, first_touch_lock

log = logging.getLogger("dashboard.staff.capacity")

ReservationStatus = Literal["reserved", "settled", "released", "expired"]
UnknownQuotaPolicy = Literal["refuse", "allow", "degraded"]


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _add_seconds(iso_str: str, seconds: int) -> str:
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return (dt + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CapacityLimits:
    """Configured bounds for fleet capacity and budget governance."""

    max_concurrent_slots: int = 10
    max_daily_budget_usd: float = 100.0
    repair_reserved_slots: int = 2
    max_pending_queue: int = 50
    default_lease_ttl_seconds: int = 600
    unknown_quota_policy: UnknownQuotaPolicy = "refuse"


@dataclass
class CapacityReservation:
    """An atomic capacity reservation for an admitted operation."""

    operation_id: str
    role: str
    provider: str
    slots: int
    estimated_spend_usd: float
    actual_spend_usd: float | None
    priority: int
    node: str
    is_subscription: bool
    reserved_at: str
    lease_expires_at: str
    settled_at: str | None
    status: ReservationStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "role": self.role,
            "provider": self.provider,
            "slots": self.slots,
            "estimated_spend_usd": self.estimated_spend_usd,
            "actual_spend_usd": self.actual_spend_usd,
            "priority": self.priority,
            "node": self.node,
            "is_subscription": self.is_subscription,
            "reserved_at": self.reserved_at,
            "lease_expires_at": self.lease_expires_at,
            "settled_at": self.settled_at,
            "status": self.status,
        }


@dataclass(frozen=True)
class ReservationDecision:
    """Result of a capacity reservation request."""

    admitted: bool
    reservation: CapacityReservation | None = None
    refusal_reason: str = ""
    queue_position: int | None = None
    next_eligible_time_iso: str | None = None
    explanation: str = ""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS capacity_reservations (
    operation_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    provider TEXT NOT NULL,
    slots INTEGER NOT NULL,
    estimated_spend_usd REAL NOT NULL,
    actual_spend_usd REAL,
    priority INTEGER NOT NULL,
    node TEXT NOT NULL,
    is_subscription INTEGER NOT NULL,
    reserved_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    settled_at TEXT,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cap_res_status_expires
    ON capacity_reservations(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_cap_res_node
    ON capacity_reservations(node, status);
"""


class CapacityReservationStore:
    """Thread-safe and SQLite-backed store for atomic fleet reservations."""

    def __init__(
        self,
        path: Path | None = None,
        limits: CapacityLimits | None = None,
    ) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.limits = limits or CapacityLimits()
        self._lock = threading.RLock()
        with first_touch_lock(self.path):
            self._conn = sqlite3.connect(
                str(self.path),
                check_same_thread=False,
                isolation_level=None,
                timeout=30.0,
            )
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout = 30000")
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
                self._conn.executescript(_SCHEMA)

    def _row_to_reservation(self, row: sqlite3.Row) -> CapacityReservation:
        return CapacityReservation(
            operation_id=row["operation_id"],
            role=row["role"],
            provider=row["provider"],
            slots=int(row["slots"]),
            estimated_spend_usd=float(row["estimated_spend_usd"]),
            actual_spend_usd=float(row["actual_spend_usd"]) if row["actual_spend_usd"] is not None else None,
            priority=int(row["priority"]),
            node=row["node"],
            is_subscription=bool(row["is_subscription"]),
            reserved_at=row["reserved_at"],
            lease_expires_at=row["lease_expires_at"],
            settled_at=row["settled_at"],
            status=row["status"],
        )

    def sweep_expired(self) -> list[str]:
        """Mark reservations past their lease expiration as expired."""
        now = _iso_now()
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT operation_id FROM capacity_reservations WHERE status = 'reserved' AND lease_expires_at <= ?",
                (now,),
            ).fetchall()
            expired_ids = [r[0] for r in rows]
            if expired_ids:
                placeholders = ",".join("?" * len(expired_ids))
                self._conn.execute(
                    f"UPDATE capacity_reservations SET status = 'expired' WHERE operation_id IN ({placeholders})",
                    expired_ids,
                )
        return expired_ids

    def reserve(
        self,
        operation_id: str,
        role: str,
        provider: str,
        slots: int = 1,
        estimated_spend_usd: float = 0.0,
        priority: int = 0,
        node: str = "local",
        is_subscription: bool = False,
        quota_known: bool = True,
        ttl_seconds: int | None = None,
    ) -> ReservationDecision:
        """Atomically reserve capacity and budget before execution."""
        ttl = ttl_seconds or self.limits.default_lease_ttl_seconds
        now = _iso_now()
        expires_at = _add_seconds(now, ttl)

        with self._lock, self._conn:
            self.sweep_expired()

            # Idempotency check: if this operation is already reserved, return existing
            existing_row = self._conn.execute(
                "SELECT * FROM capacity_reservations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing_row:
                res = self._row_to_reservation(existing_row)
                if res.status == "reserved":
                    return ReservationDecision(
                        admitted=True,
                        reservation=res,
                        explanation=f"Already reserved {res.slots} slot(s) for operation {operation_id}.",
                    )
                if res.status == "settled":
                    return ReservationDecision(
                        admitted=True,
                        reservation=res,
                        explanation=f"Operation {operation_id} was already settled.",
                    )
                return ReservationDecision(
                    admitted=False,
                    refusal_reason=f"operation_already_{res.status}",
                    explanation=f"Operation {operation_id} is already terminal ({res.status}).",
                )

            # Quota check policy
            if not quota_known and self.limits.unknown_quota_policy == "refuse":
                reason = "quota_unknown: explicit policy refuses reservation when provider quota is unknown"
                explanation = f"Refused: Quota for provider '{provider}' is unknown. Policy requires refusal."
                return ReservationDecision(
                    admitted=False,
                    refusal_reason=reason,
                    explanation=explanation,
                )

            # Active slots calculation
            active_rows = self._conn.execute(
                "SELECT slots, lease_expires_at, estimated_spend_usd, is_subscription "
                "FROM capacity_reservations WHERE status = 'reserved' AND lease_expires_at > ?",
                (now,),
            ).fetchall()

            active_slots = sum(int(r["slots"]) for r in active_rows)

            # Headroom check: normal work cannot consume repair_reserved_slots
            allowed_slots = self.limits.max_concurrent_slots
            if priority <= 0:
                allowed_slots -= self.limits.repair_reserved_slots

            if active_slots + slots > allowed_slots:
                # Find earliest expiring lease for next eligible time
                expiries = sorted(r["lease_expires_at"] for r in active_rows if r["lease_expires_at"] > now)
                next_eligible = expiries[0] if expiries else _add_seconds(now, 60)
                queue_pos = max(1, (active_slots + slots) - allowed_slots)
                refusal = "capacity_exhausted"
                explanation = (
                    f"Refused: Fleet capacity exhausted ({active_slots}/{allowed_slots} slots active). "
                    f"Queue position ~#{queue_pos}. Next capacity expected by {next_eligible}."
                )
                return ReservationDecision(
                    admitted=False,
                    refusal_reason=refusal,
                    queue_position=queue_pos,
                    next_eligible_time_iso=next_eligible,
                    explanation=explanation,
                )

            # Budget check: subscription effort is tracked separately from billed API spend
            if not is_subscription and estimated_spend_usd > 0:
                active_spend = sum(
                    float(r["estimated_spend_usd"]) for r in active_rows if not bool(r["is_subscription"])
                )
                today_start = now[:10] + "T00:00:00Z"
                settled_row = self._conn.execute(
                    "SELECT COALESCE(SUM(actual_spend_usd), 0.0) FROM capacity_reservations "
                    "WHERE status = 'settled' AND is_subscription = 0 AND reserved_at >= ?",
                    (today_start,),
                ).fetchone()
                settled_spend = float(settled_row[0]) if settled_row else 0.0

                if (active_spend + settled_spend + estimated_spend_usd) > self.limits.max_daily_budget_usd:
                    tomorrow = datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(days=1)
                    tomorrow_reset = tomorrow.strftime("%Y-%m-%dT00:00:00Z")
                    refusal = "daily_budget_exhausted"
                    explanation = (
                        f"Refused: Daily billed budget exhausted "
                        f"(${active_spend + settled_spend:.2f}/${self.limits.max_daily_budget_usd:.2f}). "
                        f"Resets at {tomorrow_reset}."
                    )
                    return ReservationDecision(
                        admitted=False,
                        refusal_reason=refusal,
                        next_eligible_time_iso=tomorrow_reset,
                        explanation=explanation,
                    )

            # Insert reservation
            self._conn.execute(
                "INSERT INTO capacity_reservations ("
                "operation_id, role, provider, slots, estimated_spend_usd, "
                "actual_spend_usd, priority, node, is_subscription, "
                "reserved_at, lease_expires_at, settled_at, status"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'reserved')",
                (
                    operation_id,
                    role,
                    provider,
                    slots,
                    estimated_spend_usd,
                    None,
                    priority,
                    node,
                    1 if is_subscription else 0,
                    now,
                    expires_at,
                ),
            )

            res = CapacityReservation(
                operation_id=operation_id,
                role=role,
                provider=provider,
                slots=slots,
                estimated_spend_usd=estimated_spend_usd,
                actual_spend_usd=None,
                priority=priority,
                node=node,
                is_subscription=is_subscription,
                reserved_at=now,
                lease_expires_at=expires_at,
                settled_at=None,
                status="reserved",
            )
            explanation = (
                f"Admitted: Reserved {slots} slot(s) on node '{node}' for operation {operation_id} until {expires_at}."
            )
            return ReservationDecision(
                admitted=True,
                reservation=res,
                explanation=explanation,
            )

    def settle(
        self,
        operation_id: str,
        actual_spend_usd: float | None = None,
    ) -> CapacityReservation | None:
        """Settle an active reservation with actual usage, freeing slot and recording actual spend."""
        now = _iso_now()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM capacity_reservations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if not row:
                return None

            current = self._row_to_reservation(row)
            actual = actual_spend_usd if actual_spend_usd is not None else current.estimated_spend_usd

            self._conn.execute(
                "UPDATE capacity_reservations SET "
                "actual_spend_usd = ?, settled_at = ?, status = 'settled' "
                "WHERE operation_id = ?",
                (actual, now, operation_id),
            )
            current.actual_spend_usd = actual
            current.settled_at = now
            current.status = "settled"
            return current

    def release(self, operation_id: str) -> bool:
        """Release a reservation without charging spend or holding slots."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE capacity_reservations SET status = 'released' WHERE operation_id = ? AND status = 'reserved'",
                (operation_id,),
            )
            return cursor.rowcount > 0

    def reconcile_node_loss(
        self,
        node: str,
        active_op_ids: set[str] | None = None,
    ) -> list[str]:
        """Reconcile lost node by releasing orphaned reservations without double-spend."""
        now = _iso_now()
        active = active_op_ids or set()
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT operation_id FROM capacity_reservations WHERE node = ? AND status = 'reserved'",
                (node,),
            ).fetchall()
            orphaned = [r[0] for r in rows if r[0] not in active]
            if orphaned:
                placeholders = ",".join("?" * len(orphaned))
                self._conn.execute(
                    f"UPDATE capacity_reservations SET status = 'released', settled_at = ? "
                    f"WHERE operation_id IN ({placeholders})",
                    [now, *orphaned],
                )
            return orphaned

    def get_reservation(self, operation_id: str) -> CapacityReservation | None:
        """Fetch a reservation by operation ID."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM capacity_reservations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return self._row_to_reservation(row) if row else None


_GLOBAL_STORE: CapacityReservationStore | None = None
_STORE_LOCK = threading.Lock()


def get_capacity_reservation_store(
    path: Path | None = None,
    limits: CapacityLimits | None = None,
) -> CapacityReservationStore:
    """Singleton accessor for capacity reservation store."""
    global _GLOBAL_STORE
    with _STORE_LOCK:
        if _GLOBAL_STORE is None or path is not None or limits is not None:
            _GLOBAL_STORE = CapacityReservationStore(path=path, limits=limits)
        return _GLOBAL_STORE


def reset_capacity_reservation_store() -> None:
    """Reset global store instance (primarily for tests)."""
    global _GLOBAL_STORE
    with _STORE_LOCK:
        _GLOBAL_STORE = None
