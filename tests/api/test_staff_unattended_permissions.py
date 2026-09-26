"""Unattended staff runs never bypass CLI permissions (issue #1586).

Every adapter launches its CLI with a declared allow-list instead of a bypass
flag. The flags below were verified against the installed CLIs on 2026-09-26
(claude 2.1.280, codex 0.156.1, cursor-agent 2026.09.18, gemini 0.39.1): an
allow-listed ``git --version`` ran and a file edit landed, and anything
unlisted was refused rather than prompted. agy 1.2.11 refuses every shell
command headlessly unless ``--dangerously-skip-permissions`` is set, so it is
chat-only until it gains a usable allow-list.
"""

from __future__ import annotations

import pytest
from staff import adapters as adapters_mod
from staff.adapters import (
    ADAPTERS,
    PERMISSION_BYPASS_FLAGS,
    UNATTENDED_SHELL_ALLOW,
    UNATTENDED_SHELL_DENY,
    UnattendedUnsupportedError,
    claude_unattended_tools,
    gemini_policy_toml,
)

UNATTENDED = [pid for pid, adapter in ADAPTERS.items() if adapter.unattended]


@pytest.mark.unit
@pytest.mark.parametrize("provider", UNATTENDED)
def test_no_unattended_argv_carries_a_bypass_flag(provider: str) -> None:
    argv = ADAPTERS[provider].build_command("do it", "/tmp/wt", model="m", gitdir="/repo/.git")
    assert not PERMISSION_BYPASS_FLAGS & set(argv), argv


@pytest.mark.unit
def test_bypass_set_names_every_known_bypass_flag() -> None:
    assert {
        "bypassPermissions",
        "--dangerously-skip-permissions",
        "--dangerously-bypass-approvals-and-sandbox",
        "--yolo",
        "--force",
        "yolo",
        "danger-full-access",
    } <= PERMISSION_BYPASS_FLAGS


@pytest.mark.unit
def test_claude_refuses_anything_not_on_the_allow_list() -> None:
    argv = ADAPTERS["claude"].build_command("do it", "/tmp/wt", model=None)
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert {"Read", "Edit", "Write", "Bash(git:*)", "Bash(gh:*)"} <= set(allowed)
    assert "Bash" not in allowed  # never the whole shell
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert "Bash(sudo:*)" in denied and "Bash(gh repo delete:*)" in denied


@pytest.mark.unit
def test_claude_ollama_shares_the_claude_permission_model() -> None:
    claude = ADAPTERS["claude"].build_command("x", "/w", model="m")
    local = ADAPTERS["claude-ollama"].build_command("x", "/w", model="m")
    assert claude == local


@pytest.mark.unit
def test_claude_tool_list_is_rendered_from_the_shared_shell_tables() -> None:
    allowed, denied = claude_unattended_tools()
    for command in UNATTENDED_SHELL_ALLOW:
        assert f"Bash({command}:*)" in allowed
    for command in UNATTENDED_SHELL_DENY:
        assert f"Bash({command}:*)" in denied
    assert not set(UNATTENDED_SHELL_ALLOW) & set(UNATTENDED_SHELL_DENY)


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["codex", "ollama"])
def test_codex_writes_only_inside_its_worktree_and_git_dir(provider: str) -> None:
    argv = ADAPTERS[provider].build_command("do it", "/tmp/wt", model=None, gitdir="/repo/.git")
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert argv[argv.index("--add-dir") + 1] == "/repo/.git"
    assert "sandbox_workspace_write.network_access=true" in argv  # git push / gh need the network
    assert "--json" in argv
    assert argv[-1] == "do it"


@pytest.mark.unit
def test_codex_git_dir_defaults_to_the_workdir() -> None:
    argv = ADAPTERS["codex"].build_command("do it", "/tmp/wt", model=None)
    assert argv[argv.index("--add-dir") + 1] == "/tmp/wt"


@pytest.mark.unit
def test_codex_json_events_carry_the_answer_and_tokens() -> None:
    codex = ADAPTERS["codex"]
    msg = codex.parse_line(
        '{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"DONE\\nSTAFF_RESULT: ok"}}'
    )
    assert "STAFF_RESULT: ok" in msg["text"]
    done = codex.parse_line(
        '{"type":"turn.completed","usage":{"input_tokens":57753,"cached_input_tokens":50176,"output_tokens":98}}'
    )
    assert done["usage"] == {"input_tokens": 57753, "output_tokens": 98}


@pytest.mark.unit
def test_codex_command_items_are_not_mistaken_for_answers() -> None:
    ev = ADAPTERS["codex"].parse_line(
        '{"type":"item.completed","item":{"type":"command_execution","command":"git status","aggregated_output":"x"}}'
    )
    assert ev["text"] == ""


@pytest.mark.unit
def test_cursor_agent_runs_sandboxed_without_force() -> None:
    argv = ADAPTERS["cursor-agent"].build_command("fix it", "/tmp/wt", model=None)
    assert argv[argv.index("--sandbox") + 1] == "enabled"
    assert "--trust" in argv  # workspace trust for its own worktree, not a permission bypass
    assert "--force" not in argv and "--yolo" not in argv


