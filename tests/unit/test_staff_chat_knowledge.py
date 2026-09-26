"""Unit tests for retrieval-augmented staff chat turns with knowledge packs (Issue #1479)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from knowledge_pack import build_pack, load_manifest
from staff.chat import (
    ChatTurnRunner,
    build_knowledge_turn_block,
)
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
    reset_conversation_store,
)
from staff.roles import RoleSpec
from staff.thread_bus import reset_thread_bus


@pytest.fixture
def conv_store(tmp_path: Path) -> ConversationStore:
    reset_conversation_store()
    reset_thread_bus()
    db_file = tmp_path / "test_chat_knowledge.sqlite3"
    return get_conversation_store(db_file)


@pytest.fixture
def knowledge_pack_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    repo_dir = tmp_path / "Repositories" / "AffineDrift"
    docs_dir = repo_dir / "articles"
    docs_dir.mkdir(parents=True, exist_ok=True)
    article = docs_dir / "timing.md"
    article.write_text(
        "# Proximal Distal Timing\n\n"
        "## Energy Sequencing\n\n"
        "Proximal to distal kinetic chain sequencing drives ball velocity.\n",
        encoding="utf-8",
    )
    (repo_dir / ".git").mkdir()

    manifest_file = tmp_path / "findings.yml"
    manifest_file.write_text(
        "id: findings\n"
        "title: Findings Pack\n"
        "chunk_chars: 1800\n"
        "sources:\n"
        "  - repo: AffineDrift\n"
        "    authority: published\n"
        "    include:\n"
        "      - 'articles/**/*.md'\n",
        encoding="utf-8",
    )

    pack_dir = tmp_path / "knowledge_dir"
    pack_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(pack_dir))
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path / "Repositories"))

    manifest = load_manifest(manifest_file)
    build_pack(manifest, {"AffineDrift": repo_dir}, pack_dir / "findings.sqlite")

    return {
        "repo": repo_dir,
        "article": article,
        "pack_dir": pack_dir,
    }


def test_build_knowledge_turn_block_with_valid_pack(knowledge_pack_fixture: dict[str, Any]) -> None:
    role = RoleSpec(
        name="disciple",
        title="Disciple",
        chat={"tools": ["search_knowledge"]},
        scope={"pack": "staff/knowledge/findings.yml"},
    )
    block = build_knowledge_turn_block(role, "kinetic chain sequencing")
    assert block is not None
    assert "## Knowledge" in block
    assert "AffineDrift:articles/timing.md#proximal-distal-timing/energy-sequencing" in block
    assert "Proximal to distal kinetic chain" in block


def test_build_knowledge_turn_block_missing_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty_pack_dir = tmp_path / "empty_knowledge"
    empty_pack_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(empty_pack_dir))

    role = RoleSpec(
        name="disciple",
        title="Disciple",
        chat={"tools": ["search_knowledge"]},
        scope={"pack": "staff/knowledge/findings.yml"},
    )
    block = build_knowledge_turn_block(role, "kinetic chain sequencing")
    assert block is not None
    assert "## Knowledge" in block
    assert "Notice: Knowledge pack 'findings' is not available." in block


def test_build_knowledge_turn_block_stale_pack(knowledge_pack_fixture: dict[str, Any]) -> None:
    # Modify source file to make pack stale
    knowledge_pack_fixture["article"].write_text(
        "# Proximal Distal Timing\n\n## Energy Sequencing\n\nModified content.\n",
        encoding="utf-8",
    )

    role = RoleSpec(
        name="disciple",
        title="Disciple",
        chat={"tools": ["search_knowledge"]},
        scope={"pack": "staff/knowledge/findings.yml"},
    )
    block = build_knowledge_turn_block(role, "modified content")
    assert block is not None
    assert "## Knowledge" in block
    assert "Notice: Knowledge pack 'findings' is stale." in block


def test_build_knowledge_turn_block_no_search_knowledge_tool() -> None:
    role = RoleSpec(
        name="barb",
        title="Barb",
        chat={"tools": ["read_repo"]},
    )
    block = build_knowledge_turn_block(role, "any query")
    assert block is None


@pytest.mark.asyncio
async def test_chat_turn_execution_carries_knowledge_block(
    conv_store: ConversationStore,
    knowledge_pack_fixture: dict[str, Any],
) -> None:
    thread = conv_store.create_thread(title="Knowledge Turn", role="disciple", created_by="alice")
    user_msg = conv_store.add_message(
        thread.id,
        author_kind="user",
        author="alice",
        body_md="What do we know about kinetic chain sequencing?",
    )
    placeholder = conv_store.add_message(
        thread.id,
        author_kind="role",
        author="disciple",
        kind="text",
        body_md="",
        delivery="pending",
        meta={"in_reply_to": user_msg.id},
    )

    mock_lines = [
        json.dumps({"type": "init", "session_id": "claude_session_k1"}),
        json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"text": "According to our findings, kinetic chain sequencing drives velocity."},
            }
        ),
    ]

    runner = ChatTurnRunner(conv_store=conv_store)
    dispatched_prompts: list[str] = []

    def mock_spawn(cmd: list[str], cwd: str, env: dict[str, str]) -> MagicMock:
        for i, arg in enumerate(cmd):
            if arg == "-p" and i + 1 < len(cmd):
                dispatched_prompts.append(cmd[i + 1])
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = iter(mock_lines)
        proc.stderr = iter([])
        proc.poll.return_value = 0
        return proc

    disciple_role = RoleSpec(
        name="disciple",
        title="Disciple",
        chat={"tools": ["search_knowledge"]},
        scope={"pack": "staff/knowledge/findings.yml"},
    )

    with (
        patch.object(runner, "_spawn_cli_process", side_effect=mock_spawn),
        patch("staff.chat.load_roles", return_value={"disciple": disciple_role}),
    ):
        result = await runner.execute_turn(
            thread_id=thread.id,
            user_message_id=user_msg.id,
            placeholder_id=placeholder.id,
            role_name="disciple",
        )

    assert result.ok is True
    assert len(dispatched_prompts) >= 1
    dispatched = dispatched_prompts[0]
    assert "## Knowledge" in dispatched
    assert "AffineDrift:articles/timing.md#proximal-distal-timing/energy-sequencing" in dispatched
    assert "What do we know about kinetic chain sequencing?" in dispatched
