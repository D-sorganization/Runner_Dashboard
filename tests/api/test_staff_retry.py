"""Tests for bounded staff-run retries and per-provider concurrency (SC-A7, Issue #1303).

Acceptance criteria:
1. Fake provider returning 429 twice then success -> one visible logical run with
   two linked attempts, final succeeded.
2. Concurrency: 3 dispatches to a provider with max_concurrency=1 run serially.
3. Retry matrix per failure class (rate_limited, provider_error, workspace_error retried;
   needs_input, auth_expired, lease_blocked, cli_missing never retried).
4. Budget stop: retries count against role budget; budget guard halts retries when cap reached.
5. Restart during backoff: persisted next_attempt_at survives restart and is resumed.
6. Fallback provider chain: e.g. claude -> codex used after retries are exhausted.
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from staff import adapters as adapters_mod
from staff import retry as retry_mod
from staff import runner as runner_mod
from staff import store as store_mod
from staff.budget import BudgetGuard
from staff.reconcile import reconcile_orphaned_runs
from staff.roles import RoleSpec
from staff.store import RunRecord, RunStore

_XHR = {"X-Requested-With": "XMLHttpRequest"}

# Script for fake CLI that can simulate 429 rate limit, 500 provider error, or success
_MOCK_CLI_SCRIPT = """
import json, sys, time
prompt = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else ""

if mode == "429":
    print("API rate limit exceeded (HTTP 429): please retry after 60 seconds", file=sys.stderr)
    sys.exit(1)
elif mode == "502":
    print("HTTP 502 Bad Gateway from Anthropic API", file=sys.stderr)
    sys.exit(1)
elif mode == "workspace":
    print("fatal: 'staff/feat-100' is already checked out at '/worktrees/dirty'", file=sys.stderr)
    sys.exit(1)
elif mode == "auth_expired":
    print("Error: 401 OAuth access token has expired. Please run claude auth login", file=sys.stderr)
    sys.exit(1)
elif mode == "needs_input":
    msg = {"content": [{"type": "text", "text": "Should I proceed with delete?"}]}
    print(json.dumps({"type": "assistant", "message": msg}))
    sys.exit(0)
elif mode == "slow":
    time.sleep(0.3)
    usage = {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.05}
    print(json.dumps({"type": "result", "result": "STAFF_RESULT: done", "usage": usage, "total_cost_usd": 0.05}))
    sys.exit(0)
else:
    usage = {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.05}
    print(json.dumps({"type": "result", "result": "STAFF_RESULT: done", "usage": usage, "total_cost_usd": 0.05}))
    sys.exit(0)