@pytest.mark.unit
def test_gemini_edits_freely_and_runs_only_policy_allowed_commands() -> None:
    gemini = ADAPTERS["gemini"]
    argv = gemini.build_command("fix it", "/tmp/wt", model=None, policy="/cfg/gemini.toml")
    assert argv[argv.index("--approval-mode") + 1] == "auto_edit"
    assert argv[argv.index("--policy") + 1] == "/cfg/gemini.toml"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert gemini.runtime_env()["GEMINI_CLI_TRUST_WORKSPACE"] == "true"
    assert gemini.policy_text is gemini_policy_toml


@pytest.mark.unit
def test_gemini_policy_allows_the_shared_list_and_denies_above_it() -> None:
    text = gemini_policy_toml()
    assert text.count("[[rule]]") == 2
    allow, deny = text.split("[[rule]]")[1:]
    assert 'decision = "allow"' in allow and 'decision = "deny"' in deny
    for command in UNATTENDED_SHELL_ALLOW:
        assert f'"{command}"' in allow
    for command in UNATTENDED_SHELL_DENY:
        assert f'"{command}"' in deny
    assert int(deny.split("priority = ")[1].split()[0]) > int(allow.split("priority = ")[1].split()[0])


@pytest.mark.unit
def test_antigravity_is_chat_only() -> None:
    agy = ADAPTERS["antigravity"]
    assert agy.unattended is False
    with pytest.raises(UnattendedUnsupportedError):
        agy.build_command("do it", "/tmp/wt")
    chat = agy.chat_argv("hi", "/tmp/wt")
    assert chat[chat.index("--mode") + 1] == "plan"


@pytest.mark.unit
def test_every_chat_argv_stays_free_of_bypass_flags() -> None:
    for pid, adapter in ADAPTERS.items():
        if pid == "maxwell":
            continue
        argv = adapter.chat_argv("hi", "/tmp/wt")
        assert not PERMISSION_BYPASS_FLAGS & set(argv), pid


@pytest.mark.unit
def test_policy_placeholder_is_dropped_for_providers_without_one() -> None:
    argv = ADAPTERS["claude"].build_command("x", "/w", policy="/cfg/p.toml")
    assert "/cfg/p.toml" not in argv
    assert adapters_mod.ADAPTERS["claude"].policy_text is None


@pytest.mark.unit
def test_retry_fallback_skips_chat_only_providers() -> None:
    from staff import retry as retry_mod
    from staff.roles import RoleSpec

    role = RoleSpec(name="r", title="R", providers=("claude", "antigravity", "codex"))
    usable = lambda pid: ADAPTERS[pid].unattended  # noqa: E731
    assert retry_mod.next_fallback_provider(role, "claude", usable=usable) == "codex"
    chained = RoleSpec(name="r", title="R", fallback_providers=("antigravity", "gemini"))
    assert retry_mod.next_fallback_provider(chained, "claude", usable=usable) == "gemini"


@pytest.mark.unit
def test_runner_never_picks_a_chat_only_provider(tmp_path) -> None:
    from staff.plan import RunRequest
    from staff.roles import RoleSpec
    from staff.runner import StaffRunner
    from staff.store import RunStore

    role = RoleSpec(name="r", title="R", providers=("antigravity", "codex"), surface="dashboard")
    runner = StaffRunner(store=RunStore(tmp_path / "s.db"), roles_loader=lambda: {"r": role})
    assert runner.plan(RunRequest(role="r", prompt="go")).provider == "codex"
    with pytest.raises(ValueError, match="chat-only"):
        runner.plan(RunRequest(role="r", provider="antigravity", prompt="go"))


@pytest.mark.unit
def test_launch_paths_point_at_git_common_dir_and_generated_policy(tmp_path, monkeypatch) -> None:
    import subprocess

    from staff.runner import StaffRunner

    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(tmp_path / "wt"))
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    codex = StaffRunner._launch_paths(ADAPTERS["codex"], repo)
    assert codex["gitdir"].replace("\\", "/").endswith("repo/.git") and "policy" not in codex
    gemini = StaffRunner._launch_paths(ADAPTERS["gemini"], repo)
    policy = tmp_path / "wt" / ".policies" / "gemini.toml"
    assert gemini == {"policy": str(policy)} and policy.read_text(encoding="utf-8") == gemini_policy_toml()
    assert StaffRunner._launch_paths(ADAPTERS["claude"], repo) == {}  # only slots the argv uses
    assert StaffRunner._launch_paths(ADAPTERS["codex"], tmp_path)["gitdir"] == str(tmp_path)


@pytest.mark.unit
def test_codex_chat_stays_plain_text_while_runs_are_json() -> None:
    for pid in ("codex", "ollama"):
        assert ADAPTERS[pid].json_lines is True and ADAPTERS[pid].chat_json is False
        assert "--json" not in ADAPTERS[pid].chat_argv("hi", "/w")
    assert ADAPTERS["claude"].chat_json is True
