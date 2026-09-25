"""Regression tests for Barb routing evaluation and dataset (SC-C7, Issue #1340)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.router import BarbRouter
from staff.router_models import RoutingFeedbackRecord
from staff.work_items import WorkItemStore

from tests.staff.routing_eval.dataset import ROUTING_EVAL_CASES
from tests.staff.routing_eval.engine import (
    load_candidate_cases_from_feedback,
    post_eval_summary_to_board,
    run_evaluation,
)
from tests.staff.routing_eval.models import EvalSummary


@pytest.fixture(autouse=True)
def clean_conversations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    yield
    reset_conversation_store()


def test_eval_dataset_integrity() -> None:
    """Validate that the routing evaluation dataset satisfies SC-C7 contract."""
    # 1. At least 60 representative requests
    assert len(ROUTING_EVAL_CASES) >= 60, f"Expected >= 60 cases, got {len(ROUTING_EVAL_CASES)}"

    # 2. Check all required categories are present
    categories = {case.category for case in ROUTING_EVAL_CASES}
    required_categories = {
        "maintenance",
        "librarian",
        "board",
        "project_steward",
        "fleet_critic",
        "remediation",
        "code",
        "barb",
        "ambiguous",
    }
    for req_cat in required_categories:
        assert req_cat in categories, f"Missing required category '{req_cat}' in eval dataset"

    # 3. Check uniqueness of case IDs and non-empty prompts
    case_ids = [c.id for c in ROUTING_EVAL_CASES]
    assert len(case_ids) == len(set(case_ids)), "Duplicate case IDs detected in evaluation dataset"

    valid_outcomes = {"route", "answer_myself", "clarify"}
    for case in ROUTING_EVAL_CASES:
        assert case.prompt.strip(), f"Case {case.id} has empty prompt"
        assert case.expected_outcome in valid_outcomes, f"Case {case.id} has invalid outcome {case.expected_outcome}"
        if case.expected_outcome == "clarify":
            assert case.expected_role is None, f"Clarify case {case.id} should have expected_role=None"
        else:
            assert case.expected_role is not None, f"Route case {case.id} missing expected_role"


def test_deterministic_pre_router_all_pass() -> None:
    """CI gate: Verify that all deterministic pre-router cases pass 100%."""
    deterministic_cases = [c for c in ROUTING_EVAL_CASES if c.deterministic]
    assert len(deterministic_cases) >= 50, f"Expected >= 50 deterministic cases, got {len(deterministic_cases)}"

    summary = run_evaluation(deterministic_cases, deterministic_only=True)
    assert summary.accuracy == 1.0, (
        f"Deterministic pre-router accuracy must be 100% in CI. "
        f"Failed cases ({summary.failed_cases}): {summary.failures}"
    )
    assert summary.passed_cases == len(deterministic_cases)
    assert summary.failed_cases == 0


def test_ambiguous_prompts_trigger_clarification() -> None:
    """Verify that ambiguous prompts trigger clarifying questions rather than guessing."""
    ambiguous_cases = [c for c in ROUTING_EVAL_CASES if c.category == "ambiguous"]
    assert len(ambiguous_cases) >= 5

    router = BarbRouter()
    summary = run_evaluation(ambiguous_cases, router=router, deterministic_only=False)
    assert summary.accuracy == 1.0, f"Ambiguous cases failed clarification: {summary.failures}"


def test_load_candidate_cases_from_feedback() -> None:
    """Test ingestion of routing overrides from SC-C2 into candidate evaluation cases."""
    feedbacks = [
        RoutingFeedbackRecord(
            id="fb-12345678",
            original_role="librarian",
            override_role="pragmatic-programmer",
            prompt="Refactor the markdown parser to use typed AST nodes",
            reason="Contained code refactoring",
            overridden_by="dieter",
            created_at="2026-09-25T12:00:00Z",
        ),
        RoutingFeedbackRecord(
            id="fb-87654321",
            original_role="ad-hoc",
            override_role="maintenance",
            prompt="Restart runner runner-9 on host-0",
            reason="Runner management",
            overridden_by="dieter",
            created_at="2026-09-25T12:05:00Z",
        ),
    ]

    candidates = load_candidate_cases_from_feedback(feedbacks)
    assert len(candidates) == 2
    assert candidates[0].id == "feedback-fb-12345"
    assert candidates[0].expected_role == "pragmatic-programmer"
    assert candidates[0].category == "feedback_override"
    assert candidates[1].expected_role == "maintenance"


def test_post_eval_summary_to_board() -> None:
    """Acceptance criterion: Verify evaluation summary publishes to the Board as a proposal."""
    store = get_conversation_store()
    w_store = WorkItemStore(store.path)

    summary = EvalSummary(
        total_cases=72,
        passed_cases=70,
        failed_cases=2,
        accuracy=70 / 72,
        deterministic_total=64,
        deterministic_passed=64,
        deterministic_accuracy=1.0,
        category_metrics={
            "maintenance": {"total": 8, "passed": 8, "accuracy": 1.0},
            "librarian": {"total": 8, "passed": 8, "accuracy": 1.0},
        },
        timestamp="2026-09-25T15:00:00Z",
    )

    item = post_eval_summary_to_board(summary, store=store, work_item_store=w_store)
    assert item is not None
    assert item.owner_role == "board_secretary"
    assert "[Board Proposal]" in item.title
    assert "97.2% Accuracy" in item.title

    # Verify queryable from work item store
    items = w_store.list_work_items(owner_role="board_secretary")
    assert len(items) >= 1
    assert items[0].id == item.id


def test_eval_summary_markdown_rendering() -> None:
    """Test formatting of evaluation summary into markdown report."""
    summary = EvalSummary(
        total_cases=10,
        passed_cases=9,
        failed_cases=1,
        accuracy=0.9,
        deterministic_total=8,
        deterministic_passed=8,
        deterministic_accuracy=1.0,
        category_metrics={
            "maintenance": {"total": 5, "passed": 5, "accuracy": 1.0},
            "ambiguous": {"total": 5, "passed": 4, "accuracy": 0.8},
        },
        failures=[
            {
                "case_id": "test-1",
                "prompt": "some prompt",
                "expected_role": "barb",
                "actual_role": "ad-hoc",
                "mode": "llm",
            }
        ],
        timestamp="2026-09-25T15:00:00Z",
    )

    md = summary.to_markdown()
    assert "## Barb Routing Evaluation Report" in md
    assert "Overall Accuracy:" in md
    assert "90.0%" in md
    assert "| `maintenance` | 5 | 5 | 100.0% |" in md
    assert "Failure Diagnostics" in md
