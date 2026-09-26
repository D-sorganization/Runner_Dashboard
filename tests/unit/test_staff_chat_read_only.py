"""Chat turns are read-only on every provider, fresh or resumed (#1484)."""

from __future__ import annotations

import pytest
from staff.adapters import (
    CHAT_READ_ONLY_TOOLS,
    ChatReadOnlyUnsupportedError,
    get_adapter,
)
from staff.validator import validate_role_data

# provider -> (flag, value) that must appear in every chat argv
READ_ONLY_FLAG = {
    "claude": ("--permission-mode", "default"),
    "claude-ollama": ("--permission-mode", "default"),
    "codex": ("--sandbox", "read-only"),
    "ollama": ("--sandbox", "read-only"),
    "antigravity": ("--mode", "plan"),
    "gemini": ("--approval-mode", "plan"),
    "cursor-agent": ("--mode", "ask"),
}
BYPASS = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-skip-permissions",
    "bypassPermissions",
    "--force",
    "--trust",
    "--yolo",
    "-y",
}


def _pair(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv else None


@pytest.mark.unit
@pytest.mark.parametrize("provider", sorted(READ_ONLY_FLAG))
@pytest.mark.parametrize("session_id", [None, "sess_123"], ids=["fresh", "resume"])
def test_every_chat_argv_is_read_only(provider: str, session_id: str | None) -> None:
    argv = get_adapter(provider).chat_argv("hi", "/tmp/scratch", session_id=session_id)
    flag, value = READ_ONLY_FLAG[provider]
    assert _pair(argv, flag) == value
    assert not BYPASS & set(argv)
    if session_id:
        assert session_id in argv


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["claude", "claude-ollama"])
@pytest.mark.parametrize("session_id", [None, "sess_123"], ids=["fresh", "resume"])
def test_claude_chat_denies_write_tools(provider: str, session_id: str | None) -> None:
    argv = get_adapter(provider).chat_argv("hi", "/tmp/scratch", session_id=session_id)
    i = argv.index("--disallowedTools")
    denied = set(argv[i + 1].split(","))
    assert {"Edit", "Write", "MultiEdit", "NotebookEdit"} <= denied


@pytest.mark.unit
def test_claude_chat_allowlist_comes_from_read_only_tools() -> None:
    argv = get_adapter("claude").chat_argv("hi", "/tmp/scratch", read_only_tools=("view_file", "search_code"))
    assert _pair(argv, "--allowedTools") == "Read,Grep,Glob"


@pytest.mark.unit
def test_chat_rejects_a_tool_outside_the_read_only_vocabulary() -> None:
    with pytest.raises(ValueError, match="write_file"):
        get_adapter("claude").chat_argv("hi", "/tmp/scratch", read_only_tools=("write_file",))


@pytest.mark.unit
def test_provider_without_read_only_mode_fails_closed() -> None:
    with pytest.raises(ChatReadOnlyUnsupportedError, match="maxwell"):
        get_adapter("maxwell").chat_argv("hi", "/tmp/scratch")


def _role(read_only_tools: list[str]) -> dict[str, object]:
    return {
        "name": "cartographer",
        "title": "Cartographer",
        "summary": "Maintains the architecture maps.",
        "playbook": "docs/fleet-cartographer.md",
        "prompt_template": "docs/templates/cartographer-report.md",
        "instructions": "Audit fleet dependencies.",
        "providers": ["claude"],
        "model": "default",
        "schedule": None,
        "window": None,
        "repos": ["Runner_Dashboard"],
        "scope": {},
        "budget": {"usd_per_run": 1.0, "usd_per_day": 5.0},
        "permissions": {
            "lease": True,
            "push_branch": False,
            "open_pr": False,
            "merge": False,
            "host_shell": False,
            "notify_user": False,
        },
        "reports_to": "orchestrator",
        "holds": [],
        "surface": "dashboard",
        "chat": {"read_only_tools": read_only_tools},
    }


@pytest.mark.unit
def test_validator_accepts_the_read_only_vocabulary() -> None:
    assert validate_role_data(_role(sorted(CHAT_READ_ONLY_TOOLS))) == []


@pytest.mark.unit
def test_validator_rejects_unknown_read_only_tool() -> None:
    problems = validate_role_data(_role(["view_file", "run_shell"]))
    assert any("read_only_tools" in p and "run_shell" in p for p in problems)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_on_provider_without_read_only_mode_fails_visibly(tmp_path) -> None:  # noqa: ANN001
    from unittest.mock import patch

    from staff.chat import ChatTurnRunner
    from staff.conversations import get_conversation_store, reset_conversation_store
    from staff.thread_bus import reset_thread_bus

    reset_conversation_store()
    reset_thread_bus()
    store = get_conversation_store(tmp_path / "conv.sqlite3")
    thread = store.create_thread(title="t", role="barb", created_by="dave")
    user = store.add_message(thread.id, author_kind="user", author="dave", body_md="hi")
    reply = store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")
    runner = ChatTurnRunner(conv_store=store)

    with (
        patch("staff.chat.is_provider_healthy", return_value=True),
        patch.object(runner, "_spawn_cli_process") as spawn,
    ):
        result = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=user.id,
            placeholder_id=reply.id,
            role_name="barb",
            provider="maxwell",
        )

    spawn.assert_not_called()
    assert result.ok is False
    assert result.failure_class == "provider_not_read_only"
    msg = store.get_message(reply.id)
    assert msg is not None and msg.delivery == "failed"
    assert msg.meta.get("failure_class") == "provider_not_read_only"
