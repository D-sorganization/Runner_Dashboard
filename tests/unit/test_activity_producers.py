"""Activity producer adapter (issue #2859, epic #2856 slice A3)."""

from __future__ import annotations

from pathlib import Path

import activity_producers
import pytest
from activity import ActivityStore


@pytest.fixture(autouse=True)
def _reset_seq_counter() -> None:
    activity_producers._next_seq.clear()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ActivityStore:
    real_store = ActivityStore(tmp_path / "activity.sqlite3")
    monkeypatch.setattr(activity_producers, "get_activity_store", lambda: real_store)
    return real_store


@pytest.mark.unit
def test_emit_activity_appends_a_durable_envelope(store: ActivityStore) -> None:
    activity_producers.emit_activity(
        source="staff_run", session_id="run-1", node="node-c", kind="start", payload={"text": "hello"}
    )
    page = store.read(session_id="run-1")
    assert [item.kind for item in page.items] == ["start"]
    assert page.items[0].payload == {"text": "hello"}
    assert page.items[0].seq == 1


@pytest.mark.unit
def test_seq_is_monotonically_increasing_per_source_and_session(store: ActivityStore) -> None:
    activity_producers.emit_activity(source="coordination", session_id="codex-1", node="hub", kind="presence")
    activity_producers.emit_activity(source="coordination", session_id="codex-1", node="hub", kind="message")
    activity_producers.emit_activity(source="coordination", session_id="codex-2", node="hub", kind="presence")
    page = store.read(session_id="codex-1")
    assert [item.seq for item in page.items] == [1, 2]
    other = store.read(session_id="codex-2")
    assert [item.seq for item in other.items] == [1]


@pytest.mark.unit
def test_explicit_seq_is_reused_so_a_retry_is_idempotent(store: ActivityStore) -> None:
    activity_producers.emit_activity(
        source="staff_run", session_id="run-1", node="node-c", kind="exit", seq=7, payload={"text": "exit 0"}
    )
    activity_producers.emit_activity(
        source="staff_run", session_id="run-1", node="node-c", kind="exit", seq=7, payload={"text": "exit 0"}
    )
    page = store.read(session_id="run-1")
    assert len(page.items) == 1
    assert page.items[0].seq == 7


@pytest.mark.unit
def test_emit_activity_never_raises_when_the_store_is_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_store() -> ActivityStore:
        raise RuntimeError("activity store unavailable")

    monkeypatch.setattr(activity_producers, "get_activity_store", broken_store)
    activity_producers.emit_activity(source="agent_bus", session_id="s-1", node="node-c", kind="session_start")


@pytest.mark.unit
def test_emit_activity_never_raises_when_ingest_itself_fails(
    store: ActivityStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_ingest(*_a: object, **_k: object) -> None:
        raise RuntimeError("ingest exploded")

    monkeypatch.setattr(store, "ingest", broken_ingest)
    activity_producers.emit_activity(source="agent_bus", session_id="s-1", node="node-c", kind="pre_push_heartbeat")


@pytest.mark.unit
def test_record_job_claim_activity_emits_required_fields_and_redacts_secret_fencing_token(
    store: ActivityStore,
) -> None:
    token_id = activity_producers.record_job_claim_activity(
        kind="claim",
        repo="Runner_Dashboard",
        issue=2861,
        agent="codex",
        session="codex-2861",
        holder_principal="agent-codex",
        receipt="https://github.com/D-sorganization/Runner_Dashboard/issues/2861#issuecomment-777",
        fencing_token="abs_super_secret_ephemeral_token_value",
    )
    assert token_id.startswith("ftk_")
    assert "super_secret" not in token_id
    page = store.read(session_id="codex-2861")
    assert len(page.items) == 1
    env = page.items[0]
    assert env.source == "job_board"
    assert env.kind == "claim"
    assert env.repo == "Runner_Dashboard"
    assert env.issue == 2861
    assert env.session_id == "codex-2861"
    assert env.payload["repo"] == "Runner_Dashboard"
    assert env.payload["issue"] == 2861
    assert env.payload["holder_principal"] == "agent-codex"
    assert env.payload["session"] == "codex-2861"
    assert env.payload["fencing_token_id"] == token_id
    assert "super_secret" not in str(env.payload)
