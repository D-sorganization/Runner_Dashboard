"""Kanban slice 3/5: claims and staff-run ingestion (#4320)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from kanban_claims_ingestion import (  # noqa: E402
    claim_snapshot_from_check,
    ingest_claim_and_staff,
    staff_run_snapshot_for_issue,
)
from kanban_projection import (  # noqa: E402
    KanbanColumn,
    KanbanIssueSnapshot,
    KanbanWorkItemSnapshot,
    project_kanban_column,
)
from staff.store import RunRecord, RunStore  # noqa: E402

pytestmark = pytest.mark.unit

_ISSUE_URL = "https://github.com/D-sorganization/Runner_Dashboard/issues/4320"


def _open_issue_snapshot(number: int = 4320) -> KanbanIssueSnapshot:
    return KanbanIssueSnapshot(
        available=True,
        number=number,
        state="open",
        html_url=_ISSUE_URL,
    )


def test_live_lease_maps_to_claimed_column() -> None:
    status = {
        "available": True,
        "held": True,
        "agent": "cursor",
        "session": "cursor-48fc",
        "reason": "active lease",
        "expires_at": "2026-11-12T00:00:00+00:00",
    }
    claim = claim_snapshot_from_check(status, repo="Runner_Dashboard", issue=4320, issue_html_url=_ISSUE_URL)
    assert claim.available is True
    assert claim.degraded is False
    assert claim.held is True
    assert claim.agent == "cursor"
    assert claim.html_url is not None

    card = project_kanban_column(
        KanbanWorkItemSnapshot(
            owner="D-sorganization",
            repo="Runner_Dashboard",
            issue=_open_issue_snapshot(),
            claim=claim,
        )
    )
    assert card.column == KanbanColumn.CLAIMED
    claim_links = [link for link in card.sources if link.kind == "claim"]
    assert claim_links and claim_links[0].available is True


def test_expired_or_released_lease_is_not_held() -> None:
    status = {"available": True, "held": False, "agent": "", "reason": "free", "expires_at": None}
    claim = claim_snapshot_from_check(status, repo="Runner_Dashboard", issue=4320, issue_html_url=_ISSUE_URL)
    assert claim.held is False
    card = project_kanban_column(
        KanbanWorkItemSnapshot(
            owner="D-sorganization",
            repo="Runner_Dashboard",
            issue=_open_issue_snapshot(),
            claim=claim,
        )
    )
    assert card.column == KanbanColumn.ISSUE_OPEN


def test_claim_label_without_lease_is_degraded_unknown() -> None:
    status = {
        "available": True,
        "held": True,
        "agent": "codex",
        "reason": "claim label claim:codex (no active lease)",
    }
    claim = claim_snapshot_from_check(status, repo="Runner_Dashboard", issue=4320, issue_html_url=_ISSUE_URL)
    assert claim.available is False
    assert claim.degraded is True
    assert claim.held is False

    card = project_kanban_column(
        KanbanWorkItemSnapshot(
            owner="D-sorganization",
            repo="Runner_Dashboard",
            issue=_open_issue_snapshot(),
            claim=claim,
        )
    )
    assert card.column == KanbanColumn.UNKNOWN
    assert any("claim" in reason for reason in card.reasons)


def test_incomplete_coordination_history_is_degraded_unknown() -> None:
    status = {
        "available": False,
        "held": None,
        "reason": "check_agent_claim incomplete (board comment limit reached; claim check incomplete)",
    }
    claim = claim_snapshot_from_check(status, repo="Runner_Dashboard", issue=4320, issue_html_url=_ISSUE_URL)
    assert claim.available is False
    assert claim.degraded is True

    card = project_kanban_column(
        KanbanWorkItemSnapshot(
            owner="D-sorganization",
            repo="Runner_Dashboard",
            issue=_open_issue_snapshot(),
            claim=claim,
        )
    )
    assert card.column == KanbanColumn.UNKNOWN


def test_staff_run_snapshot_links_active_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    store.create_run(
        RunRecord(
            id="run-kanban-4320",
            role="issue-remediator",
            provider="claude",
            model="claude-sonnet",
            machine="hub",
            repo="Runner_Dashboard",
            target_kind="issue",
            target_ref="#4320",
            prompt="fix it",
            status="running",
        )
    )
    snap = staff_run_snapshot_for_issue("Runner_Dashboard", 4320, store=store)
    assert snap is not None
    assert snap.available is True
    assert snap.run_id == "run-kanban-4320"
    assert snap.status == "running"
    assert "/api/staff/runs/run-kanban-4320" in (snap.html_url or "")

    card = project_kanban_column(
        KanbanWorkItemSnapshot(
            owner="D-sorganization",
            repo="Runner_Dashboard",
            issue=_open_issue_snapshot(),
            claim=claim_snapshot_from_check(
                {"available": True, "held": True, "agent": "cursor", "reason": "active lease"},
                repo="Runner_Dashboard",
                issue=4320,
                issue_html_url=_ISSUE_URL,
            ),
            staff_run=snap,
        )
    )
    staff_links = [link for link in card.sources if link.kind == "staff_run"]
    assert staff_links and staff_links[0].url.endswith("run-kanban-4320")


def test_ingest_claim_and_staff_never_writes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []

    def fake_check(repo: str, issue: int) -> dict:
        calls.append((repo, issue))
        return {"available": True, "held": False, "agent": "", "reason": "free"}

    store = RunStore(tmp_path / "runs.db")

    def fail_write(*_a: object, **_k: object) -> None:
        raise AssertionError("kanban claims ingestion must not write leases or claims")

    monkeypatch.setattr("kanban_claims_ingestion.claims_check", fake_check)
    monkeypatch.setattr("coordination.claims.claim", fail_write)
    monkeypatch.setattr("coordination.claims.post_lease", fail_write)

    claim, staff = ingest_claim_and_staff(
        "Runner_Dashboard",
        4320,
        store=store,
        issue_html_url=_ISSUE_URL,
    )
    assert calls == [("Runner_Dashboard", 4320)]
    assert claim.held is False
    assert staff is None
