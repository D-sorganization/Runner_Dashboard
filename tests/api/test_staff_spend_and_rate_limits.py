"""Tests for SC-F7: Rate limits and spend guards on staff conversation and dispatch APIs (Issue #1336).

Covers:
- Per-principal token-bucket limits on message send (30/min) and dispatch (10/hour).
- Exceeding limits returns HTTP 429 with Retry-After header and SC-F3 classified error.
- Resilient store failure: fails open for owner/human UI session, fails closed for bot tokens.
- Chat turns spend tracking against role usd_per_day; budget exhaustion generates fixed
  system message and notifies Barb / audit.
- Loop guard: thread with > N agent-to-agent turns without user input pauses and asks owner;
  resets when human user messages.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from server import app
from staff.budget import BudgetGuard
from staff.conversations import (
    get_conversation_store,
    reset_conversation_store,
)
from staff.loop_guard import LoopGuard
from staff.rate_limit import (
    TokenBucketLimiter,
    get_rate_limiter,
    reset_rate_limiter,
)
from staff.runner import reset_runner
from staff.store import reset_store
from staff.thread_bus import reset_thread_bus

BOT_PRINCIPAL = Principal(
    id="agent-claude",
    type="bot",
    name="Claude Agent",
    roles=["bot"],
    scopes=["staff.chat", "staff.dispatch", "staff.read"],
)

HUMAN_PRINCIPAL = Principal(
    id="dieterolson",
    type="human",
    name="Dieter Olson",
    roles=["admin", "operator"],
    scopes=["staff.chat", "staff.dispatch", "staff.read"],
)


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_file = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))
    reset_store()
    reset_runner()
    reset_conversation_store()
    reset_thread_bus()
    reset_rate_limiter()

    # Create dummy role directory with a test role
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)
    with open(roles_dir / "scout.yaml", "w", encoding="utf-8") as f:
        f.write("name: scout\ntitle: Scout\nsurface: background\nbudget_usd_per_day: 0.10\nbudget_usd_per_run: 0.05\n")
    with open(roles_dir / "barb.yaml", "w", encoding="utf-8") as f:
        f.write("name: barb\ntitle: Barb\nsurface: interactive\nbudget_usd_per_day: 1.00\nbudget_usd_per_run: 0.10\n")

    from unittest.mock import AsyncMock, patch

    with patch("routers.staff_threads.run_chat_turn_in_background", new_callable=AsyncMock):
        yield
    app.dependency_overrides.clear()
    reset_store()
    reset_runner()
    reset_conversation_store()
    reset_thread_bus()
    reset_rate_limiter()


@pytest.fixture
def client(clean_env: None) -> TestClient:
    return TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )


# ── UNIT: TOKEN BUCKET RATE LIMITER ──────────────────────────────────────────


def test_token_bucket_permits_up_to_capacity_and_rate_limits() -> None:
    current_time = 1000.0
    limiter = TokenBucketLimiter(
        capacity={"messages": 3, "dispatches": 2},
        rate_per_sec={"messages": 1.0, "dispatches": 0.5},
        clock=lambda: current_time,
    )

    # 3 messages allowed
    ok1, retry1 = limiter.acquire("p1", "messages", principal_type="bot")
    ok2, retry2 = limiter.acquire("p1", "messages", principal_type="bot")
    ok3, retry3 = limiter.acquire("p1", "messages", principal_type="bot")
    assert ok1 and ok2 and ok3
    assert retry1 == 0.0 and retry2 == 0.0 and retry3 == 0.0

    # 4th message rejected
    ok4, retry4 = limiter.acquire("p1", "messages", principal_type="bot")
    assert not ok4
    assert retry4 > 0.0

    # Advance time by 1 second -> 1 token replenishes
    current_time += 1.0
    ok5, retry5 = limiter.acquire("p1", "messages", principal_type="bot")
    assert ok5
    assert retry5 == 0.0


def test_token_bucket_principal_isolation() -> None:
    current_time = 1000.0
    limiter = TokenBucketLimiter(
        capacity={"messages": 1},
        rate_per_sec={"messages": 0.1},
        clock=lambda: current_time,
    )
    ok_a, _ = limiter.acquire("agent-a", "messages", principal_type="bot")
    assert ok_a
    # agent-a exhausted
    ok_a2, retry_a2 = limiter.acquire("agent-a", "messages", principal_type="bot")
    assert not ok_a2
    assert retry_a2 > 0.0

    # agent-b should still be allowed
    ok_b, retry_b = limiter.acquire("agent-b", "messages", principal_type="bot")
    assert ok_b
    assert retry_b == 0.0


def test_token_bucket_store_failure_fails_open_for_human_and_closed_for_bot() -> None:
    limiter = TokenBucketLimiter()
    # Force store failure simulation
    limiter._simulate_store_failure = True  # type: ignore[attr-defined]

    # Human user fails open
    ok_human, retry_human = limiter.acquire("dieterolson", "messages", principal_type="human")
    assert ok_human is True
    assert retry_human == 0.0

    # Bot user fails closed
    ok_bot, retry_bot = limiter.acquire("agent-claude", "messages", principal_type="bot")
    assert ok_bot is False
    assert retry_bot > 0.0


# ── INTEGRATION: RATE LIMIT ON MESSAGE SEND & DISPATCH ───────────────────────


def test_message_send_rate_limit_returns_429(client: TestClient) -> None:
    app.dependency_overrides[require_principal] = lambda: BOT_PRINCIPAL
    app.dependency_overrides[require_scope("staff.chat")] = lambda: BOT_PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: BOT_PRINCIPAL

    limiter = get_rate_limiter()
    limiter.set_limits(action="messages", capacity=2, rate_per_sec=0.1)

    # Create thread
    res = client.post("/api/v1/staff/threads", json={"title": "Test", "role": "scout"})
    assert res.status_code == 201
    thread_id = res.json()["id"]

    # 1st and 2nd messages succeed
    res1 = client.post(
        f"/api/v1/staff/threads/{thread_id}/messages",
        headers={"Idempotency-Key": "m1"},
        json={"body": "Hello 1"},
    )
    assert res1.status_code == 202

    res2 = client.post(
        f"/api/v1/staff/threads/{thread_id}/messages",
        headers={"Idempotency-Key": "m2"},
        json={"body": "Hello 2"},
    )
    assert res2.status_code == 202

    # 3rd message exceeds limit -> 429 Too Many Requests
    res3 = client.post(
        f"/api/v1/staff/threads/{thread_id}/messages",
        headers={"Idempotency-Key": "m3"},
        json={"body": "Hello 3"},
    )
    assert res3.status_code == 429
    assert "Retry-After" in res3.headers
    body = res3.json()
    error_obj = body.get("error", body.get("detail", body))
    assert error_obj.get("code") == "rate_limited"
    assert error_obj.get("retryable") is True


def test_dispatch_rate_limit_returns_429(client: TestClient) -> None:
    app.dependency_overrides[require_principal] = lambda: BOT_PRINCIPAL
    app.dependency_overrides[require_scope("staff.dispatch")] = lambda: BOT_PRINCIPAL

    limiter = get_rate_limiter()
    limiter.set_limits(action="dispatches", capacity=1, rate_per_sec=0.01)

    # 1st dispatch succeeds
    res1 = client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "check repos", "provider": "claude", "dry_run": True},
    )
    assert res1.status_code == 200

    # 2nd dispatch exceeds rate limit -> 429
    res2 = client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "check repos again", "provider": "claude", "dry_run": True},
    )
    assert res2.status_code == 429
    assert "Retry-After" in res2.headers
    body = res2.json()
    error_obj = body.get("error", body.get("detail", body))
    assert error_obj.get("code") == "rate_limited"


# ── SPEND GUARD & BUDGET EXHAUSTION ──────────────────────────────────────────


def test_budget_guard_chat_turns_exhaustion_produces_system_message(client: TestClient) -> None:
    app.dependency_overrides[require_principal] = lambda: BOT_PRINCIPAL
    app.dependency_overrides[require_scope("staff.chat")] = lambda: BOT_PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: BOT_PRINCIPAL

    conv_store = get_conversation_store()
    thread = conv_store.create_thread(
        title="Scout Chat",
        kind="direct",
        participants=["agent-claude", "scout"],
        created_by="agent-claude",
    )

    # Mock spent today for scout to exceed budget ($0.10)
    guard = BudgetGuard(store=MagicMock(), role_budgets={"scout": 0.10})
    guard.spent_today = MagicMock(return_value=0.15)  # type: ignore[method-assign]

    can_chat, reason = guard.can_chat("scout", role_budget_usd=0.10)
    assert can_chat is False
    assert "budget" in reason.lower()

    # Now verify when posting message with an exhausted role, response produces system message
    from staff.budget import set_global_budget_guard

    set_global_budget_guard(guard)

    res = client.post(
        f"/api/v1/staff/threads/{thread.id}/messages",
        headers={"Idempotency-Key": "msg-budget-test"},
        json={"body": "Need quick update"},
    )
    assert res.status_code == 202
    data = res.json()
    reply = data["reply_placeholder"]
    assert reply["author_kind"] == "system"
    assert "budget reached" in reply["body_md"].lower()


# ── LOOP GUARD ───────────────────────────────────────────────────────────────


def test_loop_guard_trips_after_n_consecutive_agent_turns(client: TestClient) -> None:
    conv_store = get_conversation_store()
    thread = conv_store.create_thread(
        title="Agent Loop",
        kind="direct",
        participants=["agent-claude", "scout"],
        created_by="agent-claude",
    )

    # Add 5 consecutive agent/role messages
    for i in range(1, 6):
        conv_store.add_message(
            thread_id=thread.id,
            author_kind="role",
            author="scout" if i % 2 == 0 else "agent-claude",
            kind="text",
            body_md=f"Agent response turn {i}",
        )

    # Check loop guard
    guard = LoopGuard(max_agent_turns=5)
    tripped, count = guard.check(thread.id, conv_store)
    assert tripped is True
    assert count >= 5

    # Posting as bot when loop guard is tripped returns 429 loop_guard_triggered
    app.dependency_overrides[require_principal] = lambda: BOT_PRINCIPAL
    app.dependency_overrides[require_scope("staff.chat")] = lambda: BOT_PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: BOT_PRINCIPAL

    res = client.post(
        f"/api/v1/staff/threads/{thread.id}/messages",
        headers={"Idempotency-Key": "bot-loop-turn"},
        json={"body": "Automated next turn"},
    )
    assert res.status_code == 429
    body = res.json()
    error_obj = body.get("error", body.get("detail", body))
    assert error_obj.get("code") == "loop_guard_triggered"


def test_loop_guard_resets_when_human_user_sends_message(client: TestClient) -> None:
    conv_store = get_conversation_store()
    thread = conv_store.create_thread(
        title="Agent Loop 2",
        kind="direct",
        participants=["agent-claude", "scout"],
        created_by="agent-claude",
    )

    for i in range(1, 6):
        conv_store.add_message(
            thread_id=thread.id,
            author_kind="role",
            author="scout",
            kind="text",
            body_md=f"Agent turn {i}",
        )

    guard = LoopGuard(max_agent_turns=5)
    tripped, _ = guard.check(thread.id, conv_store)
    assert tripped is True

    # Human user posts message -> loop guard resets!
    app.dependency_overrides[require_principal] = lambda: HUMAN_PRINCIPAL
    app.dependency_overrides[require_scope("staff.chat")] = lambda: HUMAN_PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: HUMAN_PRINCIPAL

    res = client.post(
        f"/api/v1/staff/threads/{thread.id}/messages",
        headers={"Idempotency-Key": "human-intervention"},
        json={"body": "I am reviewing this now, proceed with step 2."},
    )
    assert res.status_code == 202

    # Now verify loop guard is no longer tripped
    tripped_after, count_after = guard.check(thread.id, conv_store)
    assert tripped_after is False
    assert count_after == 0
