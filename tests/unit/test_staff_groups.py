"""Unit tests for staff group threads and Board Deliberation (SC-B9, Issue #1339).

Tests:
1. Group definitions and loading (Board group with seats: Alpha, Bravo, Charlie, Delta, coordinator: board-secretary).
2. Cost estimation per seat and total cost calculation with threshold guard.
3. Group turn fanout to fake seats: all seats respond -> consensus summary + expandable seat views.
4. Partial failure: one seat raises error -> listed as "no response", summary reports responding seats.
5. Timeout handling: one seat times out -> listed as "no response", remaining seats collated.
6. Formal proposal generation: "make this a proposal" action proposal (board.propose) created and executable.
7. Cost guard check: trips when estimated cost exceeds threshold; passes when confirmed.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from staff.action_executors import BOARD_PROPOSAL_ROLE, validate_action_default_roles
from staff.chat_issue_context import IssueFetcher
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
    reset_conversation_store,
)
from staff.groups import (
    SeatReply,
    SeatSpec,
    convene_board_thread,
    create_group_thread,
    estimate_group_turn_cost,
    execute_group_turn,
    get_board_group,
    get_group,
    list_groups,
    reset_group_runner_override,
    resolve_group_thread_meta,
    set_group_runner_override,
)
from staff.thread_bus import reset_thread_bus


@pytest.fixture
def conv_store(tmp_path: Path) -> ConversationStore:
    reset_conversation_store()
    reset_thread_bus()
    db_file = tmp_path / "test_groups.sqlite3"
    return get_conversation_store(db_file)


# ── 1. GROUP DEFINITIONS ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_board_group_definition() -> None:
    """Board group has board-secretary coordinator and 4 seats (Alpha, Bravo, Charlie, Delta)."""
    group = get_group("board")
    assert group is not None
    assert group.id == "board"
    assert group.coordinator == "board-secretary"
    seat_names = [s.name for s in group.seats]
    assert "alpha" in seat_names
    assert "bravo" in seat_names
    assert "charlie" in seat_names
    assert "delta" in seat_names
    assert len(group.seats) == 4


@pytest.mark.unit
def test_list_groups_contains_board() -> None:
    """list_groups returns all registered groups including board."""
    groups = list_groups()
    ids = [g.id for g in groups]
    assert "board" in ids


@pytest.mark.unit
def test_board_group_uses_board_proposal_role() -> None:
    """Board group coordinator is bound to BOARD_PROPOSAL_ROLE."""
    group = get_group("board")
    assert group is not None
    assert group.coordinator == BOARD_PROPOSAL_ROLE

    kind, coord, meta = resolve_group_thread_meta("direct", BOARD_PROPOSAL_ROLE, [])
    assert kind == "group"
    assert coord == BOARD_PROPOSAL_ROLE
    assert meta["coordinator"] == BOARD_PROPOSAL_ROLE


@pytest.mark.unit
def test_no_board_secretary_literals_in_staff_groups() -> None:
    """staff/groups.py must not contain 'board_secretary' or 'board-secretary' literals."""
    groups_path = Path(__file__).resolve().parents[2] / "backend" / "staff" / "groups.py"
    with open(groups_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(groups_path))

    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in ("board_secretary", "board-secretary")
    ]
    assert literals == [], f"Found forbidden literals in staff/groups.py: {literals}"


@pytest.mark.unit
def test_board_group_coordinator_validated_in_action_default_roles() -> None:
    """Board group coordinator is validated against loaded roster in validate_action_default_roles."""
    from staff.group_models import GroupDefinition

    mock_board = GroupDefinition(
        id="board",
        name="Board of Directors",
        coordinator="nonexistent-coordinator",
        seats=[],
        description="Test board",
        cost_threshold_usd=1.0,
    )
    with (
        patch("staff.roles.roles_dir", return_value=Path("/mock/roles")),
        patch(
            "staff.roles.load_roles",
            return_value={
                "code-reviewer": MagicMock(dispatchable=True, retired=False, surface="dashboard"),
                "barb": MagicMock(dispatchable=True, retired=False, surface="dashboard"),
                BOARD_PROPOSAL_ROLE: MagicMock(dispatchable=True, retired=False, surface="dashboard"),
            },
        ),
        patch("staff.groups.get_board_group", return_value=mock_board),
    ):
        errors = validate_action_default_roles(raise_on_error=False)
        assert any("nonexistent-coordinator" in err for err in errors)

        with pytest.raises(ValueError, match="nonexistent-coordinator"):
            validate_action_default_roles(raise_on_error=True)


# ── 2. COST ESTIMATION & GUARD ───────────────────────────────────────────────


@pytest.mark.unit
def test_estimate_group_turn_cost() -> None:
    """estimate_group_turn_cost calculates per-seat and total estimated USD cost."""
    estimate = estimate_group_turn_cost("board", prompt="Should we migrate runner cache to S3?")
    assert estimate.group_id == "board"
    assert estimate.total_cost_usd > 0.0
    assert len(estimate.cost_per_seat) == 4
    assert all(c > 0.0 for c in estimate.cost_per_seat.values())
    assert isinstance(estimate.exceeds_threshold, bool)


@pytest.mark.unit
def test_cost_guard_threshold_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cost guard flags turns that exceed configured cost threshold."""
    # Force a very low threshold so it triggers
    monkeypatch.setenv("GROUP_COST_GUARD_THRESHOLD_USD", "0.0001")
    estimate = estimate_group_turn_cost("board", prompt="A regular prompt")
    assert estimate.exceeds_threshold is True
    assert estimate.warning is not None
    assert "exceeds threshold" in estimate.warning.lower()


