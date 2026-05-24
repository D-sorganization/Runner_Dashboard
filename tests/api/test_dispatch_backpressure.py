"""API-level regression coverage for quick-dispatch backpressure (#709)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from quick_dispatch import QuickDispatchResponse

VALID_QUICK_DISPATCH_BODY = {
    "repository": "runner-dashboard",
    "prompt": "Fix the failing test in test_api.py",
    "provider": "claude_code_cli",
}


def _mock_principal():
    from identity import Principal

    return Principal(id="test-admin", type="bot", name="Test", roles=["admin"])


def test_api_quick_dispatch_returns_503_with_retry_after_when_not_ready() -> None:
    os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

    from identity import require_principal
    from server import app

    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[require_principal] = _mock_principal
    try:
        with patch("quota_enforcement.quota_enforcement.check_dispatch_quota", return_value=(True, "")):
            with patch(
                "routers.remediation._quick_dispatch.quick_dispatch",
                new=AsyncMock(
                    return_value=QuickDispatchResponse(
                        accepted=False,
                        error_code="not_ready",
                        reason="no_online_runners",
                        retry_after_seconds=30,
                    )
                ),
            ):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    "/api/agents/quick-dispatch",
                    json=VALID_QUICK_DISPATCH_BODY,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "30"
    assert resp.json() == {
        "error": "not_ready",
        "reason": "no_online_runners",
        "retry_after_seconds": 30,
    }


def test_api_quick_dispatch_returns_202_on_success() -> None:
    os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

    from identity import require_principal
    from server import app

    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[require_principal] = _mock_principal
    try:
        with patch("quota_enforcement.quota_enforcement.check_dispatch_quota", return_value=(True, "")):
            with patch(
                "routers.remediation._quick_dispatch.quick_dispatch",
                new=AsyncMock(
                    return_value=QuickDispatchResponse(
                        accepted=True,
                        envelope_id="env-1",
                        fingerprint="fp-1",
                        workflow_run_url="https://example.test/run/1",
                        history_id="hist-1",
                    )
                ),
            ):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    "/api/agents/quick-dispatch",
                    json=VALID_QUICK_DISPATCH_BODY,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] is True
    assert body["workflow_run_url"] == "https://example.test/run/1"


def test_api_quick_dispatch_passes_force_flag_to_core_dispatch() -> None:
    os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

    from identity import require_principal
    from server import app

    seen_force: list[bool] = []

    async def _fake_quick_dispatch(req, **_kwargs):
        seen_force.append(bool(req.force))
        return QuickDispatchResponse(
            accepted=True,
            envelope_id="env-2",
            fingerprint="fp-2",
            workflow_run_url="https://example.test/run/2",
            history_id="hist-2",
        )

    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[require_principal] = _mock_principal
    try:
        with patch("quota_enforcement.quota_enforcement.check_dispatch_quota", return_value=(True, "")):
            with patch(
                "routers.remediation._quick_dispatch.quick_dispatch",
                new=AsyncMock(side_effect=_fake_quick_dispatch),
            ):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    "/api/agents/quick-dispatch",
                    json={**VALID_QUICK_DISPATCH_BODY, "force": True},
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)

    assert resp.status_code == 202
    assert seen_force == [True]
