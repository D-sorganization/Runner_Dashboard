"""A real minted staff-run token must be able to file an HTTP lesson proposal (#2665).

``authenticated_lesson_run`` only accepts a principal holding a ``staff-run:<run_id>``
role, and ``mint_run_token`` is the only production code path that grants that role.
These tests exercise the real token end to end (mint -> Bearer header -> HTTP ->
server-derived locator), not a dependency-override shortcut.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import identity_manager

pytestmark = pytest.mark.integration

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.25", 52003)
_RUN_ID = "run-token-auth-1"
_REPO = "RD"
_LESSON_TEXT = "Minted staff-run tokens must be able to file their own lesson proposals."


@pytest.fixture
def lesson_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    from memory.store import reset_memory_store
    from staff import store as run_stores
    from staff import tokens
    from staff.store import RunRecord

    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(tmp_path / "identity"))
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(tmp_path / "memory.sqlite3"))
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    (tmp_path / "identity").mkdir()
    identity_manager.config_dir = tmp_path / "identity"
    identity_manager.principals_path = tmp_path / "identity" / "principals.yml"
    identity_manager.tokens_path = tmp_path / "identity" / "tokens.yml"
    identity_manager.principals = {}
    identity_manager.tokens = []
    identity_manager.load_principals()
    identity_manager.load_tokens()
    reset_memory_store()
    run_stores.reset_store()

    store = run_stores.get_store()
    store.create_run(
        RunRecord(
            _RUN_ID,
            "agent",
            "claude",
            None,
            "machine",
            _REPO,
            "prompt",
            "",
            "test",
            status="running",
            requested_by="operator",
            standards_version="v1",
        )
    )
    store.append_event(_RUN_ID, "assistant", f"STAFF_LESSON: {_LESSON_TEXT}", lesson_evidence="assistant_answer")

    yield _RUN_ID

    tokens.revoke_run_token(_RUN_ID)
    run_stores.reset_store()
    reset_memory_store()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from server import app

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def _propose(client: TestClient, token: str) -> object:
    return client.post(
        "/api/v1/memory",
        headers={**_XHR, "Authorization": f"Bearer {token}"},
        json={
            "entry_type": "lesson",
            "source_product": "claude",
            "source_run": _RUN_ID,
            "repo": _REPO,
            "body": {"text": _LESSON_TEXT},
        },
    )


def test_minted_run_token_proposes_lesson_with_server_derived_locator(client: TestClient, lesson_run: str) -> None:
    from staff import store as run_stores
    from staff.lesson_locator import locate_lesson_evidence
    from staff.tokens import mint_run_token

    token = mint_run_token(role="agent", run_id=lesson_run, fleet_actions=[], ttl_seconds=300.0)

    response = _propose(client, token)

    assert response.status_code == 201, response.text
    entry = response.json()["entry"]
    expected = locate_lesson_evidence(run_stores.get_store(), lesson_run, _REPO, _LESSON_TEXT)
    assert entry["body"]["locator"] == expected
    assert expected is not None


def test_forged_token_cannot_propose_a_lesson(client: TestClient, lesson_run: str) -> None:
    response = _propose(client, "forged-not-a-real-token")

    assert response.status_code == 401


def test_revoked_token_cannot_propose_a_lesson(client: TestClient, lesson_run: str) -> None:
    from staff.tokens import mint_run_token, revoke_run_token

    token = mint_run_token(role="agent", run_id=lesson_run, fleet_actions=[], ttl_seconds=300.0)
    revoke_run_token(lesson_run)

    response = _propose(client, token)

    assert response.status_code == 401
