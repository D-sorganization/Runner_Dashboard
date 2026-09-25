"""Tests for CodeRequestStore and front-matter round-trip serialization (CR-2, #1282)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from code_requests.lifecycle import CodeRequestState  # noqa: E402
from code_requests.model import (  # noqa: E402
    BoardRoute,
    CodeRequest,
    Requester,
    RequesterKind,
    code_request_from_issue,
    parse_issue_body,
    serialize_issue_body,
)
from code_requests.store import CodeRequestStore  # noqa: E402


def _sample_request() -> CodeRequest:
    return CodeRequest(
        id="cr-test-42",
        repository="Runner_Dashboard",
        title="Code Request: Add biomechanics pipeline",
        issue_number=42,
        issue_url="https://github.com/D-sorganization/Runner_Dashboard/issues/42",
        state=CodeRequestState.TRIAGE,
        prompt="Add a new biomechanics pipeline for kinematics validation.\nEnsure TDD and DbC are followed.",
        requester=Requester(id="dieterolson", kind=RequesterKind.HUMAN),
        planner_profile_id="planner-v1",
        executor_profile_id="executor-v2",
        board_route=BoardRoute.FORCE_BOARD,
        board_proposal="https://github.com/D-sorganization/Runner_Dashboard/issues/999",
        plan_epic="https://github.com/D-sorganization/Runner_Dashboard/issues/1001",
        branch="feat/kinematics",
        standards=["tdd", "dbc", "dry"],
        created_at="2026-09-25T10:00:00Z",
        updated_at="2026-09-25T11:00:00Z",
    )


def test_front_matter_roundtrip_lossless() -> None:
    """Serializing and parsing issue front-matter preserves all typed fields losslessly."""
    original = _sample_request()
    body = serialize_issue_body(original)

    # Front matter block exists
    assert body.startswith("```yaml\n")
    assert "```\n\n## Prompt\n\n" in body
    assert original.prompt in body

    # Parse body
    parsed = parse_issue_body(body, default_id=original.id, default_repo=original.repository)
    assert parsed["id"] == original.id
    assert parsed["repository"] == original.repository
    assert parsed["state"] == original.state.value
    assert parsed["board_route"] == original.board_route.value
    assert parsed["board_proposal"] == original.board_proposal
    assert parsed["plan_epic"] == original.plan_epic
    assert parsed["planner_profile_id"] == original.planner_profile_id
    assert parsed["executor_profile_id"] == original.executor_profile_id
    assert parsed["requester"]["id"] == original.requester.id
    assert parsed["requester"]["kind"] == original.requester.kind.value
    assert parsed["standards"] == original.standards
    assert parsed["branch"] == original.branch
    assert parsed["created_at"] == original.created_at
    assert parsed["updated_at"] == original.updated_at
    assert parsed["prompt"] == original.prompt

    # Convert back to CodeRequest
    reconstructed = code_request_from_issue(
        {
            "number": original.issue_number,
            "title": original.title,
            "body": body,
            "html_url": original.issue_url,
            "labels": [{"name": "code-request"}, {"name": f"code-request:{original.state.value}"}],
        },
        default_repo=original.repository,
    )

    assert reconstructed.id == original.id
    assert reconstructed.repository == original.repository
    assert reconstructed.state == original.state
    assert reconstructed.board_route == original.board_route
    assert reconstructed.board_proposal == original.board_proposal
    assert reconstructed.plan_epic == original.plan_epic
    assert reconstructed.planner_profile_id == original.planner_profile_id
    assert reconstructed.executor_profile_id == original.executor_profile_id
    assert reconstructed.requester == original.requester
    assert reconstructed.standards == original.standards
    assert reconstructed.branch == original.branch
    assert reconstructed.prompt == original.prompt
    assert reconstructed.created_at == original.created_at
    assert reconstructed.updated_at == original.updated_at


@pytest.mark.asyncio
async def test_store_create_persists_to_github_and_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "code_requests.json"
    req = _sample_request()
    req.issue_number = None
    req.issue_url = None

    mock_write = AsyncMock(
        return_value={
            "number": 123,
            "html_url": "https://github.com/D-sorganization/Runner_Dashboard/issues/123",
        }
    )
    mock_fetch = AsyncMock(return_value=[])

    store = CodeRequestStore(cache_path=cache_path, fetch_fn=mock_fetch, write_fn=mock_write)
    created = await store.create(req)

    assert created.issue_number == 123
    assert created.issue_url == "https://github.com/D-sorganization/Runner_Dashboard/issues/123"
    assert cache_path.exists()

    # Verify write was called with issue creation payload
    mock_write.assert_called_once()
    args, kwargs = mock_write.call_args
    assert args[0] == "/repos/D-sorganization/Runner_Dashboard/issues"
    assert kwargs["method"] == "POST"
    assert "code-request" in kwargs["json_body"]["labels"]
    assert "code-request:triage" in kwargs["json_body"]["labels"]

    # Verify cache content
    cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert len(cache_data) == 1
    assert cache_data[0]["issue_number"] == 123


@pytest.mark.asyncio
async def test_store_transition_posts_comment_and_updates_labels(tmp_path: Path) -> None:
    cache_path = tmp_path / "code_requests.json"
    req = _sample_request()
    req.state = CodeRequestState.TRIAGE

    mock_write = AsyncMock(return_value={})
    mock_fetch = AsyncMock(return_value=[])

    store = CodeRequestStore(cache_path=cache_path, fetch_fn=mock_fetch, write_fn=mock_write)
    # Prime cache
    store._write_cache([req])

    updated = await store.transition(
        req.id,
        CodeRequestState.PLANNING,
        actor="operator-bob",
        reason="Fast-tracked into planning",
        is_operator_override=False,
    )

    assert updated.state == CodeRequestState.PLANNING
    assert len(updated.audit_trail) == 1
    assert updated.audit_trail[0].actor == "operator-bob"

    # mock_write should have been called twice: 1 for issue comment, 1 for issue patch
    assert mock_write.call_count == 2
    comment_call = mock_write.call_args_list[0]
    assert comment_call[0][0] == f"/repos/D-sorganization/{req.repository}/issues/{req.issue_number}/comments"
    assert "State changed from `triage` to `planning`" in comment_call[1]["json_body"]["body"]

    patch_call = mock_write.call_args_list[1]
    assert patch_call[0][0] == f"/repos/D-sorganization/{req.repository}/issues/{req.issue_number}"
    assert patch_call[1]["method"] == "PATCH"
    assert "code-request:planning" in patch_call[1]["json_body"]["labels"]


@pytest.mark.asyncio
async def test_deleting_local_cache_rebuilds_from_github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_path = tmp_path / "code_requests.json"
    req = _sample_request()
    body = serialize_issue_body(req)

    # Mock configured_repos to return just Runner_Dashboard
    monkeypatch.setattr("code_requests.store.configured_repos", lambda: ["Runner_Dashboard"])

    issue_payload = [
        {
            "number": 42,
            "title": req.title,
            "body": body,
            "html_url": req.issue_url,
            "labels": [{"name": "code-request"}, {"name": "code-request:triage"}],
            "created_at": req.created_at,
            "updated_at": req.updated_at,
        }
    ]
    comments_payload = [
        {
            "body": (
                "**[Code Request]** State changed from `draft` to `triage` by `dieterolson`.\n\n"
                "**Reason:** Initial submission"
            ),
            "created_at": "2026-09-25T10:05:00Z",
        }
    ]

    async def mock_fetch(endpoint: str) -> Any:
        if "comments" in endpoint:
            return comments_payload
        if "issues" in endpoint:
            return issue_payload
        return []

    mock_write = AsyncMock(return_value={})
    store = CodeRequestStore(cache_path=cache_path, fetch_fn=mock_fetch, write_fn=mock_write)

    # 1. First list rebuilds cache
    assert not cache_path.exists()
    items = await store.list()
    assert len(items) == 1
    assert items[0].id == req.id
    assert items[0].state == CodeRequestState.TRIAGE
    assert len(items[0].audit_trail) == 1
    assert items[0].audit_trail[0].actor == "dieterolson"
    assert cache_path.exists()

    # 2. Delete cache and verify rebuild works losslessly
    cache_path.unlink()
    assert not cache_path.exists()

    rebuilt = await store.list()
    assert len(rebuilt) == 1
    assert rebuilt[0].id == req.id
    assert rebuilt[0].prompt == req.prompt
    assert rebuilt[0].state == CodeRequestState.TRIAGE
    assert cache_path.exists(), "Cache must be repopulated after rebuild"
