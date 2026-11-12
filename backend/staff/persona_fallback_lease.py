"""Persona fallback lease with fencing, slot idempotency ledger, and offline spool ingest (#3295 S4).

Reuses ``BarbContinuityStore`` and ``barb_lease_fence`` (DRY: no parallel lease store) so
``persona:<role>`` leases share the same monotonic ``epoch`` fencing token and SQLite store.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from staff.barb_continuity import DEFAULT_LEASE_TTL_SECONDS, BarbContinuityStore, _is_released, _now_iso
from staff.barb_lease_fence import BarbClaim

log = logging.getLogger("dashboard.staff.persona_fallback_lease")

GROK_FALLBACK_HOLDER = "grok-fallback"
_ROLE_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_RD_NODE_RE = re.compile(r"^rd:[a-z0-9][a-z0-9._-]*$")

_SLOT_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS persona_slot_ledger (
    slot_key TEXT PRIMARY KEY, role TEXT NOT NULL, job TEXT NOT NULL, slot TEXT NOT NULL,
    holder TEXT NOT NULL, epoch INTEGER NOT NULL, board_claim_ref TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'completed'
);
CREATE INDEX IF NOT EXISTS idx_persona_slot_role ON persona_slot_ledger(role, recorded_at DESC);
"""

# Slot lifecycle states (#3789): a claim starts "in_progress" and only becomes "completed"
# once the scheduled work (capsule save + notify) actually succeeds. A row created before this
# migration has no way to know whether its work finished, so the schema default above treats it
# as "completed" -- the same "never retry" behavior the ledger already had for every row.
_SLOT_STATUS_IN_PROGRESS = "in_progress"
_SLOT_STATUS_COMPLETED = "completed"


def validate_role_name(role: str) -> str:
    norm = (role or "").strip()
    if not _ROLE_RE.match(norm):
        raise ValueError(f"Invalid persona role {role!r}: expected lowercase slug")
    return norm


def persona_scope(role: str) -> str:
    return f"persona:{validate_role_name(role)}"


def validate_persona_holder(holder: str) -> str:
    norm = (holder or "").strip()
    if norm == GROK_FALLBACK_HOLDER or _RD_NODE_RE.match(norm):
        return norm
    raise ValueError(f"Invalid persona lease holder {holder!r}: expected {GROK_FALLBACK_HOLDER!r} or 'rd:<node>'")


def _validate_part(value: str, field: str) -> str:
    norm = (value or "").strip()
    if not norm or "/" in norm:
        raise ValueError(f"Invalid {field} {value!r}: must be non-empty without '/'")
    return norm


def slot_ledger_key(role: str, job: str, slot: str) -> str:
    return f"{validate_role_name(role)}/{_validate_part(job, 'job')}/{_validate_part(slot, 'slot')}"


def _ensure_slot_table(store: BarbContinuityStore) -> None:
    with store._lock, store._conn:
        store._conn.executescript(_SLOT_LEDGER_SCHEMA)
        cols = {row[1] for row in store._conn.execute("PRAGMA table_info(persona_slot_ledger)").fetchall()}
        if "status" not in cols:
            store._conn.execute(
                f"ALTER TABLE persona_slot_ledger ADD COLUMN status TEXT NOT NULL DEFAULT '{_SLOT_STATUS_COMPLETED}'"
            )


def _row_to_slot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "key": str(row["slot_key"]),
        "role": str(row["role"]),
        "job": str(row["job"]),
        "slot": str(row["slot"]),
        "holder": str(row["holder"]),
        "epoch": int(row["epoch"]),
        "board_claim_ref": str(row["board_claim_ref"]),
        "recorded_at": str(row["recorded_at"]),
        "status": str(row["status"]),
    }


