"""Unit tests for code-read-only unattended staff runs (#1659)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.adapter_policies import (
    CLAUDE_WRITE_TOOLS,
    PERMISSION_BYPASS_FLAGS,
    READ_ONLY_RUN_PROVIDERS,
    READ_ONLY_RUN_SHELL_ALLOW,
    UnattendedUnsupportedError,
    claude_read_only_run_tools,
)
from staff.adapters import ADAPTERS
from staff.plan import RunRequest
from staff.roles import RoleSpec
from staff.runner import StaffRunner
from staff.store import RunStore
from staff.workspace import FLEET_RULES, READ_ONLY_FLEET_RULES, compose_prompt


@pytest.mark.unit
def test_role_spec_code_read_only_is_explicit_opt_in() -> None:
    def role(**perms: object) -> RoleSpec:
        return RoleSpec(name="r", title="R", permissions=dict(perms))

    assert role(code_read_only=True, push_branch=False, open_pr=False).code_read_only is True
    # Not derived: code-reviewer / maintenance push nothing yet still write reviews or call write APIs.
    assert role(push_branch=False, open_pr=False).code_read_only is False
    assert role().code_read_only is False
    assert role(code_read_only="yes").code_read_only is False
    assert role(code_read_only=False).code_read_only is False


@pytest.mark.unit
def test_claude_read_only_run_tools() -> None:
    allowed, denied = claude_read_only_run_tools()
    allowed_list = allowed.split(",")
    denied_list = denied.split(",")

    # None of "Edit", "Write", "MultiEdit", "NotebookEdit" is an allowed entry
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        assert tool not in allowed_list

    # "Bash(git:*)", "Bash(gh:*)", "Bash(gh api:*)", "Bash(find:*)", "Bash(curl:*)" are NOT allowed entries
    for not_allowed in ("Bash(git:*)", "Bash(gh:*)", "Bash(gh api:*)", "Bash(find:*)", "Bash(curl:*)"):
        assert not_allowed not in allowed_list

    # Every allowed "Bash(...)" entry is in the exact allowlist
    bash_entries = [entry for entry in allowed_list if entry.startswith("Bash(")]
    assert len(bash_entries) == len(READ_ONLY_RUN_SHELL_ALLOW)
    for entry in bash_entries:
        assert entry.startswith("Bash(") and entry.endswith(":*)")
        prefix = entry[len("Bash(") : -len(":*)")]
        assert prefix in READ_ONLY_RUN_SHELL_ALLOW

    # The four write tools ARE in denied
    for write_tool in CLAUDE_WRITE_TOOLS:
        assert write_tool in denied_list

    # "Bash(git push:*)" and "Bash(gh pr create:*)" are in denied
    assert "Bash(git push:*)" in denied_list
    assert "Bash(gh pr create:*)" in denied_list


@pytest.mark.unit
def test_claude_adapter_read_only_build_command() -> None:
    adapter = ADAPTERS["claude"]
    argv = adapter.build_command("investigate repo", "/w", None, read_only=True)

    # Argv never contains a PERMISSION_BYPASS_FLAGS entry
    assert not PERMISSION_BYPASS_FLAGS & set(argv)

    # Value after "--allowedTools" equals the allowed string from claude_read_only_run_tools()
    allowed_str = argv[argv.index("--allowedTools") + 1]
    ro_allowed, ro_denied = claude_read_only_run_tools()
    assert allowed_str == ro_allowed

    # Allowed tools string does not contain "Edit,"
    assert "Edit," not in allowed_str

    # No argv element contains "Bash(git:*)", "Bash(gh:*)", or "gh api -X"
    for forbidden in ("Bash(git:*)", "Bash(gh:*)", "gh api -X"):
        assert not any(forbidden in arg for arg in argv)

    # No argv element outside --disallowedTools contains "Edit,"
    disallowed_idx = argv.index("--disallowedTools") + 1
    assert not any("Edit," in arg for i, arg in enumerate(argv) if i != disallowed_idx)

    # build_command without read_only is unchanged (still contains "Bash(git:*)")
    normal_argv = adapter.build_command("investigate repo", "/w", None)
    assert any("Bash(git:*)" in arg for arg in normal_argv)


@pytest.mark.unit
def test_non_claude_unattended_adapter_raises_read_only() -> None:
    for pid, adapter in ADAPTERS.items():
        if adapter.unattended and pid not in READ_ONLY_RUN_PROVIDERS:
            with pytest.raises(UnattendedUnsupportedError, match="has no code-read-only unattended mode"):
                adapter.build_command("do task", "/w", None, read_only=True)


@pytest.mark.unit
def test_compose_prompt_code_read_only() -> None:
    role_ro = RoleSpec(
        name="barb",
        title="Barb",
        permissions={"push_branch": False, "open_pr": False, "code_read_only": True},
    )
    prompt_ro = compose_prompt(
        role_ro,
        repo="UpstreamDrift",
        target_ref="123",
        operator_prompt="triage",
        branch="staff/barb-123",
    )
    assert READ_ONLY_FLEET_RULES in prompt_ro
    assert "DRAFT pull request" not in prompt_ro
    assert "You are in a read-only working copy." in prompt_ro

    role_normal = RoleSpec(
        name="night-watch",
        title="Night Watch",
        permissions={"push_branch": True, "open_pr": True},
    )
    prompt_normal = compose_prompt(
        role_normal,
        repo="UpstreamDrift",
        target_ref="123",
        operator_prompt="fix it",
        branch="staff/night-watch-123",
    )
    assert FLEET_RULES in prompt_normal
    assert "DRAFT pull request" in prompt_normal
    assert "You are in an isolated git worktree on branch staff/night-watch-123." in prompt_normal


@pytest.mark.unit
def test_staff_runner_plan_code_read_only(tmp_path: Path) -> None:
    role = RoleSpec(
        name="barb",
        title="Barb",
        providers=("claude",),
        permissions={"push_branch": False, "open_pr": False, "code_read_only": True},
        surface="dashboard",
    )
    runner = StaffRunner(
        store=RunStore(tmp_path / "runs.sqlite3"),
        roles_loader=lambda: {"barb": role},
    )
    plan = runner.plan(RunRequest(role="barb", prompt="check status", repo="UpstreamDrift"))
    allowed_tools = plan.argv[plan.argv.index("--allowedTools") + 1].split(",")
    assert "Edit" not in allowed_tools
    assert "DRAFT pull request" not in plan.prompt
    assert READ_ONLY_FLEET_RULES in plan.prompt


@pytest.mark.unit
def test_staff_runner_resolve_provider_read_only(tmp_path: Path) -> None:
    role = RoleSpec(
        name="barb",
        title="Barb",
        providers=("codex", "claude"),
        permissions={"push_branch": False, "open_pr": False, "code_read_only": True},
        surface="dashboard",
    )
    runner = StaffRunner(
        store=RunStore(tmp_path / "runs.sqlite3"),
        roles_loader=lambda: {"barb": role},
    )
    # Picks first provider in READ_ONLY_RUN_PROVIDERS when caller didn't specify
    plan = runner.plan(RunRequest(role="barb", prompt="read only"))
    assert plan.provider == "claude"

    # Raises ValueError when caller specifies a non-read-only provider
    with pytest.raises(ValueError, match="runs code-read-only; provider 'codex' has no read-only unattended mode"):
        runner.plan(RunRequest(role="barb", provider="codex", prompt="read only"))


@pytest.mark.unit
@pytest.mark.parametrize(
    "write_rule",
    ["Edit", "Write", "Bash(git push:*)", "Bash(git commit:*)", "Bash(gh pr create:*)", "Bash(gh api:*)", "Bash(gh:*)"],
)
def test_read_only_allowlist_never_grants_a_write_path(write_rule: str) -> None:
    """Peer review of #1659: an allowlist, not a denylist, closes gh api -X / git push aliases."""
    allowed, _ = claude_read_only_run_tools()
    assert write_rule not in allowed.split(",")
    assert not any(entry.startswith("Bash(gh api") for entry in allowed.split(","))


@pytest.mark.unit
@pytest.mark.parametrize(("value", "ok"), [(True, True), (False, True), ("yes", False)])
def test_validator_accepts_boolean_code_read_only(value: object, ok: bool) -> None:
    """The flag must validate before Repository_Management sets it on Barb, or she turns invalid."""
    from staff.validator import validate_role_data

    from tests.unit.test_staff_roles import _VALID_ROLE_DICT

    role_dict = dict(_VALID_ROLE_DICT)
    role_dict["permissions"] = {**_VALID_ROLE_DICT["permissions"], "code_read_only": value}
    problems = validate_role_data(role_dict)
    assert (problems == []) is ok, problems
