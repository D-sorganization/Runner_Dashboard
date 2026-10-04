"""Tests for RD-1 Event-Driven CI-Fix Dispatch (Issue #1846).

Validates:
- Trigger evaluation on failing workflow runs for open PRs with auto-merge armed.
- No auto-fix on draft PRs.
- Concurrency locking (one active CI-fix session per PR).
- Log tail truncation (<= 200 lines) and failing test name extraction.
- Provider routing: cheapest provider for lint/formatting, tier:cli for tests/logic.
- Escalation after max_same_failure_attempts (default 3).
- Audit trail recording provider, model, attempt number, and cost.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal

from backend.ci_fix_dispatch import (
    CIFixLockManager,
    build_ci_fix_prompt,
    evaluate_ci_fix_trigger,
    extract_failing_test_names,
    record_ci_fix_audit,
    route_ci_fix,
    truncate_log_tail,
)
from backend.server import app

OPERATOR = Principal(id="op", type="human", name="Op", roles=["operator"], scopes=["remediation.dispatch"])


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[require_principal] = lambda: OPERATOR
    yield TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    app.dependency_overrides.pop(require_principal, None)


def test_evaluate_trigger_success() -> None:
    event = {
        "event_type": "workflow_run",
        "action": "completed",
        "conclusion": "failure",
        "workflow_name": "CI Standard",
        "run_id": 123456,
        "repository": "Runner_Dashboard",
        "branch": "feat/my-feature",
        "pull_requests": [
            {
                "number": 42,
                "head_branch": "feat/my-feature",
                "state": "open",
                "is_draft": False,
                "auto_merge_armed": True,
            }
        ],
    }
    decision = evaluate_ci_fix_trigger(event)
    assert decision.eligible is True
    assert decision.reason == "Eligible for event-driven CI-fix dispatch"
    assert decision.pr_number == 42


def test_evaluate_trigger_ignores_successful_run() -> None:
    event = {
        "event_type": "workflow_run",
        "action": "completed",
        "conclusion": "success",
        "workflow_name": "CI Standard",
        "run_id": 123456,
        "repository": "Runner_Dashboard",
        "branch": "feat/my-feature",
        "pull_requests": [
            {
                "number": 42,
                "head_branch": "feat/my-feature",
                "state": "open",
                "is_draft": False,
                "auto_merge_armed": True,
            }
        ],
    }
    decision = evaluate_ci_fix_trigger(event)
    assert decision.eligible is False
    assert "not failed" in decision.reason.lower()


def test_evaluate_trigger_rejects_draft_pr() -> None:
    event = {
        "event_type": "workflow_run",
        "action": "completed",
        "conclusion": "failure",
        "workflow_name": "CI Standard",
        "run_id": 123456,
        "repository": "Runner_Dashboard",
        "branch": "feat/my-feature",
        "pull_requests": [
            {
                "number": 42,
                "head_branch": "feat/my-feature",
                "state": "open",
                "is_draft": True,
                "auto_merge_armed": True,
            }
        ],
    }
    decision = evaluate_ci_fix_trigger(event)
    assert decision.eligible is False
    assert "draft" in decision.reason.lower()


def test_evaluate_trigger_rejects_pr_without_auto_merge() -> None:
    event = {
        "event_type": "workflow_run",
        "action": "completed",
        "conclusion": "failure",
        "workflow_name": "CI Standard",
        "run_id": 123456,
        "repository": "Runner_Dashboard",
        "branch": "feat/my-feature",
        "pull_requests": [
            {
                "number": 42,
                "head_branch": "feat/my-feature",
                "state": "open",
                "is_draft": False,
                "auto_merge_armed": False,
            }
        ],
    }
    decision = evaluate_ci_fix_trigger(event)
    assert decision.eligible is False
    assert "auto-merge" in decision.reason.lower()


def test_concurrency_lock_manager() -> None:
    lock_mgr = CIFixLockManager()
    assert lock_mgr.acquire("Runner_Dashboard", 42, session_id="session-1") is True
    # Second acquisition on same PR should fail
    assert lock_mgr.acquire("Runner_Dashboard", 42, session_id="session-2") is False

    # Different PR should succeed
    assert lock_mgr.acquire("Runner_Dashboard", 43, session_id="session-3") is True

    # Release and re-acquire
    lock_mgr.release("Runner_Dashboard", 42)
    assert lock_mgr.acquire("Runner_Dashboard", 42, session_id="session-4") is True
    lock_mgr.release("Runner_Dashboard", 42)
    lock_mgr.release("Runner_Dashboard", 43)


def test_log_tail_truncation() -> None:
    lines = [f"Line {i}" for i in range(500)]
    full_log = "\n".join(lines)
    tail = truncate_log_tail(full_log, max_lines=200)
    tail_lines = tail.splitlines()
    assert len(tail_lines) == 200
    assert tail_lines[-1] == "Line 499"
    assert tail_lines[0] == "Line 300"


def test_extract_failing_test_names() -> None:
    log_sample = """
    FAILED tests/test_auth.py::test_login_invalid_password - AssertionError: expected 401
    FAILED tests/api/test_users.py::test_create_duplicate - ValueError: duplicate email
    PASSED tests/test_health.py::test_ping
    """
    failing = extract_failing_test_names(log_sample)
    assert len(failing) == 2
    assert "tests/test_auth.py::test_login_invalid_password" in failing
    assert "tests/api/test_users.py::test_create_duplicate" in failing


def test_route_ci_fix_lint_to_cheapest() -> None:
    route = route_ci_fix(
        failure_type="lint",
        attempt_number=1,
        max_attempts=3,
    )
    assert route.provider == "codex_cli"
    assert route.model == "gpt-6-luna"
    assert route.tier == "cheap"
    assert route.cost_budget <= 0.50
    assert route.escalated is False


def test_route_ci_fix_test_prefers_agy_gemini_flash_when_unattended(monkeypatch: pytest.MonkeyPatch) -> None:
    """tier:cli prefers agy with Gemini 3.8 Flash once agy can run unattended (#1880)."""
    monkeypatch.setattr("backend.ci_fix_dispatch._agy_runs_unattended", lambda: True)
    route = route_ci_fix(
        failure_type="test",
        attempt_number=1,
        max_attempts=3,
    )
    assert route.provider == "antigravity"
    assert route.model == "gemini-3.8-flash-high"
    assert route.tier == "cli"
    assert route.escalated is False


def test_route_ci_fix_test_falls_back_to_sonnet_while_agy_is_chat_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.ci_fix_dispatch._agy_runs_unattended", lambda: False)
    route = route_ci_fix(
        failure_type="test",
        attempt_number=1,
        max_attempts=3,
    )
    assert route.provider == "claude_code_cli"
    assert route.model == "claude-sonnet-5-5"
    assert route.tier == "cli"
    assert "agy" in route.reason


def test_agy_gate_reads_the_staff_adapter() -> None:
    """The gate is the staff antigravity adapter's ``unattended`` flag, not a second switch."""
    from staff.adapters import ADAPTERS

    from backend.ci_fix_dispatch import _agy_runs_unattended

    assert _agy_runs_unattended() is ADAPTERS["antigravity"].unattended


def test_route_ci_fix_escalation_after_cap() -> None:
    route = route_ci_fix(
        failure_type="test",
        attempt_number=3,
        max_attempts=3,
    )
    assert route.escalated is True
    assert route.tier == "strong"
    assert route.provider == "claude_code_cli"
    assert route.model == "claude-opus-5-5"
    assert "escalated" in route.reason.lower()


def test_build_ci_fix_prompt() -> None:
    prompt = build_ci_fix_prompt(
        repo="Runner_Dashboard",
        pr_number=42,
        branch="feat/test-branch",
        log_tail="AssertionError: Expected 200 got 500",
        failing_tests=["tests/test_api.py::test_users"],
        pr_diff="diff --git a/backend/user.py b/backend/user.py\n+ return True",
        conflicting_files=[],
    )
    assert "Runner_Dashboard" in prompt
    assert "#42" in prompt
    assert "python -m scripts.pre_pr" in prompt
    assert "tests/test_api.py::test_users" in prompt
    assert "AssertionError" in prompt


def test_build_ci_fix_prompt_merge_conflict() -> None:
    prompt = build_ci_fix_prompt(
        repo="Runner_Dashboard",
        pr_number=42,
        branch="feat/test-branch",
        log_tail="",
        failing_tests=[],
        pr_diff="",
        conflicting_files=["backend/routes.py", "pyproject.toml"],
    )
    assert "Merge conflict" in prompt
    assert "backend/routes.py" in prompt
    assert "pyproject.toml" in prompt


def test_record_ci_fix_audit(tmp_path: Path) -> None:
    audit_file = tmp_path / "ci_fix_audit.json"
    record_ci_fix_audit(
        repo="Runner_Dashboard",
        pr_number=42,
        workflow_name="CI Standard",
        run_id=123,
        failure_type="lint",
        provider="codex_cli",
        model="gpt-5-codex",
        attempt_number=1,
        cost_budget=0.15,
        audit_file=audit_file,
        staff_run_id="run-abc",
        effort="low",
    )
    assert audit_file.is_file()
    import json

    entries = json.loads(audit_file.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert entries[0]["pr_number"] == 42
    # #1881: the recorded figure is the route's budget, not the spend; the spend lives on
    # the staff run the entry names.
    assert entries[0]["cost_budget"] == 0.15
    assert "cost_estimate" not in entries[0]
    assert entries[0]["staff_run_id"] == "run-abc"
    assert entries[0]["effort"] == "low"


def test_remediation_ci_fix_api_endpoints(client: TestClient) -> None:
    payload = {
        "event_type": "workflow_run",
        "action": "completed",
        "conclusion": "failure",
        "workflow_name": "CI Standard",
        "run_id": 999888,
        "repository": "Runner_Dashboard",
        "branch": "feat/demo",
        "pull_requests": [
            {
                "number": 101,
                "head_branch": "feat/demo",
                "state": "open",
                "is_draft": False,
                "auto_merge_armed": True,
            }
        ],
    }
    resp = client.post("/api/remediation/ci-fix/evaluate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["eligible"] is True
    assert data["pr_number"] == 101
    assert "route" in data
    assert data["route"]["tier"] in ("cheap", "cli", "strong")


def test_remediation_ci_fix_dispatch_lifecycle(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import ci_fix_dispatch as bare_dispatch
    import ci_fix_service

    launched: list[object] = []

    async def fake_launch(cmd: object, caller: Principal) -> dict:
        launched.append(cmd)
        return {"dry_run": False, "run": {"id": f"run-l{len(launched)}"}}

    monkeypatch.setattr(ci_fix_service, "dispatch_staff_run", fake_launch)
    monkeypatch.setattr(
        ci_fix_service, "available_providers", lambda: {"claude": True, "codex": True, "antigravity": True}
    )
    monkeypatch.setattr(bare_dispatch, "DEFAULT_AUDIT_PATH", tmp_path / "audit.json")
    # Fake run ids are not in the staff store, so isolate the lock from the run-liveness probe.
    from ci_fix_locks import CIFixLockManager as BareLockManager
    from routers import remediation_ci_fix

    isolated = BareLockManager()
    monkeypatch.setattr(ci_fix_service, "GLOBAL_CI_FIX_LOCK_MGR", isolated)
    monkeypatch.setattr(remediation_ci_fix, "GLOBAL_CI_FIX_LOCK_MGR", isolated)
    dispatch_payload = {
        "repo": "Runner_Dashboard",
        "pr_number": 202,
        "branch": "feat/test-dispatch",
        "workflow_name": "Lint and Format",
        "log_tail": "ruff check failed: 3 errors",
        "pr_diff": "+ x = 1",
        "attempt_number": 1,
    }

    # Clean release first in case of leftover state
    client.delete("/api/remediation/ci-fix/locks/Runner_Dashboard/202")

    # 1. Dispatch first session -> Success (200)
    resp = client.post("/api/remediation/ci-fix/dispatch", json=dispatch_payload)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["status"] == "dispatched"
    assert res_data["repo"] == "Runner_Dashboard"
    assert res_data["pr_number"] == 202
    assert res_data["route"]["tier"] == "cheap"
    assert res_data["route"]["cost_budget"] < 0.50
    assert "python -m scripts.pre_pr" in res_data["prompt"]
    assert res_data["staff_run_id"] == "run-l1"
    assert len(launched) == 1

    # 2. Concurrency Lock: Dispatching second concurrent session on same PR -> 409 Conflict
    resp_conflict = client.post("/api/remediation/ci-fix/dispatch", json=dispatch_payload)
    assert resp_conflict.status_code == 409
    assert "already active" in resp_conflict.json()["detail"]

    # 3. Query locks
    resp_locks = client.get("/api/remediation/ci-fix/locks")
    assert resp_locks.status_code == 200
    locks = resp_locks.json()["locks"]
    assert any(lock["repo"] == "Runner_Dashboard" and lock["pr_number"] == 202 for lock in locks)

    # 4. Release lock
    resp_del = client.delete("/api/remediation/ci-fix/locks/Runner_Dashboard/202")
    assert resp_del.status_code == 200
    assert resp_del.json()["released"] is True

    # 5. Subsequent dispatch can now acquire
    resp_reacquire = client.post("/api/remediation/ci-fix/dispatch", json=dispatch_payload)
    assert resp_reacquire.status_code == 200
    client.delete("/api/remediation/ci-fix/locks/Runner_Dashboard/202")


def test_acceptance_criteria_escalation_after_three_attempts() -> None:
    # Acceptance requirement: A seeded test failure escalates after 3 failed attempts
    route_att1 = route_ci_fix(failure_type="test", attempt_number=1, max_attempts=3)
    assert route_att1.escalated is False
    assert route_att1.tier == "cli"

    route_att2 = route_ci_fix(failure_type="test", attempt_number=2, max_attempts=3)
    assert route_att2.escalated is False
    assert route_att2.tier == "cli"

    route_att3 = route_ci_fix(failure_type="test", attempt_number=3, max_attempts=3)
    assert route_att3.escalated is True
    assert route_att3.tier == "strong"
    assert route_att3.model == "claude-opus-5-5"


QUEUE_REF = "gh-readonly-queue/main/pr-77-0123456789abcdef0123456789abcdef01234567"


def test_evaluate_trigger_recovers_pr_from_merge_queue_ref() -> None:
    """#1879: merge_group runs carry ``pull_requests: []``; the PR comes from the queue ref."""
    event = {
        "conclusion": "failure",
        "workflow_name": "CI Standard",
        "run_id": 1,
        "repository": "Runner_Dashboard",
        "head_branch": QUEUE_REF,
        "pull_requests": [],
    }
    decision = evaluate_ci_fix_trigger(event, lock_manager=CIFixLockManager())
    assert decision.eligible is True, decision.reason
    assert decision.pr_number == 77


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (QUEUE_REF, 77),
        ("gh-readonly-queue/release/2.0/pr-5-abc1234", 5),
        ("feat/pr-77-abc", None),
        ("gh-readonly-queue/main/pr-x-abc", None),
        ("", None),
    ],
)
def test_pr_number_from_queue_ref(ref: str, expected: int | None) -> None:
    from backend.ci_fix_dispatch import pr_number_from_queue_ref

    assert pr_number_from_queue_ref(ref) == expected


def test_count_prior_attempts(tmp_path: Path) -> None:
    from backend.ci_fix_dispatch import count_prior_attempts

    audit_file = tmp_path / "a.json"
    for pr, wf in ((42, "CI"), (42, "CI"), (42, "Lint"), (43, "CI")):
        record_ci_fix_audit(
            "Runner_Dashboard", pr, wf, 1, "test", "claude_code_cli", "m", 1, 1.0, audit_file=audit_file
        )
    assert count_prior_attempts("runner_dashboard", 42, "CI", audit_file=audit_file) == 2
    assert count_prior_attempts("Runner_Dashboard", 44, "CI", audit_file=audit_file) == 0
    assert count_prior_attempts("Runner_Dashboard", 42, "CI", audit_file=tmp_path / "missing.json") == 0
