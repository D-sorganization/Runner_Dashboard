"""Barb callers bypass per-role repo allowlists when planning dispatches (#4117)."""

from __future__ import annotations

from typing import Any

import pytest
from staff.plan import RunRequest
from staff.roles import RoleSpec
from staff.runner_planning import build_run_plan


@pytest.mark.unit
def test_barb_bypass_skips_role_repo_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    role = RoleSpec(
        name="backlog-push",
        title="Backlog Push",
        providers=("claude",),
        repos=("Runner_Dashboard",),
        permissions={"push_branch": True, "open_pr": True},
    )
    roles = {"backlog-push": role}

    class _StubAdapter:
        unattended = True

        def installed(self) -> bool:
            return True

    adapters = {"claude": _StubAdapter()}

    def _fake_bundle(*_a: Any, **_k: Any) -> Any:
        class _B:
            skill_pin = ""
            standards_version = ""
            standards_block = ""
            standards_tokens = 0
            lessons_block = ""

            def context_stamp(self, **_: Any) -> dict[str, str]:
                return {}

        return _B()

    monkeypatch.setattr("staff.dispatch_bundle.resolve_dispatch_bundle", _fake_bundle)
    monkeypatch.setattr("library.dispatch_hook.library_references_for_plan", lambda *_a, **_k: ("", []))
    monkeypatch.setattr("staff.lessons.select_repo_lessons", lambda *_a, **_k: type("L", (), {"tokens": 0})())
    monkeypatch.setattr("staff.consolidation.prompt_paragraph", lambda *_a, **_k: "")
    monkeypatch.setattr("staff.focus.load_items", lambda *_a, **_k: [])
    monkeypatch.setattr("staff.focus.focus_paragraph", lambda *_a, **_k: "")
    monkeypatch.setattr("staff.workspace.compose_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr("staff.lifecycle_fence.assert_fence_consumed", lambda *_a, **_k: None)
    monkeypatch.setattr("staff.provider_failover_config.resolve_failover_chain", lambda *_a, **_k: ())
    monkeypatch.setattr(
        "staff.failover_dispatch.resolve_provider_and_model",
        lambda *_a, **_k: ("claude", None, "", ()),
    )
    monkeypatch.setattr(
        "staff.failover_model.resolve_launch_model",
        lambda **_k: ("opus", None),
    )
    monkeypatch.setattr(
        "staff.cloud_dispatch.resolve_plan_execution",
        lambda *a, **k: ("local", ["claude", "--print", "x"], None, False),
    )

    req_narrow = RunRequest(
        role="backlog-push",
        repo="OtherRepo",
        issue=1,
        prompt="push",
        requested_by="agent-grok",
        barb_bypass_role_repos=False,
    )
    with pytest.raises(ValueError, match="scoped to"):
        build_run_plan(req_narrow, roles=roles, adapters=adapters, machine="local", focus_loader=lambda: [])

    req_barb = RunRequest(
        role="backlog-push",
        repo="OtherRepo",
        issue=1,
        prompt="push",
        requested_by="agent-grok",
        barb_bypass_role_repos=True,
    )
    plan = build_run_plan(req_barb, roles=roles, adapters=adapters, machine="local", focus_loader=lambda: [])
    assert plan.repo == "OtherRepo"
