"""Tests for duplicate proposal collapsing in staff inbox (Workstream G)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff import inbox
from staff.conversations import get_conversation_store, reset_conversation_store


@pytest.fixture(autouse=True)
def clean_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    yield
    reset_conversation_store()


@pytest.mark.unit
def test_duplicate_approvals_collapse_into_one_item() -> None:
    c_store = get_conversation_store()
    th = c_store.create_thread(title="Approval Thread", kind="direct", participants=["barb", "user"])

    # Proposal 1
    p1 = c_store.create_proposal(
        message_id="msg_1",
        thread_id=th.id,
        action="runner.restart",
        params={"runner_name": "oglaptop", "rationale": "High load"},
        risk="high",
    )
    # Proposal 2: exact duplicate of Proposal 1 (same action, thread_id, params)
    p2 = c_store.create_proposal(
        message_id="msg_2",
        thread_id=th.id,
        action="runner.restart",
        params={"runner_name": "oglaptop", "rationale": "High load"},
        risk="high",
    )
    # Proposal 3: distinct action
    c_store.create_proposal(
        message_id="msg_3",
        thread_id=th.id,
        action="runner.stop",
        params={"runner_name": "oglaptop"},
        risk="low",
    )

    items = inbox._collect_approvals(c_store)  # noqa: SLF001

    # p1 and p2 should collapse into 1 item, so 2 items total instead of 3
    assert len(items) == 2

    collapsed_item = next(i for i in items if i.metadata["action"] == "runner.restart")
    assert "(and 1 identical requests)" in collapsed_item.summary
    assert collapsed_item.details == [p2.id, p1.id] or collapsed_item.details == [p1.id, p2.id]
    assert set(collapsed_item.metadata["proposal_ids"]) == {p1.id, p2.id}

    # Verify neither proposal was modified or auto-denied
    prop1_db = c_store.get_proposal(p1.id)
    prop2_db = c_store.get_proposal(p2.id)
    assert prop1_db is not None and prop1_db.state == "proposed"
    assert prop2_db is not None and prop2_db.state == "proposed"


@pytest.mark.unit
def test_duplicate_approvals_three_identical_collapse() -> None:
    c_store = get_conversation_store()
    th = c_store.create_thread(title="Multi-duplicate", kind="direct", participants=["barb", "user"])

    props = [
        c_store.create_proposal(
            message_id=f"msg_{i}",
            thread_id=th.id,
            action="cache.clear",
            params={"target": "redis"},
            risk="medium",
        )
        for i in range(3)
    ]

    items = inbox._collect_approvals(c_store)  # noqa: SLF001
    assert len(items) == 1
    assert "(and 2 identical requests)" in items[0].summary
    assert len(items[0].details) == 3
    assert set(items[0].details) == {p.id for p in props}


@pytest.mark.unit
def test_different_params_do_not_collapse() -> None:
    c_store = get_conversation_store()
    th = c_store.create_thread(title="Distinct params", kind="direct", participants=["barb", "user"])

    c_store.create_proposal(
        message_id="msg_a",
        thread_id=th.id,
        action="runner.restart",
        params={"runner_name": "host-1"},
        risk="medium",
    )
    c_store.create_proposal(
        message_id="msg_b",
        thread_id=th.id,
        action="runner.restart",
        params={"runner_name": "host-2"},
        risk="medium",
    )

    items = inbox._collect_approvals(c_store)  # noqa: SLF001
    assert len(items) == 2
    for it in items:
        assert "identical request" not in it.summary
        assert it.details == []
