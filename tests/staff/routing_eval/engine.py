"""Evaluation engine and metrics collector for Barb request routing (SC-C7, Issue #1340)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from staff.conversations import ConversationStore, get_conversation_store
from staff.router import BarbRouter, route_deterministic
from staff.router_models import RoutingFeedbackRecord
from staff.work_items import WorkItemRecord, WorkItemStore

from tests.staff.routing_eval.dataset import ROUTING_EVAL_CASES
from tests.staff.routing_eval.models import CaseEvalResult, EvalSummary, RoutingEvalCase

log = logging.getLogger("dashboard.staff.routing_eval")


def evaluate_single_case(
    case: RoutingEvalCase,
    router: BarbRouter | None = None,
    deterministic_only: bool = False,
) -> CaseEvalResult:
    """Evaluate a single test case through the deterministic or two-stage router."""
    t0 = time.perf_counter()

    if deterministic_only:
        decision = route_deterministic(case.prompt)
    else:
        r = router or BarbRouter()
        decision = r.route(case.prompt)

    latency_ms = (time.perf_counter() - t0) * 1000.0

    if decision is None:
        actual_role = None
        actual_outcome = "unhandled"
        mode = "unhandled"
        conf = 0.0
        reason = "Pre-router returned None"
    else:
        actual_role = decision.chosen_role
        conf = decision.confidence
        mode = decision.mode
        reason = decision.reason
        if decision.needs_clarification:
            actual_outcome = "clarify"
        elif decision.chosen_role == "barb":
            actual_outcome = "answer_myself"
        else:
            actual_outcome = "route"

    # Evaluate pass/fail based on expected outcome
    if case.expected_outcome == "clarify":
        passed = actual_outcome == "clarify"
    elif case.expected_outcome == "answer_myself":
        passed = (actual_role == "barb") and (actual_outcome == "answer_myself")
    elif case.expected_outcome == "route":
        passed = (actual_role == case.expected_role) and (actual_outcome == "route")
    else:
        passed = False

    return CaseEvalResult(
        case_id=case.id,
        prompt=case.prompt,
        category=case.category,
        expected_role=case.expected_role,
        actual_role=actual_role,
        expected_outcome=case.expected_outcome,
        actual_outcome=actual_outcome,
        passed=passed,
        confidence=conf,
        mode=mode,
        reason=reason,
        latency_ms=latency_ms,
    )


def run_evaluation(
    cases: Iterable[RoutingEvalCase] | None = None,
    router: BarbRouter | None = None,
    deterministic_only: bool = False,
    include_feedback_candidates: bool = False,
    store: ConversationStore | None = None,
) -> EvalSummary:
    """Run full evaluation suite across provided cases or default curated dataset."""
    eval_cases = list(cases or ROUTING_EVAL_CASES)
    candidate_count = 0

    if include_feedback_candidates:
        s = store or get_conversation_store()
        r = router or BarbRouter()
        feedback_records = r.list_routing_feedback(store=s, limit=100)
        candidate_cases = load_candidate_cases_from_feedback(feedback_records)
        eval_cases.extend(candidate_cases)
        candidate_count = len(candidate_cases)

    r = router or BarbRouter()
    results: list[CaseEvalResult] = []

    cat_counts: dict[str, dict[str, int]] = {}
    det_total = 0
    det_passed = 0

    for c in eval_cases:
        if deterministic_only and not c.deterministic:
            continue

        res = evaluate_single_case(c, router=r, deterministic_only=deterministic_only)
        results.append(res)

        # Track category metrics
        if c.category not in cat_counts:
            cat_counts[c.category] = {"total": 0, "passed": 0}
        cat_counts[c.category]["total"] += 1
        if res.passed:
            cat_counts[c.category]["passed"] += 1

        # Track deterministic subsets
        if c.deterministic:
            det_total += 1
            if res.passed:
                det_passed += 1

    total = len(results)
    passed = sum(1 for r_item in results if r_item.passed)
    failed = total - passed
    accuracy = (passed / total) if total > 0 else 0.0
    det_accuracy = (det_passed / det_total) if det_total > 0 else 0.0

    category_metrics: dict[str, dict[str, Any]] = {}
    for cat, counts in cat_counts.items():
        c_tot = counts["total"]
        c_pass = counts["passed"]
        category_metrics[cat] = {
            "total": c_tot,
            "passed": c_pass,
            "failed": c_tot - c_pass,
            "accuracy": (c_pass / c_tot) if c_tot > 0 else 0.0,
        }

    failures = [res.to_dict() for res in results if not res.passed]

    return EvalSummary(
        total_cases=total,
        passed_cases=passed,
        failed_cases=failed,
        accuracy=accuracy,
        deterministic_total=det_total,
        deterministic_passed=det_passed,
        deterministic_accuracy=det_accuracy,
        category_metrics=category_metrics,
        failures=failures,
        candidate_cases_count=candidate_count,
        timestamp=datetime.now(UTC).isoformat(),
    )


def load_candidate_cases_from_feedback(
    feedback_records: list[RoutingFeedbackRecord],
) -> list[RoutingEvalCase]:
    """Transform user routing overrides into candidate test cases for regression eval."""
    candidates: list[RoutingEvalCase] = []
    for fb in feedback_records:
        if not fb.prompt or not fb.override_role:
            continue
        case = RoutingEvalCase(
            id=f"feedback-{fb.id[:8]}",
            prompt=fb.prompt,
            expected_role=fb.override_role,
            expected_outcome="route",
            category="feedback_override",
            deterministic=False,
            tags=("feedback_override", f"orig:{fb.original_role}"),
        )
        candidates.append(case)
    return candidates


def post_eval_summary_to_board(
    summary: EvalSummary,
    store: ConversationStore | None = None,
    work_item_store: WorkItemStore | None = None,
) -> WorkItemRecord:
    """Publish evaluation summary to the Board via a board-secretary work item proposal."""
    s = store or get_conversation_store()
    w_store = work_item_store or WorkItemStore(s.path)

    markdown_body = summary.to_markdown()

    item = w_store.create_work_item(
        title=f"[Board Proposal] Nightly Barb Routing Evaluation: {summary.accuracy:.1%} Accuracy",
        requested_by="eval_barb_routing",
        owner_role="board_secretary",
        links={
            "eval_summary": [json.dumps(summary.to_dict())],
            "eval_markdown": [markdown_body],
            "runs": [],
            "issues": [],
            "prs": [],
            "code_requests": [],
        },
    )
    return item
