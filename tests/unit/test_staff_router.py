"""Unit tests for Barb two-stage router, decision records, handoffs, and overrides (SC-C2, Issue #1315)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.router import (
    BarbRouter,
    RoutingDecision,
    route_deterministic,
)
from staff.work_items import WorkItemStore


@pytest.fixture(autouse=True)
def clean_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    yield
    reset_conversation_store()


def test_pre_router_explicit_at_mention() -> None:
    """Explicit @mention routes directly with confidence 1.0."""
    decision = route_deterministic("@librarian please review the documentation style guide")
    assert decision is not None
    assert decision.chosen_role == "librarian"
    assert decision.confidence == 1.0
    assert decision.mode == "explicit"
    assert "librarian" in decision.reason.lower()
    assert decision.needs_clarification is False


def test_pre_router_explicit_role_command() -> None:
    """Explicit /role command routes directly with confidence 1.0."""
    decision = route_deterministic("/role maintenance restart runner og-laptop")
    assert decision is not None
    assert decision.chosen_role == "maintenance"
    assert decision.confidence == 1.0
    assert decision.mode == "explicit"
    assert decision.needs_clarification is False


def test_pre_router_barb_self_handling() -> None:
    """Barb handles fleet status, directives, priorities, and portfolio questions directly."""
    decision = route_deterministic("What are Dieter's current directives and active priorities?")
    assert decision is not None
    assert decision.chosen_role == "barb"
    assert decision.confidence >= 0.9
    assert decision.mode in ("pre_router", "deterministic")
    assert decision.needs_clarification is False


def test_pre_router_keyword_rules_for_specialist_roles() -> None:
    """Keyword/scope rules route to the correct specialist role."""
    cases = [
        ("Fix bug in issue #1234 where authentication fails", "issue-remediator"),
        ("Review PR #1402 and fix CI failure on the tests job", "pr-remediator"),
        ("Audit the architecture map and contract seams for Runner_Dashboard", "cartographer"),
        ("Clean up stale git branches and orphaned worktrees", "sanitation"),
        ("Update documentation and README with latest API guides", "librarian"),
        ("Runner linux-1 is offline and jobs are stalled, compact disk and restart", "maintenance"),
    ]
    for prompt, expected_role in cases:
        decision = route_deterministic(prompt)
        assert decision is not None, f"Failed to route: {prompt}"
        msg = f"Expected {expected_role} for '{prompt}', got {decision.chosen_role}"
        assert decision.chosen_role == expected_role, msg
        assert decision.confidence >= 0.8
        assert decision.needs_clarification is False


def test_code_change_detection() -> None:
    """Prompts requesting code implementation are marked as code changes."""
    decision = route_deterministic("Please implement a new endpoint in backend/routers/repos.py and open a PR")
    assert decision is not None
    assert decision.is_code_change is True


def test_ambiguous_prompt_asks_clarifying_question() -> None:
    """Unroutable/ambiguous prompt below confidence threshold asks a clarifying question."""
    router = BarbRouter(confidence_threshold=0.7)
    decision = router.route("Can you look at something weird that happened yesterday?")
    assert decision.needs_clarification is True
    assert decision.clarifying_question is not None
    assert len(decision.clarifying_question) > 0
    assert decision.confidence < 0.7


def test_llm_unavailable_falls_back_to_quick_mode() -> None:
    """When LLM is unavailable, router operates in quick fallback mode with visible note."""
    router = BarbRouter()
    with patch.object(router, "_call_llm_router", side_effect=RuntimeError("LLM offline")):
        decision = router.route("Update documentation style guide")
        assert decision.chosen_role == "librarian"
        assert decision.mode in ("pre_router", "fallback_quick")
        if decision.mode == "fallback_quick":
            assert "quick mode" in decision.reason.lower()


def test_execute_handoff_creates_thread_message_and_work_item() -> None:
    """Hand-off creates target thread, handoff message with metadata, and work item."""
    store = get_conversation_store()
    source_th = store.create_thread(title="Auto Thread", kind="auto", participants=["barb", "dieter"])
    source_msg = store.add_message(
        thread_id=source_th.id,
        author_kind="user",
        author="dieter",
        body_md="Update documentation style guide for docs/",
    )

    router = BarbRouter()
    decision = RoutingDecision(
        chosen_role="librarian",
        confidence=0.9,
        reason="Librarian owns documentation audits and style guides",
        alternatives=("cartographer",),
        mode="deterministic",
        needs_clarification=False,
        is_code_change=False,
    )

    work_item_store = WorkItemStore(store.path)
    result = router.execute_handoff(
        decision=decision,
        original_message=source_msg.body_md,
        caller_id="dieter",
        source_thread_id=source_th.id,
        store=store,
        work_item_store=work_item_store,
    )

    assert result.success is True
    assert result.target_role == "librarian"
    assert result.target_thread_id != ""
    assert result.work_item_id != ""

    # Verify handoff message posted in source thread
    msgs = store.list_messages(source_th.id)
    handoff_msgs = [m for m in msgs if m.kind == "handoff"]
    assert len(handoff_msgs) == 1
    h_msg = handoff_msgs[0]
    assert "Barb → Librarian" in h_msg.body_md
    assert h_msg.meta.get("to_role") == "librarian"
    assert h_msg.meta.get("allow_override") is True
    assert h_msg.meta.get("work_item_id") == result.work_item_id

    # Verify target thread seeded
    target_msgs = store.list_messages(result.target_thread_id)
    assert len(target_msgs) >= 1
    assert "Hand-off from Barb" in target_msgs[0].body_md

    # Verify work item created
    wi = work_item_store.get_work_item(result.work_item_id)
    assert wi is not None
    assert wi.owner_role == "librarian"
    assert wi.thread_id == result.target_thread_id


def test_override_routing_logs_feedback_and_redirects() -> None:
    """One-click override updates target role, records audit, and logs routing feedback."""
    store = get_conversation_store()
    source_th = store.create_thread(title="Auto Thread", kind="auto", participants=["barb", "dieter"])
    router = BarbRouter()
    decision = RoutingDecision(
        chosen_role="librarian",
        confidence=0.85,
        reason="Docs mention",
        mode="deterministic",
    )
    work_item_store = WorkItemStore(store.path)
    res = router.execute_handoff(
        decision=decision,
        original_message="Fix documentation code block",
        caller_id="dieter",
        source_thread_id=source_th.id,
        store=store,
        work_item_store=work_item_store,
    )

    # Now override Barb's routing decision to pragmatic-programmer
    override_res = router.override_routing(
        handoff_message_id=res.handoff_message_id,
        new_target_role="pragmatic-programmer",
        reason="This is a code refactoring task, not docs",
        overridden_by="dieter",
        store=store,
        work_item_store=work_item_store,
    )

    assert override_res.success is True
    assert override_res.new_target_role == "pragmatic-programmer"
    assert override_res.original_role == "librarian"

    # Verify feedback logged
    feedback = router.list_routing_feedback(store=store)
    assert len(feedback) >= 1
    fb = feedback[0]
    assert fb.original_role == "librarian"
    assert fb.override_role == "pragmatic-programmer"
    assert "code refactoring" in fb.reason
