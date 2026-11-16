"""Regression tests for #4262 storage split, idempotent create, and session flock."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from code_requests.dispatch_service import (
    HISTORY_LIMIT,
    _append_history_sync,
    append_history,
)
from code_requests.model import CodeRequest, CodeRequestState, Requester, RequesterKind
from code_requests.storage_split import migrate_combined_storage_if_needed
from code_requests.store import CodeRequestStore
from session_management import is_session_active, register_session, revoke_session, touch_session


def test_migrate_splits_dispatch_rows_without_loss(tmp_path: Path) -> None:
    cache_path = tmp_path / "code_requests.json"
    dispatch_path = tmp_path / "code_request_dispatches.json"
    cr = {
        "id": "cr-Runner_Dashboard-1",
        "repository": "Runner_Dashboard",
        "state": "draft",
        "prompt": "keep me",
        "requester": {"id": "human", "kind": "human"},
        "branch": "main",
        "standards": [],
        "audit_trail": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    dispatch = {
        "id": "1700000000",
        "repository": "Runner_Dashboard",
        "branch": "main",
        "provider": "codex_cli",
        "model": "gpt",
        "prompt": "dispatch row",
        "standards": [],
        "status": "dispatched",
        "created_at": "2026-01-01T00:00:00Z",
    }
    cache_path.write_text(json.dumps([cr, dispatch]), encoding="utf-8")

    migrate_combined_storage_if_needed(cache_path, dispatch_path)

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    dispatches = json.loads(dispatch_path.read_text(encoding="utf-8"))
    assert len(cache) == 1
    assert cache[0]["id"] == "cr-Runner_Dashboard-1"
    assert len(dispatches) == 1
    assert dispatches[0]["status"] == "dispatched"


def test_concurrent_migration_and_append_keeps_both_dispatch_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Append racing first-use migration must not erase the appended dispatch row."""
    cache_path = tmp_path / "code_requests.json"
    dispatch_path = tmp_path / "code_request_dispatches.json"
    cr = {
        "id": "cr-Runner_Dashboard-9",
        "repository": "Runner_Dashboard",
        "state": "draft",
        "prompt": "cache row",
        "requester": {"id": "human", "kind": "human"},
        "branch": "main",
        "standards": [],
        "audit_trail": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    legacy_dispatch = {
        "id": "1700000001",
        "repository": "Runner_Dashboard",
        "branch": "main",
        "provider": "codex_cli",
        "model": "gpt",
        "prompt": "legacy in cache",
        "standards": [],
        "status": "dispatched",
        "created_at": "2026-01-01T00:00:01Z",
    }
    raced_append = {
        "id": "1700000099",
        "repository": "Runner_Dashboard",
        "branch": "main",
        "provider": "codex_cli",
        "model": "gpt",
        "prompt": "won the race",
        "standards": [],
        "status": "dispatched",
        "created_at": "2026-01-01T00:00:02Z",
    }
    cache_path.write_text(json.dumps([cr, legacy_dispatch]), encoding="utf-8")
    monkeypatch.setattr("code_requests.dispatch_service.CODE_REQUEST_CACHE_PATH", cache_path)
    monkeypatch.setattr("code_requests.dispatch_service.DISPATCH_HISTORY_PATH", dispatch_path)

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def migrate_worker() -> None:
        try:
            barrier.wait()
            migrate_combined_storage_if_needed(cache_path, dispatch_path)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def append_worker() -> None:
        try:
            barrier.wait()
            _append_history_sync(dispatch_path, raced_append)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=migrate_worker), threading.Thread(target=append_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    dispatches = json.loads(dispatch_path.read_text(encoding="utf-8"))
    ids = {str(row.get("id")) for row in dispatches}
    assert legacy_dispatch["id"] in ids
    assert raced_append["id"] in ids


@pytest.mark.asyncio
async def test_two_hundred_fifty_dispatches_do_not_evict_code_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "code_requests.json"
    dispatch_path = tmp_path / "code_request_dispatches.json"
    cr = {
        "id": "cr-Runner_Dashboard-42",
        "repository": "Runner_Dashboard",
        "title": "Stay",
        "state": "draft",
        "prompt": "persistent",
        "requester": {"id": "human", "kind": "human"},
        "branch": "main",
        "standards": [],
        "audit_trail": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    cache_path.write_text(json.dumps([cr]), encoding="utf-8")
    monkeypatch.setattr("code_requests.dispatch_service.CODE_REQUEST_CACHE_PATH", cache_path)
    monkeypatch.setattr("code_requests.dispatch_service.DISPATCH_HISTORY_PATH", dispatch_path)

    for i in range(250):
        await append_history(
            dispatch_path,
            {
                "id": str(i),
                "repository": "Runner_Dashboard",
                "branch": "main",
                "provider": "codex_cli",
                "model": "m",
                "prompt": f"p{i}",
                "standards": [],
                "status": "dispatched",
                "created_at": f"2026-01-01T00:00:{i % 60:02d}Z",
            },
        )

    remaining_cache = json.loads(cache_path.read_text(encoding="utf-8"))
    history = json.loads(dispatch_path.read_text(encoding="utf-8"))
    assert len(remaining_cache) == 1
    assert remaining_cache[0]["id"] == "cr-Runner_Dashboard-42"
    assert len(history) == min(250, HISTORY_LIMIT)


@pytest.mark.asyncio
async def test_concurrent_create_same_request_file_one_github_issue(tmp_path: Path) -> None:
    cache_path = tmp_path / "code_requests.json"
    issue_counter = {"n": 0}

    async def fake_write(_endpoint: str, method: str = "POST", json_body: Any = None) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        issue_counter["n"] += 1
        return {"number": issue_counter["n"], "html_url": f"https://example/{issue_counter['n']}"}

    store = CodeRequestStore(cache_path=cache_path, write_fn=fake_write)

    now = datetime.now(UTC).isoformat()
    base = CodeRequest(
        id="cr-unknown",
        repository="Runner_Dashboard",
        title="Dup test",
        state=CodeRequestState.DRAFT,
        prompt="same prompt",
        requester=Requester(id="agent", kind=RequesterKind.AGENT),
        request_file="changes/foo.md",
        created_at=now,
        updated_at=now,
    )

    results = await asyncio.gather(store.create(base.model_copy(deep=True)), store.create(base.model_copy(deep=True)))
    assert issue_counter["n"] == 1
    assert results[0].issue_number == results[1].issue_number
    cache = store._read_cache()
    assert len([e for e in cache if e.get("request_file") == "changes/foo.md"]) == 1


def test_revoke_wins_over_concurrent_touch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions_path = tmp_path / "sessions.json"
    monkeypatch.setattr("session_management._SESSIONS_PATH", sessions_path)

    sid = register_session("user-1")
    barrier = threading.Barrier(2)

    def touch_worker() -> None:
        barrier.wait()
        for _ in range(50):
            touch_session(sid)

    def revoke_worker() -> None:
        barrier.wait()
        revoke_session(sid)

    t_touch = threading.Thread(target=touch_worker)
    t_revoke = threading.Thread(target=revoke_worker)
    t_touch.start()
    t_revoke.start()
    t_touch.join()
    t_revoke.join()

    assert not is_session_active(sid)
    records = json.loads(sessions_path.read_text(encoding="utf-8"))
    tomb = next(r for r in records if r["session_id"] == sid)
    assert tomb["revoked_at"] is not None