# ── 3. FANOUT TO FAKE SEATS (ALL SUCCEED) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_group_turn_all_seats_succeed() -> None:
    """All 4 seats respond with thoughtful replies -> consensus summary with all seats."""

    async def fake_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        return SeatReply(
            seat_name=seat.name,
            status="ok",
            text=f"Seat {seat.name.title()} perspective on {prompt}: strongly in favor.",
            cost_usd=0.01,
        )

    res = await execute_group_turn(
        group_id="board",
        prompt="Adopt WebGPU for visualization",
        thread_id="th_test1",
        seat_runner=fake_runner,
    )

    assert res.group_id == "board"
    assert res.coordinator == "board-secretary"
    assert len(res.seat_replies) == 4
    assert all(r.status == "ok" for r in res.seat_replies)
    assert "4/4 seats answered" in res.quorum
    assert "Where the seats stand" in res.summary
    assert "Alpha" in res.summary
    assert "Bravo" in res.summary
    assert "Charlie" in res.summary
    assert "Delta" in res.summary
    # Formal proposal recommended
    assert len(res.proposed_actions) >= 1
    assert res.proposed_actions[0].action == "board.propose"
    assert "Adopt WebGPU" in res.proposed_actions[0].params.get("title", "")


# ── 4. PARTIAL FAILURE ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_group_turn_partial_failure() -> None:
    """If one seat errors out, it is recorded as 'no response' and quorum is updated."""

    async def failing_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        if seat.name == "bravo":
            raise RuntimeError("API connection refused 502")
        return SeatReply(
            seat_name=seat.name,
            status="ok",
            text=f"Seat {seat.name.title()} recommends approval.",
            cost_usd=0.01,
        )

    res = await execute_group_turn(
        group_id="board",
        prompt="Review database compaction frequency",
        thread_id="th_test2",
        seat_runner=failing_runner,
    )

    assert len(res.seat_replies) == 4
    bravo_reply = next(r for r in res.seat_replies if r.seat_name == "bravo")
    assert bravo_reply.status == "error"
    assert "no response" in bravo_reply.text.lower()
    assert "3/4 seats answered" in res.quorum
    assert "Bravo: no response" in res.quorum


# ── 5. TIMEOUT HANDLING ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_group_turn_timeout() -> None:
    """If a seat exceeds seat_timeout_seconds, it is aborted and marked as timed out."""

    async def slow_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        if seat.name == "charlie":
            await asyncio.sleep(2.0)  # Will exceed our test timeout of 0.1s
            return SeatReply(seat_name=seat.name, status="ok", text="Late reply")
        return SeatReply(
            seat_name=seat.name,
            status="ok",
            text=f"Seat {seat.name.title()} reply on time.",
            cost_usd=0.01,
        )

    res = await execute_group_turn(
        group_id="board",
        prompt="Evaluate runner autoscaling",
        thread_id="th_test3",
        seat_runner=slow_runner,
        seat_timeout_seconds=0.1,
    )

    charlie_reply = next(r for r in res.seat_replies if r.seat_name == "charlie")
    assert charlie_reply.status == "timeout"
    assert "no response - timed out" in charlie_reply.text.lower()
    assert "Charlie: no response" in res.quorum


# ── 5b. REFERENCED-ITEMS BLOCK FOR SEATS (Runner_Dashboard#1767) ────────────