"""


@pytest.fixture
def mock_cli_path(tmp_path: Path) -> Path:
    script = tmp_path / "mock_cli.py"
    script.write_text(_MOCK_CLI_SCRIPT, encoding="utf-8")
    return script


@pytest.fixture
def temp_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunStore:
    db = tmp_path / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    store_mod.reset_store()
    return store_mod.get_store()


def test_retry_constants() -> None:
    """Verify retryable and non-retryable failure classes per issue specification."""
    assert "rate_limited" in retry_mod.RETRYABLE_FAILURE_CLASSES
    assert "provider_error" in retry_mod.RETRYABLE_FAILURE_CLASSES
    assert "workspace_error" in retry_mod.RETRYABLE_FAILURE_CLASSES

    for non_retryable in (
        "needs_input",
        "auth_expired",
        "lease_blocked",
        "cli_missing",
    ):
        assert non_retryable not in retry_mod.RETRYABLE_FAILURE_CLASSES
        assert non_retryable in retry_mod.NON_RETRYABLE_FAILURE_CLASSES


def test_compute_backoff_exponential() -> None:
    """Backoff scales exponentially: 2^0, 2^1, 2^2 with base delay."""
    # Without jitter for deterministic math
    assert retry_mod.compute_backoff(1, base_seconds=2.0, jitter=False) == 2.0
    assert retry_mod.compute_backoff(2, base_seconds=2.0, jitter=False) == 4.0
    assert retry_mod.compute_backoff(3, base_seconds=2.0, jitter=False) == 8.0
    # Jitter stays bounded
    with_jitter = retry_mod.compute_backoff(1, base_seconds=2.0, jitter=True)
    assert 2.0 <= with_jitter <= 3.0


@pytest.mark.parametrize(
    ("failure_class", "expected_retryable"),
    [
        ("rate_limited", True),
        ("provider_error", True),
        ("workspace_error", True),
        ("needs_input", False),
        ("auth_expired", False),
        ("lease_blocked", False),
        ("cli_missing", False),
        ("orphaned", False),
        ("unkillable", False),
        ("timeout", False),
        ("stalled", False),
        ("unknown", False),
    ],
)
def test_retry_matrix_per_failure_class(failure_class: str, expected_retryable: bool) -> None:
    """Only rate_limited, provider_error, and workspace_error are retried."""
    assert retry_mod.is_retryable_class(failure_class) is expected_retryable


def test_should_retry_respects_max_attempts() -> None:
    """Attempt 1/2 is retried; attempt 2/2 is exhausted."""
    role = RoleSpec(name="tester", title="Tester", providers=("fake",), max_attempts=2)
    rec1 = RunRecord(
        id="run-1",
        role="tester",
        provider="fake",
        model=None,
        machine="local",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="hello",
        status="failed",
        failure_class="rate_limited",
        attempt=1,
        max_attempts=2,
    )
    ok1, _ = retry_mod.should_retry(rec1, role)
    assert ok1 is True

    rec2 = RunRecord(
        id="run-2",
        role="tester",
        provider="fake",
        model=None,
        machine="local",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="hello",
        status="failed",
        failure_class="rate_limited",
        attempt=2,
        max_attempts=2,
    )
    ok2, reason2 = retry_mod.should_retry(rec2, role)
    assert ok2 is False
    assert "exhausted" in reason2


def test_should_retry_stopped_by_budget(temp_store: RunStore) -> None:
    """Role budget cap stops retries even if attempts remain."""
    role = RoleSpec(
        name="costly-role",
        title="Costly Role",
        providers=("fake",),
        budget_usd_per_day=1.00,
        max_attempts=3,
    )
    guard = BudgetGuard(temp_store)

    # Simulate already spent $1.05 today
    temp_store.create_run(
        RunRecord(
            id="run-prior",
            role="costly-role",
            provider="fake",
            model=None,
            machine="local",
            repo="",
            target_kind="prompt",
            target_ref="",
            prompt="prior",
            status="succeeded",
            cost_usd=1.05,
        )
    )

    rec = RunRecord(
        id="run-current",
        role="costly-role",
        provider="fake",
        model=None,
        machine="local",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="test",
        status="failed",
        failure_class="rate_limited",
        attempt=1,
        max_attempts=3,
    )
    ok, reason = retry_mod.should_retry(rec, role, budget_guard=guard)
    assert ok is False
    assert "budget" in reason


def test_fallback_provider_chain() -> None:
    """Provider fallback chain transitions claude -> codex when configured."""
    role = RoleSpec(
        name="multi-role",
        title="Multi Role",
        providers=("claude", "codex", "gemini"),
    )
    assert retry_mod.next_fallback_provider(role, "claude") == "codex"
    assert retry_mod.next_fallback_provider(role, "codex") == "gemini"
    assert retry_mod.next_fallback_provider(role, "gemini") is None


def test_provider_concurrency_enforced_serially(
    temp_store: RunStore, mock_cli_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 dispatches to a provider with max_concurrency=1 run serially."""
    active_count = 0
    max_active = 0
    import threading

    lock = threading.Lock()

    # Wrap mock execution to track concurrent active executions
    adapter = adapters_mod.ProviderAdapter(
        provider_id="fake-serial",
        label="Fake Serial CLI",
        executable=sys.executable,
        argv=(str(mock_cli_path), "{prompt}", "slow"),
        json_lines=True,
        max_concurrency=1,
    )
    adapters = {"fake-serial": adapter}
    role = RoleSpec(
        name="serial-worker",
        title="Serial Worker",
        providers=("fake-serial",),
    )
    roles = {"serial-worker": role}

    runner = runner_mod.StaffRunner(
        store=temp_store,
        roles_loader=lambda: roles,
        adapters=adapters,
        machine="test-node",
    )

    orig_execute = runner._execute

    def tracked_execute(rec: RunRecord, plan: runner_mod.RunPlan, workdir: Path, lease_note: str) -> None:
        nonlocal active_count, max_active
        with lock:
            active_count += 1
            if active_count > max_active:
                max_active = active_count
        try:
            orig_execute(rec, plan, workdir, lease_note)
        finally:
            with lock:
                active_count -= 1

    monkeypatch.setattr(runner, "_execute", tracked_execute)

    # Dispatch 3 runs
    r1 = runner.submit(runner_mod.RunRequest(role="serial-worker", prompt="task 1"))
    r2 = runner.submit(runner_mod.RunRequest(role="serial-worker", prompt="task 2"))
    r3 = runner.submit(runner_mod.RunRequest(role="serial-worker", prompt="task 3"))

    # Wait for all 3 runs to complete
    for r in (r1, r2, r3):
        for _ in range(300):
            row = temp_store.get_run(r.id)
            if row and row.status in ("succeeded", "failed"):
                break
            time.sleep(0.05)

    assert max_active == 1, f"Expected strictly serial execution (max 1), got max {max_active}"
    assert temp_store.get_run(r1.id).status == "succeeded"
    assert temp_store.get_run(r2.id).status == "succeeded"
    assert temp_store.get_run(r3.id).status == "succeeded"


