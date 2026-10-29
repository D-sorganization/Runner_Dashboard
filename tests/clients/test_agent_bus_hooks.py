"""Agent bus hook helpers (#2155 slice 2): session start and pre-push heartbeat."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import activity_producers
import pytest
from activity import ActivityStore
from agent_bus import session_hooks as hooks
from fleet_client import FleetAPIError, FleetClient
from fleet_validators import default_session


@pytest.fixture(autouse=True)
def _reset_activity_seq() -> None:
    activity_producers._next_seq.clear()


@pytest.fixture
def activity_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ActivityStore:
    real_store = ActivityStore(tmp_path / "activity.sqlite3")
    monkeypatch.setattr(activity_producers, "get_activity_store", lambda: real_store)
    return real_store


@pytest.fixture
def state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "agent-bus-state.json"
    monkeypatch.setattr(hooks, "state_file_path", lambda: path)
    return path


@pytest.mark.unit
def test_session_start_registers_and_persists_token(state_file: Path) -> None:
    client = FleetClient(base_url="http://127.0.0.1:9", agent="local", session="local-testhost-20261022-abcd")
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, path: str, **kwargs):  # noqa: ANN003
        calls.append((method, path))
        if path == "/api/v1/agent/register":
            return {
                "session": client.session,
                "token": "tok_test_register_inbox_01",
                "principal_id": f"fleet-session:{client.session}",
            }
        if path.startswith("/api/v1/agent/inbox"):
            return {"session": client.session, "messages": [{"id": "abm_1", "type": "request"}], "next_cursor": 1}
        if path.startswith("/api/v1/staff/skills"):
            if path == "/api/v1/staff/skills":
                return {"count": 0, "skills": []}
            return {
                "name": "demo",
                "version": "1.0.0",
                "description": "d",
                "roles": ["local"],
                "repos": ["Runner_Dashboard"],
                "task_types": ["demo"],
                "content_hash": "abc",
                "pin": "demo@1.0.0+abc",
                "body": "x",
                "attachments": [],
            }
        raise AssertionError(path)

    with mock.patch.object(client, "request", side_effect=fake_request):
        summary = hooks.session_start(client, role="local", provider="cursor", node="node-c")

    assert summary["registered"] is True
    assert summary["inbox_count"] == 1
    assert summary.get("skills_exported", 0) >= 0
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["session"] == client.session
    assert saved["token"] == "tok_test_register_inbox_01"


@pytest.mark.unit
def test_session_start_noop_when_dashboard_unreachable(state_file: Path) -> None:
    client = FleetClient(base_url="http://127.0.0.1:9", agent="codex", session=default_session("codex"))

    def boom(*_a, **_k):  # noqa: ANN002
        raise FleetAPIError(0, {"error": "unreachable"})

    with mock.patch.object(client, "request", side_effect=boom):
        summary = hooks.session_start(client, role="codex", provider="codex", node="Hub")
    assert summary["offline"] is True
    assert not state_file.exists()


@pytest.mark.unit
def test_session_start_reregisters_when_cached_token_is_rejected(state_file: Path) -> None:
    """#3496: a dashboard restart wipes in-memory ephemeral tokens, so a cached
    token from a prior session can come back 401/403. session_start must drop
    it and re-register instead of crashing the caller."""
    sess = "local-testhost-20261022-stale"
    state_file.write_text(
        json.dumps({"session": sess, "token": "tok_stale_revoked", "role": "local"}),
        encoding="utf-8",
    )
    client = FleetClient(base_url="http://127.0.0.1:9", agent="local", session=sess)
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, path: str, **kwargs):  # noqa: ANN003
        calls.append((method, path))
        if path.startswith("/api/v1/agent/inbox"):
            if client.token == "tok_stale_revoked":
                raise FleetAPIError(401, {"detail": "unknown or revoked token"})
            return {"session": sess, "messages": [], "next_cursor": 0}
        if path == "/api/v1/agent/register":
            return {"session": sess, "token": "tok_fresh_reregistered", "principal_id": f"fleet-session:{sess}"}
        raise AssertionError(path)

    with mock.patch.object(client, "request", side_effect=fake_request):
        summary = hooks.session_start(client, role="local", provider="cursor", node="node-c")

    assert summary["registered"] is True
    assert summary["offline"] is False
    assert [p for _, p in calls].count("/api/v1/agent/register") == 1
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["token"] == "tok_fresh_reregistered"


@pytest.mark.unit
def test_session_start_reraises_non_auth_errors_with_cached_token(state_file: Path) -> None:
    """A non-auth failure (e.g. 500) with a cached token must not be treated as
    a stale-token condition; it should propagate like any other API error."""
    sess = "local-testhost-20261022-stale500"
    state_file.write_text(
        json.dumps({"session": sess, "token": "tok_cached", "role": "local"}),
        encoding="utf-8",
    )
    client = FleetClient(base_url="http://127.0.0.1:9", agent="local", session=sess)

    def fake_request(method: str, path: str, **kwargs):  # noqa: ANN003
        if path.startswith("/api/v1/agent/inbox"):
            raise FleetAPIError(500, {"detail": "internal error"})
        raise AssertionError(path)

    with mock.patch.object(client, "request", side_effect=fake_request):
        with pytest.raises(FleetAPIError) as excinfo:
            hooks.session_start(client, role="local", provider="cursor", node="node-c")
    assert excinfo.value.status == 500


@pytest.mark.unit
def test_pre_push_clears_stale_state_when_token_is_rejected(state_file: Path) -> None:
    """#3496: pre_push has no role to re-register with, so on a rejected/expired
    token it must clear the stale cached state (forcing a full re-register at
    the next session_start) and return cleanly instead of raising."""
    sess = "issue-remediator-host-20261022-stale"
    state_file.write_text(
        json.dumps({"session": sess, "token": "tok_stale_revoked", "role": "issue-remediator"}),
        encoding="utf-8",
    )
    client = FleetClient(
        base_url="http://127.0.0.1:9", token="tok_stale_revoked", session=sess, agent="issue-remediator"
    )

    def fake_request(method: str, path: str, **kwargs):  # noqa: ANN003
        if path == "/api/v1/agent/heartbeat":
            raise FleetAPIError(403, {"detail": "unknown or revoked token"})
        raise AssertionError(path)

    with mock.patch.object(client, "request", side_effect=fake_request):
        out = hooks.pre_push(client)

    assert out["heartbeat"] is False
    assert out.get("stale_token") is True
    assert not state_file.exists()


@pytest.mark.unit
def test_pre_push_heartbeats_with_saved_token(state_file: Path) -> None:
    sess = "issue-remediator-host-20261022-xyz"
    state_file.write_text(
        json.dumps({"session": sess, "token": "tok_test_saved_state_01", "role": "issue-remediator"}),
        encoding="utf-8",
    )
    client = FleetClient(
        base_url="http://127.0.0.1:9", token="tok_test_saved_state_01", session=sess, agent="issue-remediator"
    )
    seen: list[str] = []

    def fake_request(method: str, path: str, **kwargs):  # noqa: ANN003
        seen.append(path)
        if path == "/api/v1/agent/heartbeat":
            return {"session": sess, "lease_expires_at": "2026-10-22T05:00:00Z"}
        if path.startswith("/api/v1/agent/inbox"):
            return {"session": sess, "messages": [], "next_cursor": 0}
        raise AssertionError(path)

    with mock.patch.object(client, "request", side_effect=fake_request):
        out = hooks.pre_push(client)
    assert out["heartbeat"] is True
    assert "/api/v1/agent/heartbeat" in seen


# ── activity log producer adapter (#2859) ──────────────────────────────────
@pytest.mark.unit
def test_session_start_feeds_the_activity_log(state_file: Path, activity_store: ActivityStore) -> None:
    client = FleetClient(base_url="http://127.0.0.1:9", agent="local", session="local-testhost-20261022-abcd")

    def fake_request(method: str, path: str, **kwargs):  # noqa: ANN003
        if path == "/api/v1/agent/register":
            return {"session": client.session, "token": "tok_activity_01", "principal_id": "fleet-session:x"}
        if path.startswith("/api/v1/agent/inbox"):
            return {"session": client.session, "messages": [], "next_cursor": 0}
        raise AssertionError(path)

    with mock.patch.object(client, "request", side_effect=fake_request):
        hooks.session_start(client, role="local", provider="cursor", node="node-c")

    page = activity_store.read(session_id=client.session)
    assert [item.kind for item in page.items] == ["session_start"]
    assert page.items[0].source == "agent_bus"
    assert page.items[0].node == "node-c"


@pytest.mark.unit
def test_session_start_offline_does_not_feed_the_activity_log(state_file: Path, activity_store: ActivityStore) -> None:
    client = FleetClient(base_url="http://127.0.0.1:9", agent="codex", session=default_session("codex"))

    def boom(*_a, **_k):  # noqa: ANN002
        raise FleetAPIError(0, {"error": "unreachable"})

    with mock.patch.object(client, "request", side_effect=boom):
        hooks.session_start(client, role="codex", provider="codex", node="Hub")
    assert activity_store.read(session_id=client.session).items == []


@pytest.mark.unit
def test_pre_push_feeds_the_activity_log(state_file: Path, activity_store: ActivityStore) -> None:
    sess = "issue-remediator-host-20261022-xyz"
    state_file.write_text(
        json.dumps({"session": sess, "token": "tok_test_saved_state_01", "role": "issue-remediator"}),
        encoding="utf-8",
    )
    client = FleetClient(
        base_url="http://127.0.0.1:9", token="tok_test_saved_state_01", session=sess, agent="issue-remediator"
    )

    def fake_request(method: str, path: str, **kwargs):  # noqa: ANN003
        if path == "/api/v1/agent/heartbeat":
            return {"session": sess, "lease_expires_at": "2026-10-22T05:00:00Z"}
        if path.startswith("/api/v1/agent/inbox"):
            return {"session": sess, "messages": [{"id": "m1"}], "next_cursor": 1}
        raise AssertionError(path)

    with mock.patch.object(client, "request", side_effect=fake_request):
        hooks.pre_push(client)

    page = activity_store.read(session_id=sess)
    assert [item.kind for item in page.items] == ["pre_push_heartbeat"]
    assert page.items[0].payload == {"inbox_count": 1}
    assert page.items[0].node


@pytest.mark.unit
def test_pre_push_survives_a_broken_activity_store(state_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sess = "issue-remediator-host-20261022-xyz"
    state_file.write_text(
        json.dumps({"session": sess, "token": "tok_test_saved_state_01", "role": "issue-remediator"}),
        encoding="utf-8",
    )
    client = FleetClient(
        base_url="http://127.0.0.1:9", token="tok_test_saved_state_01", session=sess, agent="issue-remediator"
    )

    def broken_store():  # noqa: ANN202
        raise RuntimeError("activity store unavailable")

    monkeypatch.setattr(activity_producers, "get_activity_store", broken_store)

    def fake_request(method: str, path: str, **kwargs):  # noqa: ANN003
        if path == "/api/v1/agent/heartbeat":
            return {"session": sess, "lease_expires_at": "2026-10-22T05:00:00Z"}
        if path.startswith("/api/v1/agent/inbox"):
            return {"session": sess, "messages": [], "next_cursor": 0}
        raise AssertionError(path)

    with mock.patch.object(client, "request", side_effect=fake_request):
        out = hooks.pre_push(client)
    assert out["heartbeat"] is True
