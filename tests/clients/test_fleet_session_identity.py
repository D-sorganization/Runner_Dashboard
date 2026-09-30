"""Default fleet sessions are unique per client, stable per client, and resumable (BR-05, #1799).

``default_session`` derived ``<agent>-<host>-<YYYYMMDD>``, so two Codex sessions on one computer
on one day shared an identity: presence, mailbox attribution and release all collided. A derived
session now ends in a random suffix chosen once per client; an explicit session still wins.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import fleet_client as fc
import fleetctl
import pytest
from fleet_client import FleetClient
from fleet_fixtures import _clean_fleet_env, fake_api  # pytest fixtures
from test_fleet_mcp import INIT, _call
from test_fleet_mcp import _session as _mcp_session


def _inbox_session(client: FleetClient, fake_api: Any) -> str:
    client.inbox()
    return str(fake_api.last.query["session"])


def test_default_session_keeps_the_readable_prefix_and_adds_a_suffix() -> None:
    day = dt.date(2026, 9, 29)
    fixed = fc.default_session("codex", "DeskComputer.ts.net", day, nonce="a1b2c3")
    assert fixed == "codex-DeskComputer-20260929-a1b2c3"
    first, second = (fc.default_session("codex", "DeskComputer", day) for _ in range(2))
    assert first != second
    assert first.startswith("codex-DeskComputer-20260929-")
    long = fc.default_session("grok", "h" * 300, day)
    assert len(long) <= 128 and long.startswith("grok-") and fc.PATTERNS.session.match(long)


def test_two_clients_for_one_agent_on_one_host_never_share_a_session(fake_api: Any) -> None:
    sessions = {_inbox_session(FleetClient(fake_api.url, agent="codex"), fake_api) for _ in range(5)}
    assert len(sessions) == 5
    assert all(s.startswith("codex-") for s in sessions)


def test_one_client_keeps_its_session_across_calls(fake_api: Any) -> None:
    client = FleetClient(fake_api.url, agent="codex")
    client.inbox()
    client.claim("Runner_Dashboard", 4)
    client.release_presence("Runner_Dashboard")
    sent = [r.query.get("session") or r.body["session"] for r in fake_api.requests]
    assert sent == [client.session] * 3


def test_a_per_call_agent_session_is_also_stable(fake_api: Any) -> None:
    client = FleetClient(fake_api.url)
    client.claim("Runner_Dashboard", 4, agent="grok")
    client.release_claim("Runner_Dashboard", 4, agent="grok")
    first, second = (r.body["session"] for r in fake_api.requests)
    assert first == second and first.startswith("grok-")


def test_release_by_one_session_names_only_that_session(fake_api: Any) -> None:
    a, b = FleetClient(fake_api.url, agent="codex"), FleetClient(fake_api.url, agent="codex")
    a.register_presence("Runner_Dashboard", issue=1, branch="x")
    b.register_presence("Runner_Dashboard", issue=2, branch="y")
    a.release_presence("Runner_Dashboard")
    assert fake_api.last.body == {"session": a.session, "repo": "Runner_Dashboard"}
    assert a.session != b.session


def test_deliberate_resume_reuses_the_session(fake_api: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    first = FleetClient(fake_api.url, agent="codex")
    resumed = FleetClient(fake_api.url, agent="codex", session=first.session)
    assert _inbox_session(resumed, fake_api) == first.session

    monkeypatch.setenv("FLEET_SESSION", str(first.session))
    assert _inbox_session(FleetClient(fake_api.url, agent="codex"), fake_api) == first.session


def test_identity_separates_principal_host_and_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("socket.gethostname", lambda: "DeskComputer.tail.ts.net")
    derived = FleetClient("http://127.0.0.1:9", agent="codex").identity()
    assert derived["agent"] == "codex"
    assert derived["host"] == "DeskComputer"
    assert derived["session"].startswith("codex-DeskComputer-")
    assert derived["session_source"] == "derived"

    monkeypatch.setenv("FLEET_SESSION", "codex-resume-1")
    assert FleetClient("http://127.0.0.1:9", agent="codex").identity()["session_source"] == "env"
    explicit = FleetClient("http://127.0.0.1:9", agent="codex", session="codex-x")
    assert explicit.identity()["session_source"] == "argument"


def test_cli_announces_a_derived_session_so_it_can_be_resumed(
    capsys: pytest.CaptureFixture[str], fake_api: Any
) -> None:
    assert fleetctl.main(["--url", fake_api.url, "--as-agent", "codex", "inbox"]) == 0
    captured = capsys.readouterr()
    session = fake_api.last.query["session"]
    assert json.loads(captured.out)["ok"] is True
    assert f"FLEET_SESSION={session}" in captured.err

    assert fleetctl.main(["--url", fake_api.url, "--as-agent", "codex", "--as-session", session, "inbox"]) == 0
    assert capsys.readouterr().err == ""
    assert fake_api.last.query["session"] == session


def test_mcp_servers_get_distinct_sessions_and_keep_them(fake_api: Any) -> None:
    calls = [INIT, _call(2, "fleet_inbox", {}), _call(3, "fleet_inbox", {})]
    _mcp_session(fake_api, calls, {"FLEET_AGENT": "codex"})
    _mcp_session(fake_api, calls, {"FLEET_AGENT": "codex"})
    sessions = [r.query["session"] for r in fake_api.requests]
    assert sessions[0] == sessions[1] and sessions[2] == sessions[3]
    assert sessions[0] != sessions[2]
