"""Dual-read/drain/rollback harness for the runner-lease YAML->SQLite cutover (#3877).

Standalone migration-safety layer for the eventual ``runner_lease.py`` cutover
from YAML+fcntl to SQLite WAL storage (#3878). YAML and SQLite must never both
act as claim authority for the same runner fleet-wide, so this harness lets an
operator stage a secondary backend, verify it agrees with the primary
(dual-read), copy state into it under a monotonic fencing token (drain), and
either promote it to authority (cutover) or discard it without touching the
primary (rollback) -- all in-process, with no deploy-dependent recovery path.

This module is backend-agnostic: it is exercised here against fakes, and
#3878's SQLite store is a future ``LeaseStore`` implementation, not something
this harness constructs. Per the epic (#3874), JWT rollout completion does
not satisfy this item, and no production cutover may use this harness without
a separate recorded security/infra sign-off.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from runner_lease import LeaseManager, LeaseRecord

log = logging.getLogger("dashboard.runner_lease_migration")


class LeaseStore(Protocol):
    """Backend contract the dual-read harness drives.

    The existing YAML ``LeaseManager`` (via :class:`YamlLeaseStore`) and
    #3878's future SQLite store both satisfy this.
    """

    def read_all(self) -> list[LeaseRecord]: ...

    def write_all(self, records: list[LeaseRecord]) -> None: ...


class YamlLeaseStore:
    """Adapts the existing YAML ``LeaseManager`` to the ``LeaseStore`` protocol.

    Reuses ``LeaseManager``'s own locking and atomic read-modify-write (#327,
    #936) rather than re-implementing file safety here.
    """

    def __init__(self, manager: LeaseManager):
        self._manager = manager

    def read_all(self) -> list[LeaseRecord]:
        return self._manager.get_active_leases()

    def write_all(self, records: list[LeaseRecord]) -> None:
        snapshot = list(records)
        self._manager._atomic_read_modify_write(lambda _current: snapshot)


class SingleAuthorityViolation(RuntimeError):
    """Raised when a caller tries to cut over without a matching fresh drain."""


@dataclasses.dataclass(frozen=True)
class DualReadResult:
    """Read-only snapshot comparison; never raises on drift.

    The secondary legitimately lags the primary until a drain has run, so
    disagreement is reported, not treated as an error.
    """

    primary_records: tuple[LeaseRecord, ...]
    secondary_records: tuple[LeaseRecord, ...]
    agrees: bool
    primary_only: tuple[str, ...]
    secondary_only: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class DrainResult:
    fencing_token: int
    record_count: int


class LeaseMigrationHarness:
    """Coordinates a one-way primary(YAML)->secondary(SQLite) lease cutover.

    Exactly one store is authoritative at a time (``self.authority``).
    Precondition: ``primary`` starts as authority. Postcondition: at most one
    of ``primary``/``secondary`` is ever written to as the live claim store
    by normal lease operations; ``drain``/``cutover``/``rollback`` are the
    only paths that move state between them.
    """

    def __init__(self, primary: LeaseStore, secondary: LeaseStore):
        self._primary = primary
        self._secondary = secondary
        self._authority = "primary"
        self._next_fencing_token = 1
        self._last_drain_token: int | None = None

    @property
    def authority(self) -> str:
        return self._authority

    def authoritative_store(self) -> LeaseStore:
        """The single store normal lease operations must read/write."""
        return self._primary if self._authority == "primary" else self._secondary

    def read_dual(self) -> DualReadResult:
        """Read both backends without mutating either; report any drift."""
        primary_records = tuple(self._primary.read_all())
        secondary_records = tuple(self._secondary.read_all())
        primary_ids = {r.runner_id for r in primary_records}
        secondary_ids = {r.runner_id for r in secondary_records}
        return DualReadResult(
            primary_records=primary_records,
            secondary_records=secondary_records,
            agrees=primary_ids == secondary_ids,
            primary_only=tuple(sorted(primary_ids - secondary_ids)),
            secondary_only=tuple(sorted(secondary_ids - primary_ids)),
        )

    def drain(self) -> DrainResult:
        """Copy the authoritative store's current state into the secondary.

        Does not switch authority. Idempotent: safe to call repeatedly before
        ``cutover``. Each call mints a fresh monotonic fencing token so a
        stale drain cannot be mistaken for the most recent one.
        """
        if self._authority != "primary":
            raise SingleAuthorityViolation("drain is only valid before cutover (authority is already secondary)")
        records = list(self._primary.read_all())
        self._secondary.write_all(records)
        token = self._next_fencing_token
        self._next_fencing_token += 1
        self._last_drain_token = token
        log.info("runner_lease migration: drained %d record(s) under fencing token %d", len(records), token)
        return DrainResult(fencing_token=token, record_count=len(records))

    def cutover(self, fencing_token: int) -> None:
        """Promote the drained secondary store to authority.

        Precondition: ``fencing_token`` must match the most recent
        ``drain()`` call's token -- a stale or already-consumed token means
        the drained snapshot may be out of date, or cutover already ran, and
        the single-authority invariant would be at risk if honored anyway.
        """
        if self._authority != "primary":
            raise SingleAuthorityViolation("cutover already complete; authority is secondary")
        if self._last_drain_token is None or fencing_token != self._last_drain_token:
            raise SingleAuthorityViolation(
                f"cutover requested with stale fencing token {fencing_token!r} "
                f"(last drain token was {self._last_drain_token!r}); drain again before cutover"
            )
        self._authority = "secondary"
        self._last_drain_token = None
        log.info("runner_lease migration: cutover complete, authority=secondary")

    def rollback(self) -> None:
        """Restore the primary (YAML) store as authority, in-process.

        Exit criterion (#3877): rollback never requires a deploy or any
        production release (#2657) -- it is a pure in-memory authority flip.
        Persisting the authority choice across a process restart, if needed,
        is the caller's responsibility (e.g. an env var read at startup).
        """
        self._authority = "primary"
        self._last_drain_token = None
        log.info("runner_lease migration: rolled back, authority=primary")
