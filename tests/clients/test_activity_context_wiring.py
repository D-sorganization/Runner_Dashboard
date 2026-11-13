"""Tests for Activity C2 (#2863): briefing, session hooks, fleet client/MCP tools & tier docs."""

from __future__ import annotations

import datetime as _dt
import typing as _typing
from pathlib import Path
from typing import Any
from unittest import mock

if not hasattr(_dt, "UTC"):
    _dt.UTC = _dt.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017
if not hasattr(_typing, "Self"):
    from typing_extensions import Self as _Self  # noqa: UP035

    _typing.Self = _Self  # type: ignore[attr-defined]

import fleetctl
import pytest
from activity import ActivityEnvelope, ActivityStore
from agent_bus import session_hooks as hooks
from coordination import briefing as briefing_mod
from fleet_client import FleetAPIError, FleetArgumentError, FleetClient
from fleet_fixtures import _clean_fleet_env, fake_api  # noqa: F401
from fleet_mcp import FleetMCPServer
from fleet_tools import BY_CLI, BY_TOOL

REPO_ROOT = Path(__file__).resolve().parents[2]


def _client(fake_api: Any, **kw: Any) -> FleetClient:
    return FleetClient(fake_api.url, token="tok", agent="codex", **kw)


@pytest.mark.asyncio
async def test_briefing_includes_shared_activity_context_and_degrades_to_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import activity

    assert "activity_context" in briefing_mod.ENDPOINTS
    store = ActivityStore(tmp_path / "activity.sqlite3")
    store.ingest(
        [
            ActivityEnvelope(
                schema_version=1,
                event_id="evt-c2-1",
                source="job_board",
                principal="agent-codex",
                node="node-alpha",
                session_id="codex-1",
                seq=1,
                ts=_dt.datetime.now(_dt.UTC).isoformat(),
                repo="Runner_Dashboard",
                issue=2863,
                kind="claim",
                payload={"holder_principal": "agent-codex", "fencing_token_id": "lease-2863"},
                redaction_version=1,
            )
        ],
        principal="agent-codex",
    )
    monkeypatch.setattr(activity, "get_activity_store", lambda: store)

    brief = await briefing_mod.build_briefing(repo="Runner_Dashboard", agent="codex")
    ctx = brief["activity_context"]
    assert ctx["budget_tokens"] == 1500
    assert ctx["truncated"] is False
    assert ctx["claims"][0]["issue"] == 2863
    assert ctx["claims"][0]["holder"] == "agent-codex"

    def _broken_store() -> ActivityStore:
        raise RuntimeError("hub activity db unreachable")

    monkeypatch.setattr(activity, "get_activity_store", _broken_store)
    degraded = await briefing_mod.build_briefing(repo="Runner_Dashboard", agent="codex")
    assert degraded["activity_context"]["status"] == "unavailable"
    assert degraded["activity_context"]["available"] is False
    assert any("activity_context unavailable" in w for w in degraded["warnings"])

    unreachable_client = FleetClient("http://127.0.0.1:9", token="tok", agent="codex")
    with mock.patch.object(
        unreachable_client,
        "request",
        side_effect=FleetAPIError(0, {"error": "unreachable", "reason": "connection refused"}),
    ):
        client_degraded = unreachable_client.briefing(repo="Runner_Dashboard")
    assert client_degraded["status"] == "unavailable"
    assert client_degraded["available"] is False
    assert client_degraded["activity_context"]["status"] == "unavailable"


@pytest.mark.unit
def test_session_start_includes_activity_context_and_degrades_when_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "agent-bus-state.json"
    monkeypatch.setattr(hooks, "state_file_path", lambda: state_path)
    client = FleetClient(base_url="http://127.0.0.1:9", agent="local", session="local-testhost-20261030-abcd")

    def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        if path == "/api/v1/agent/register":
            return {"session": client.session, "token": "tok_c2", "principal_id": f"fleet-session:{client.session}"}
        if path.startswith("/api/v1/agent/inbox"):
            return {"session": client.session, "messages": [], "next_cursor": 0}
        if path == "/api/v1/activity/context":
            return {
                "schema_version": 1,
                "budget_tokens": 1500,
                "estimated_tokens": 12,
                "truncated": False,
                "text": "ok",
            }
        raise FleetAPIError(404, {"detail": "not found"})

    with mock.patch.object(client, "request", side_effect=fake_request):
        summary = hooks.session_start(client, role="local", provider="cursor", node="node-a")
    assert summary["activity_context"]["text"] == "ok"

    with mock.patch.object(client, "request", side_effect=FleetAPIError(0, {"error": "unreachable"})):
        offline = hooks.session_start(client, role="local", provider="cursor", node="node-a")
    assert offline["offline"] is True
    assert offline["activity_context"]["status"] == "unavailable"


