"""Task inventory and CLI <-> Desktop App handoff protocol for Multi-Barb continuity.

Split out of ``barb_continuity`` to keep that module under the line cap. The store
class there mixes in :class:`BarbTaskInventoryMixin`; names are re-exported.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

TASK_SURFACES: tuple[str, ...] = ("headless_cli", "desktop_app", "hybrid_handoff")
HANDOFF_STATES: tuple[str, ...] = ("none", "needs_desktop", "desktop_claimed", "resume_ready", "completed")

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


class BarbTaskInventoryMixin:
    """Task inventory methods; expects ``_lock`` and ``_conn`` from the host store."""

    _lock: Any
    _conn: Any

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
        clauses: list[str] = []
        params: list[Any] = []
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
