"""Regression tests for cloud dispatch context and retry mode (#2647)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from staff.cloud_dispatch import build_cloud_dispatch, resolve_plan_execution
from staff.retry import retry_plan
from staff.roles import RoleSpec
from staff.store import RunRecord


def _role() -> RoleSpec:
    return RoleSpec(
        name="ad-hoc",
        title="Ad hoc",
        providers=("claude",),
        permissions={"lease": False},
    )


def test_cloud_plan_uses_composed_prompt_not_raw_operator_prompt() -> None:
    """Issue-only cloud dispatch must keep role/task/repo context in the CLI argv."""
    req = SimpleNamespace(
        prompt="",
        execution_mode="cloud",
        repo="Runner_Dashboard",
        issue=2647,
        pr=None,
    )
    adapter = SimpleNamespace(
        build_command=lambda *a, **k: ["claude", "local-only"],
    )
    composed = "ROLE: implementer\nTARGET: Runner_Dashboard#2647\nFix cloud dispatch."
    mode, argv, _stdin, lease = resolve_plan_execution(
        req,
        _role(),
        "claude",
        adapter,
        composed,
        None,
        True,
        {},
    )
    assert mode == "cloud"
    assert lease is False
    assert argv[:2] == ["claude", "--remote"]
    assert argv[2] == composed


def test_retry_plan_preserves_cloud_argv_and_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_CODEX_CLOUD_ENV", "fleet-default")
    rec = RunRecord(
        id="run-cloud",
        role="ad-hoc",
        provider="codex",
        model="gpt-5",
        machine="hub",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="#99",
        prompt="composed retry body",
        status="failed",
        execution_mode="cloud",
    )
    runner = SimpleNamespace(_adapters={"codex": SimpleNamespace(build_command=lambda *a, **k: ["codex", "local"])})
    plan = retry_plan(runner, rec, _role())
    assert plan.execution_mode == "cloud"
    assert plan.argv == ["codex", "cloud", "exec", "--env", "fleet-default", "composed retry body"]


def test_build_codex_cloud_argv_matches_subscription_cli_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """``codex cloud exec`` requires ``--env``; ``--json`` is not valid on exec (#2647)."""
    monkeypatch.setenv("STAFF_CODEX_CLOUD_ENV", "acme-web")
    spec = build_cloud_dispatch("codex", "ship it", model="gpt-5.2")
    assert spec.argv == ["codex", "cloud", "exec", "--env", "acme-web", "ship it"]
