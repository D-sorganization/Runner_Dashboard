"""Unit and regression tests for Barb routing evaluation suite (SC-C7, Issue #1340)."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from staff.conversations import ConversationStore
from staff.models import RoutingEvalSummary
from staff.router import BarbRouter, route_deterministic
from staff.routing_eval import (
    DEFAULT_CASES_PATH,
    RoutingEvalCase,
    evaluate_deterministic,
    evaluate_full,
    extract_candidate_cases_from_feedback,
    get_latest_routing_eval,
    load_eval_cases,
    refresh_routing_eval,
    routing_eval_loop,
    save_eval_result,
)


def test_eval_dataset_integrity() -> None:
    """Eval dataset must contain at least 60 valid, unique cases covering all domains."""
    cases = load_eval_cases()
    assert len(cases) >= 60, f"Expected at least 60 eval cases, got {len(cases)}"

    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "Case IDs must be unique"

    categories = {c.category for c in cases}
    required_cats = {
        "maintenance",
        "librarian",
        "board",
        "pms",
        "reviews",
        "code_requests",
        "ambiguous",
        "barb_self",
    }
    missing = required_cats - categories
    assert not missing, f"Missing required categories in eval set: {missing}"

    for case in cases:
        assert case.prompt.strip(), f"Case {case.id} has empty prompt"
        if case.expected_clarify:
            assert case.expected_role is None
        else:
            assert case.expected_roles, f"Case {case.id} must define expected_roles"


def test_deterministic_pre_router_cases() -> None:
    """Every deterministic case routes as expected (CI regression gate)."""
    cases = load_eval_cases()
    det_cases = [c for c in cases if c.deterministic]
    assert len(det_cases) >= 50

    result = evaluate_deterministic(det_cases)
    assert result.total == len(det_cases)
    # Exact regression gate: every case passes today, so any failure is a regression.
    assert result.failures == [], f"Deterministic pre-router regressed: {result.failures}"


def test_deterministic_pre_router_ignores_ambiguous() -> None:
    """Deterministic pre-router must NOT claim ambiguous requests (defers to Stage 2)."""
    cases = load_eval_cases()
    amb_cases = [c for c in cases if c.expected_clarify]
    assert len(amb_cases) >= 6

    for case in amb_cases:
        decision = route_deterministic(case.prompt)
        assert decision is None, f"Pre-router should defer ambiguous prompt '{case.prompt}', got {decision}"


def test_barb_router_clarification_on_ambiguous() -> None:
    """Full router must ask a clarifying question rather than guessing on ambiguous prompts."""
    cases = load_eval_cases()
    amb_cases = [c for c in cases if c.expected_clarify]
    router = BarbRouter()

    for case in amb_cases:
        decision = router.route(case.prompt)
        assert decision.needs_clarification is True, f"Prompt '{case.prompt}' did not trigger clarification"
        assert decision.clarifying_question, f"Missing clarifying question for '{case.prompt}'"


def test_barb_self_handling_cases() -> None:
    """Fleet status, directives, briefs, and priorities must route directly to Barb."""
    cases = load_eval_cases()
    barb_cases = [c for c in cases if c.expected_answer_myself]
    assert len(barb_cases) >= 8

    for case in barb_cases:
        decision = route_deterministic(case.prompt)
        assert decision is not None, f"Prompt '{case.prompt}' failed deterministic routing"
        assert decision.chosen_role == "barb", f"Expected 'barb', got '{decision.chosen_role}'"
        assert decision.confidence >= 0.90


def test_code_change_detection() -> None:
    """Cases with code changes must have is_code_change set to True."""
    cases = load_eval_cases()
    code_cases = [c for c in cases if c.is_code_change]
    assert len(code_cases) >= 3

    for case in code_cases:
        decision = route_deterministic(case.prompt)
        assert decision is not None, f"Failed routing for code prompt: {case.prompt}"
        assert decision.is_code_change is True, f"Code change not detected for: {case.prompt}"


def test_candidate_cases_from_feedback(tmp_path: Path) -> None:
    """Routing overrides from SC-C2 feed candidate new evaluation cases."""
    db_file = tmp_path / "test_feedback.sqlite3"
    store = ConversationStore(db_file)
    router = BarbRouter()

    # Create dummy thread and handoff message
    th = store.create_thread(title="Test Thread", kind="auto", participants=["barb", "user"])
    h_msg = store.add_message(
        thread_id=th.id,
        author_kind="role",
        author="barb",
        kind="handoff",
        body_md="Please check the docs",
        meta={"to_role": "librarian", "work_item_id": "wi_123"},
    )

    # Perform override
    router.override_routing(
        handoff_message_id=h_msg.id,
        new_target_role="pragmatic-programmer",
        reason="It actually contains an implementation bug",
        overridden_by="dieter",
        store=store,
    )

    candidates = extract_candidate_cases_from_feedback(store=store)
    assert len(candidates) >= 1
    cand = candidates[0]
    assert cand["expected_role"] == "pragmatic-programmer"
    assert cand["original_role"] == "librarian"
    assert "implementation bug" in cand["reason"]


def test_save_and_load_routing_eval_result(tmp_path: Path) -> None:
    """Evaluation results are saved and loaded correctly for Board display."""
    cases = load_eval_cases()
    res = evaluate_deterministic([c for c in cases if c.deterministic])

    target_file = tmp_path / "routing_eval_latest.json"
    saved_path = save_eval_result(res, path=target_file)
    assert saved_path == target_file
    assert target_file.exists()

    loaded = get_latest_routing_eval(path=target_file)
    assert loaded is not None
    assert loaded["total"] == res.total
    assert loaded["passed"] == res.passed
    assert abs(loaded["accuracy"] - res.accuracy) < 1e-5
    assert "categories" in loaded


def test_scripts_eval_barb_routing_cli(tmp_path: Path) -> None:
    """scripts/eval_barb_routing.py CLI runs and outputs valid JSON."""
    result_file = tmp_path / "cli_eval_result.json"
    repo_root = Path(__file__).resolve().parents[3]
    cmd = [
        sys.executable,
        str(repo_root / "scripts" / "eval_barb_routing.py"),
        "--deterministic",
        "--json",
        "--record-path",
        str(result_file),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"Script failed: {proc.stderr}\nOutput: {proc.stdout}"

    payload = json.loads(proc.stdout)
    assert payload["total"] >= 50
    assert payload["failures"] == []
    assert result_file.exists()


def test_cases_ship_with_the_backend() -> None:
    """Deployed nodes run the eval, so the cases must live under backend/, not tests/."""
    assert DEFAULT_CASES_PATH.parent.name == "staff"
    assert "tests" not in DEFAULT_CASES_PATH.parts


def test_full_router_passes_every_case() -> None:
    """The two-stage router handles every case, including clarification on ambiguous ones."""
    result = evaluate_full(load_eval_cases())
    assert result.failures == [], f"Full router regressed: {result.failures}"
    assert result.passed == result.total


def test_score_counts_failures_and_rejects_empty_input() -> None:
    wrong = RoutingEvalCase(
        id="w1",
        prompt="show me the fleet status",
        category="t",
        expected_role="librarian",
        expected_roles=("librarian",),
    )
    result = evaluate_deterministic([wrong])
    assert (result.total, result.passed, len(result.failures)) == (1, 0, 1)
    assert result.categories["t"] == {"total": 1, "passed": 0, "accuracy": 0.0}
    with pytest.raises(ValueError):
        evaluate_deterministic([])


def test_case_must_expect_either_clarification_or_a_role() -> None:
    with pytest.raises(ValueError):
        RoutingEvalCase(id="x", prompt="p", category="c", expected_role=None)
    with pytest.raises(ValueError):
        RoutingEvalCase(
            id="y", prompt="p", category="c", expected_role="barb", expected_roles=("barb",), expected_clarify=True
        )


def test_refresh_persists_the_full_eval_for_the_board(tmp_path: Path) -> None:
    target = tmp_path / "latest.json"
    result = refresh_routing_eval(path=target)
    loaded = get_latest_routing_eval(path=target)
    assert loaded is not None and loaded["mode"] == "full"
    summary = RoutingEvalSummary.from_result(loaded)
    assert summary is not None
    assert (summary.total, summary.passed) == (result.total, result.passed)


def test_malformed_result_file_reads_as_absent(tmp_path: Path) -> None:
    bad = tmp_path / "latest.json"
    bad.write_text("{not json", encoding="utf-8")
    assert get_latest_routing_eval(path=bad) is None
    bad.write_text("[1, 2]", encoding="utf-8")
    assert get_latest_routing_eval(path=bad) is None


def test_summary_passes_none_through_and_rejects_inconsistent_counts() -> None:
    assert RoutingEvalSummary.from_result(None) is None
    with pytest.raises(AssertionError):
        RoutingEvalSummary.from_result(
            {"evaluated_at": "2026-09-25T00:00:00Z", "mode": "full", "total": 1, "passed": 2, "accuracy": 1.0}
        )


def test_loop_rejects_a_too_short_interval() -> None:
    with pytest.raises(AssertionError):
        asyncio.run(routing_eval_loop(interval_s=1))
