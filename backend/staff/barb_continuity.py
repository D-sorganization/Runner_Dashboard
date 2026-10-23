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

BARB_SCOPES: tuple[str, ...] = tuple(
    (
        "barb:front-door barb:ci-shepherd barb:pr-queue barb:dispatch-coordinator barb:issue-triage barb:night-watch"
    ).split()
)
BARB_PERSONAS: tuple[str, ...] = tuple("grok-barb codex-barb claude-barb agy-barb cursor-barb rd-barb".split())
TASK_SURFACES: tuple[str, ...] = ("headless_cli", "desktop_app", "hybrid_handoff")
HANDOFF_STATES: tuple[str, ...] = ("none", "needs_desktop", "desktop_claimed", "resume_ready", "completed")
DEFAULT_LEASE_TTL_SECONDS = 1800
_DESKTOP_KEYWORDS: tuple[str, ...] = tuple(
    (
        "browser,gui,oauth,sso,sign-in,login,re-authenticate,visual,screenshot,"
        "computer use,desktop app,interactive,window,click,dialog,2fa,mfa"
    ).split(",")
)
_CLI_KEYWORDS: tuple[str, ...] = tuple(
    "pytest,ruff,unit test,refactor,lint,typecheck,worktree,git branch,sql,migration,endpoint,docstring".split(",")
)


def _now_iso(now: datetime | None = None) -> str:
    dt = (now or datetime.now(UTC)) if (now is None or now.tzinfo) else now.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify_task_surface(
    title: str,
    description: str = "",
    *,
    requires_browser_auth: bool = False,
    requires_gui: bool = False,
    requires_code_edit: bool = True,
) -> dict[str, Any]:
    """Classify whether a task belongs on Headless CLI, Desktop App, or Hybrid Handoff."""
    text = f"{title} {description}".lower()
    matched_desktop = [kw for kw in _DESKTOP_KEYWORDS if kw in text]
    matched_cli = [kw for kw in _CLI_KEYWORDS if kw in text]
    needs_desktop = requires_browser_auth or requires_gui or bool(matched_desktop)
    needs_cli = requires_code_edit or bool(matched_cli)
    sig = ", ".join(matched_desktop or ["gui/auth"])
    if needs_desktop and needs_cli:
        surface, handoff_req = "hybrid_handoff", True
        apps, clis = (
            ["Grok Bot Desktop", "Claude Desktop / Computer Use", "Cursor IDE"],
            ["claude", "codex", "antigravity"],
        )
        rationale = f"Hybrid CLI <-> Desktop workflow: desktop step ({sig}) combined with code/test execution."
    elif needs_desktop:
        surface, handoff_req = "desktop_app", True
        apps, clis = ["Grok Bot Desktop", "Claude Desktop / Computer Use", "Cursor IDE"], []
        rationale = f"Requires interactive Desktop/GUI/Browser capabilities ({sig})."
    else:
        surface, handoff_req = "headless_cli", False
        apps, clis = [], ["claude", "codex", "antigravity"]
        rationale = "Pure headless CLI code, test, or repository automation task."
    return {
        "surface": surface,
        "requires_desktop": needs_desktop,
        "handoff_required": handoff_req,
        "recommended_desktop_apps": apps,
        "recommended_cli_providers": clis,
        "matched_desktop_signals": matched_desktop,
        "matched_cli_signals": matched_cli,
        "rationale": rationale,
    }


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


