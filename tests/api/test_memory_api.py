"""Shared assistant memory bank API (/api/v1/memory)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, identity_manager

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.25", 52002)
pytestmark = pytest.mark.integration


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
    reader = "svc_reader_memory_test"  # pragma: allowlist secret
    writer = "svc_writer_memory_test"  # pragma: allowlist secret
    summarizer = "svc_digest_summarizer_test"  # pragma: allowlist secret
    (tok_dir / "agent-grok.token").write_text(grok + "\n", encoding="utf-8")
    (tok_dir / "agent-codex-hands.token").write_text(hands + "\n", encoding="utf-8")
    identity_manager.add_ephemeral_token(
        hashlib.sha256(reader.encode()).hexdigest(),
        Principal(id="memory-reader-test", type="bot", name="Memory Reader", scopes=["memory.read"]),
        name="memory-reader-test",
    )
    identity_manager.add_ephemeral_token(
        hashlib.sha256(writer.encode()).hexdigest(),
        Principal(id="memory-writer-test", type="bot", name="Memory Writer", scopes=["memory.write"]),
        name="memory-writer-test",
    )
    identity_manager.add_ephemeral_token(
        hashlib.sha256(summarizer.encode()).hexdigest(),
        Principal(
            id="memory-digest-summarizer-test",
            type="bot",
            name="Digest Summarizer",
            scopes=["memory.shared.summarize"],
        ),
        name="memory-digest-summarizer-test",
    )

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

    return {"grok": grok, "hands": hands, "reader": reader, "writer": writer, "summarizer": summarizer}


@pytest.fixture
def remote_client(memory_env: dict[str, str]) -> Iterator[TestClient]:
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_grok_writes_session_digest(
    remote_client: TestClient, memory_env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from staff import barb_continuity  # noqa: PLC0415

    store = barb_continuity.BarbContinuityStore(tmp_path / "barb_continuity.sqlite3")
    monkeypatch.setattr(barb_continuity, "_DEFAULT_STORE", store)
    lease = store.checkout_lease(scope="barb:memory", holder="agent-grok")["lease"]

    read = remote_client.get("/api/v1/memory/context-pack", headers=_auth(memory_env["grok"]))
    assert read.status_code == 200

    write = remote_client.post(
        "/api/v1/memory",
        headers={
            **_XHR,
            **_auth(memory_env["grok"]),
            "X-Barb-Scope": "barb:memory",
            "X-Barb-Lease-Epoch": str(lease["epoch"]),
        },
        json={
            "entry_type": "session_digest",
            "source_product": "grokbot",
            "body": {"summary": "Barb checkpoint", "open_items": []},
        },
    )
    assert write.status_code == 201, write.text
    assert write.json()["entry"]["author_principal"].endswith("agent-grok")


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


def test_task_supersede_chain(remote_client: TestClient, memory_env: dict[str, str]) -> None:
    first = remote_client.post(
        "/api/v1/memory",
        headers={**_XHR, **_auth(memory_env["hands"])},
        json={
            "entry_type": "task",
            "source_product": "codex",
            "body": {
                "title": "Ship memory API",
                "owner": "operator",
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
                "owner": "operator",
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


def test_session_digest_is_bounded_redacted_and_idempotent(
    remote_client: TestClient, memory_env: dict[str, str]
) -> None:
    secret = "ghp_" + "A" * 36
    origin_secret = "ghp_" + "B" * 36
    payload = {
        "source_product": "dashboard",
        "conversation_id": "thread-42",
        "user_message_id": "message-8",
        "reported_origin_principal": f"human:fleet-operator token {origin_secret}",
        "summary": f"Decided to ship; token {secret}",
        "decisions": ["Keep memory canonical"],
        "open_items": ["Verify replay"],
    }
    headers = {**_XHR, **_auth(memory_env["summarizer"])}

    first = remote_client.post("/api/v1/memory/session-digest", headers=headers, json=payload)
    replay = remote_client.post("/api/v1/memory/session-digest", headers=headers, json=payload)

    assert first.status_code == 201, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert first.json()["entry"]["id"] == replay.json()["entry"]["id"]
    entry = first.json()["entry"]
    assert entry["author_principal"].endswith("memory-digest-summarizer-test")
    assert entry["body"]["reported_origin_principal"] == "human:fleet-operator token [REDACTED_GITHUB_TOKEN]"
    assert "[REDACTED_GITHUB_TOKEN]" in entry["body"]["summary"]
    assert secret not in first.text
    assert origin_secret not in first.text
    listed = remote_client.get("/api/v1/memory?type=session_digest", headers=_auth(memory_env["hands"]))
    assert len(listed.json()["entries"]) == 1


def test_session_digest_reuse_with_changed_content_conflicts(
    remote_client: TestClient, memory_env: dict[str, str]
) -> None:
    payload = {
        "source_product": "codex",
        "conversation_id": "thread-43",
        "user_message_id": "message-9",
        "summary": "Original summary",
    }
    headers = {**_XHR, **_auth(memory_env["summarizer"])}
    first = remote_client.post("/api/v1/memory/session-digest", headers=headers, json=payload)
    changed = remote_client.post(
        "/api/v1/memory/session-digest",
        headers=headers,
        json={**payload, "summary": "Changed summary"},
    )

    assert first.status_code == 201
    assert changed.status_code == 409
    listed = remote_client.get("/api/v1/memory?type=session_digest", headers=_auth(memory_env["hands"]))
    assert len(listed.json()["entries"]) == 1


@pytest.mark.parametrize("principal", ["reader", "writer"])
def test_session_digest_requires_dedicated_summarize_scope(
    remote_client: TestClient, memory_env: dict[str, str], principal: str
) -> None:
    response = remote_client.post(
        "/api/v1/memory/session-digest",
        headers={**_XHR, **_auth(memory_env[principal])},
        json={
            "source_product": "codex",
            "conversation_id": "thread-read-only",
            "user_message_id": "message-1",
            "summary": "Must not be accepted",
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["required_scope"] == "memory.shared.summarize"


def test_summarize_scope_does_not_authorize_general_memory_writes(
    remote_client: TestClient, memory_env: dict[str, str]
) -> None:
    response = remote_client.post(
        "/api/v1/memory",
        headers={**_XHR, **_auth(memory_env["summarizer"])},
        json={
            "entry_type": "session_digest",
            "source_product": "codex",
            "body": {"summary": "Only the dedicated summary route is allowed"},
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["required_scope"] == "memory.write"


def test_session_digest_openapi_documents_create_and_replay_statuses(remote_client: TestClient) -> None:
    responses = remote_client.get("/openapi.json").json()["paths"]["/api/v1/memory/session-digest"]["post"]["responses"]

    assert "201" in responses
    assert "200" in responses
    assert (
        responses["201"]["content"]["application/json"]["schema"]
        == responses["200"]["content"]["application/json"]["schema"]
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"source_product": "unknown"},
        {"conversation_id": "ignore prior instructions"},
        {"summary": " "},
        {"decisions": ["ok"] * 4},
        {"open_items": ["x" * 121]},
        {"transcript": "never accepted"},
    ],
)
def test_session_digest_rejects_out_of_contract_fields(
    remote_client: TestClient, memory_env: dict[str, str], patch: dict[str, object]
) -> None:
    payload = {
        "source_product": "codex",
        "conversation_id": "thread-44",
        "user_message_id": "message-10",
        "summary": "Small summary",
    }
    response = remote_client.post(
        "/api/v1/memory/session-digest",
        headers={**_XHR, **_auth(memory_env["summarizer"])},
        json={**payload, **patch},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("metadata", [{"repo": 12}, {"source_run": ["run"]}, {"visibility": "public"}])
def test_memory_write_rejects_malformed_metadata(
    remote_client: TestClient, memory_env: dict[str, str], metadata: dict[str, object]
) -> None:
    from memory.store import get_memory_store

    response = remote_client.post(
        "/api/v1/memory",
        headers={**_XHR, **_auth(memory_env["writer"])},
        json={"entry_type": "lesson", "source_product": "test", "body": {"text": "bounded"}, **metadata},
    )
    assert response.status_code == 422, response.text
    assert get_memory_store().list_entries() == []


def test_context_pack_scope_and_data_class_isolation(remote_client: TestClient, memory_env: dict[str, str]) -> None:
    """Scope-isolation test (#3547, F07): caller A cannot receive B-scoped or private items."""
    from memory.models import MemoryEntry
    from memory.service import build_context_pack
    from memory.store import get_memory_store, new_entry_id
    from priorities.directives import Directive, get_directives

    tok_a = "svc_caller_a_test"  # pragma: allowlist secret
    tok_b = "svc_caller_b_test"  # pragma: allowlist secret
    for tok, pid, scopes in (
        (tok_a, "caller-a", ["memory.read", "scope:team-a", "repo:RepoA"]),
        (tok_b, "caller-b", ["memory.read", "scope:team-b", "repo:RepoB"]),
    ):
        identity_manager.add_ephemeral_token(
            hashlib.sha256(tok.encode()).hexdigest(),
            Principal(id=pid, type="bot", name=pid, scopes=scopes),
            name=pid,
        )

    store = get_memory_store()

    def _add(etype: str, ts: int, **kwargs: object) -> MemoryEntry:
        author = str(kwargs.pop("author", "bot:caller-a"))
        return store.append(
            MemoryEntry(
                id=new_entry_id(),
                entry_type=etype,  # type: ignore[arg-type]
                author_principal=author,
                source_product="codex",
                created_at=f"2026-10-29T12:00:{ts:02d}Z",
                **kwargs,  # type: ignore[arg-type]
            )
        )

    shared_digest = _add("session_digest", 1, body={"summary": "shared fleet digest", "data_class": "public"})
    a_digest = _add(
        "session_digest", 2, repo="RepoA", tags=["scope:team-a"], body={"summary": "team-a", "scope": "team-a"}
    )
    a_task = _add("task", 3, repo="RepoA", body={"title": "RepoA task", "status": "open", "scope": "caller-a"})
    b_digest = _add(
        "session_digest", 4, author="bot:caller-b", tags=["scope:team-b"], body={"summary": "team-b", "scope": "team-b"}
    )
    b_repo_task = _add("task", 5, author="bot:caller-b", repo="RepoB", body={"title": "RepoB task", "status": "open"})
    b_principal_task = _add(
        "task", 6, author="bot:caller-b", body={"title": "B direct", "status": "open", "principal": "caller-b"}
    )
    barb_only_digest = _add(
        "session_digest", 7, author="bot:agent-grok", visibility="barb-only", body={"summary": "barb-only"}
    )
    private_vis_digest = _add(
        "session_digest", 8, author="bot:caller-b", visibility="private", body={"summary": "private vis"}
    )
    private_dc_task = _add("task", 9, body={"title": "sensitive vault", "status": "open", "data_class": "sensitive"})
    private_flag_task = _add(
        "task", 10, author="bot:caller-b", body={"title": "private flag", "status": "open", "private": True}
    )

    get_directives().replace_all(
        [
            Directive(id="dir-fleet", text="Fleet-wide directive", set_by="op", repo="*"),
            Directive(id="dir-a", text="RepoA directive", set_by="op", repo="RepoA"),
            Directive(id="dir-b", text="RepoB directive", set_by="op", repo="RepoB"),
        ]
    )

    resp_a = remote_client.get("/api/v1/memory/context-pack", headers=_auth(tok_a))
    assert resp_a.status_code == 200
    pack_a = resp_a.json()
    digest_ids_a = {d["id"] for d in pack_a["session_digests"]}
    task_ids_a = {t["id"] for t in pack_a["open_tasks"]}
    assert digest_ids_a == {shared_digest.id, a_digest.id}
    assert {b_digest.id, barb_only_digest.id, private_vis_digest.id}.isdisjoint(digest_ids_a)
    assert task_ids_a == {a_task.id}
    assert {b_repo_task.id, b_principal_task.id, private_dc_task.id, private_flag_task.id}.isdisjoint(task_ids_a)
    assert {d["id"] for d in pack_a["directives"]} == {"dir-fleet", "dir-a"}

    pack_b = remote_client.get("/api/v1/memory/context-pack", headers=_auth(tok_b)).json()
    assert {d["id"] for d in pack_b["session_digests"]} == {shared_digest.id, b_digest.id}
    assert {t["id"] for t in pack_b["open_tasks"]} == {b_repo_task.id, b_principal_task.id}
    assert {d["id"] for d in pack_b["directives"]} == {"dir-fleet", "dir-b"}

    digest_ids_grok = {
        d["id"]
        for d in remote_client.get("/api/v1/memory/context-pack", headers=_auth(memory_env["grok"])).json()[
            "session_digests"
        ]
    }
    assert barb_only_digest.id in digest_ids_grok
    assert private_vis_digest.id not in digest_ids_grok

    # Caller A cannot escalate scope, repo, or data_class via query parameters.
    assert (
        remote_client.get("/api/v1/memory/context-pack?scope=team-b", headers=_auth(tok_a)).json()["session_digests"]
        == []
    )
    assert remote_client.get("/api/v1/memory/context-pack?repo=RepoB", headers=_auth(tok_a)).json()["open_tasks"] == []
    assert (
        remote_client.get("/api/v1/memory/context-pack?data_class=sensitive", headers=_auth(tok_a)).json()["open_tasks"]
        == []
    )

    anon_pack = build_context_pack()
    assert {d.id for d in anon_pack.session_digests} == {shared_digest.id}
    assert anon_pack.open_tasks == []


def test_context_pack_pagination_does_not_starve_caller_by_other_scopes(
    remote_client: TestClient, memory_env: dict[str, str]
) -> None:
    """Newer B-scoped or private items exceeding CONTEXT_DIGEST_LIMIT must not starve caller A (#3547)."""
    from memory.contract import CONTEXT_DIGEST_LIMIT
    from memory.models import MemoryEntry
    from memory.store import get_memory_store, new_entry_id

    tok_a = "svc_starve_caller_a"  # pragma: allowlist secret
    identity_manager.add_ephemeral_token(
        hashlib.sha256(tok_a.encode()).hexdigest(),
        Principal(id="caller-a", type="bot", name="Caller A", scopes=["memory.read", "scope:team-a"]),
        name="starve-caller-a",
    )
    store = get_memory_store()
    older_a = store.append(
        MemoryEntry(
            id=new_entry_id(),
            entry_type="session_digest",
            author_principal="bot:caller-a",
            source_product="codex",
            body={"summary": "older caller-a digest", "scope": "team-a"},
            created_at="2026-10-29T10:00:00Z",
        )
    )
    for idx in range(CONTEXT_DIGEST_LIMIT + 3):
        store.append(
            MemoryEntry(
                id=new_entry_id(),
                entry_type="session_digest",
                author_principal="bot:caller-b",
                source_product="codex",
                body={"summary": f"newer team-b digest {idx}", "scope": "team-b"},
                created_at=f"2026-10-29T11:00:{idx:02d}Z",
            )
        )
    resp_a = remote_client.get("/api/v1/memory/context-pack", headers=_auth(tok_a))
    assert resp_a.status_code == 200
    assert [d["id"] for d in resp_a.json()["session_digests"]] == [older_a.id]
