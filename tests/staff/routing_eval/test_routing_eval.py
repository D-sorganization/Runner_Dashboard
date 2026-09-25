"""Unit and regression tests for Barb routing evaluation suite (SC-C7, Issue #1340)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from staff.conversations import ConversationStore
from staff.router import BarbRouter, route_deterministic
from staff.routing_eval import (
    evaluate_deterministic,
    extract_candidate_cases_from_feedback,
    get_latest_routing_eval,
    load_eval_cases,
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
    """Pre-router evaluates deterministic cases with high accuracy."""
    cases = load_eval_cases()
    det_cases = [c for c in cases if c.deterministic]
    assert len(det_cases) >= 50

    result = evaluate_deterministic(det_cases)
    assert result.total == len(det_cases)
    assert result.accuracy >= 0.95, (
        f"Deterministic accuracy too low: {result.accuracy:.2%}, failures: {result.failures}"
    )
    assert len(result.failures) == 0, f"Unexpected failures in deterministic pre-router: {result.failures}"


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
    assert payload["accuracy"] >= 0.95
    assert result_file.exists()
