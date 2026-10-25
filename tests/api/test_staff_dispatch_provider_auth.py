"""Dispatch refuses expired provider OAuth before queueing (#2170)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from identity import Principal
from staff.dispatch_service import DispatchCommand, dispatch_staff_run


def test_dispatch_skips_oauth_preflight_in_e2e_hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_E2E_HERMETIC", "1")
    from staff.provider_dispatch_preflight import refusal_for_provider_auth

    assert refusal_for_provider_auth("claude", "local") is None


@pytest.mark.asyncio
async def test_dispatch_refuses_expired_antigravity_oauth() -> None:
    cmd = DispatchCommand(
        role="night-watch",
        requested_by="tester",
        provider="antigravity",
        repo="Runner_Dashboard",
        issue=1,
        prompt="fix it",
        skip_premise_check=True,
    )
    caller = Principal(id="tester", type="user", name="Tester", roles=["admin"])

    with (
        patch("staff.dispatch_service.get_runner") as mock_runner,
        patch(
            "staff.provider_dispatch_preflight.refusal_for_provider_auth",
            return_value=type(
                "R",
                (),
                {
                    "to_http_detail": lambda self: {
                        "code": "provider_auth_expired",
                        "message": "Authentication expired for antigravity",
                        "remediation": "Run `agy auth login` on node hub-a.",
                        "failure_class": "auth_expired",
                    }
                },
            )(),
        ),
    ):
        runner = mock_runner.return_value
        runner.machine = "hub-a"
        runner.plan.return_value = type("Plan", (), {"provider": "antigravity", "role": "night-watch"})()
        with pytest.raises(HTTPException) as exc:
            await dispatch_staff_run(cmd, caller)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "provider_auth_expired"
