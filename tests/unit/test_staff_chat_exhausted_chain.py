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
