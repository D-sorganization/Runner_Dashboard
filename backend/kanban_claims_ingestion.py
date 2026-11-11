"""Read-only claims and staff-run ingestion for kanban cards (#4320, epic #4317).

Maps ``coordination.claims.check`` output and Staff Hub run rows into the
``KanbanClaimSnapshot`` / ``KanbanStaffRunSnapshot`` contracts from
``kanban_projection``. No writes to leases, claims, or runs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from coordination import claims as coordination_claims
from kanban_projection import KanbanClaimSnapshot, KanbanStaffRunSnapshot
from staff.store import ACTIVE_STATUSES, RunRecord, RunStore, get_store

ClaimCheck = Callable[[str, int], Mapping[str, Any]]

claims_check: ClaimCheck = coordination_claims.check

_BARE_LABEL_MARKERS = ("claim label", "(no active lease)")
_ACTIVE_LEASE = "active lease"
_BOARD_PRESENCE = "active board presence"


def _reason_lower(reason: str) -> str:
    return reason.casefold()


def _bare_claim_label_without_lease(reason: str) -> bool:
    rl = _reason_lower(reason)
    return all(marker in rl for marker in _BARE_LABEL_MARKERS)


def _active_lease_in_reason(reason: str) -> bool:
    """True when ``reason`` asserts an active lease, not RM's ``no active lease`` negation."""
    rl = _reason_lower(reason)
    start = 0
    while True:
        idx = rl.find(_ACTIVE_LEASE, start)
        if idx == -1:
            return False
        if not rl[:idx].rstrip().endswith("no"):
            return True
        start = idx + len(_ACTIVE_LEASE)
    return False


def _claim_source_url(*, repo: str, issue: int, issue_html_url: str | None, dashboard_origin: str) -> str:
    origin = (dashboard_origin or "").rstrip("/")
    if origin:
        return f"{origin}/api/coordination/claims?repo={repo}&issue={issue}"
    if issue_html_url:
        return issue_html_url
    return f"https://github.com/D-sorganization/{repo}/issues/{issue}"


def _staff_run_url(run_id: str, dashboard_origin: str) -> str:
    origin = (dashboard_origin or "").rstrip("/")
    path = f"/api/staff/runs/{run_id}"
    return f"{origin}{path}" if origin else path


def claim_snapshot_from_check(
    status: Mapping[str, Any],
    *,
    repo: str,
    issue: int,
    issue_html_url: str | None = None,
    dashboard_origin: str = "",
) -> KanbanClaimSnapshot:
    """Map one ``claims.check`` payload to ``KanbanClaimSnapshot`` (pure, no I/O)."""
    url = _claim_source_url(repo=repo, issue=issue, issue_html_url=issue_html_url, dashboard_origin=dashboard_origin)
    agent = status.get("agent") or None
    if agent == "":
        agent = None

    if not status.get("available", False):
        return KanbanClaimSnapshot(
            available=False,
            held=False,
            agent=agent,
            html_url=url,
            degraded=True,
        )

    reason = str(status.get("reason") or "")
    if status.get("held") and _bare_claim_label_without_lease(reason):
        return KanbanClaimSnapshot(
            available=False,
            held=False,
            agent=agent,
            html_url=url,
            degraded=True,
        )

    held = bool(status.get("held")) and (_active_lease_in_reason(reason) or _BOARD_PRESENCE in _reason_lower(reason))
    return KanbanClaimSnapshot(
        available=True,
        held=held,
        agent=agent if held else None,
        html_url=url,
        degraded=False,
    )


def _runs_for_issue(store: RunStore, repo: str, issue: int, *, scan_limit: int = 500) -> list[RunRecord]:
    """Runs targeting ``repo``#``issue`` via the existing store list API (read-only)."""
    wanted = str(int(issue))
    matches = [
        run
        for run in store.list_runs(limit=scan_limit, logical_only=False)
        if run.repo == repo
        and run.target_kind == "issue"
        and run.target_ref.lstrip("#") == wanted
    ]
    return sorted(matches, key=lambda r: r.created_at)


def _pick_staff_run(runs: list[RunRecord]) -> RunRecord | None:
    if not runs:
        return None
    active = [r for r in runs if r.status in ACTIVE_STATUSES]
    pool = active or runs
    return max(pool, key=lambda r: r.created_at)


def staff_run_snapshot_for_issue(
    repo: str,
    issue: int,
    *,
    store: RunStore | None = None,
    dashboard_origin: str = "",
) -> KanbanStaffRunSnapshot | None:
    """Best-effort staff run linked to ``repo``#``issue`` (read-only)."""
    db = store if store is not None else get_store()
    runs = _runs_for_issue(db, repo, issue)
    picked = _pick_staff_run(runs)
    if picked is None:
        return None
    return KanbanStaffRunSnapshot(
        available=True,
        run_id=picked.id,
        status=picked.status,
        html_url=_staff_run_url(picked.id, dashboard_origin),
        degraded=False,
    )


def ingest_claim_and_staff(
    repo: str,
    issue: int,
    *,
    store: RunStore | None = None,
    issue_html_url: str | None = None,
    dashboard_origin: str = "",
    check: ClaimCheck | None = None,
) -> tuple[KanbanClaimSnapshot, KanbanStaffRunSnapshot | None]:
    """Read claim state and optional staff run for one issue (no writes)."""
    reader = check if check is not None else claims_check
    status = reader(repo, issue)
    claim = claim_snapshot_from_check(
        status,
        repo=repo,
        issue=issue,
        issue_html_url=issue_html_url,
        dashboard_origin=dashboard_origin,
    )
    staff = staff_run_snapshot_for_issue(repo, issue, store=store, dashboard_origin=dashboard_origin)
    return claim, staff
