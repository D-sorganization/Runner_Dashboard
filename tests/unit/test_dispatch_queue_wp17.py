"""WP17 (#4272): dispatch queue session release, classifier stream, legacy fallback model."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from dispatch_routing import DispatchQueueManager
from staff.adapter_failure import adapter_failure_classification
from staff.classifier import classify_run_failure, error_exit_stream_for_classification
from staff.provider_fallback import ProviderFallbackTarget, resolve_fallback_target_model
from staff.retry import handle_post_execution_retry
from staff.roles import RoleSpec
from staff.store import RunRecord


def test_error_stream_ignores_benign_429_in_tool_output() -> None:
    transcript = (
        "Editing backend/ratelimit.py\n"
        "const RETRY_AFTER_429_MS = 5000;\n"
        "Saved file.\n"
    )
    stream = error_exit_stream_for_classification(output_text=transcript)
    assert adapter_failure_classification("claude", stream) is None
    row = classify_run_failure(
        "claude",
        1,
        output_text=transcript,
        error_message="",
        machine="node-a",
    )
    assert row.failure_class != "rate_limited"


def test_error_stream_still_detects_real_429_line() -> None:
    err = "API rate limit exceeded (HTTP 429): please retry after 60 seconds"
    stream = error_exit_stream_for_classification(error_message=err)
    row = adapter_failure_classification("claude", stream)
    assert row is not None
    assert row[0] == "rate_limited"


@pytest.mark.unit
def test_legacy_fallback_resolves_target_provider_model() -> None:
    role = RoleSpec(
        name="r",
        title="R",
        providers=("codex", "claude"),
        model="gpt-6-luna",
    )
    adapters = {
        "codex": MagicMock(),
        "claude": MagicMock(),
    }
    adapters["claude"].build_command.return_value = ["claude", "run"]
    runner = MagicMock()
    runner.machine = "test"
    runner._adapters = adapters
    runner.store = MagicMock()
    runner.store.claim_run.return_value = True
    runner._cancel_flags = set()
    runner.roles.return_value = {"r": role}

    plan = MagicMock()
    plan.role = "r"
    plan.provider = "codex"
    plan.model = "gpt-6-luna"
    plan.prompt = "fix"
    plan.repo = "org/repo"
    plan.target_kind = "issue"
    plan.target_ref = "1"
    plan.operator_prompt = ""
    plan.branch = "staff/1"
    plan.lease_ritual = ""
    plan.consolidation = None
    plan.focus = ""
    plan.head_ref = ""
    plan.strategy_mode = ""

    guard = MagicMock()
    guard.can_run.return_value = (True, "")

    latest = RunRecord(
        id="run-root",
        role="r",
        provider="codex",
        model="gpt-6-luna",
        machine="test",
        repo="org/repo",
        target_kind="issue",
        target_ref="1",
        prompt="fix",
        status="failed",
        failure_class="rate_limited",
        attempt=2,
        max_attempts=2,
    )
    runner.store.get_run.return_value = latest

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("staff.retry.should_retry", lambda *a, **k: (False, "exhausted"))
        mp.setattr(
            "staff.retry.next_fallback_provider",
            lambda role, provider, usable=None, visited=None: "claude" if provider == "codex" else None,
        )
        mp.setattr("staff.failover_dispatch.try_chain_failover", lambda *a, **k: False)
        mp.setattr("staff.provider_fallback.try_declarative_fallback", lambda *a, **k: False)
        mp.setattr("staff.retry._schedule", lambda *a, **k: None)
        mp.setattr("staff.retry._claim", lambda store, rec: True)
        mp.setattr("staff.claim_guard.scheduler_blocker", lambda *_a, **_k: "")

        scheduled = handle_post_execution_retry(runner, latest, plan)
    assert scheduled is True
    fallback_call = runner._adapters["claude"].build_command.call_args
    assert fallback_call is not None
    model_arg = fallback_call[0][2]
    assert model_arg != "gpt-6-luna"
    resolved = resolve_fallback_target_model(
        target=ProviderFallbackTarget("claude", None),
        role=role,
        source_provider="codex",
        target_provider="claude",
        adapter=adapters["claude"],
    )
    assert model_arg == resolved


def test_release_unbound_overlap_session_clears_active() -> None:
    from dispatch_queue_link import release_unbound_overlap_session
    from dispatch_routing import dispatch_queue_manager

    dispatch_queue_manager.reset_state_for_tests()
    attempt = dispatch_queue_manager.try_dispatch(repository="org/repo", number=1, prompt="touch `backend/a.py`")
    assert attempt.session is not None
    release_unbound_overlap_session(attempt.session.session_id, outcome="dispatch_exited")
    assert dispatch_queue_manager.get_active_sessions("org/repo") == []


def test_staff_session_release_drains_queued_dispatch() -> None:
    manager = DispatchQueueManager()
    d1 = manager.try_dispatch(
        repository="org/repo",
        number=1,
        prompt="Edit `backend/a.py`",
    )
    assert d1.session is not None
    d2 = manager.try_dispatch(
        repository="org/repo",
        number=2,
        prompt="Also edit `backend/a.py`",
    )
    assert d2.dispatched is False
    assert d2.queued_item is not None

    dequeued = manager.release_session(d1.session.session_id, outcome="succeeded")
    assert len(dequeued) == 1
    assert dequeued[0].number == 2
    retry = manager.try_dispatch(
        repository="org/repo",
        number=2,
        prompt="Also edit `backend/a.py`",
    )
    assert retry.dispatched is True
