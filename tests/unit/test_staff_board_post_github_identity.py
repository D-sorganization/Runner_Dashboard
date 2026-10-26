"""Staff board / lease GitHub writes must use fleet App tokens only (#3000)."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from staff.board_post import ensure_staff_github_app_write_env, staff_github_write_env_flags
from staff.github_run_env import build_staff_run_env

pytestmark = pytest.mark.unit

_FAKE_GHS = "ghs_" + "A" * 40 + "_fleet"
_FAKE_GHP = "ghp_" + "Z" * 36


def test_staff_github_write_flags_require_app() -> None:
    flags = staff_github_write_env_flags()
    assert flags["FLEET_GH_REQUIRE_APP"] == "1"
    assert flags["FLEET_AGENT_SESSION"] == "1"


def test_build_staff_run_env_sets_require_app_and_agent_session() -> None:
    env = build_staff_run_env(
        {"PATH": "/bin"},
        _FAKE_GHS,
        run_id="run-board-01",
        real_gh_path="/usr/bin/gh",
    )
    assert env["FLEET_GH_REQUIRE_APP"] == "1"
    assert env["FLEET_AGENT_SESSION"] == "1"
    assert env["GH_TOKEN"] == _FAKE_GHS


def test_board_post_path_refuses_personal_token() -> None:
    base = {"PATH": "/bin", "GH_TOKEN": _FAKE_GHP, "GITHUB_TOKEN": _FAKE_GHP}
    with pytest.raises(RuntimeError, match="personal or missing credentials are refused"):
        ensure_staff_github_app_write_env(base)


def test_board_post_path_accepts_app_installation_token() -> None:
    env = ensure_staff_github_app_write_env({"GH_TOKEN": _FAKE_GHS})
    assert env["GH_TOKEN"] == _FAKE_GHS
    assert env["FLEET_GH_REQUIRE_APP"] == "1"


def test_lease_acquire_passes_staff_github_env_to_rm_scripts(tmp_path: Any) -> None:
    from staff.lease import acquire
    from staff.store import RunStore

    store = RunStore(tmp_path / "runs.db")
    staff_env = {
        "PATH": "/wrappers:/bin",
        "GH_TOKEN": _FAKE_GHS,
        "GITHUB_TOKEN": _FAKE_GHS,
        "FLEET_GH_REQUIRE_APP": "1",
    }
    calls: list[dict[str, Any]] = []

    def fake_run(module: str, *args: str, root=None, timeout=120, env=None):  # noqa: ANN001
        calls.append({"module": module, "args": args, "env": dict(env or {})})
        from coordination.rm_scripts import ScriptResult

        if module == "check_agent_claim":
            return ScriptResult(0, '{"held": false}', "")
        return ScriptResult(0, '{"ok": true}', "")

    with patch("staff.lease.rm_root", return_value=tmp_path):
        with patch("staff.lease.run_module", side_effect=fake_run):
            note = acquire(
                store,
                "run-lease-env",
                repo="Tools",
                issue="42",
                agent="codex",
                branch="staff/codex-42",
                github_env=staff_env,
            )
    assert note is not None
    assert len(calls) == 3
    for call in calls:
        assert call["env"]["GH_TOKEN"] == _FAKE_GHS
        assert call["env"]["FLEET_GH_REQUIRE_APP"] == "1"
        assert _FAKE_GHP not in call["env"].values()


def test_compose_prompt_appends_staff_result_when_standards_pinned() -> None:
    from staff.roles import RoleSpec
    from staff.standards_registry import FleetStandards
    from staff.workspace import compose_prompt

    sample_role = RoleSpec(
        name="maintenance",
        title="Maintenance",
        providers=("dummy",),
        permissions={"lease": False},
    )

    pinned = FleetStandards(
        version="9.9.9",
        name="fleet-standards",
        body="short body without result line",
        content_hash="abc",
        source_path="<test>",
        digest="abc",
        source_revision="test",
    )
    from staff.dispatch_bundle import DispatchBundle

    bundle = DispatchBundle(
        skill_pin="",
        skill_version="",
        standards_version=pinned.version,
        standards_pin=pinned.pin,
        skill_block="",
        standards_block=f"Fleet standards pinned: {pinned.pin} (matches repository AGENTS.md).",
        standards=pinned,
    )
    prompt = compose_prompt(
        sample_role,
        repo="Tools",
        target_ref="issue #10",
        operator_prompt="Fix bug",
        branch="feat/10-bug",
        dispatch_bundle=bundle,
        reads_repo_instructions=False,
    )
    assert "STAFF_RESULT:" in prompt
