"""RD deploy/release HOLD blocks deploy slices on Runner_Dashboard dispatch (#2657)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from identity import Principal
from staff.dispatch_service import DispatchCommand, dispatch_staff_run
from staff.holds import HoldsList, RD_DEPLOY_RELEASE_HOLD_TEXT

_OPERATOR = Principal(id="tester", type="user", name="Tester", roles=["operator"], scopes=["staff.dispatch"])


def _hold_body() -> dict[str, Any]:
    return {
        "text": RD_DEPLOY_RELEASE_HOLD_TEXT,
        "applies_to": ["repo:Runner_Dashboard"],
        "kind": "guardrail",
        "active": True,
        "lifted_when": (
            "Main CI auto-release passes on two consecutive main commits with zero cancelled runs."
        ),
    }


@pytest.fixture
def holds_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "staff_holds.json"
    monkeypatch.setenv("STAFF_HOLDS_FILE", str(path))
    HoldsList(path, roles_loader=dict).replace([_hold_body()])
    return path


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_refused_for_runner_dashboard_deploy_slice_under_hold(
    holds_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cmd = DispatchCommand(
        role="ad-hoc",
        requested_by="tester",
        repo="Runner_Dashboard",
        prompt="Add a new step to `deploy/package-dashboard-artifact.sh` for release packaging.",
        dry_run=True,
    )
    with patch("staff.dispatch_service._refuse_if_excluded"), patch(
        "staff.dispatch_service.get_runner"
    ) as get_runner:
        get_runner.return_value.roles.return_value = {"ad-hoc": object()}
        with pytest.raises(HTTPException) as exc:
            await dispatch_staff_run(cmd, _OPERATOR)
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert detail["code"] == "dispatch_hold"
    assert RD_DEPLOY_RELEASE_HOLD_TEXT in detail["message"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_allows_auto_release_fix_under_rd_deploy_hold(
    holds_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cmd = DispatchCommand(
        role="ad-hoc",
        requested_by="tester",
        repo="Runner_Dashboard",
        prompt="Fix auto-release: adjust `.github/workflows/main-ci-auto-release.yml` concurrency.",
        dry_run=True,
    )
    with patch("staff.dispatch_service._refuse_if_excluded"), patch(
        "staff.dispatch_service.get_runner"
    ) as get_runner:
        runner = get_runner.return_value
        runner.roles.return_value = {"ad-hoc": SimpleNamespace(strategy={})}
        runner.plan.return_value = SimpleNamespace(
            to_dict=lambda: {},
            role="ad-hoc",
            provider="fake",
            repo="Runner_Dashboard",
            model="",
            target_ref="",
            provider_auth_skipped=None,
        )
        with patch("staff.dispatch_service.record_audit"), patch(
            "staff.provider_dispatch_preflight.refusal_for_provider_auth", return_value=None
        ), patch("staff.dispatch_service.check_rate_limit"), patch(
            "staff.dispatch_service._resolve_target", return_value="local"
        ):
            result = await dispatch_staff_run(cmd, _OPERATOR)
    assert result["dry_run"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_unrelated_runner_dashboard_work_not_blocked_by_rd_deploy_hold(
    holds_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cmd = DispatchCommand(
        role="ad-hoc",
        requested_by="tester",
        repo="Runner_Dashboard",
        prompt="Fix frontend tooltip copy in `frontend/src/pages/Fleet.tsx`.",
        dry_run=True,
    )
    with patch("staff.dispatch_service._refuse_if_excluded"), patch(
        "staff.dispatch_service.get_runner"
    ) as get_runner:
        runner = get_runner.return_value
        runner.roles.return_value = {"ad-hoc": SimpleNamespace(strategy={})}
        runner.plan.return_value = SimpleNamespace(
            to_dict=lambda: {},
            role="ad-hoc",
            provider="fake",
            repo="Runner_Dashboard",
            model="",
            target_ref="",
            provider_auth_skipped=None,
        )
        runner.machine = "test-node"
        with patch("staff.dispatch_service.record_audit"), patch(
            "staff.provider_dispatch_preflight.refusal_for_provider_auth", return_value=None
        ), patch("staff.dispatch_service.check_rate_limit"), patch(
            "staff.dispatch_service._resolve_target", return_value="local"
        ):
            result = await dispatch_staff_run(cmd, _OPERATOR)
    assert result["dry_run"] is True
