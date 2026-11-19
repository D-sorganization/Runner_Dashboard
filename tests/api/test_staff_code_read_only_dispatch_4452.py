"""Dispatch API: read-only provider substitution (#4452)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from identity import Principal
from staff.dispatch_service import DispatchCommand, dispatch_staff_run
from staff.provider_failover_config import FailoverStep

from staff.roles import RoleSpec
from tests.api.test_staff_fleet import staff  # noqa: F401,F811

pytestmark = pytest.mark.unit

_ROLE = RoleSpec(
    name="code-reviewer",
    title="Code Reviewer",
    providers=("cursor-agent", "claude"),
    surface="dashboard",
    permissions={"code_read_only": True, "push_branch": False, "open_pr": False, "merge": False, "lease": False},
    failover_chain=(FailoverStep("cursor-agent"), FailoverStep("claude", "claude-opus-5-5")),
)

_CALLER = Principal(
    id="operator",
    type="human",
    name="Operator",
    roles=[],
    scopes=["staff.dispatch"],
)


@pytest.mark.unit
def _patch_role(staff, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    base = staff._roles_loader

    def _loader() -> dict:
        loaded = dict(base())
        loaded["code-reviewer"] = _ROLE
        return loaded

    monkeypatch.setattr(staff, "_roles_loader", _loader)


def test_dry_run_reports_substitution(staff, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _patch_role(staff, monkeypatch)

    async def _run() -> dict:
        return await dispatch_staff_run(
            DispatchCommand(
                role="code-reviewer",
                requested_by="operator",
                provider="cursor-agent",
                prompt="review",
                dry_run=True,
            ),
            _CALLER,
        )

    res = asyncio.run(_run())
    assert res["dry_run"] is True
    assert res["plan"]["provider"] == "claude"
    assert res["requested_provider"] == "cursor-agent"
    assert res["substitution_reason"] == "code_read_only"


@pytest.mark.unit
def test_dispatch_records_substitution_event(staff, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _patch_role(staff, monkeypatch)

    async def _run() -> dict:
        with patch.object(staff, "launch", lambda rec, plan: None):
            return await dispatch_staff_run(
                DispatchCommand(
                    role="code-reviewer",
                    requested_by="operator",
                    provider="cursor-agent",
                    prompt="review",
                ),
                _CALLER,
            )

    res = asyncio.run(_run())
    run_id = res["run"]["id"]
    kinds = [row["kind"] for row in staff.store.events_after(run_id, 0)]
    assert "requested_provider_substituted" in kinds
    assert res["substitution_reason"] == "code_read_only"
