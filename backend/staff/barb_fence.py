"""Persisted Barb authority epoch fence (#1800, BR-06; ADR 0008 invariant I4).

The configured ``BARB_AUTHORITY_EPOCH`` alone cannot refuse a restarted old authority whose configuration
was never updated, or a restored backup whose epoch rolled back. The fence is a monotonic high-water mark
``(epoch, node)`` stored in the conversation database (migration 7, one row):

* an epoch below the fence is refused (a demoted authority stays demoted across restarts);
* the same epoch naming a different node is refused (a node change must bump the epoch: it is explicit);
* a higher epoch advances the fence; the fence never moves backwards.

Only an epoch-aware write (``authority_epoch >= 1``) touches the fence, so the legacy single-node role
(``UNSET``, epoch 0) is unchanged. The fence is advanced by the authority's own writes and by the operator's
failover script (``scripts/barb_failover.py advance``); it is never learned from a request, so a misconfigured
sender cannot fence the real authority.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from staff.barb_versions import StaleEpochError
from staff.store import _now

__all__ = ["EpochFence", "FencedAuthorityError", "advance_fence", "check_fence", "read_fence"]


class FencedAuthorityError(StaleEpochError):
    """This node's authority epoch/node is behind the persisted fence (maps to 409 stale_authority_epoch)."""


@dataclass(frozen=True)
class EpochFence:
    epoch: int
    node: str
    reason: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_fence(fence: EpochFence | None, epoch: int, node: str) -> None:
    """Pre: ``epoch >= 1``. Post: returns when ``(epoch, node)`` may act as the authority under ``fence``;
    otherwise raises :class:`FencedAuthorityError`. An empty stored or requested node matches any node."""
    if epoch < 1:
        raise ValueError(f"an authority epoch must be >= 1, got {epoch}")
    if fence is None or epoch > fence.epoch:
        return
    if epoch < fence.epoch:
        raise FencedAuthorityError(f"epoch {epoch} is below the fence {fence.epoch} held by {fence.node or '?'}")
    if node and fence.node and node.lower() != fence.node.lower():
        raise FencedAuthorityError(
            f"node change from {fence.node} to {node} at epoch {epoch} needs an epoch bump (> {fence.epoch})"
        )


def read_fence(conn: sqlite3.Connection) -> EpochFence | None:
    row = conn.execute("SELECT epoch, node, reason, updated_at FROM barb_epoch_fence WHERE id = 1").fetchone()
    if row is None:
        return None
    return EpochFence(int(row[0]), str(row[1]), str(row[2]), str(row[3]))


def advance_fence(conn: sqlite3.Connection, epoch: int, node: str, reason: str = "") -> EpochFence:
    """Pre: the caller holds a write transaction. Post: the fence is ``>= (epoch, node)`` and returned;
    raises :class:`FencedAuthorityError` (nothing written) when ``(epoch, node)`` is behind it.

    Writes only on a change: a higher epoch, or adopting a node for an epoch stored without one.
    """
    current = read_fence(conn)
    check_fence(current, epoch, node)
    if current is not None and epoch == current.epoch and (current.node or not node):
        return current
    same_epoch = current is not None and epoch == current.epoch
    kept_reason = current.reason if same_epoch and current is not None else ""
    updated = EpochFence(epoch, node or (current.node if current else ""), reason or kept_reason, _now())
    conn.execute(
        "INSERT INTO barb_epoch_fence (id, epoch, node, reason, updated_at) VALUES (1, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET epoch = excluded.epoch, node = excluded.node, "
        "reason = excluded.reason, updated_at = excluded.updated_at",
        (updated.epoch, updated.node, updated.reason, updated.updated_at),
    )
    return updated