@pytest.mark.asyncio
async def test_execute_group_turn_seats_get_fetched_body_proposal_keeps_plain_prompt() -> None:
    """Seats see the fetched packet under the header; the board.propose card keeps the plain prompt."""
    seen_prompts: list[str] = []

    async def fake_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        seen_prompts.append(prompt)
        return SeatReply(seat_name=seat.name, status="ok", text=f"{seat.name} position", cost_usd=0.01)

    async def fake_gh_api(endpoint: str) -> dict:
        return {"title": "Proposal packet", "state": "open", "labels": [], "body": "R09-R12 findings here."}

    async def fake_gh_api_raw(endpoint: str) -> str:
        return "unused"

    fetch = IssueFetcher(gh_api=fake_gh_api, gh_api_raw=fake_gh_api_raw)
    prompt = "Review D-sorganization/UpstreamDrift#11080 and decide."

    res = await execute_group_turn(
        group_id="board",
        prompt=prompt,
        thread_id="th_refs",
        seat_runner=fake_runner,
        fetch=fetch,
    )

    assert len(seen_prompts) == 4
    for seat_prompt_text in seen_prompts:
        assert prompt in seat_prompt_text
        assert "## Referenced items" in seat_prompt_text
        assert "R09-R12 findings here." in seat_prompt_text

    # The proposal was built from the ORIGINAL prompt, never the 60 KB packet.
    assert res.proposed_actions, "expected a board.propose action"
    proposal_text = res.proposed_actions[0].params.get("proposal", "")
    assert "R09-R12 findings here." not in proposal_text
    assert prompt in proposal_text


@pytest.mark.asyncio
async def test_execute_group_turn_fetch_failure_falls_back_to_plain_prompt() -> None:
    """A raising fetch path never fails the turn; seats still run on the plain prompt.

    build_referenced_items_block itself never raises (a failed per-ref fetch renders
    'unavailable'), so this exercises groups.py's own defensive try/except by forcing
    a failure above that per-ref safety net — the same as an unexpected error in the
    default fetcher's construction would look like.
    """
    seen_prompts: list[str] = []

    async def fake_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        seen_prompts.append(prompt)
        return SeatReply(seat_name=seat.name, status="ok", text=f"{seat.name} position", cost_usd=0.01)

    prompt = "Review D-sorganization/UpstreamDrift#11080 and decide."

    with patch("staff.groups.build_referenced_items_block", side_effect=RuntimeError("network unavailable")):
        res = await execute_group_turn(
            group_id="board",
            prompt=prompt,
            thread_id="th_refs_fail",
            seat_runner=fake_runner,
        )

    assert len(seen_prompts) == 4
    for seat_prompt_text in seen_prompts:
        assert seat_prompt_text == prompt
    assert all(r.status == "ok" for r in res.seat_replies)


# ── 6. SHARED THREAD-CREATION HELPER (board.convene, Runner_Dashboard#1762) ──


@pytest.mark.unit
def test_create_group_thread_populates_coordinator_seats_and_caller(conv_store: ConversationStore) -> None:
    """create_group_thread builds the same shape the router endpoint used to build inline."""
    group = get_board_group()
    thread = create_group_thread(group, "Custom Title", "alice", store=conv_store)

    assert thread.kind == "group"
    assert thread.title == "Custom Title"
    assert thread.meta["group"] == "board"
    assert thread.meta["coordinator"] == "board-secretary"
    assert "board-secretary" in thread.participants
    assert "alpha" in thread.participants
    assert "alice" in thread.participants


@pytest.mark.unit
def test_create_group_thread_default_title(conv_store: ConversationStore) -> None:
    group = get_board_group()
    thread = create_group_thread(group, None, "alice", store=conv_store)
    assert thread.title == f"{group.name} Deliberation"


@pytest.mark.asyncio
async def test_convene_board_thread_creates_thread_and_starts_one_group_turn(
    conv_store: ConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """convene_board_thread creates a Board thread and fans the question out once (stub seat runner)."""
    # convene_board_thread resolves the module-level singleton via get_conversation_store();
    # pin it to the fixture's db so both sides see the same store.
    monkeypatch.setenv("STAFF_RUNS_DB", str(conv_store.path))
    call_count = 0

    async def stub_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        nonlocal call_count
        call_count += 1
        return SeatReply(seat_name=seat.name, status="ok", text=f"{seat.name} view on: {prompt}")

    set_group_runner_override(stub_runner)
    try:
        out = await convene_board_thread("Should we adopt WebGPU?", "WebGPU Deliberation", "barb")
        assert out["title"] == "WebGPU Deliberation"
        thread_id = out["thread_id"]

        thread = conv_store.get_thread(thread_id)
        assert thread is not None
        assert thread.kind == "group"
        assert thread.meta["group"] == "board"

        messages = conv_store.list_messages(thread_id)
        assert any("Should we adopt WebGPU?" in (m.body_md or "") for m in messages)
        placeholder = next(m for m in messages if m.meta.get("is_group_turn"))
        assert placeholder.delivery == "pending"

        for _ in range(50):
            await asyncio.sleep(0.05)
            rec = conv_store.get_message(placeholder.id)
            if rec and rec.delivery == "complete":
                break

        completed = conv_store.get_message(placeholder.id)
        assert completed is not None
        assert completed.delivery == "complete"
        assert "### Board Deliberation" in completed.body_md
        # Exactly one group turn ran: one call per seat, not one per seat per turn.
        assert call_count == len(get_board_group().seats)
    finally:
        reset_group_runner_override()
