"""Multi-Barb persona continuity, scoped sub-role checkout, turnover ledger,
active task inventory, and CLI <-> Desktop App task routing & handoff protocol.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff.barb_task_inventory import (  # noqa: F401 - re-exported for existing importers
    HANDOFF_STATES,
    TASK_SURFACES,
    BarbTaskInventoryMixin,
    _now_iso,
    classify_task_surface,
)

BARB_SCOPES: tuple[str, ...] = tuple(
    (
        "barb:front-door barb:ci-shepherd barb:pr-queue barb:dispatch-coordinator barb:issue-triage barb:night-watch"
    ).split()
)
BARB_PERSONAS: tuple[str, ...] = tuple("grok-barb codex-barb claude-barb agy-barb cursor-barb rd-barb".split())
DEFAULT_LEASE_TTL_SECONDS = 1800


_SCHEMA = """
CREATE TABLE IF NOT EXISTS barb_scoped_leases (
    scope TEXT PRIMARY KEY, holder TEXT NOT NULL, persona TEXT NOT NULL DEFAULT '',
    machine TEXT NOT NULL DEFAULT '', epoch INTEGER NOT NULL DEFAULT 1,
    acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '', last_turnover_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS barb_turnover_log (
    id TEXT PRIMARY KEY, scope TEXT NOT NULL, author TEXT NOT NULL,
    persona TEXT NOT NULL DEFAULT '', machine TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, summary TEXT NOT NULL,
    completed_json TEXT NOT NULL DEFAULT '[]', in_progress_json TEXT NOT NULL DEFAULT '[]',
    blockers_json TEXT NOT NULL DEFAULT '[]', next_actions_json TEXT NOT NULL DEFAULT '[]',
    lease_epoch INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_barb_turnover_created ON barb_turnover_log(created_at DESC);
CREATE TABLE IF NOT EXISTS barb_task_inventory (
    task_id TEXT PRIMARY KEY, title TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'barb:front-door',
    status TEXT NOT NULL DEFAULT 'pending', owner_persona TEXT NOT NULL DEFAULT '',
    machine TEXT NOT NULL DEFAULT '', repo TEXT NOT NULL DEFAULT '',
    issue_or_pr TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'P1',
    surface TEXT NOT NULL DEFAULT 'headless_cli', handoff_state TEXT NOT NULL DEFAULT 'none',
    fencing_epoch INTEGER NOT NULL DEFAULT 1, handoff_request TEXT NOT NULL DEFAULT '',
    handoff_resolution TEXT NOT NULL DEFAULT '', artifact_ref TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_barb_tasks_status ON barb_task_inventory(status, priority);
"""


class BarbContinuityStore(BarbTaskInventoryMixin):
    """Thread-safe SQLite store for Multi-Barb leases, turnover log, and task inventory."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def checkout_lease(
        self,
        *,
        scope: str,
        holder: str,
        persona: str = "",
        machine: str = "",
        summary: str = "",
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
        force: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Acquire or renew a scoped Barb sub-role lease with monotonic epoch."""
        norm_scope = scope.strip()
        if norm_scope not in BARB_SCOPES and not norm_scope.startswith("barb:"):
            raise ValueError(f"Invalid Barb scope '{scope}'. Expected one of {BARB_SCOPES} or 'barb:<subrole>'.")
        if not holder.strip():
            raise ValueError("holder must not be empty")
        dt = now if now is not None else datetime.now(UTC)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now_s = _now_iso(dt)
        exp_dt = datetime.fromtimestamp(dt.timestamp() + max(60, min(ttl_seconds, 14400)), tz=UTC)
        exp_s = _now_iso(exp_dt)
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM barb_scoped_leases WHERE scope = ?", (norm_scope,)).fetchone()
            if cur is not None:
                is_expired = str(cur["expires_at"]) <= now_s
                same_holder = str(cur["holder"]) == holder
                if not is_expired and not same_holder and not force:
                    return {"acquired": False, "conflict": True, "lease": self._row_to_lease(cur, now_s)}
                next_epoch = int(cur["epoch"]) + (0 if same_holder and not is_expired else 1)
                acquired_at = str(cur["acquired_at"]) if (same_holder and not is_expired) else now_s
                # Same live holder renews; an explicit takeover inherits the previous seat's
                # last turnover so the new seat sees its next_actions (seat continuity).
                keep_turnover = (same_holder and not is_expired) or force
                last_to = str(cur["last_turnover_id"]) if keep_turnover else ""
                self._conn.execute(
                    "UPDATE barb_scoped_leases SET holder=?, persona=?, machine=?, epoch=?, "
                    "acquired_at=?, heartbeat_at=?, expires_at=?, summary=?, last_turnover_id=? WHERE scope=?",
                    (holder, persona, machine, next_epoch, acquired_at, now_s, exp_s, summary, last_to, norm_scope),
                )
            else:
                self._conn.execute(
                    "INSERT INTO barb_scoped_leases (scope, holder, persona, machine, epoch, "
                    "acquired_at, heartbeat_at, expires_at, summary, last_turnover_id) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, '')",
                    (norm_scope, holder, persona, machine, now_s, now_s, exp_s, summary),
                )
            row = self._conn.execute("SELECT * FROM barb_scoped_leases WHERE scope = ?", (norm_scope,)).fetchone()
        return {"acquired": True, "conflict": False, "lease": self._row_to_lease(row, now_s)}

    def heartbeat_lease(
        self,
        *,
        scope: str,
        holder: str,
        epoch: int,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Extend a live lease owned by ``holder`` at ``epoch`` (no epoch bump)."""
        dt = now if now is not None else datetime.now(UTC)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now_s = _now_iso(dt)
        exp_s = _now_iso(datetime.fromtimestamp(dt.timestamp() + max(60, min(ttl_seconds, 14400)), tz=UTC))
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM barb_scoped_leases WHERE scope = ?", (scope,)).fetchone()
            if cur is None:
                return {"renewed": False, "reason": "not_found"}
            if str(cur["holder"]) != holder:
                return {"renewed": False, "reason": "holder_mismatch", "lease": self._row_to_lease(cur, now_s)}
            if str(cur["expires_at"]) <= now_s:
                return {"renewed": False, "reason": "expired", "lease": self._row_to_lease(cur, now_s)}
            if int(cur["epoch"]) != epoch:
                return {"renewed": False, "reason": "stale_epoch", "lease": self._row_to_lease(cur, now_s)}
            self._conn.execute(
                "UPDATE barb_scoped_leases SET heartbeat_at=?, expires_at=? WHERE scope=?", (now_s, exp_s, scope)
            )
            row = self._conn.execute("SELECT * FROM barb_scoped_leases WHERE scope = ?", (scope,)).fetchone()
        return {"renewed": True, "lease": self._row_to_lease(row, now_s)}

    def release_lease(
        self, *, scope: str, holder: str, require_turnover: bool = True, now: datetime | None = None
    ) -> dict[str, Any]:
        """Release a scoped Barb lease, enforcing shift-end turnover compliance."""
        now_s = _now_iso(now)
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM barb_scoped_leases WHERE scope = ?", (scope,)).fetchone()
            if cur is None:
                return {"released": False, "reason": "not_found", "turnover_compliant": True}
            if str(cur["holder"]) != holder:
                return {"released": False, "reason": "holder_mismatch", "lease": self._row_to_lease(cur, now_s)}
            has_turnover = bool(str(cur["last_turnover_id"]).strip())
            if require_turnover and not has_turnover:
                return {
                    "released": False,
                    "reason": "turnover_required_before_release",
                    "turnover_compliant": False,
                    "lease": self._row_to_lease(cur, now_s),
                }
            self._conn.execute("DELETE FROM barb_scoped_leases WHERE scope = ?", (scope,))
        return {"released": True, "turnover_compliant": has_turnover, "scope": scope, "holder": holder}

    def list_leases(self, *, include_expired: bool = False, now: datetime | None = None) -> list[dict[str, Any]]:
        now_s = _now_iso(now)
        with self._lock:
            rows = self._conn.execute("SELECT * FROM barb_scoped_leases ORDER BY scope ASC").fetchall()
        leases = [self._row_to_lease(r, now_s) for r in rows]
        return leases if include_expired else [x for x in leases if x["active"]]

    @staticmethod
    def _row_to_lease(row: sqlite3.Row, now_s: str) -> dict[str, Any]:
        d: dict[str, Any] = {k: (int(row[k]) if k == "epoch" else str(row[k] or "")) for k in row.keys()}
        d["active"] = str(d["expires_at"]) > now_s
        d["turnover_compliant"] = bool(d["last_turnover_id"])
        return d

    def append_turnover(
        self,
        *,
        scope: str,
        author: str,
        summary: str,
        persona: str = "",
        machine: str = "",
        completed: list[str] | None = None,
        in_progress: list[str] | None = None,
        blockers: list[str] | None = None,
        next_actions: list[str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Append a shift turnover entry and mark any matching active lease compliant."""
        if not summary.strip():
            raise ValueError("Turnover summary must not be empty")
        now_s = _now_iso(now)
        entry_id = f"to-{uuid.uuid4().hex[:10]}"
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM barb_scoped_leases WHERE scope = ?", (scope,)).fetchone()
            epoch = int(cur["epoch"]) if cur is not None else 0
            self._conn.execute(
                "INSERT INTO barb_turnover_log (id, scope, author, persona, machine, created_at, summary, "
                "completed_json, in_progress_json, blockers_json, next_actions_json, lease_epoch) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_id,
                    scope,
                    author,
                    persona,
                    machine,
                    now_s,
                    summary.strip(),
                    json.dumps(completed or []),
                    json.dumps(in_progress or []),
                    json.dumps(blockers or []),
                    json.dumps(next_actions or []),
                    epoch,
                ),
            )
            if cur is not None and str(cur["holder"]) == author:
                self._conn.execute(
                    "UPDATE barb_scoped_leases SET last_turnover_id = ?, heartbeat_at = ? WHERE scope = ?",
                    (entry_id, now_s, scope),
                )
            row = self._conn.execute("SELECT * FROM barb_turnover_log WHERE id = ?", (entry_id,)).fetchone()
        return self._row_to_turnover(row)

    def list_turnover(self, *, scope: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        cap = max(1, min(limit, 200))
        with self._lock:
            if scope:
                rows = self._conn.execute(
                    "SELECT * FROM barb_turnover_log WHERE scope = ? ORDER BY created_at DESC LIMIT ?", (scope, cap)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM barb_turnover_log ORDER BY created_at DESC LIMIT ?", (cap,)
                ).fetchall()
        return [self._row_to_turnover(r) for r in rows]

    @staticmethod
    def _row_to_turnover(row: sqlite3.Row) -> dict[str, Any]:
        d: dict[str, Any] = {
            k: str(row[k] or "") for k in ("id", "scope", "author", "persona", "machine", "created_at", "summary")
        }
        d.update(
            {k: json.loads(row[f"{k}_json"] or "[]") for k in ("completed", "in_progress", "blockers", "next_actions")}
        )
        d["lease_epoch"] = int(row["lease_epoch"])
        return d

    def continuity_snapshot(self, *, turnover_limit: int = 15, now: datetime | None = None) -> dict[str, Any]:
        """Return full multi-Barb continuity snapshot for seamless agent takeover."""
        leases = self.list_leases(include_expired=True, now=now)
        turnovers, tasks = self.list_turnover(limit=turnover_limit), self.list_tasks(limit=100)
        active = [x for x in leases if x["active"]]
        return {
            "supported_scopes": list(BARB_SCOPES),
            "supported_personas": list(BARB_PERSONAS),
            "active_leases": active,
            "all_leases": leases,
            "turnover_compliance": {
                "active_scopes": len(active),
                "compliant_scopes": sum(1 for x in active if x["turnover_compliant"]),
                "pending_turnover_scopes": [x["scope"] for x in active if not x["turnover_compliant"]],
                "total_turnover_entries": len(turnovers),
            },
            "recent_turnover": turnovers,
            "active_tasks": [t for t in tasks if t["status"] != "completed"],
            "needs_desktop_handoffs": [t for t in tasks if t["handoff_state"] == "needs_desktop"],
            "resume_ready_handoffs": [t for t in tasks if t["handoff_state"] == "resume_ready"],
        }


_DEFAULT_STORE: BarbContinuityStore | None = None
_DEFAULT_LOCK = threading.Lock()


def get_barb_continuity_store(db_path: Path | None = None) -> BarbContinuityStore:
    """Return process-wide BarbContinuityStore singleton (or custom db_path instance)."""
    global _DEFAULT_STORE
    if db_path is not None:
        return BarbContinuityStore(db_path)
    with _DEFAULT_LOCK:
        if _DEFAULT_STORE is None:
            env_path = os.environ.get("BARB_CONTINUITY_DB", "").strip()
            default_p = (
                Path(env_path)
                if env_path
                else Path(__file__).resolve().parents[2] / "state" / "barb_continuity.sqlite3"
            )
            _DEFAULT_STORE = BarbContinuityStore(default_p)
        return _DEFAULT_STORE
