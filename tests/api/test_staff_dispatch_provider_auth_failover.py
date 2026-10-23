"""Dispatch fails over when the first provider OAuth seat is expired (#2170)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from identity import Principal
from staff.dispatch_service import DispatchCommand, dispatch_staff_run


@pytest.mark.asyncio
async def test_dispatch_uses_next_provider_when_first_auth_expired() -> None:
    cmd = DispatchCommand(
        role="night-watch",
        requested_by="tester",
        repo="Runner_Dashboard",
        issue=1,
        prompt="fix it",
        skip_premise_check=True,
        dry_run=True,
    )
    caller = Principal(id="tester", type="user", name="Tester", roles=["admin"])

    from staff.plan import RunPlan

    plan = RunPlan(
        role="night-watch",
        provider="codex",
        model="gpt-6-luna",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="#1",
        operator_prompt="fix it",
        prompt="full",
        argv=["codex"],
        branch="staff/night-watch-1-abc",
        lease_ritual=False,
        provider_auth_skipped=({"provider": "claude", "outcome": "auth_expired"},),
    )

    with (
        patch("staff.dispatch_service.get_runner") as mock_runner,
        patch("staff.provider_dispatch_preflight.refusal_for_provider_auth", return_value=None),
    ):
        runner = mock_runner.return_value
        runner.machine = "DeskComputer"
        runner.plan.return_value = plan
        out = await dispatch_staff_run(cmd, caller)

    assert out["dry_run"] is True
    assert out["plan"]["provider"] == "codex"
    assert out["plan"]["provider_auth_skipped"][0]["provider"] == "claude"


@pytest.mark.asyncio
async def test_dispatch_still_409_when_all_providers_expired() -> None:
    cmd = DispatchCommand(
        role="night-watch",
        requested_by="tester",
        provider="claude",
        repo="Runner_Dashboard",
        prompt="x",
        skip_premise_check=True,
    )
    caller = Principal(id="tester", type="user", name="Tester", roles=["admin"])
    refusal = type(
        "R",
        (),
        {
            "to_http_detail": lambda self: {
                "code": "provider_auth_expired",
                "failure_class": "auth_expired",
                "message": "expired",
                "remediation": "login",
            }
        },
    )()

    with (
        patch("staff.dispatch_service.get_runner") as mock_runner,
        patch("staff.provider_dispatch_preflight.refusal_for_provider_auth", return_value=refusal),
    ):
        runner = mock_runner.return_value
        runner.machine = "Desk"
        runner.plan.return_value = type("Plan", (), {"provider": "claude", "role": "night-watch"})()
        with pytest.raises(HTTPException) as exc:
            await dispatch_staff_run(cmd, caller)
    assert exc.value.status_code == 409