class BarbContinuityStore:
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
                last_to = str(cur["last_turnover_id"]) if (same_holder and not is_expired) else ""
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
        d = {k: (int(row[k]) if k == "epoch" else str(row[k] or "")) for k in row.keys()}
        d["active"] = d["expires_at"] > now_s
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

    def upsert_task(
        self,
        *,
        task_id: str | None = None,
        title: str,
        scope: str = "barb:front-door",
        status: str = "pending",
        owner_persona: str = "",
        machine: str = "",
        repo: str = "",
        issue_or_pr: str = "",
        priority: str = "P1",
        surface: str | None = None,
        handoff_state: str | None = None,
        handoff_request: str | None = None,
        handoff_resolution: str | None = None,
        artifact_ref: str | None = None,
        next_action: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Create or update an active task inventory item and its CLI<->Desktop handoff state."""
        tid = (task_id or "").strip() or f"task-{uuid.uuid4().hex[:8]}"
        now_s = _now_iso(now)
        auto_cls = classify_task_surface(title, f"{next_action} {handoff_request or ''}")
        resolved_surface = surface if surface in TASK_SURFACES else auto_cls["surface"]
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM barb_task_inventory WHERE task_id = ?", (tid,)).fetchone()
            if cur is None:
                h_state = (
                    handoff_state
                    if handoff_state in HANDOFF_STATES
                    else ("needs_desktop" if handoff_request else "none")
                )
                self._conn.execute(
                    "INSERT INTO barb_task_inventory (task_id, title, scope, status, owner_persona, machine, "
                    "repo, issue_or_pr, priority, surface, handoff_state, fencing_epoch, handoff_request, "
                    "handoff_resolution, artifact_ref, next_action, updated_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                    (
                        tid,
                        title,
                        scope,
                        status,
                        owner_persona,
                        machine,
                        repo,
                        issue_or_pr,
                        priority,
                        resolved_surface,
                        h_state,
                        handoff_request or "",
                        handoff_resolution or "",
                        artifact_ref or "",
                        next_action,
                        now_s,
                        now_s,
                    ),
                )
            else:
                h_state = handoff_state if handoff_state in HANDOFF_STATES else str(cur["handoff_state"])
                next_epoch = int(cur["fencing_epoch"]) + (1 if h_state != str(cur["handoff_state"]) else 0)
                self._conn.execute(
                    "UPDATE barb_task_inventory SET title=?, scope=?, status=?, owner_persona=?, machine=?, "
                    "repo=?, issue_or_pr=?, priority=?, surface=?, handoff_state=?, fencing_epoch=?, "
                    "handoff_request=?, handoff_resolution=?, artifact_ref=?, next_action=?, updated_at=? "
                    "WHERE task_id=?",
                    (
                        title or str(cur["title"]),
                        scope or str(cur["scope"]),
                        status or str(cur["status"]),
                        owner_persona if owner_persona != "" else str(cur["owner_persona"]),
                        machine if machine != "" else str(cur["machine"]),
                        repo if repo != "" else str(cur["repo"]),
                        issue_or_pr if issue_or_pr != "" else str(cur["issue_or_pr"]),
                        priority or str(cur["priority"]),
                        surface if surface in TASK_SURFACES else str(cur["surface"]),
                        h_state,
                        next_epoch,
                        handoff_request if handoff_request is not None else str(cur["handoff_request"]),
                        handoff_resolution if handoff_resolution is not None else str(cur["handoff_resolution"]),
                        artifact_ref if artifact_ref is not None else str(cur["artifact_ref"]),
                        next_action if next_action != "" else str(cur["next_action"]),
                        now_s,
                        tid,
                    ),
                )
            row = self._conn.execute("SELECT * FROM barb_task_inventory WHERE task_id = ?", (tid,)).fetchone()
        return self._row_to_task(row)

    def transition_handoff(
        self,
        *,
        task_id: str,
        target_state: str,
        actor_persona: str,
        expected_epoch: int | None = None,
        handoff_request: str | None = None,
        handoff_resolution: str | None = None,
        artifact_ref: str | None = None,
        next_action: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Transition a task between CLI and Desktop App with fencing-token verification."""
        if target_state not in HANDOFF_STATES:
            raise ValueError(f"Invalid handoff target_state '{target_state}'. Expected one of {HANDOFF_STATES}.")
        now_s = _now_iso(now)
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM barb_task_inventory WHERE task_id = ?", (task_id,)).fetchone()
            if cur is None:
                raise KeyError(f"Task '{task_id}' not found")
            cur_epoch = int(cur["fencing_epoch"])
            if expected_epoch is not None and expected_epoch != cur_epoch:
                return {
                    "transitioned": False,
                    "reason": "stale_fencing_epoch",
                    "current_epoch": cur_epoch,
                    "task": self._row_to_task(cur),
                }
            next_epoch = cur_epoch + 1
            new_status = str(cur["status"])
            if target_state == "needs_desktop":
                new_status = "blocked_desktop"
            elif target_state == "desktop_claimed":
                new_status = "in_desktop"
            elif target_state == "resume_ready":
                new_status = "in_progress"
            elif target_state == "completed":
                new_status = "completed"
            self._conn.execute(
                "UPDATE barb_task_inventory SET handoff_state=?, status=?, owner_persona=?, fencing_epoch=?, "
                "handoff_request=?, handoff_resolution=?, artifact_ref=?, next_action=?, updated_at=? WHERE task_id=?",
                (
                    target_state,
                    new_status,
                    actor_persona or str(cur["owner_persona"]),
                    next_epoch,
                    handoff_request if handoff_request is not None else str(cur["handoff_request"]),
                    handoff_resolution if handoff_resolution is not None else str(cur["handoff_resolution"]),
                    artifact_ref if artifact_ref is not None else str(cur["artifact_ref"]),
                    next_action if next_action is not None else str(cur["next_action"]),
                    now_s,
                    task_id,
                ),
            )
            row = self._conn.execute("SELECT * FROM barb_task_inventory WHERE task_id = ?", (task_id,)).fetchone()
        return {"transitioned": True, "task": self._row_to_task(row)}

    def list_tasks(
        self, *, status: str | None = None, scope: str | None = None, handoff_state: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if handoff_state:
            clauses.append("handoff_state = ?")
            params.append(handoff_state)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM barb_task_inventory {where} ORDER BY priority ASC, updated_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        return {k: (int(row[k]) if k == "fencing_epoch" else str(row[k] or "")) for k in row.keys()}

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