@pytest.mark.unit
def test_activity_context_and_tail_session_enforce_budget_and_scopes(
    fake_api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert {"activity_context", "tail_session"} <= set(BY_TOOL)
    assert {"activity-context", "tail-session"} <= set(BY_CLI)

    client = _client(fake_api)
    for bad_budget in (0, -1, 4001, True):
        with pytest.raises(FleetArgumentError, match="budget_tokens"):
            client.activity_context(budget_tokens=bad_budget)  # type: ignore[arg-type]
        with pytest.raises(FleetArgumentError, match="budget_tokens"):
            client.tail_session("codex-1", budget_tokens=bad_budget)  # type: ignore[arg-type]
    for bad_limit in (0, 501):
        with pytest.raises(FleetArgumentError, match="limit"):
            client.tail_session("codex-1", limit=bad_limit)
    with pytest.raises(FleetArgumentError, match="after_seq"):
        client.tail_session("codex-1", after_seq=-2)
    assert fake_api.requests == []

    # Caller scope enforcement: checkin-only caller scope rejected before network I/O
    monkeypatch.setenv("FLEET_SCOPES", "activity.checkin")
    with pytest.raises(FleetAPIError) as exc_ctx:
        _client(fake_api).activity_context()
    assert exc_ctx.value.status == 403 and "activity.read" in str(exc_ctx.value.body)
    with pytest.raises(FleetAPIError) as exc_tail:
        _client(fake_api).tail_session("codex-1")
    assert exc_tail.value.status == 403 and "activity.read" in str(exc_tail.value.body)
    assert fake_api.requests == []
    monkeypatch.delenv("FLEET_SCOPES")

    # Hub 403 translates to explicit activity.read scope message
    fake_api.respond("GET", "/api/v1/activity/context", 403, {"detail": "forbidden"})
    with pytest.raises(FleetAPIError) as info:
        client.activity_context(repo="Runner_Dashboard", budget_tokens=1200)
    assert "activity.read" in info.value.to_envelope()["message"]

    # tail_session enforces hard token budget cap on returned transcript chunks
    chunks_payload = {
        "session_id": "codex-1",
        "items": [
            {"seq": 1, "text": "A" * 120, "data": "A" * 120, "byte_len": 120},
            {"seq": 2, "text": "B" * 400, "data": "B" * 400, "byte_len": 400},
        ],
        "gaps": [],
        "next_seq": None,
    }
    fake_api.respond("GET", "/api/v1/activity/sessions/codex-1/chunks", 200, chunks_payload)
    tailed = client.tail_session("codex-1", after_seq=0, limit=50, budget_tokens=50)
    assert tailed["truncated"] is True
    assert tailed["budget_tokens"] == 50
    assert tailed["estimated_tokens"] <= 50
    assert [c["seq"] for c in tailed["items"]] == [1]
    assert tailed["next_seq"] == 1


@pytest.mark.unit
def test_mcp_and_cli_and_tier_docs(capsys: pytest.CaptureFixture[str], fake_api: Any) -> None:
    fake_api.respond(
        "GET",
        "/api/v1/activity/context",
        200,
        {"schema_version": 1, "budget_tokens": 800, "estimated_tokens": 10, "truncated": False, "text": "ctx"},
    )
    server = FleetMCPServer(_client(fake_api))
    res = server.call_tool(
        {"name": "activity_context", "arguments": {"repo": "Runner_Dashboard", "budget_tokens": 800}}
    )
    assert res["isError"] is False
    bad = server.call_tool({"name": "tail_session", "arguments": {"session_id": "codex-1", "budget_tokens": 9000}})
    assert bad["isError"] is True

    assert (
        fleetctl.main(["--url", fake_api.url, "--as-agent", "codex", "activity-context", "--repo", "Runner_Dashboard"])
        == 0
    )
    assert (
        fleetctl.main(["--url", fake_api.url, "--as-agent", "codex", "tail-session", "codex-1", "--limit", "10"]) == 0
    )
    capsys.readouterr()

    connect_doc = (REPO_ROOT / "docs" / "agents" / "connect.md").read_text(encoding="utf-8")
    assert "activity_context" in connect_doc and "tail_session" in connect_doc
    assert "Connected tier" in connect_doc and "Unconnected tier" in connect_doc
    assert "fleetctl checkin" in connect_doc
