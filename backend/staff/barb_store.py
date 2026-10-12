"""Barb-authority methods of the conversation store (#1800, BR-06).

Extracted from ``staff.conversations`` to keep that module under the 500-line cap. The mixin is
behaviour-neutral: ``ConversationStore`` inherits it, so ``store.barb_outbox(...)`` and
``store.apply_run_card_update(...)`` keep their exact names, signatures and semantics.
"""

from __future__ import annotations

import random
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from staff.barb_db import immediate_txn
from staff.barb_outbox import OutboxStore, bounds_from_env
from staff.barb_versions import (
    DEFAULT_RECEIPT_PRUNE_BATCH,
    ApplyOutcome,
    ApplyResult,
    check_epoch,
    current_version,
    decide,
    prune_receipts,
    receipt_limits_from_env,
    record_applied,
)
from staff.conversation_models import MessageRecord
from staff.store import RUN_STATUSES

__all__ = ["BarbAuthorityMixin"]

RECEIPT_PRUNE_EVERY = 64  # applied updates between automatic bounded receipt prunes


class BarbAuthorityMixin:
    """Outbox + version-gated run-card upsert, mixed into ``ConversationStore``."""

    if TYPE_CHECKING:
        _conn: sqlite3.Connection
        _lock: threading.RLock

        def _ensure_available(self) -> None: ...

        def get_message(self, message_id: str) -> MessageRecord | None: ...

        def update_message(self, message_id: str, **fields: Any) -> MessageRecord | None: ...

        def add_message(
            self,
            thread_id: str,
            author_kind: str = ...,
            author: str = ...,
            kind: str = ...,
            body_md: str = ...,
            meta: dict[str, Any] | None = ...,
            run_id: str = ...,
            idempotency_key: str | None = ...,
            delivery: str = ...,
            message_id: str | None = ...,
        ) -> MessageRecord: ...

    def barb_outbox(
        self,
        *,
        max_entries: int | None = None,
        max_bytes: int | None = None,
        max_entry_bytes: int | None = None,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> OutboxStore:
        """The node's bounded delivery outbox (bounds default from ``BARB_OUTBOX_*``)."""
        self._ensure_available()
        env_entries, env_bytes, env_entry = bounds_from_env()
        return OutboxStore(
            self._conn,
            self._lock,
            max_entries=max_entries if max_entries is not None else env_entries,
            max_bytes=max_bytes if max_bytes is not None else env_bytes,
            max_entry_bytes=max_entry_bytes if max_entry_bytes is not None else env_entry,
            clock=clock,
            rng=rng,
        )

    def prune_barb_receipts(
        self, *, max_rows: int | None = None, max_age_seconds: float | None = None, batch: int | None = None
    ) -> int:
        """Bounded prune of superseded receipts (limits default from ``BARB_RECEIPT_*``); returns rows removed.

        Post: the newest-version receipt of every run is kept, so dedupe and stale rejection are unchanged.
        """
        self._ensure_available()
        env_rows, env_age = receipt_limits_from_env()
        size = DEFAULT_RECEIPT_PRUNE_BATCH if batch is None else batch
        with immediate_txn(self._conn, self._lock) as conn:
            return prune_receipts(
                conn,
                max_rows=env_rows if max_rows is None else max_rows,
                max_age_seconds=env_age if max_age_seconds is None else max_age_seconds,
                batch=size,
            )

    def apply_run_card_update(
        self,
        *,
        thread_id: str,
        run_id: str,
        message_id: str,
        status: str,
        version: int | None,
        body_md: str,
        meta: dict[str, Any],
        author: str,
        epoch: int | None = None,
        authority_epoch: int = 0,
        author_kind: str = "role",
    ) -> tuple[MessageRecord | None, ApplyResult]:
        """Idempotent, version-gated upsert of a run card (replaces read-then-write in ``post_run_card``).

        Pre: ``status`` is a run status; ``version`` is ``>= 1`` (``None`` = next local version); a given
        ``epoch`` equals ``authority_epoch`` (else :class:`StaleEpochError`, nothing written).
        Post: the card message, version row and receipt commit atomically, or not at all. APPLIED
        returns the new card; DUPLICATE/STALE leave state untouched and return the stored card.
        """
        self._ensure_available()
        if status not in RUN_STATUSES:
            raise ValueError(f"invalid run status {status!r}")
        if version is not None and version < 1:
            raise ValueError("version must be >= 1")
        check_epoch(epoch, authority_epoch)
        with immediate_txn(self._conn, self._lock) as conn:
            ver = version if version is not None else current_version(conn, run_id) + 1
            result = decide(conn, run_id=run_id, version=ver, status=status, message_id=message_id)
            if result.outcome is not ApplyOutcome.APPLIED:
                return self.get_message(message_id), result
            # The version lives only in run_card_versions: card meta keeps its existing API shape.
            msg = self.update_message(message_id, body_md=body_md, meta=meta) or self.add_message(
                thread_id=thread_id,
                author_kind=author_kind,
                author=author,
                kind="run_card",
                body_md=body_md,
                meta=meta,
                run_id=run_id,
                delivery="complete",
                message_id=message_id,
            )
            receipt = record_applied(
                conn,
                run_id=run_id,
                version=ver,
                status=status,
                message_id=message_id,
                thread_id=thread_id,
                seq=msg.seq,
                epoch=authority_epoch,
            )
            self._applied_since_prune = getattr(self, "_applied_since_prune", 0) + 1
            if self._applied_since_prune >= RECEIPT_PRUNE_EVERY:
                self._applied_since_prune = 0
                self.prune_barb_receipts()
            return msg, ApplyResult(ApplyOutcome.APPLIED, ver, receipt)
