"""Hermetic tests for kanban column projection (#4318)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from kanban_projection import (  # noqa: E402
    KanbanColumn,
    project_kanban_column,
    snapshot_from_mapping,
)

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kanban"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("fixture", "column"),
    [
        ("issue_open.json", KanbanColumn.ISSUE_OPEN),
        ("claimed.json", KanbanColumn.CLAIMED),
        ("pr_open.json", KanbanColumn.PR_OPEN),
        ("review.json", KanbanColumn.REVIEW),
        ("armed.json", KanbanColumn.ARMED),
        ("merged.json", KanbanColumn.MERGED),
        ("waiting_on_dieter.json", KanbanColumn.WAITING_ON_DIETER),
        ("unknown_claim_degraded.json", KanbanColumn.UNKNOWN),
        ("unknown_pr_degraded.json", KanbanColumn.UNKNOWN),
        ("stale_review.json", KanbanColumn.REVIEW),
    ],
)
def test_fixture_projects_to_column(fixture: str, column: KanbanColumn) -> None:
    card = project_kanban_column(snapshot_from_mapping(_load(fixture)))
    assert card.column == column


def test_armed_precedence_over_review_and_pr_open() -> None:
    card = project_kanban_column(snapshot_from_mapping(_load("armed.json")))
    assert KanbanColumn.ARMED in card.matched_columns
    assert KanbanColumn.REVIEW in card.matched_columns
    assert KanbanColumn.PR_OPEN in card.matched_columns
    assert card.column == KanbanColumn.ARMED


def test_waiting_on_dieter_precedence_over_armed() -> None:
    card = project_kanban_column(snapshot_from_mapping(_load("waiting_on_dieter.json")))
    assert card.column == KanbanColumn.WAITING_ON_DIETER
    assert KanbanColumn.ARMED in card.matched_columns


def test_stale_reviews_do_not_clear_review_column() -> None:
    card = project_kanban_column(snapshot_from_mapping(_load("stale_review.json")))
    assert card.column == KanbanColumn.REVIEW
    assert len(card.stale_reviews) == 1
    assert card.stale_reviews[0].commit_id == "old888"


def test_sources_include_issue_and_pull_request_links() -> None:
    card = project_kanban_column(snapshot_from_mapping(_load("pr_open.json")))
    kinds = {link.kind for link in card.sources}
    assert {"issue", "pull_request", "claim", "auto_merge", "review"}.issubset(kinds)
    issue_link = next(link for link in card.sources if link.kind == "issue")
    assert issue_link.url.endswith("/issues/102")


def test_unknown_includes_fail_closed_reasons() -> None:
    card = project_kanban_column(snapshot_from_mapping(_load("unknown_claim_degraded.json")))
    assert card.column == KanbanColumn.UNKNOWN
    assert any("claim" in reason for reason in card.reasons)