def acquire_persona_lease(
    store: BarbContinuityStore,
    role: str,
    *,
    holder: str,
    summary: str = "",
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    scope = persona_scope(role)
    valid_holder = validate_persona_holder(holder)
    return store.checkout_lease(
        scope=scope,
        holder=valid_holder,
        persona=role.strip(),
        summary=summary or f"{valid_holder} lease for {scope}",
        ttl_seconds=ttl_seconds,
        force=force,
        now=now,
    )


def release_persona_lease(
    store: BarbContinuityStore,
    role: str,
    *,
    holder: str,
    epoch: int,
    summary: str = "persona fallback lease released",
    now: datetime | None = None,
) -> dict[str, Any]:
    scope = persona_scope(role)
    valid_holder = validate_persona_holder(holder)
    if int(epoch) < 1:
        raise ValueError("epoch must be >= 1")
    claim = BarbClaim(scope=scope, epoch=int(epoch), caller_id=valid_holder, holder=valid_holder)
    with store.fence(claim, now=now):
        return store.release_lease(
            scope=scope,
            holder=valid_holder,
            require_turnover=False,
            turnover={"summary": summary} if summary.strip() else None,
            now=now,
        )


def is_scheduler_blocked_by_fallback(
    store: BarbContinuityStore,
    role: str,
    *,
    now: datetime | None = None,
) -> bool:
    scope = persona_scope(role)
    now_s = _now_iso(now)
    try:
        with store._lock:
            row = store._conn.execute("SELECT * FROM barb_scoped_leases WHERE scope = ?", (scope,)).fetchone()
    except sqlite3.Error:
        log.error("persona lease lookup failed for %s; failing closed", scope, exc_info=True)
        return True
    if row is None or _is_released(row) or str(row["expires_at"]) <= now_s:
        return False
    return str(row["holder"]) == GROK_FALLBACK_HOLDER


def record_slot_claim(
    store: BarbContinuityStore,
    *,
    role: str,
    job: str,
    slot: str,
    holder: str,
    epoch: int,
    board_claim_ref: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically claim a slot for in-progress execution (#3789).

    Returns ``recorded=True`` when this call now owns the slot. Returns
    ``recorded=False, duplicate=True`` when another claim already exists for this slot --
    either still ``in_progress`` (a concurrent attempt must not also execute) or already
    ``completed`` (the work genuinely ran; never re-execute). Callers must only perform the
    scheduled work when ``recorded`` is True, and must pair a successful claim with
    :func:`mark_slot_complete` on success or :func:`release_slot_claim` on failure so a failed
    attempt remains retryable instead of being stuck "in progress" forever.
    """
    key = slot_ledger_key(role, job, slot)
    valid_holder = validate_persona_holder(holder)
    if int(epoch) < 1:
        raise ValueError("epoch must be >= 1")
    now_s = _now_iso(now)
    _ensure_slot_table(store)
    with store._lock, store._conn:
        existing = store._conn.execute("SELECT * FROM persona_slot_ledger WHERE slot_key = ?", (key,)).fetchone()
        if existing is not None:
            return {"recorded": False, "duplicate": True, **_row_to_slot(existing)}
        store._conn.execute(
            "INSERT INTO persona_slot_ledger (slot_key, role, job, slot, holder, epoch, board_claim_ref, "
            "recorded_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                role.strip(),
                job.strip(),
                slot.strip(),
                valid_holder,
                int(epoch),
                board_claim_ref.strip(),
                now_s,
                _SLOT_STATUS_IN_PROGRESS,
            ),
        )
        row = store._conn.execute("SELECT * FROM persona_slot_ledger WHERE slot_key = ?", (key,)).fetchone()
    return {"recorded": True, "duplicate": False, **_row_to_slot(row)}


def _fence_slot_claim(store: BarbContinuityStore, key: str, holder: str, epoch: int) -> sqlite3.Row | None:
    """Return the ledger row for ``key`` iff it is still ``in_progress`` under this exact
    ``holder``/``epoch`` -- rejecting a stale claimant whose claim was released and reclaimed by
    someone else in the meantime. Caller already holds ``store._lock``."""
    row = store._conn.execute("SELECT * FROM persona_slot_ledger WHERE slot_key = ?", (key,)).fetchone()
    if row is None or str(row["status"]) != _SLOT_STATUS_IN_PROGRESS:
        return None
    if str(row["holder"]) != holder or int(row["epoch"]) != int(epoch):
        return None
    return row


def mark_slot_complete(
    store: BarbContinuityStore,
    *,
    role: str,
    job: str,
    slot: str,
    holder: str,
    epoch: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark a slot claimed by this exact ``holder``/``epoch`` as completed (#3789).

    Fenced like :func:`release_persona_lease`: a stale holder/epoch -- one whose in-progress
    claim was already released and reclaimed by a different attempt -- cannot complete it.
    """
    key = slot_ledger_key(role, job, slot)
    valid_holder = validate_persona_holder(holder)
    now_s = _now_iso(now)
    _ensure_slot_table(store)
    with store._lock, store._conn:
        row = _fence_slot_claim(store, key, valid_holder, epoch)
        if row is None:
            return {"completed": False, "reason": "stale_or_missing_claim", "key": key}
        store._conn.execute(
            "UPDATE persona_slot_ledger SET status = ?, recorded_at = ? WHERE slot_key = ?",
            (_SLOT_STATUS_COMPLETED, now_s, key),
        )
        updated = store._conn.execute("SELECT * FROM persona_slot_ledger WHERE slot_key = ?", (key,)).fetchone()
    assert updated is not None  # just written above, under the same lock
    return {"completed": True, **_row_to_slot(updated)}


def release_slot_claim(
    store: BarbContinuityStore,
    *,
    role: str,
    job: str,
    slot: str,
    holder: str,
    epoch: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Release a failed in-progress slot claim so it becomes retryable (#3789).

    Fenced like :func:`mark_slot_complete`: a stale holder/epoch cannot release a claim it no
    longer owns.
    """
    key = slot_ledger_key(role, job, slot)
    valid_holder = validate_persona_holder(holder)
    _ensure_slot_table(store)
    with store._lock, store._conn:
        row = _fence_slot_claim(store, key, valid_holder, epoch)
        if row is None:
            return {"released": False, "reason": "stale_or_missing_claim", "key": key}
        store._conn.execute("DELETE FROM persona_slot_ledger WHERE slot_key = ?", (key,))
    return {"released": True, "key": key}


def should_skip_slot(
    store: BarbContinuityStore,
    *,
    role: str,
    job: str,
    slot: str,
    now: datetime | None = None,
) -> bool:
    if is_scheduler_blocked_by_fallback(store, role, now=now):
        return True
    key = slot_ledger_key(role, job, slot)
    try:
        _ensure_slot_table(store)
        with store._lock:
            row = store._conn.execute("SELECT 1 FROM persona_slot_ledger WHERE slot_key = ?", (key,)).fetchone()
    except sqlite3.Error:
        log.error("persona slot ledger lookup failed for %s; failing closed", key, exc_info=True)
        return True
    return row is not None


def list_slot_ledger(
    store: BarbContinuityStore,
    *,
    role: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _ensure_slot_table(store)
    cap = max(1, min(int(limit), 500))
    with store._lock:
        if role is not None:
            rows = store._conn.execute(
                "SELECT * FROM persona_slot_ledger WHERE role = ? ORDER BY recorded_at DESC LIMIT ?",
                (validate_role_name(role), cap),
            ).fetchall()
        else:
            rows = store._conn.execute(
                "SELECT * FROM persona_slot_ledger ORDER BY recorded_at DESC LIMIT ?", (cap,)
            ).fetchall()
    return [_row_to_slot(r) for r in rows]


def append_fallback_spool(
    spool_path: Path,
    *,
    role: str,
    job: str,
    slot: str,
    epoch: int,
    holder: str = GROK_FALLBACK_HOLDER,
    board_claim_ref: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    key = slot_ledger_key(role, job, slot)
    valid_holder = validate_persona_holder(holder)
    if int(epoch) < 1:
        raise ValueError("epoch must be >= 1")
    record = {
        "key": key,
        "role": role.strip(),
        "job": job.strip(),
        "slot": slot.strip(),
        "holder": valid_holder,
        "epoch": int(epoch),
        "board_claim_ref": board_claim_ref.strip(),
        "recorded_at": _now_iso(now),
    }
    spool_path = Path(spool_path)
    spool_path.parent.mkdir(parents=True, exist_ok=True)
    with spool_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def _bump_epoch_floor(store: BarbContinuityStore, role: str, min_epoch: int, now_s: str) -> None:
    scope = persona_scope(role)
    with store._lock, store._conn:
        cur = store._conn.execute("SELECT * FROM barb_scoped_leases WHERE scope = ?", (scope,)).fetchone()
        if cur is None:
            store._conn.execute(
                "INSERT INTO barb_scoped_leases (scope, holder, persona, machine, epoch, "
                "acquired_at, heartbeat_at, expires_at, summary, last_turnover_id, released_at) "
                "VALUES (?, '', ?, '', ?, ?, ?, ?, 'ingested offline fallback spool', '', ?)",
                (scope, role, int(min_epoch), now_s, now_s, now_s, now_s),
            )
        elif int(cur["epoch"]) < int(min_epoch):
            store._conn.execute("UPDATE barb_scoped_leases SET epoch = ? WHERE scope = ?", (int(min_epoch), scope))


def ingest_fallback_spool(
    store: BarbContinuityStore,
    spool_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    spool_path = Path(spool_path)
    if not spool_path.exists():
        return {"ingested": 0, "duplicates": 0, "keys": []}
    raw_lines = [line.strip() for line in spool_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not raw_lines:
        return {"ingested": 0, "duplicates": 0, "keys": []}

    parsed: list[dict[str, Any]] = []
    for idx, line in enumerate(raw_lines):
        try:
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError("expected JSON object")
            role = validate_role_name(str(item.get("role") or ""))
            job = _validate_part(str(item.get("job") or ""), "job")
            slot = _validate_part(str(item.get("slot") or ""), "slot")
            holder = validate_persona_holder(str(item.get("holder") or GROK_FALLBACK_HOLDER))
            epoch = int(item["epoch"])
            if epoch < 1:
                raise ValueError("epoch must be >= 1")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid fallback spool entry at line {idx + 1}: {exc}") from exc
        parsed.append(
            {
                "role": role,
                "job": job,
                "slot": slot,
                "holder": holder,
                "epoch": epoch,
                "board_claim_ref": str(item.get("board_claim_ref") or ""),
            }
        )

    now_s = _now_iso(now)
    ingested = duplicates = 0
    keys: list[str] = []
    max_epoch_by_role: dict[str, int] = {}
    for entry in parsed:
        res = record_slot_claim(store, now=now, **entry)
        keys.append(res["key"])
        if res["recorded"]:
            ingested += 1
        else:
            duplicates += 1
        max_epoch_by_role[entry["role"]] = max(max_epoch_by_role.get(entry["role"], 0), entry["epoch"])

    for role, max_epoch in max_epoch_by_role.items():
        _bump_epoch_floor(store, role, max_epoch, now_s)

    spool_path.write_text("", encoding="utf-8")
    return {"ingested": ingested, "duplicates": duplicates, "keys": keys}
