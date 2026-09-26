"""A turn whose every runnable provider fails ends as a visible error, never a pending reply (#1341).

Only the chain's last entry recorded its failure on the placeholder. When that
entry was skipped as unhealthy (on most nodes ``ollama``, whose ``codex``
binary is absent), the earlier failures were never written and the reply stayed
``pending`` forever. The Staff Console e2e suite found it with a crashing
provider.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from staff.availability import reset_availability_metrics, reset_provider_health, set_provider_health
from staff.chat import ChatTurnRunner
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.thread_bus import reset_thread_bus


@pytest.fixture(autouse=True)
def _clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    monkeypatch.setenv("STAFF_MOCK_INSTALLED", "1")
    for reset in (reset_conversation_store, reset_thread_bus, reset_availability_metrics, reset_provider_health):
        reset()
    yield
    for reset in (reset_conversation_store, reset_thread_bus, reset_availability_metrics, reset_provider_health):
        reset()


def _crash(cmd: list[str], cwd: str, env: dict[str, str]) -> MagicMock:
    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = iter([])
    proc.stderr = iter(["RuntimeError: provider crashed\n"])
    proc.poll.return_value = 1
    proc.wait.return_value = 1
    return proc


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failures_are_recorded_when_the_last_provider_in_the_chain_is_skipped() -> None:
    store = get_conversation_store()
    thread = store.create_thread(title="Crash", role="barb", created_by="bob")
    user_msg = store.add_message(thread.id, author_kind="user", author="bob", body_md="hello")
    placeholder = store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")
    for missing in ("codex", "ollama"):
        set_provider_health(missing, False)

    runner = ChatTurnRunner(conv_store=store)
    with patch.object(runner, "_spawn_cli_process", side_effect=_crash):
        result = await runner.execute_turn(
            thread_id=thread.id, user_message_id=user_msg.id, placeholder_id=placeholder.id, role_name="barb"
        )

    assert result.ok is False
    reply = store.get_message(placeholder.id)
    assert reply is not None
    assert reply.delivery == "failed"
    assert reply.kind == "error"
    assert reply.meta.get("failure_class") == result.failure_class


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exhausted_chain_keeps_most_specific_classified_failure() -> None:
    """auth_expired from claude beats provider_error/unavailable from ollama (#1551)."""
    store = get_conversation_store()
    thread = store.create_thread(title="AuthThenOllama", role="barb", created_by="alice")
    user_msg = store.add_message(thread.id, author_kind="user", author="alice", body_md="hello")
    placeholder = store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")

    def _auth_then_ollama_fail(cmd: list[str], cwd: str, env: dict[str, str]) -> MagicMock:
        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = iter([])
        cmd_str = " ".join(cmd)
        is_primary = (
            "claude" in cmd_str
            and "--local-provider" not in cmd_str
            and "claude-ollama" not in env.get("ANTHROPIC_BASE_URL", "")
        )
        if is_primary:
            proc.stderr = iter(["Invalid API key · Please run claude auth login\n"])
        else:
            proc.stderr = iter(["failed to connect to ollama: connection refused\n"])
        proc.poll.return_value = 1
        proc.wait.return_value = 1
        return proc

    runner = ChatTurnRunner(conv_store=store)
    with patch.object(runner, "_spawn_cli_process", side_effect=_auth_then_ollama_fail):
        result = await runner.execute_turn(
            thread_id=thread.id, user_message_id=user_msg.id, placeholder_id=placeholder.id, role_name="barb"
        )

    assert result.ok is False
    assert result.failure_class == "auth_expired"
    assert "claude auth login" in result.remediation.lower()
    assert "systemctl" not in result.remediation.lower()

    reply = store.get_message(placeholder.id)
    assert reply is not None
    assert reply.delivery == "failed"
    assert reply.kind == "error"
    assert reply.meta.get("failure_class") == "auth_expired"
    assert "claude auth login" in reply.body_md.lower()
    assert "systemctl" not in reply.body_md.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exhausted_chain_crash_beats_subsequent_unavailable_fallback() -> None:
    """A crash on the primary provider beats subsequent connection/unavailable errors (#1551)."""
    store = get_conversation_store()
    thread = store.create_thread(title="CrashThenOllama", role="barb", created_by="alice")
    user_msg = store.add_message(thread.id, author_kind="user", author="alice", body_md="hello")
    placeholder = store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")

    def _crash_then_ollama_fail(cmd: list[str], cwd: str, env: dict[str, str]) -> MagicMock:
        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = iter([])
        cmd_str = " ".join(cmd)
        is_primary = (
            "claude" in cmd_str
            and "--local-provider" not in cmd_str
            and "claude-ollama" not in env.get("ANTHROPIC_BASE_URL", "")
        )
        if is_primary:
            proc.stderr = iter(["Traceback (most recent call last):\nRuntimeError: primary provider crashed\n"])
        else:
            proc.stderr = iter(["failed to connect to ollama: connection refused\n"])
        proc.poll.return_value = 1
        proc.wait.return_value = 1
        return proc

    runner = ChatTurnRunner(conv_store=store)
    with patch.object(runner, "_spawn_cli_process", side_effect=_crash_then_ollama_fail):
        result = await runner.execute_turn(
            thread_id=thread.id, user_message_id=user_msg.id, placeholder_id=placeholder.id, role_name="barb"
        )

    assert result.ok is False
    assert result.failure_class == "unknown"
    assert "primary provider crashed" in (result.remediation or "")

    reply = store.get_message(placeholder.id)
    assert reply is not None
    assert reply.delivery == "failed"
    assert reply.kind == "error"
    assert reply.meta.get("failure_class") == "unknown"
    assert "primary provider crashed" in reply.body_md
    assert "systemctl" not in reply.body_md.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exhausted_chain_preserves_primary_provider_on_equal_specificity() -> None:
    """When both primary (claude) and fallback (claude-ollama) fail with auth_expired, claude wins (#1551)."""
    store = get_conversation_store()
    thread = store.create_thread(title="ClaudeVsClaudeOllama", role="barb", created_by="alice")
    user_msg = store.add_message(thread.id, author_kind="user", author="alice", body_md="hello")
    placeholder = store.add_message(thread.id, author_kind="role", author="barb", delivery="pending")

    def _all_auth_fail(cmd: list[str], cwd: str, env: dict[str, str]) -> MagicMock:
        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = iter([])
        # Every candidate returns an auth error
        proc.stderr = iter(["Invalid API key · Please login\n"])
        proc.poll.return_value = 1
        proc.wait.return_value = 1
        return proc

    runner = ChatTurnRunner(conv_store=store)
    with patch.object(runner, "_spawn_cli_process", side_effect=_all_auth_fail):
        result = await runner.execute_turn(
            thread_id=thread.id, user_message_id=user_msg.id, placeholder_id=placeholder.id, role_name="barb"
        )

    assert result.ok is False
    assert result.failure_class == "auth_expired"
    # Primary claude remediation must win over fallback claude-ollama (which suggests systemctl start ollama)
    assert "claude auth login" in result.remediation.lower()
    assert "systemctl" not in result.remediation.lower()

    reply = store.get_message(placeholder.id)
    assert reply is not None
    assert reply.delivery == "failed"
    assert reply.kind == "error"
    assert reply.meta.get("failure_class") == "auth_expired"
    assert "claude auth login" in reply.body_md.lower()
    assert "systemctl" not in reply.body_md.lower()
    assert "claude auth login" in reply.meta.get("remediation", "").lower()
    assert "systemctl" not in reply.meta.get("remediation", "").lower()
