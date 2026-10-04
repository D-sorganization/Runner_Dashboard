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
    assert route.provider in ("codex_cli", "gemini_cli")
    assert route.tier == "cheap"
    assert route.cost_budget <= 0.50
    assert route.escalated is False


def test_route_ci_fix_test_to_cli_tier() -> None:
    route = route_ci_fix(
        failure_type="test",
        attempt_number=1,
        max_attempts=3,
    )
    assert route.provider == "claude_code_cli"
    assert route.tier == "cli"
    assert route.escalated is False


def test_route_ci_fix_escalation_after_cap() -> None:
    route = route_ci_fix(
        failure_type="test",
        attempt_number=3,
        max_attempts=3,
    )
    assert route.escalated is True
    assert route.tier == "strong"
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
        cost_estimate=0.15,
        audit_file=audit_file,
    )
    assert audit_file.is_file()
    import json

    entries = json.loads(audit_file.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert entries[0]["pr_number"] == 42
    assert entries[0]["cost_estimate"] == 0.15


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


def test_remediation_ci_fix_dispatch_lifecycle(client: TestClient) -> None:
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
    assert route_att3.model == "claude-3-7-opus"
