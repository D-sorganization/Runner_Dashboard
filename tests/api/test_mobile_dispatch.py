"""Backend tests for the mobile dispatch path through POST /api/agent-remediation/dispatch.

Tests the same endpoint that RemediationMobile.tsx (issue #196 M10) calls when
the user completes the 3-tap agent dispatch flow on mobile.

Test patterns follow tests/test_quick_dispatch.py and tests/conftest.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import agent_remediation  # noqa: E402


# -- Helpers ------------------------------------------------------------------


def _make_run_cmd(returncode: int = 0, stdout: str = "", stderr: str = "") -> AsyncMock:
    """Build a mock run_cmd coroutine that returns (returncode, stdout, stderr)."""

    async def _run(
        cmd: list[str],
        timeout: int = 30,
        cwd: Path | None = None,
    ) -> tuple[int, str, str]:
        return returncode, stdout, stderr

    return AsyncMock(side_effect=_run)


def _make_avail_patch(available: bool = True):
    """Context-manager patch that stubs probe_provider_availability."""
    status = "available" if available else "unavailable"
    detail = "ready" if available else "agent binary missing"
    return patch(
        "agent_remediation.probe_provider_availability",
        return_value={
            "claude_code_cli": type(
                "_Avail",
                (),
                {"available": available, "status": status, "detail": detail},
            )(),
        },
    )


def _make_policy_patch(auto_dispatch: bool = True):
    """Return a RemediationPolicy that accepts dispatches by default."""
    policy = agent_remediation.RemediationPolicy(
        auto_dispatch_on_failure=auto_dispatch,
        require_failure_summary=False,
        require_non_protected_branch=False,
        max_same_failure_attempts=5,
        attempt_window_hours=24,
        provider_order=("claude_code_cli",),
        enabled_providers=("claude_code_cli",),
        default_provider="claude_code_cli",
    )
    return patch("agent_remediation.load_policy", return_value=policy)


# -- Minimal request body fixtures --------------------------------------------


VALID_MOBILE_DISPATCH_BODY = {
    "repository": "runner-dashboard",
    "workflow_name": "ci.yml",
    "branch": "main",
    "failure_reason": "Mobile dispatch for runner-dashboard: ci.yml",
    "log_excerpt": "Dispatched via mobile remediation flow. Item ID: 1001",
    "run_id": 1001,
    "provider": "claude_code_cli",
    "dispatch_origin": "manual",
}


# -- Tests --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_well_formed_mobile_dispatch_accepted() -> None:
    """A well-formed mobile dispatch request does not get rejected with a 422."""
    import os

    os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

    from fastapi.testclient import TestClient
    from identity import Principal, require_principal
    from server import app

    def _mock_principal():
        return Principal(id="test-admin", type="bot", name="Test", roles=["admin"])

    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[require_principal] = _mock_principal
    try:
        with _make_policy_patch(), _make_avail_patch(True):
            with patch("routers.remediation.run_cmd", return_value=_make_run_cmd(0)):
                with patch("quota_enforcement.quota_enforcement.check_dispatch_quota", return_value=(True, "")):
                    with patch("quota_enforcement.quota_enforcement.add_spend"):
                        with patch("routers.remediation._append_remediation_history"):
                            client = TestClient(app, raise_server_exceptions=False)
                            resp = client.post(
                                "/api/agent-remediation/dispatch",
                                json=VALID_MOBILE_DISPATCH_BODY,
                                headers={"X-Requested-With": "XMLHttpRequest"},
                            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    assert resp.status_code not in (422, 500), (
        f"Unexpected error {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_invalid_repo_name_rejected_422() -> None:
    """A repository name with shell metacharacters is rejected with 422."""
    import os

    os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

    from fastapi.testclient import TestClient
    from identity import Principal, require_principal
    from server import app

    bad_body = {**VALID_MOBILE_DISPATCH_BODY, "repository": "repo-with-bad; chars"}

    def _mock_principal():
        return Principal(id="test-admin", type="bot", name="Test", roles=["admin"])

    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[require_principal] = _mock_principal
    try:
        with _make_policy_patch(), _make_avail_patch(True):
            with patch("quota_enforcement.quota_enforcement.check_dispatch_quota", return_value=(True, "")):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    "/api/agent-remediation/dispatch",
                    json=bad_body,
                )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    assert resp.status_code == 422, (
        f"Expected 422 for invalid repo name, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_rate_limit_enforced() -> None:
    """check_dispatch_rate raises HTTPException 429 on rate-limit breach."""
    import os

    os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from identity import Principal, require_principal
    from server import app

    def _mock_principal():
        return Principal(id="test-admin", type="bot", name="Test", roles=["admin"])

    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[require_principal] = _mock_principal
    try:
        with _make_policy_patch(), _make_avail_patch(True):
            with patch(
                "routers.remediation.check_dispatch_rate",
                side_effect=HTTPException(status_code=429, detail="rate_limited"),
            ):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    "/api/agent-remediation/dispatch",
                    json=VALID_MOBILE_DISPATCH_BODY,
                )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    assert resp.status_code == 429, (
        f"Expected 429, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_missing_required_fields_rejected() -> None:
    """A request body missing repository/workflow_name/branch is rejected."""
    import os

    os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

    from fastapi.testclient import TestClient
    from identity import Principal, require_principal
    from server import app

    incomplete_body = {
        "failure_reason": "some failure",
        "provider": "claude_code_cli",
    }

    def _mock_principal():
        return Principal(id="test-admin", type="bot", name="Test", roles=["admin"])

    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[require_principal] = _mock_principal
    try:
        with _make_policy_patch(), _make_avail_patch(True):
            with patch("quota_enforcement.quota_enforcement.check_dispatch_quota", return_value=(True, "")):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    "/api/agent-remediation/dispatch",
                    json=incomplete_body,
                )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    assert resp.status_code != 200, (
        f"Expected non-200 for incomplete body, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_plan_dispatch_accepts_mobile_context() -> None:
    """Unit-level: plan_dispatch accepts a FailureContext mirroring the mobile payload."""
    context = agent_remediation.FailureContext(
        repository="runner-dashboard",
        workflow_name="ci.yml",
        branch="main",
        failure_reason="Mobile dispatch for runner-dashboard: ci.yml",
        log_excerpt="Dispatched via mobile remediation flow. Item ID: 1001",
        run_id=1001,
        source="mobile",
    )
    policy = agent_remediation.RemediationPolicy(
        auto_dispatch_on_failure=True,
        require_failure_summary=False,
        require_non_protected_branch=False,
        max_same_failure_attempts=5,
        attempt_window_hours=24,
        provider_order=("claude_code_cli",),
        enabled_providers=("claude_code_cli",),
        default_provider="claude_code_cli",
    )
    availability = {
        "claude_code_cli": agent_remediation.ProviderAvailability(
            provider_id="claude_code_cli",
            available=True,
            status="available",
            detail="ready",
        )
    }
    decision = agent_remediation.plan_dispatch(
        context,
        policy=policy,
        availability=availability,
        attempts=[],
        provider_override="claude_code_cli",
        dispatch_origin="manual",
    )

    assert decision.accepted is True
    assert decision.provider_id == "claude_code_cli"
    assert decision.fingerprint


@pytest.mark.asyncio
async def test_quota_exceeded_returns_403() -> None:
    """When quota is exceeded, dispatch returns 403."""
    import os

    os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

    from fastapi.testclient import TestClient
    from identity import Principal, require_principal
    from server import app

    def _mock_principal():
        return Principal(id="test-admin", type="bot", name="Test", roles=["admin"])

    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[require_principal] = _mock_principal
    try:
        with _make_policy_patch(), _make_avail_patch(True):
            with patch("routers.remediation.check_dispatch_rate"):
                with patch(
                    "quota_enforcement.quota_enforcement.check_dispatch_quota",
                    return_value=(False, "hourly cap reached"),
                ):
                    client = TestClient(app, raise_server_exceptions=False)
                    resp = client.post(
                        "/api/agent-remediation/dispatch",
                        json=VALID_MOBILE_DISPATCH_BODY,
                    )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    assert resp.status_code == 403, (
        f"Expected 403 for quota exceeded, got {resp.status_code}: {resp.text}"
    )