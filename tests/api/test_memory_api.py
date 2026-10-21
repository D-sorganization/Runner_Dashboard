"""Shared assistant memory bank API (/api/v1/memory)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import identity
import pytest
from fastapi.testclient import TestClient
from identity import identity_manager

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.25", 52002)


@pytest.fixture
def memory_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    id_dir = tmp_path / "identity"
    id_dir.mkdir(parents=True)
    tok_dir = id_dir / "agent-tokens"
    tok_dir.mkdir()
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(id_dir))
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(tmp_path / "memory.sqlite3"))
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "directives.json"))
    monkeypatch.setenv("STAFF_HOLDS_FILE", str(tmp_path / "holds.json"))

    grok = "svc_grok_memory_test"  # pragma: allowlist secret
    hands = "svc_hands_memory_test"  # pragma: allowlist secret
    (tok_dir / "agent-grok.token").write_text(grok + "\n", encoding="utf-8")
    (tok_dir / "agent-codex-hands.token").write_text(hands + "\n", encoding="utf-8")

    identity_manager.config_dir = id_dir
    identity_manager.principals_path = id_dir / "principals.yml"
    identity_manager.tokens_path = id_dir / "tokens.yml"
    identity_manager.principals = {}
    identity_manager.tokens = []
    identity_manager.load_principals()
    identity_manager.load_tokens()
    from agent_principal_defaults import merge_agent_principal_defaults  # noqa: PLC0415

    merge_agent_principal_defaults(identity_manager)

    from memory.store import reset_memory_store  # noqa: PLC0415

    reset_memory_store()

    return {"grok": grok, "hands": hands}


@pytest.fixture
def remote_client(memory_env: dict[str, str]) -> Iterator[TestClient]:
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
def test_grok_writes_session_digest(remote_client: TestClient, memory_env: dict[str, str]) -> None:
    read = remote_client.get("/api/v1/memory/context-pack", headers=_auth(memory_env["grok"]))
    assert read.status_code == 200

    write = remote_client.post(
        "/api/v1/memory",
        headers={**_XHR, **_auth(memory_env["grok"])},
        json={
            "entry_type": "session_digest",
            "source_product": "grokbot",
            "body": {"summary": "Barb checkpoint", "open_items": []},
        },
    )
    assert write.status_code == 201, write.text
    assert write.json()["entry"]["author_principal"].endswith("agent-grok")


@pytest.mark.integration
def test_append_digest_redacts_secrets_and_lists(remote_client: TestClient, memory_env: dict[str, str]) -> None:
    secret = "ghp_" + "A" * 36
    res = remote_client.post(
        "/api/v1/memory",
        headers={**_XHR, **_auth(memory_env["hands"])},
        json={
            "entry_type": "session_digest",
            "source_product": "codex",
            "tags": ["Runner_Dashboard"],
            "body": {"summary": f"token was {secret}"},
        },
    )
    assert res.status_code == 201, res.text
    entry = res.json()["entry"]
    assert "[REDACTED_GITHUB_TOKEN]" in entry["body"]["summary"]
    assert secret not in entry["body"]["summary"]

    listed = remote_client.get(
        "/api/v1/memory?entry_type=session_digest&tag=Runner_Dashboard",
        headers=_auth(memory_env["hands"]),
    )
    assert listed.status_code == 200
    assert len(listed.json()["entries"]) == 1


@pytest.mark.integration
def test_task_supersede_chain(remote_client: TestClient, memory_env: dict[str, str]) -> None:
    first = remote_client.post(
        "/api/v1/memory",
        headers={**_XHR, **_auth(memory_env["hands"])},
        json={
            "entry_type": "task",
            "source_product": "codex",
            "body": {
                "title": "Ship memory API",
                "owner": "dieter",
                "status": "open",
                "recommendation": "merge PR1",
            },
        },
    )
    assert first.status_code == 201
    first_id = first.json()["entry"]["id"]

    second = remote_client.post(
        "/api/v1/memory",
        headers={**_XHR, **_auth(memory_env["hands"])},
        json={
            "entry_type": "task",
            "source_product": "codex",
            "supersedes_id": first_id,
            "body": {
                "title": "Ship memory API",
                "owner": "dieter",
                "status": "in_progress",
                "recommendation": "merge PR2",
            },
        },
    )
    assert second.status_code == 201
    second_id = second.json()["entry"]["id"]

    listed = remote_client.get("/api/v1/memory?entry_type=task", headers=_auth(memory_env["hands"]))
    active = [e for e in listed.json()["entries"] if not e.get("superseded_by")]
    assert len(active) == 1
    assert active[0]["id"] == second_id


@pytest.mark.integration
def test_context_pack_includes_digest_task_directives_holds(
    remote_client: TestClient, memory_env: dict[str, str]
) -> None:
    remote_client.post(
        "/api/v1/memory",
        headers={**_XHR, **_auth(memory_env["hands"])},
        json={
            "entry_type": "session_digest",
            "source_product": "codex",
            "body": {"summary": "working on memory"},
        },
    )
    remote_client.post(
        "/api/v1/memory",
        headers={**_XHR, **_auth(memory_env["hands"])},
        json={
            "entry_type": "task",
            "source_product": "codex",
            "body": {"title": "Triage CI", "owner": "barb", "status": "open"},
        },
    )
    pack = remote_client.get("/api/v1/memory/context-pack", headers=_auth(memory_env["hands"]))
    assert pack.status_code == 200
    body = pack.json()
    assert body["session_digests"]
    assert body["open_tasks"]
    assert "directives" in body
    assert "holds" in body


@pytest.mark.integration
def test_transcript_size_cap(remote_client: TestClient, memory_env: dict[str, str]) -> None:
    huge = "x" * 50_000
    res = remote_client.post(
        "/api/v1/memory",
        headers={**_XHR, **_auth(memory_env["hands"])},
        json={
            "entry_type": "transcript",
            "source_product": "codex",
            "body": {"text": huge},
        },
    )
    assert res.status_code == 422