def test_bounded_retry_fake_provider_429_twice_then_success(
    temp_store: RunStore, mock_cli_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance criterion: Fake provider returning 429 twice then success ->
    one visible logical run with two linked attempts, final succeeded.
    """
    monkeypatch.setenv("STAFF_RETRY_BASE_SECONDS", "0.01")
    call_count = 0
    import threading

    lock = threading.Lock()

    class FlakyAdapter(adapters_mod.ProviderAdapter):
        def build_command(self, prompt: str, workdir: str, model: str | None = None) -> list[str]:
            nonlocal call_count
            with lock:
                call_count += 1
                current = call_count
            mode = "429" if current <= 3 else "ok"
            return [sys.executable, str(mock_cli_path), prompt, mode]

    adapter = FlakyAdapter(
        provider_id="fake-flaky",
        label="Fake Flaky CLI",
        executable=sys.executable,
        argv=(),
        json_lines=True,
    )

    role = RoleSpec(
        name="flaky-worker",
        title="Flaky Worker",
        providers=("fake-flaky",),
        max_attempts=3,
    )
    roles = {"flaky-worker": role}

    runner = runner_mod.StaffRunner(
        store=temp_store,
        roles_loader=lambda: roles,
        adapters={"fake-flaky": adapter},
        machine="test-node",
    )

    rec = runner.submit(runner_mod.RunRequest(role="flaky-worker", prompt="process data"))

    # Wait for logical run and attempts to reach terminal state
    for _ in range(300):
        row = temp_store.get_run(rec.id)
        if row and row.status == "succeeded":
            break
        time.sleep(0.05)

    final_root = temp_store.get_run(rec.id)
    assert final_root is not None
    assert final_root.status == "succeeded"

    attempts = temp_store.get_attempts(rec.id)
    assert len(attempts) == 3
    assert attempts[0].id == rec.id
    assert attempts[0].attempt == 1
    assert attempts[0].failure_class == "rate_limited"

    assert attempts[1].retry_of == rec.id
    assert attempts[1].attempt == 2
    assert attempts[1].failure_class == "rate_limited"

    assert attempts[2].retry_of == rec.id
    assert attempts[2].attempt == 3
    assert attempts[2].status == "succeeded"

    # Verify list_runs shows exactly one visible logical run
    logical_runs = temp_store.list_runs(logical_only=True)
    assert len(logical_runs) == 1
    assert logical_runs[0].id == rec.id
    assert logical_runs[0].status == "succeeded"


def test_reconcile_preserves_scheduled_retries(temp_store: RunStore) -> None:
    """Scheduled retry with next_attempt_at in the future is not marked orphaned across restart."""
    future_time = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    rec = RunRecord(
        id="run-pending-retry",
        role="worker",
        provider="fake",
        model=None,
        machine="test-node",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="hello",
        status="queued",
        retry_of="run-root",
        attempt=2,
        next_attempt_at=future_time,
    )
    temp_store.create_run(rec)

    runner = runner_mod.StaffRunner(store=temp_store, machine="test-node")
    reconciled = reconcile_orphaned_runs(runner)

    # Should NOT have reconciled/orphaned the scheduled retry
    assert "run-pending-retry" not in reconciled
    updated = temp_store.get_run("run-pending-retry")
    assert updated is not None
    assert updated.status == "queued"
    assert updated.next_attempt_at == future_time
