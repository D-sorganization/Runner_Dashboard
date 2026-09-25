"""Unit tests for Barb availability, reserved capacity, provider fallback chain,
acknowledgment SLA and degraded mode (SC-C6, Issue #1329).
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from staff.availability import (
    DEFAULT_PROVIDER_CHAIN,
    AvailabilityMetrics,
    get_availability_metrics,
    is_provider_healthy,
    record_fast_acknowledgment,
    reset_availability_metrics,
    reset_provider_health,
    resolve_provider_chain,
    set_provider_health,
)
from staff.chat import ChatConcurrencyPool, ChatTurnRunner
from staff.conversations import (
    get_conversation_store,
    reset_conversation_store,
)
from staff.runner import StaffRunner
from staff.thread_bus import get_thread_bus, reset_thread_bus
from staff.work_items import get_work_item_store, reset_work_item_store


@pytest.fixture(autouse=True)
def clean_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    monkeypatch.setenv("STAFF_MOCK_INSTALLED", "1")
    reset_conversation_store()
    reset_work_item_store()
    reset_thread_bus()
    reset_availability_metrics()
    reset_provider_health()
    yield
    reset_conversation_store()
    reset_work_item_store()
    reset_thread_bus()
    reset_availability_metrics()
    reset_provider_health()


# ── 1. CONCURRENCY ISOLATION & RESERVED BARB CAPACITY ─────────────────────────


@pytest.mark.unit
def test_work_runs_do_not_consume_chat_pool_and_barb_slot_reserved() -> None:
    """Work runs bound by StaffRunner._sema cannot exhaust ChatConcurrencyPool,
    and Barb can acquire reserved capacity even under chat load.
    """
    runner = StaffRunner()
    # Acquire all 3 work run semaphore slots
    assert runner._sema.acquire(blocking=False) is True
    assert runner._sema.acquire(blocking=False) is True
    assert runner._sema.acquire(blocking=False) is True
    assert runner._sema.acquire(blocking=False) is False  # Exhausted

    # Chat pool is completely separate: work runs do not touch chat concurrency
    pool = ChatConcurrencyPool(max_concurrency=4, barb_reserved=1)

    # Acquire all non-reserved chat slots (max 4 - 1 = 3)
    assert pool.try_acquire(role="night-watch") is True
    assert pool.try_acquire(role="cartographer") is True
    assert pool.try_acquire(role="librarian") is True

    # 4th slot cannot be acquired by another non-Barb role
    assert pool.try_acquire(role="sanitation") is False

    # But Barb CAN acquire the reserved slot!
    assert pool.try_acquire(role="barb") is True

    # Release runner semaphores
    runner._sema.release()
    runner._sema.release()
    runner._sema.release()


# ── 2. PROVIDER FALLBACK CHAIN RESOLUTION & PROBING ──────────────────────────


@pytest.mark.unit
def test_resolve_provider_chain_order() -> None:
    """Default fallback chain follows claude -> codex -> claude-ollama -> ollama."""
    barb_chain = resolve_provider_chain("barb")
    assert barb_chain == list(DEFAULT_PROVIDER_CHAIN)
    assert barb_chain[0] == "claude"
    assert barb_chain[1] == "codex"
    assert barb_chain[2] == "claude-ollama"
    assert barb_chain[3] == "ollama"

    # Requested provider takes precedence as first candidate
    custom_chain = resolve_provider_chain("barb", requested_provider="codex")
    assert custom_chain[0] == "codex"
    assert "claude" in custom_chain


@pytest.mark.unit
def test_provider_health_probing_and_disabling() -> None:
    """Health probes detect disabled/unhealthy providers."""
    assert is_provider_healthy("claude") is True

    set_provider_health("claude", False)
    assert is_provider_healthy("claude") is False
    assert is_provider_healthy("codex") is True

    reset_provider_health()
    assert is_provider_healthy("claude") is True


# ── 3. EXECUTE TURN WITH FALLBACK ON PROBE FAILURE ───────────────────────────


@pytest.mark.asyncio
async def test_execute_turn_falls_back_when_primary_disabled(tmp_path: Path) -> None:
    """When claude is probe-disabled, execution falls back to codex."""
    conv_store = get_conversation_store()
    thread = conv_store.create_thread(title="Availability Test", role="barb", created_by="alice")
    user_msg = conv_store.add_message(thread.id, author_kind="user", author="alice", body_md="Status report")
    placeholder = conv_store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")

    # Disable claude
    set_provider_health("claude", False)

    runner = ChatTurnRunner(conv_store=conv_store)

    codex_lines = [
        "Session: codex_sess_fallback_1",
        "Here is the fleet status from Codex.",
    ]

    with patch.object(runner, "_spawn_cli_process") as mock_spawn:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = iter([f"{line}\n" for line in codex_lines])
        mock_proc.stderr = iter([])
        mock_proc.poll.return_value = 0
        mock_spawn.return_value = mock_proc

        result = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=user_msg.id,
            placeholder_id=placeholder.id,
            role_name="barb",
        )

        assert result.ok is True
        assert "Here is the fleet status from Codex" in result.reply
        # Active provider was codex
        assert result.metrics.get("provider") == "codex"

        # Check thread metadata updated with active provider
        th = conv_store.get_thread(thread.id)
        assert th is not None
        assert th.meta.get("active_provider") == "codex"

        # Check metrics recorded fallback
        metrics = get_availability_metrics()
        assert metrics.fallback_count >= 1


# ── 4. EXECUTE TURN WITH FALLBACK ON RUNTIME CLI FAILURE ──────────────────────


@pytest.mark.asyncio
async def test_execute_turn_falls_back_on_runtime_failure() -> None:
    """When claude CLI execution crashes or exits non-zero, runner falls back to codex."""
    conv_store = get_conversation_store()
    thread = conv_store.create_thread(title="Runtime Fail Test", role="barb", created_by="bob")
    user_msg = conv_store.add_message(thread.id, author_kind="user", author="bob", body_md="Review PRs")
    placeholder = conv_store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")

    runner = ChatTurnRunner(conv_store=conv_store)

    call_count = 0

    def mock_spawn_side_effect(cmd, cwd, env):
        nonlocal call_count
        call_count += 1
        proc = MagicMock()
        if call_count == 1:
            # First attempt (claude): fails with 500 / exit 1
            proc.returncode = 1
            proc.stdout = iter([])
            proc.stderr = iter(["500 Internal Server Error from Claude API\n"])
            proc.poll.return_value = 1
        else:
            # Second attempt (codex): succeeds
            proc.returncode = 0
            proc.stdout = iter(["Session: sess_ok_123\n", "Codex recovered and handled the request.\n"])
            proc.stderr = iter([])
            proc.poll.return_value = 0
        return proc

    with patch.object(runner, "_spawn_cli_process", side_effect=mock_spawn_side_effect):
        result = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=user_msg.id,
            placeholder_id=placeholder.id,
            role_name="barb",
        )

        assert result.ok is True
        assert "Codex recovered" in result.reply
        assert result.metrics.get("provider") == "codex"

        th = conv_store.get_thread(thread.id)
        assert th is not None
        assert th.meta.get("active_provider") == "codex"

        metrics = get_availability_metrics()
        assert metrics.fallback_count >= 1


# ── 5. DEGRADED MODE WHEN ALL PROVIDERS FAIL ─────────────────────────────────


@pytest.mark.asyncio
async def test_degraded_mode_deterministic_routing_and_queued_followup() -> None:
    """When all providers are disabled, Barb switches to degraded mode,
    applies rule-based routing, queues a work item, and clearly labels the reply.
    """
    conv_store = get_conversation_store()
    work_store = get_work_item_store()

    thread = conv_store.create_thread(title="Degraded Test", role="barb", created_by="carol")
    user_msg = conv_store.add_message(
        thread.id,
        author_kind="user",
        author="carol",
        body_md="Night-watch please inspect red CI build",
    )
    placeholder = conv_store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")

    # Disable all providers
    for p in DEFAULT_PROVIDER_CHAIN:
        set_provider_health(p, False)

    runner = ChatTurnRunner(conv_store=conv_store)
    result = await runner.execute_turn(
        thread_id=thread.id,
        user_message_id=user_msg.id,
        placeholder_id=placeholder.id,
        role_name="barb",
    )

    assert result.ok is True
    assert "[Degraded Mode]" in result.reply
    assert "Automatic routing applied" in result.reply

    # Check work item was queued
    work_items = work_store.list_work_items(thread_id=thread.id)
    assert len(work_items) == 1
    wi = work_items[0]
    assert wi.state == "open"
    assert "ci" in wi.title.lower() or "night-watch" in wi.title.lower() or "degraded" in wi.title.lower()

    # Check thread and message meta
    th = conv_store.get_thread(thread.id)
    assert th is not None
    assert th.meta.get("active_provider") == "degraded"
    assert th.meta.get("degraded_mode") is True

    msg = conv_store.get_message(placeholder.id)
    assert msg is not None
    assert msg.delivery == "complete"
    assert msg.meta.get("degraded_mode") is True
    assert msg.meta.get("active_provider") == "degraded"

    metrics = get_availability_metrics()
    assert metrics.degraded_mode_count >= 1


# ── 6. FAST ACKNOWLEDGMENT SLA (< 3 S) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_fast_acknowledgment_sla_under_3_seconds() -> None:
    """Acknowledgment system message is created and broadcast within 3 seconds."""
    conv_store = get_conversation_store()
    bus = get_thread_bus()

    thread = conv_store.create_thread(title="Ack Test", role="barb", created_by="dan")
    user_msg = conv_store.add_message(
        thread.id,
        author_kind="user",
        author="dan",
        body_md="Please deploy the new release",
    )

    t0 = time.perf_counter()
    ack_msg = await record_fast_acknowledgment(
        conv_store=conv_store,
        bus=bus,
        thread_id=thread.id,
        user_message_id=user_msg.id,
        target_role="barb",
        prompt_text="Please deploy the new release",
        start_time=t0,
    )
    t1 = time.perf_counter()
    latency_ms = (t1 - t0) * 1000.0

    assert ack_msg is not None
    assert ack_msg.author_kind == "system"
    assert ack_msg.body_md.startswith("On it:")
    assert ack_msg.meta.get("acknowledgement") is True
    assert latency_ms < 3000.0  # Strict SLA < 3 s

    metrics = get_availability_metrics()
    assert metrics.ack_latency_ms is not None
    assert metrics.ack_latency_ms < 3000.0


# ── 7. METRICS EXPOSITION ────────────────────────────────────────────────────


@pytest.mark.unit
def test_availability_metrics_tracking_and_dict() -> None:
    """AvailabilityMetrics records latencies, fallbacks, and degraded modes."""
    metrics = AvailabilityMetrics()
    metrics.record_ack(12.5)
    metrics.record_ack(15.0)
    metrics.record_first_token(120.0)
    metrics.record_fallback()
    metrics.record_fallback()
    metrics.record_degraded()

    d = metrics.to_dict()
    assert d["fallback_count"] == 2
    assert d["degraded_mode_count"] == 1
    assert 12.0 <= d["ack_latency_ms"] <= 15.0
    assert d["first_token_latency_ms"] == 120.0


@pytest.mark.unit
def test_board_exposes_availability_metrics() -> None:
    """local_board exposes availability metrics on the Board."""
    from staff.fleet import local_board

    metrics = get_availability_metrics()
    metrics.record_fallback()
    runner = StaffRunner()
    board = local_board(runner)
    assert "availability" in board
    assert board["availability"]["fallback_count"] == 1


@pytest.mark.asyncio
async def test_health_endpoint_exposes_availability_metrics() -> None:
    """/api/health exposes staff_availability metrics."""
    from health import _health_impl

    metrics = get_availability_metrics()
    metrics.record_ack(25.0)
    health_data = await _health_impl()
    assert "staff_availability" in health_data
    assert health_data["staff_availability"]["ack_latency_ms"] == 25.0
