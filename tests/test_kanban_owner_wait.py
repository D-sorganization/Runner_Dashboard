"""Unit tests for kanban owner-wait ingestion (#4321)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from kanban_owner_wait import (  # noqa: E402
    build_owner_wait_snapshot,
    holds_sync_state,
)
from kanban_projection import KanbanColumn, project_kanban_column, snapshot_from_mapping  # noqa: E402
from staff.holds import Hold, HoldsList  # noqa: E402

pytestmark = pytest.mark.unit


def _schedule_hold(hold_id: str, text: str, *, active: bool = True) -> Hold:
    return Hold(id=hold_id, text=text, applies_to=["repo:Runner_Dashboard"], active=active, kind="schedule")


def test_active_hold_sets_owner_wait_and_waiting_column(tmp_path: Path) -> None:
    holds = HoldsList(path=tmp_path / "staff_holds.json")
    holds.replace([_schedule_hold("rd-2657", "RD #2657: no deploy").to_dict()])

    snap = build_owner_wait_snapshot(
        issue_labels=["hold:rd-2657"],
        scoped_holds=holds.load(),
    )
    assert snap.waiting is True
    assert any("hold:" in reason for reason in snap.reasons)

    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "kanban" / "issue_open.json").read_text(encoding="utf-8")
    )
    payload["issue"]["labels"] = ["hold:rd-2657"]
    payload["owner_wait"] = {
        "waiting": snap.waiting,
        "reasons": list(snap.reasons),
        "html_url": snap.html_url,
    }
    card = project_kanban_column(snapshot_from_mapping(payload))
    assert card.column == KanbanColumn.WAITING_ON_DIETER


def test_lifted_hold_does_not_wait(tmp_path: Path) -> None:
    holds = HoldsList(path=tmp_path / "staff_holds.json")
    holds.replace([_schedule_hold("rd-2657", "lifted", active=False).to_dict()])

    snap = build_owner_wait_snapshot(
        issue_labels=[],
        scoped_holds=[h for h in holds.load() if h.active and h.kind == "schedule"],
    )
    assert snap.waiting is False


def test_pending_approval_reason_projects_to_waiting_column() -> None:
    snap = build_owner_wait_snapshot(
        issue_labels=[],
        scoped_holds=[],
        approval_reason="pending staff.approve (staff.dispatch)",
        detail_url="https://github.com/D-sorganization/Runner_Dashboard/issues/9",
    )
    assert snap.waiting is True
    payload = {
        "repo": "Runner_Dashboard",
        "issue": {
            "number": 9,
            "state": "open",
            "html_url": "https://github.com/D-sorganization/Runner_Dashboard/issues/9",
        },
        "claim": {"held": False},
        "owner_wait": {"waiting": True, "reasons": list(snap.reasons), "html_url": snap.html_url},
    }
    card = project_kanban_column(snapshot_from_mapping(payload))
    assert card.column == KanbanColumn.WAITING_ON_DIETER


def test_unreadable_holds_list_is_not_readable(tmp_path: Path) -> None:
    path = tmp_path / "staff_holds.json"
    path.write_text("{not-json", encoding="utf-8")
    sync = holds_sync_state(HoldsList(path=path))
    assert sync.readable is False
